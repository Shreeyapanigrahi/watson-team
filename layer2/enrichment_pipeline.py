"""The enrichment pipeline (brief 5.3): runs when a document reaches ACTIVE.

  taxonomy check -> NER -> entity resolution -> sentiment -> embeddings -> vector index -> triples -> brand links

Order, and why it differs from the brief's list:
  * The taxonomy check (the brief's stage 5) runs FIRST. It has no side effects and the brief says it must pass
    before any triple is written; running it first also means a bad document leaves no provisional entities behind.
  * Everything that can fail without changing a store (NER, sentiment, embeddings) runs before anything is committed.
  * The vectors are added before the graph write so the graph can record each chunk's faiss id. If the graph write then
    fails, the vectors are removed again, so the index and the graph never disagree.

A failed run logs ENRICHMENT_FAILED with the stage and the error, and the document stays ACTIVE (brief). Its status
shows FAILED in the UI with a retry button. Nothing is half-written: the graph write is all-or-nothing.

Enrichment runs on one background worker, so a review click returns at once and two documents never write at the same
time. Tests and the CLI call run_now() instead.
"""
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Optional

from layer1 import audit_log, lifecycle_manager as lifecycle
from layer1.errors import LifecycleViolationError
from layer1.jsonstore import LOCK, read_json, write_json

from . import config, embedder, entity_extractor, entity_resolver, kg as kgmod, sentiment_extractor, taxonomy_manager, triple_writer, vector_index
from .catalog.catalog_store import load
from .populate_graph import ensure_seeded

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="enrich")
_sync = {"on": False}
_pending = 0
_pending_lock = threading.Lock()


# ---------------------------------------------------------------- status store
def _status_path():
    return config.store_path("enrichment_status.json")


def get_status(document_uri: str) -> Optional[dict]:
    return read_json(_status_path(), {}).get(document_uri)


def all_status() -> dict:
    return read_json(_status_path(), {})


def _set_status(document_uri: str, **fields) -> dict:
    with LOCK:
        store = read_json(_status_path(), {})
        cur = store.setdefault(document_uri, {})
        cur.update(fields)
        write_json(_status_path(), store)
        return cur


def clear_status() -> None:
    write_json(_status_path(), {})
    write_json(_mentions_path(), {})


# Every mention with its text, label, span, chunk, confidence and method (brief 5.3 stage 1). The graph keeps only
# "this chunk mentions that entity"; the evidence behind each mention lives here and is shown on the document's
# Knowledge tab, only to users who may read the document.
def _mentions_path():
    return config.store_path("entity_mentions.json")


def get_mentions(document_uri: str) -> list:
    return read_json(_mentions_path(), {}).get(document_uri, [])


def _save_mentions(document_uri: str, mentions: list) -> None:
    with LOCK:
        store = read_json(_mentions_path(), {})
        store[document_uri] = mentions
        write_json(_mentions_path(), store)


# ---------------------------------------------------------------- the run
def run_now(document_uri: str, actor: str = "system", now: Optional[str] = None) -> dict:
    """Enrich one document in this thread. Returns its status record; never raises for a pipeline failure."""
    started = time.time()
    now = now or audit_log.now_iso()
    try:
        lifecycle.assert_indexable(document_uri)
    except LifecycleViolationError as e:
        return _set_status(document_uri, state="SKIPPED", stage="precondition", error=str(e), finished=now)
    audit_log.log_event("ENRICHMENT_START", document_uri, {"actor": actor})
    _set_status(document_uri, state="RUNNING", stage="start", started=now, error=None, finished=None)
    stage, vectors_added = "load", False
    try:
        record = lifecycle.get_record(document_uri)
        chunks = lifecycle.load_staging(document_uri)["chunks"]
        if not chunks:
            raise ValueError("the document has no chunks (no extractable text), so there is nothing to enrich")
        kg, cat, index = kgmod.get(), load(), entity_extractor.term_index()
        ensure_seeded(kg, cat)

        stage = "taxonomy"
        versions = taxonomy_manager.validate_document(record)
        stage = "ner"
        raw = [m for ch in chunks for m in entity_extractor.extract(ch["text"], ch["chunk_uri"], index)]
        stage = "resolution"
        mentions, by_method = entity_resolver.resolve_all(raw, document_uri, index)
        stage = "sentiment"
        sentiments, skipped = sentiment_extractor.extract(chunks, mentions, record.get("extracted_date"), index, cat)
        stage = "embedding"
        vectors = embedder.embed([c["text"] for c in chunks])
        stage = "index"
        faiss_ids = vector_index.get().add(document_uri, [c["chunk_uri"] for c in chunks], vectors)
        vectors_added = True
        stage = "triples"
        with kg.write() as g:
            counts = triple_writer.write_document(g, record, chunks, mentions, sentiments, faiss_ids, versions, cat, now)
            links = triple_writer.relink_brands(g)
            triple_writer.prune_orphan_provisionals(g)
        _save_mentions(document_uri, mentions)
        result = {**counts, **links, "mentions": len(mentions), "mentions_by_method": by_method,
                  "sentiment_statements_skipped": len(skipped), "vectors": len(faiss_ids)}
        audit_log.log_event("ENRICHMENT_COMPLETE", document_uri, {
            **{k: v for k, v in result.items() if k != "mentions_by_method"}, "catalog_version": cat.version,
            "seconds": round(time.time() - started, 2)})
        return _set_status(document_uri, state="COMPLETE", stage="done", error=None, finished=audit_log.now_iso(),
                           catalog_version=cat.version, skipped_sentiment=skipped, **result)
    except Exception as e:                                           # any stage: log, keep ACTIVE, keep stores consistent
        if vectors_added:
            vector_index.get().remove_document(document_uri)
        audit_log.log_event("ENRICHMENT_FAILED", document_uri, {"stage": stage, "error": f"{type(e).__name__}: {e}"})
        return _set_status(document_uri, state="FAILED", stage=stage, error=f"{type(e).__name__}: {e}", finished=audit_log.now_iso())


def deactivate(document_uri: str, new_state: str) -> dict:
    """The document left ACTIVE: mark it in the graph, drop its vectors, recompute brand links. Lineage stays."""
    kg = kgmod.get()
    with kg.write() as g:
        known = triple_writer.set_state(g, document_uri, new_state)
        triple_writer.relink_brands(g)
    removed = vector_index.get().remove_document(document_uri)
    return _set_status(document_uri, state="DEACTIVATED" if known else "NOT_ENRICHED", stage=new_state,
                       vectors_removed=removed, finished=audit_log.now_iso())


# ---------------------------------------------------------------- background worker and lifecycle hook
def set_sync(on: bool) -> None:
    """Tests and the CLI run jobs inline instead of on the worker."""
    _sync["on"] = on


def _submit(fn, *args) -> Future:
    global _pending
    if _sync["on"]:
        f = Future()
        f.set_result(fn(*args))
        return f
    with _pending_lock:
        _pending += 1

    def job():
        global _pending
        try:
            return fn(*args)
        finally:
            with _pending_lock:
                _pending -= 1
    return _executor.submit(job)


def enqueue(document_uri: str, actor: str = "system") -> Future:
    _set_status(document_uri, state="QUEUED", stage="queued", error=None)
    return _submit(run_now, document_uri, actor)


def pending_jobs() -> int:
    return _pending


def wait_idle(timeout: float = 120) -> bool:
    end = time.time() + timeout
    while _pending and time.time() < end:
        time.sleep(0.05)
    return not _pending


def forget(document_uri: str) -> dict:
    """The document is being deleted: remove everything Layer 2 holds for it (graph facts with their chunks and sentiment, vectors,
    mentions, enrichment status, its place in provisional entities), then recompute brand links. The search-telemetry log is kept
    (a record of what was searched), like the audit log. Raises if a store cannot be written, so the caller deletes nothing."""
    kg = kgmod.get()
    with kg.write() as g:
        triples = triple_writer.remove_document(g, document_uri)
        triple_writer.relink_brands(g)
        triple_writer.prune_orphan_provisionals(g)
    vectors = vector_index.get().remove_document(document_uri)
    provisional = entity_resolver.drop_document(document_uri)
    with LOCK:
        for path in (_status_path(), _mentions_path()):
            store = read_json(path, {})
            if store.pop(document_uri, None) is not None:
                write_json(path, store)
    return {"triples_removed": triples, "vectors_removed": vectors, "provisional_entities_removed": provisional}


def on_delete(document_uri: str) -> None:
    """Registered with Layer 1's lifecycle manager. Runs on the enrichment worker, behind any job already queued, so a document that is
    still being enriched is finished first and cannot be written back after it is deleted. Waits, and raises what forget raised."""
    _submit(forget, document_uri).result(timeout=300)


def on_state_change(document_uri: str, old: str, new: str, actor: str) -> None:
    """Registered with Layer 1's lifecycle manager. Layer 1 never imports Layer 2; it only calls what is registered."""
    if new == "ACTIVE":
        enqueue(document_uri, actor)
    elif old == "ACTIVE":
        _submit(deactivate, document_uri, new)


def install_hooks() -> None:
    lifecycle.register_state_hook(on_state_change)
    lifecycle.register_delete_hook(on_delete)

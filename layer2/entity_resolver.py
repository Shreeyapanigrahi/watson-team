"""Stage 2: entity resolution (brief 5.3).

For every candidate the extractor could not place in the catalog:
  1. exact / case-insensitive match against the catalog          (already done for ruler hits)
  2. rapidfuzz similarity above RESOLVE_RATIO (0.85) to a catalog label of the right kind  -> reuse that entity
  3. otherwise, if it came from the 'Brand (inn)' pattern, a Watson:Provisional_{uuid} node
     written to provisional_entities.json for a human to decide. A near-miss with no close catalog name is dropped:
     an unknown capitalised word is not an entity.

The provisional id is a uuid5 of (kind, normalised text), so the same unknown name in ten documents is one node,
and rebuilding the graph gives the same ids.

A decision is remembered. A REJECTED provisional is never created again; an APPROVED one is now in the catalog
(see catalog_admin.py), so it resolves in stage 1.
"""
import uuid
from datetime import datetime, timezone
from typing import Optional

from layer1 import audit_log
from layer1.jsonstore import LOCK, read_json, write_json

from . import config
from .entity_extractor import FUZZY_KINDS, KIND_LABEL, TermIndex, term_index

_NAMESPACE = uuid.UUID("6f1d2a52-3c1b-4a55-9a56-0a7c1ce5e000")      # fixed, so ids repeat across rebuilds


def _path():
    return config.store_path("provisional_entities.json")


def load_store() -> dict:
    return read_json(_path(), {"entities": {}})


def _save(store: dict) -> None:
    write_json(_path(), store)


def reset_pending() -> int:
    """Forget PENDING entries (they are derived from documents and come back on re-enrichment). Decisions are kept:
    a REJECTED or APPROVED entry is a human decision, not derived data."""
    with LOCK:
        store = load_store()
        keep = {k: v for k, v in store["entities"].items() if v["status"] != "PENDING"}
        n = len(store["entities"]) - len(keep)
        _save({"entities": keep})
        return n


def provisional_id(kind: str, text: str) -> str:
    return "Provisional_" + str(uuid.uuid5(_NAMESPACE, f"{kind}|{' '.join(text.lower().split())}"))


def list_provisional(status: Optional[str] = None) -> list:
    items = list(load_store()["entities"].values())
    return [e for e in items if status is None or e["status"] == status]


def _register(kind: str, text: str, document_uri: str, chunk_uri: str) -> Optional[dict]:
    pid = provisional_id(kind, text)
    with LOCK:
        store = load_store()
        e = store["entities"].get(pid)
        if e is None:
            e = {"id": pid, "label": text, "kind": kind, "status": "PENDING", "created": audit_log.now_iso(),
                 "documents": [], "chunks": [], "decided_by": None, "decided_at": None, "note": None, "resolved_to": None}
            store["entities"][pid] = e
        if e["status"] == "REJECTED":
            return None
        if document_uri not in e["documents"]:
            e["documents"].append(document_uri)
        if chunk_uri not in e["chunks"]:
            e["chunks"].append(chunk_uri)
        _save(store)
        return e


def resolve(candidate: dict, document_uri: str, index: TermIndex = None) -> Optional[dict]:
    """Turn one candidate mention into a resolved mention, or None when it is dropped. The input is not changed."""
    index = index or term_index()
    m = dict(candidate)
    hint = [candidate["kind"]] if candidate["kind"] in KIND_LABEL else list(FUZZY_KINDS)
    hit = index.nearest(candidate["text"], kinds=hint)
    if hit:
        eid, kind, label, ratio = hit
        m.update(entity_id=eid, kind=kind, label=KIND_LABEL[kind], method="fuzzy", similarity=round(ratio, 3),
                 matched_label=label)
        return m
    if candidate.get("source") != "pattern":
        return None
    e = _register(candidate["kind"], candidate["text"], document_uri, candidate["chunk_uri"])
    if e is None:
        return None
    m.update(entity_id=e["id"], method="provisional")
    return m


def resolve_all(mentions: list, document_uri: str, index: TermIndex = None) -> tuple:
    """(resolved mentions, summary). Logs ENTITY_RESOLUTION once per fuzzy or provisional entity and once per document."""
    index = index or term_index()
    out, notable = [], {}
    for m in mentions:
        if m["method"] != "candidate":
            out.append(m)
            continue
        r = resolve(m, document_uri, index)
        if r is None:
            continue
        out.append(r)
        key = (r["method"], r["entity_id"], r["text"])
        notable.setdefault(key, 0)
        notable[key] += 1
    for (method, eid, text), n in sorted(notable.items()):
        audit_log.log_event("ENTITY_RESOLUTION", document_uri, {
            "method": method, "text": text, "entity": eid, "mentions": n,
            "rule": f"similarity > {config.RESOLVE_RATIO}" if method == "fuzzy" else "no catalog match: provisional, awaiting review"})
    by_method = {}
    for m in out:
        by_method[m["method"]] = by_method.get(m["method"], 0) + 1
    audit_log.log_event("ENTITY_RESOLUTION", document_uri, {"summary": by_method, "mentions": len(out)})
    return out, by_method

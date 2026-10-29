"""Semantic search over the vector index (the retrieval step Layer 3 builds on).

Order of operations, and why:
  1. ABAC FIRST: the set of chunks this user may read (ACTIVE documents whose attributes pass acl_allows).
  2. Embed the query; search ONLY those chunks (vector_index.search(allowed=...)). A chunk the user may not read is
     never scored, so it cannot appear, shift a rank, or be inferred from a score.
  3. Re-rank: final = 0.7 * cosine + 0.3 * document quality (brief 6.1), so a weak document does not outrank a strong
     one on a tiny similarity difference.
  4. Record telemetry (what was returned to whom) and a QUERY audit event.
"""
import re

from layer1 import audit_log, lifecycle_manager as lifecycle

from . import config, embedder, graph_view, kg as kgmod, telemetry, vector_index
from .catalog.catalog_store import load
from .kg import W, local, short, uri
from rdflib.namespace import RDFS


def _snippet(text: str, n: int = 420) -> str:
    t = re.sub(r"\s+", " ", text).strip()
    return t if len(t) <= n else t[:n].rsplit(" ", 1)[0] + "..."


def search(user: dict, query: str, k: int = 8) -> dict:
    query = (query or "").strip()
    if not query:
        raise ValueError("The query is empty")
    k = max(1, min(int(k), 25))
    records = graph_view.visible_records(user)
    allowed = graph_view.visible_chunks(user, records=records)            # {chunk uri: document uri}
    raw = vector_index.get().search(embedder.embed_query(query), allowed.keys(), k=k * 3)
    kg, cat = kgmod.get(), load()
    labels = {e["id"]: e["label"] for kind in graph_view.KINDS for e in cat.instances.get(kind, [])}
    hits = []
    for chunk_uri, cosine in raw:
        doc = allowed[chunk_uri]
        quality = records[doc]["quality"]["quality_score"]
        final = config.W_COSINE * cosine + config.W_QUALITY * quality
        hits.append({"chunk_uri": chunk_uri, "document_uri": doc, "cosine": round(cosine, 4), "quality": quality, "score": round(final, 4)})
    hits.sort(key=lambda h: -h["score"])
    hits = hits[:k]
    staging = {}
    for h in hits:
        rec = records[h["document_uri"]]
        if h["document_uri"] not in staging:
            staging[h["document_uri"]] = {c["chunk_uri"]: c for c in lifecycle.load_staging(h["document_uri"])["chunks"]}
        ch = staging[h["document_uri"]][h["chunk_uri"]]
        with kg.lock:
            ents = []
            for prop in (W.mentionsBrand, W.mentionsDrug, W.mentionsIndication, W.coversGeography):
                for o in kg.g.objects(uri(h["chunk_uri"]), prop):
                    ents.append({"id": local(o), "label": labels.get(local(o), str(kg.g.value(o, RDFS.label)))})
        h.update(title=rec["metadata"].get("extracted_title"), study_type=rec["metadata"].get("study_type"),
                 sensitivity_level=rec["access"]["sensitivity_level"], snippet=_snippet(ch["text"]), entities=ents)
    telemetry.record_search(user, query, hits)
    audit_log.log_event("QUERY", None, {"user_id": user["user_id"], "role": user["role"], "query": query,
                                         "chunks_searched": len(allowed), "returned": len(hits),
                                         "documents": sorted({h["document_uri"] for h in hits})})
    return {"query": query, "searched_chunks": len(allowed), "readable_documents": len(records), "hits": hits,
            "ranking": f"{config.W_COSINE} x cosine + {config.W_QUALITY} x document quality"}

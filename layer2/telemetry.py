"""Search telemetry: one record per chunk returned to a user (telemetry_store.json).

The search endpoint writes it; SPARQL query Q10 (ABAC governance audit) loads it into a temporary graph and joins it
to the chunks' sensitivity levels. It is written by the search code path itself, so the audit is a real check of what
search returned, not a simulation. Nothing is invented: with no searches the store is empty and Q10 returns no rows.
"""
from rdflib import Graph, Literal
from rdflib.namespace import RDF

from layer1 import audit_log
from layer1.jsonstore import LOCK, read_json, write_json

from . import config, kg as kgmod
from .kg import W, dt, uri


def _path():
    return config.store_path("telemetry_store.json")


def events() -> list:
    return read_json(_path(), {"events": []})["events"]


def record_search(user: dict, query: str, hits: list) -> int:
    """hits: [{chunk_uri, document_uri, score}]. Returns the number of events written."""
    now = audit_log.now_iso()
    with LOCK:
        store = read_json(_path(), {"events": []})
        n = len(store["events"])
        for h in hits:
            n += 1
            store["events"].append({"id": n, "time": now, "user_id": user["user_id"], "role": user["role"], "query": query,
                                    "chunk_uri": h["chunk_uri"], "document_uri": h["document_uri"], "score": round(h["score"], 4)})
        write_json(_path(), store)
    return len(hits)


def clear() -> None:
    write_json(_path(), {"events": []})


def as_graph() -> Graph:
    g = kgmod.bind(Graph())
    for e in events():
        node = W[f"Retrieval_{e['id']}"]
        g.add((node, RDF.type, W.RetrievalEvent))
        g.add((node, W.requestedBy, Literal(e["user_id"])))
        g.add((node, W.userRole, Literal(e["role"])))
        g.add((node, W.eventTime, dt(e["time"])))
        g.add((node, W.retrievedChunk, uri(e["chunk_uri"])))
    return g

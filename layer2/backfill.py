"""Enrich every ACTIVE document. Rebuilds the graph and the vector index from Layer 1's store.

  python -m layer2.backfill                  # enrich ACTIVE documents that are not enriched yet
  python -m layer2.backfill --reset          # start from the bare ontology + catalog seed, enrich all ACTIVE documents
  python -m layer2.backfill --doc Watson:Doc_001

The graph, the FAISS index and the enrichment status are DERIVED data. They can be deleted and rebuilt with --reset;
tests/test_rebuild.py checks the rebuild gives the same graph. Human decisions on provisional entities are kept.
"""
import argparse
import json
import time

from layer1 import lifecycle_manager as lifecycle

from . import enrichment_pipeline as pipeline, entity_resolver, kg as kgmod, populate_graph, vector_index
from .catalog.catalog_store import load


def reset_all() -> dict:
    kg = kgmod.get().reset()
    kg.save()
    vector_index.get().reset().save()
    pipeline.clear_status()
    return {"provisional_pending_forgotten": entity_resolver.reset_pending()}


def run(reset: bool = False, only: str = None) -> dict:
    t0 = time.time()
    out = {}
    pipeline.set_sync(True)
    if reset:
        out.update(reset_all())
    kg, cat = kgmod.get(), load()
    out["seed"] = populate_graph.seed(kg, cat) if reset else {"already_seeded": not populate_graph.ensure_seeded(kg, cat)}
    docs = [lifecycle.get_record(only)] if only else sorted(lifecycle.list_documents("ACTIVE"), key=lambda r: r["document_uri"])
    results = {}
    for rec in docs:
        uri = rec["document_uri"]
        status = pipeline.get_status(uri)
        if not reset and not only and status and status.get("state") == "COMPLETE" and status.get("catalog_version") == cat.version:
            continue
        results[uri] = pipeline.run_now(uri, actor="backfill")
    failed = {u: r for u, r in results.items() if r["state"] != "COMPLETE"}
    out.update(documents_active=len(docs), enriched=len(results) - len(failed), failed={u: f"{r['stage']}: {r.get('error')}" for u, r in failed.items()},
               graph_triples=len(kg.g), vectors=vector_index.get().size(),
               provisional_pending=len(entity_resolver.list_provisional("PENDING")), seconds=round(time.time() - t0, 1))
    return out


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--reset", action="store_true")
    p.add_argument("--doc")
    a = p.parse_args(argv)
    print(json.dumps(run(a.reset, a.doc), indent=2, default=str))


if __name__ == "__main__":
    main()

"""Data catalog: what Watson knows about its documents, fields and entities.

Layer 2 grounds the ontology on this catalog: every class and property should
trace back to a catalog field or entity. A snapshot of everything is written to
stores/data_catalog.json; per-user views are filtered by the ABAC view level.
"""
from collections import Counter
from typing import Optional

from . import abac_engine as abac
from . import audit_log, config, lifecycle_manager as lm, metadata_extractor as mx, vocab
from .jsonstore import write_json


def _row(rec: dict) -> dict:
    m, a = rec["metadata"], rec.get("access") or {}
    return {
        "document_uri": rec["document_uri"], "state": rec["state"], "title": m.get("extracted_title"),
        "source_filename": rec["source_filename"], "format": m.get("format"),
        "study_type": m.get("study_type"), "therapeutic_area": m.get("therapeutic_area"),
        "geography": m.get("geography"), "sensitivity_level": a.get("sensitivity_level"),
        "brands_mentioned": m.get("brands_mentioned", []), "indications_mentioned": m.get("indications_mentioned", []),
        "language": m.get("language"), "word_count": m.get("word_count"),
        "quality_score": (rec.get("quality") or {}).get("quality_score"), "expiry_date": rec.get("expiry_date"),
        "chunk_count": rec.get("assessment", {}).get("chunking", {}).get("chunk_count"),
    }


def build(user: Optional[dict] = None) -> dict:
    """Catalog for `user` (None = everything, used for the stored snapshot)."""
    recs = lm.list_documents()
    if user is not None:
        recs = [r for r in recs if abac.view_level(user, r) == "full"]
    rows = [_row(r) for r in recs]
    active = [r for r in rows if r["state"] == "ACTIVE"]
    brands, inds = Counter(), Counter()
    for r in active:
        brands.update(r["brands_mentioned"])
        inds.update(r["indications_mentioned"])
    return {
        "generated_at": audit_log.now_iso(),
        "fields": [{"field": f, "source": mx.FIELD_INFO[f][0], "method": mx.FIELD_INFO[f][1],
                    "required_for_active": f in mx.REQUIRED} for f in mx.FIELDS],
        "summary": {"documents": len(rows), "active": len(active),
                    "by_state": dict(Counter(r["state"] for r in rows)),
                    "by_study_type": dict(Counter(r["study_type"] for r in active)),
                    "by_therapeutic_area": dict(Counter(r["therapeutic_area"] for r in active))},
        "entities": {
            "brands": [{"name": b, "therapeutic_area": vocab.BRANDS[b]["ta"], "drug": vocab.BRANDS[b]["drug"],
                        "drug_class": vocab.BRANDS[b]["drug_class"], "active_documents": n} for b, n in brands.most_common()],
            "indications": [{"name": i, "therapeutic_area": vocab.INDICATIONS[i]["ta"], "active_documents": n}
                            for i, n in inds.most_common()],
        },
        "documents": rows,
    }


def write_snapshot() -> None:
    write_json(config.store_path("data_catalog.json"), build(None))

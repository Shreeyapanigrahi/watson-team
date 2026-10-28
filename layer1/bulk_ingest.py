"""Seed the system by sending the generated corpus through the REAL pipeline.

Every file goes through ingest(); approvals go through review() as the ADMIN user.
Nothing is written to the stores by hand. The manifest (ground truth from the
generator) is used only to (a) back-date ingestion so the expiry scanner has
something to find, and (b) supply the reviewer's decisions, standing in for a human.

Left UNDER_REVIEW on purpose so the review queue has real work in it:
the scanned PDF, the near-duplicate and the new-version file.

  python -m layer1.bulk_ingest --reset
"""
import argparse
import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import catalog, config, lifecycle_manager as lm
from .errors import WatsonError

REVIEWER = "u_admin"


def reset_state() -> None:
    for name in ("lifecycle_store.json", "dedup_store.json", "audit_log.jsonl", "taxonomy_store.json"):
        config.store_path(name).unlink(missing_ok=True)
    for d in (config.STAGING_DIR, config.UPLOAD_DIR):
        if d.exists():
            shutil.rmtree(d)
    config.ensure_dirs()


def run(reset: bool = True, data_dir: Path = None) -> dict:
    data_dir = Path(data_dir or config.DATA_DIR)
    if reset:
        reset_state()
    manifest = json.loads((data_dir / "manifest.json").read_text(encoding="utf-8"))
    now = datetime.now(timezone.utc)
    stats = {"files": 0, "states": {}, "classifier": {"study_type": [0, 0], "therapeutic_area": [0, 0], "geography": [0, 0]},
             "unexpected_pii_flags": [], "review_errors": [], "dedup": {}, "chunks": 0, "pii_flagged": []}

    for idx, m in enumerate(manifest):
        age = m.get("ingest_age")
        if age is None:
            age = 15 + (idx * 37) % 180
        ts = (now - timedelta(days=age)).isoformat(timespec="seconds")
        rec = lm.ingest(data_dir / m["filename"], actor=REVIEWER, ingest_timestamp=ts)
        stats["files"] += 1
        uri, planted = rec["document_uri"], m.get("planted", {})
        verdict = rec["assessment"]["dedup"]["verdict"]
        stats["dedup"][verdict] = stats["dedup"].get(verdict, 0) + 1
        stats["chunks"] += rec["assessment"]["chunking"]["chunk_count"]
        if rec["assessment"]["pii"]["pii_detected"]:
            stats["pii_flagged"].append(m["filename"])
            if not planted.get("pii"):
                stats["unexpected_pii_flags"].append(m["filename"])
        if rec["state"] == "REJECTED":
            continue                                  # exact duplicate
        meta = rec["metadata"]
        for f, truth in (("study_type", m["study_type"]), ("therapeutic_area", m["therapeutic_area"]),
                         ("geography", m["region"])):
            stats["classifier"][f][1] += 1
            stats["classifier"][f][0] += int(meta.get(f) == truth)
        try:
            if planted.get("pii"):
                lm.review(uri, False, REVIEWER, reason="PII detected - named individual with date of birth and email",
                          notes="Rejected by the seed reviewer")
            elif planted.get("scanned_pdf") or planted.get("near_duplicate_of") or planted.get("new_version_of"):
                continue                              # keep in the review queue
            elif rec["assessment"]["pii"]["pii_detected"]:
                continue                              # unexpected PII flag: needs a human, do not auto-override
            else:
                updates = {k: v for k, v in (("study_type", m["study_type"]), ("therapeutic_area", m["therapeutic_area"]),
                                             ("geography", m["region"])) if meta.get(k) != v}
                if not meta.get("extracted_title"):
                    updates["extracted_title"] = m["title"]
                lm.review(uri, True, REVIEWER, sensitivity_level=m["sensitivity"], metadata_updates=updates,
                          notes="Verified. No PII.")
        except WatsonError as e:
            stats["review_errors"].append({"file": m["filename"], "error": str(e)})

    for rec in lm.list_documents():
        stats["states"][rec["state"]] = stats["states"].get(rec["state"], 0) + 1
    stats["classifier"] = {k: {"correct": a, "total": b, "accuracy": round(a / b, 3) if b else None}
                           for k, (a, b) in stats["classifier"].items()}
    catalog.write_snapshot()                          # stores/data_catalog.json, the input to Layer 2
    return stats


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true", help="wipe stores, staging and uploads first")
    a = ap.parse_args()
    stats = run(reset=a.reset)
    out = config.ROOT.parent / "results" / "layer1_ingest_summary.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(stats, indent=2), encoding="utf-8")
    print(json.dumps(stats, indent=2))
    print(f"\nSaved {out}")


if __name__ == "__main__":
    main()

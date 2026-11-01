"""Document lifecycle state machine and ingestion pipeline (Sections 4.6, 4.3-4.5).

    DRAFT -> UNDER_REVIEW -> ACTIVE -> ARCHIVED -> ACTIVE
       \\          \\            \\-> UNDER_REVIEW (expired)
        \\-> REJECTED  \\-> REJECTED            REJECTED is terminal

ingest()  parse -> dedup -> chunk -> PII -> metadata, then DRAFT -> UNDER_REVIEW
          (or REJECTED for an exact duplicate / unparseable file)
review()  the human-in-the-loop gate: writes the access attributes, expiry and
          quality score, then UNDER_REVIEW -> ACTIVE (or REJECTED)
Only ACTIVE documents may be indexed: assert_indexable() raises LifecycleViolationError.

CLI examples (run from the project root):
  python -m layer1.lifecycle_manager ingest layer1/data/study_001.pdf
  python -m layer1.lifecycle_manager review Watson:Doc_001 --approve --sensitivity RESTRICTED --ta Oncology --geo EU --notes "Verified. No PII."
  python -m layer1.lifecycle_manager review Watson:Doc_002 --reject --reason "PII detected - named patient identified"
"""
import argparse
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from . import abac_engine as abac
from . import audit_log, chunker, config, dedup_detector, expiry_manager, metadata_extractor, pii_detector, quality_scorer
from .errors import (LifecycleViolationError, MetadataIncompleteError, NotFoundError,
                     PIIBlockedError)
from .jsonstore import LOCK, read_json, write_json
from .parser import parse_file
from .vocab import GEOGRAPHY_VALUES, REGIONS, SENSITIVITY_LEVELS, THERAPEUTIC_AREAS

log = logging.getLogger(__name__)

STATES = ["DRAFT", "UNDER_REVIEW", "ACTIVE", "ARCHIVED", "REJECTED"]
# The brief's table lists ACTIVE -> ARCHIVED only, but 4.8 also sends expired
# ACTIVE documents back to UNDER_REVIEW, so that edge is allowed too.
ALLOWED = {
    "DRAFT": {"UNDER_REVIEW", "REJECTED"},
    "UNDER_REVIEW": {"ACTIVE", "REJECTED"},
    "ACTIVE": {"ARCHIVED", "UNDER_REVIEW"},
    "ARCHIVED": {"ACTIVE"},
    "REJECTED": set(),
}
EDITABLE_AT_REVIEW = ("study_type", "therapeutic_area", "geography", "extracted_title", "extracted_author")


# ---------------------------------------------------------------- store access
def _path():
    return config.store_path("lifecycle_store.json")


def _load() -> dict:
    return read_json(_path(), {"counter": 0, "documents": {}})


def _save(store: dict) -> None:
    write_json(_path(), store)


def get_record(uri: str) -> dict:
    rec = _load()["documents"].get(uri)
    if rec is None:
        raise NotFoundError(f"No document '{uri}'")
    return rec


def get_state(uri: str) -> Optional[str]:
    rec = _load()["documents"].get(uri)
    return rec["state"] if rec else None


def list_documents(state: Optional[str] = None) -> list:
    docs = list(_load()["documents"].values())
    return [d for d in docs if state is None or d["state"] == state]


def _update(uri: str, fn) -> dict:
    with LOCK:
        store = _load()
        rec = store["documents"][uri]
        fn(rec)
        rec["updated_at"] = audit_log.now_iso()
        _save(store)
        return rec


def staging_path(uri: str) -> Path:
    return config.STAGING_DIR / (uri.replace(":", "_") + ".json")


def load_staging(uri: str) -> dict:
    p = staging_path(uri)
    if not p.exists():
        raise NotFoundError(f"No staged content for '{uri}'")
    return read_json(p, None)


def _raw_text_of(uri: str) -> Optional[str]:
    p = staging_path(uri)
    return read_json(p, {}).get("parsed", {}).get("raw_text") if p.exists() else None


# ---------------------------------------------------------------- transitions
def transition(uri: str, new_state: str, actor: str, notes: Optional[str] = None,
               reviewer_note: Optional[str] = None, extra: Optional[dict] = None) -> dict:
    rec = get_record(uri)
    old = rec["state"]
    if new_state not in ALLOWED.get(old, set()):
        audit_log.log_event("LIFECYCLE_VIOLATION", uri, {
            "from": old, "to": new_state, "actor": actor, "reason": "transition not allowed"})
        raise LifecycleViolationError(f"{uri}: {old} -> {new_state} is not an allowed transition")

    def apply(r):
        r["state"] = new_state
        r["history"].append({"timestamp": audit_log.now_iso(), "from": old, "to": new_state,
                             "actor": actor, "notes": notes})
        if reviewer_note:
            r["reviewer_notes"].append(reviewer_note)
    rec = _update(uri, apply)
    audit_log.log_event("STATE_TRANSITION", uri, {"from": old, "to": new_state, "actor": actor,
                                                  "notes": notes, **(extra or {})})
    for hook in list(_STATE_HOOKS):
        try:
            hook(uri, old, new_state, actor)
        except Exception:               # a failing listener must never undo or block a review decision
            log.exception("state hook %r failed for %s (%s -> %s)", hook, uri, old, new_state)
    return rec


# Listeners called after every successful transition: hook(document_uri, old_state, new_state, actor).
# Layer 2 registers one to enrich a document when it becomes ACTIVE. Layer 1 imports nothing from Layer 2.
_STATE_HOOKS: list = []


def register_state_hook(fn) -> None:
    if fn not in _STATE_HOOKS:
        _STATE_HOOKS.append(fn)


def clear_state_hooks() -> None:
    _STATE_HOOKS.clear()
    _DELETE_HOOKS.clear()


# Listeners called when a document is being deleted, BEFORE anything of it is removed here: hook(document_uri). Layer 2 removes the
# document's graph facts and vectors. A hook that raises stops the delete with nothing removed, so it can simply be retried.
_DELETE_HOOKS: list = []


def register_delete_hook(fn) -> None:
    if fn not in _DELETE_HOOKS:
        _DELETE_HOOKS.append(fn)


def assert_indexable(uri: str) -> None:
    """Called before anything enters the knowledge graph or vector index (Layers 2/3)."""
    state = get_state(uri)
    if state != "ACTIVE":
        audit_log.log_event("LIFECYCLE_VIOLATION", uri, {"attempted": "index", "state": state})
        raise LifecycleViolationError(f"{uri} is {state}; only ACTIVE documents may be indexed")


# ---------------------------------------------------------------- ingestion
def _new_record(path: Path, actor: str, ts: str) -> dict:
    with LOCK:
        store = _load()
        store["counter"] += 1
        uri = f"Watson:Doc_{store['counter']:03d}"
        now = audit_log.now_iso()
        rec = {
            "document_uri": uri, "state": "DRAFT", "source_filename": path.name,
            "stored_path": str(path), "uploaded_by": actor, "created_at": now, "updated_at": now,
            "metadata": {}, "assessment": {}, "access": None, "expiry_date": None, "quality": None,
            "review": None, "reviewer_notes": [], "supersedes": None, "rejection_reason": None,
            "history": [{"timestamp": now, "from": None, "to": "DRAFT", "actor": actor, "notes": "ingested"}],
        }
        store["documents"][uri] = rec
        _save(store)
    return rec


def ingest(file_path, actor: str = "system", ingest_timestamp: Optional[str] = None) -> dict:
    """Run one file through parse -> dedup -> chunk -> PII -> metadata. Returns the final record."""
    config.ensure_dirs()
    metadata_extractor.ensure_taxonomy()
    abac.ensure_registry()
    path = Path(file_path)
    ts = ingest_timestamp or audit_log.now_iso()
    rec = _new_record(path, actor, ts)
    uri = rec["document_uri"]
    audit_log.log_event("INGEST", uri, {"filename": path.name, "actor": actor,
                                        "bytes": path.stat().st_size if path.exists() else None})
    try:
        parsed = parse_file(path)
    except Exception as exc:  # unreadable or corrupt file
        _update(uri, lambda r: r.update(rejection_reason=f"unparseable: {exc}"))
        audit_log.log_event("PARSE_WARNING", uri, {"warning": f"UNPARSEABLE: {exc}"})
        return transition(uri, "REJECTED", "pipeline", f"unparseable: {exc}")
    for w in parsed.parse_warnings:
        audit_log.log_event("PARSE_WARNING", uri, {"warning": w})

    title, _ = metadata_extractor.guess_title(parsed)
    author = parsed.extracted_metadata.get("author")
    date = metadata_extractor.extract_date(parsed)
    verdict = dedup_detector.check(parsed.raw_text, path.name, title, author, date,
                                   load_text=_raw_text_of, state_of=get_state)
    if verdict["verdict"] != "unique":
        audit_log.log_event("DEDUP_HIT", uri, {k: verdict[k] for k in ("verdict", "original_uri", "similarity", "reason")})

    chunks = chunker.chunk_document(parsed, uri)
    pii = pii_detector.assess_chunks(chunks)
    if pii["pii_detected"]:
        audit_log.log_event("PII_FLAG", uri, {"types": pii["pii_types"], "flagged_chunks": pii["flagged_chunks"],
                                              "total_chunks": pii["total_chunks"]})
    built = metadata_extractor.build_metadata(parsed, uri, ts, pii)
    meta = built["fields"]

    config.STAGING_DIR.mkdir(parents=True, exist_ok=True)
    write_json(staging_path(uri), {"parsed": parsed.to_dict(), "chunks": chunks})
    if verdict["verdict"] != "exact_duplicate":
        dedup_detector.register(uri, verdict["sha256"], path.name, title, author, date)

    notes = []
    if verdict["verdict"] == "near_duplicate":
        notes.append(f"near duplicate - please verify (similarity {verdict['similarity']} with {verdict['original_uri']})")
    elif verdict["verdict"] == "new_version":
        notes.append(f"possible new version of {verdict['original_uri']}; decide whether to archive the old one")
    if pii["pii_detected"]:
        notes.append(f"PII detected ({', '.join(pii['pii_types'])}) in {pii['flagged_chunks']} chunk(s); approval needs an explicit override")
    if parsed.word_count == 0:
        notes.append("no extractable text; cannot be approved")
    if "untitled" in built["flags"]:
        notes.append("untitled: set a title at review")

    def fill(r):
        r["metadata"] = meta
        r["extracted_date"] = built["extracted_date"]
        r["metadata_sources"] = {f: metadata_extractor.FIELD_INFO[f][0] for f in metadata_extractor.FIELDS}
        r["metadata_flags"] = built["flags"]
        r["assessment"] = {
            "dedup": {k: verdict[k] for k in ("verdict", "original_uri", "similarity", "reason")},
            "pii": pii,
            "parse_warnings": parsed.parse_warnings,
            "chunking": {"strategy": config.CHUNK_STRATEGY, "chunk_count": len(chunks)},
        }
        r["reviewer_notes"].extend(notes)
    _update(uri, fill)

    if verdict["verdict"] == "exact_duplicate":
        _update(uri, lambda r: r.update(rejection_reason=f"exact_duplicate of {verdict['original_uri']}"))
        return transition(uri, "REJECTED", "pipeline", f"exact_duplicate of {verdict['original_uri']}")
    return transition(uri, "UNDER_REVIEW", "pipeline", "automatic checks complete")


# ---------------------------------------------------------------- review (HITL gate)
def review(uri: str, approved: bool, reviewer_id: str, *, sensitivity_level: Optional[str] = None,
           allowed_therapeutic_areas: Optional[list] = None, allowed_geographies: Optional[list] = None,
           metadata_updates: Optional[dict] = None, notes: Optional[str] = None,
           reason: Optional[str] = None, expiry_date: Optional[str] = None,
           pii_override: bool = False, pii_override_reason: Optional[str] = None,
           archive_superseded: bool = False) -> dict:
    user = abac.get_user(reviewer_id)
    rec = get_record(uri)
    abac.authorize(user, "review", rec)
    if rec["state"] != "UNDER_REVIEW":
        audit_log.log_event("LIFECYCLE_VIOLATION", uri, {"attempted": "review", "state": rec["state"], "actor": reviewer_id})
        raise LifecycleViolationError(f"{uri} is {rec['state']}; only UNDER_REVIEW documents can be reviewed")

    if not approved:
        why = reason or notes
        if not why:
            raise ValueError("A rejection needs a reason")
        _update(uri, lambda r: r.update(rejection_reason=why, review={
            "approved": False, "reviewer": reviewer_id, "notes": notes, "reason": why, "timestamp": audit_log.now_iso()}))
        return transition(uri, "REJECTED", reviewer_id, why, extra={"reviewer": reviewer_id})

    meta = dict(rec["metadata"])
    for k, v in (metadata_updates or {}).items():
        if k not in EDITABLE_AT_REVIEW:
            raise ValueError(f"'{k}' cannot be edited at review")
        if v not in (None, ""):
            meta[k] = v
    meta["sensitivity_level"] = sensitivity_level or meta.get("sensitivity_level")

    if meta["sensitivity_level"] not in SENSITIVITY_LEVELS:
        raise MetadataIncompleteError(f"sensitivity_level must be one of {SENSITIVITY_LEVELS}")
    if meta.get("geography") not in GEOGRAPHY_VALUES:
        raise MetadataIncompleteError(f"geography must be one of {GEOGRAPHY_VALUES}")
    unc = metadata_extractor.uncontrolled_terms(meta.get("study_type"), meta.get("therapeutic_area"))
    if unc:
        raise MetadataIncompleteError(
            "uncontrolled_term: " + ", ".join(f"{u['field']}='{u['value']}'" for u in unc)
            + " is not in the taxonomy. Add the term via the taxonomy manager first")
    missing = metadata_extractor.missing_required(meta)
    if missing:
        raise MetadataIncompleteError("Required metadata missing for ACTIVE: " + ", ".join(missing))

    pii = rec["assessment"]["pii"]
    if pii["pii_detected"]:
        if not pii_override:
            raise PIIBlockedError("PII was detected. Reject the document, or approve with an explicit override and a reason")
        if not (pii_override_reason or "").strip():
            raise PIIBlockedError("A PII override needs a written reason")

    tas = allowed_therapeutic_areas if allowed_therapeutic_areas is not None else [meta["therapeutic_area"]]
    geos = allowed_geographies if allowed_geographies is not None else ([] if meta["geography"] == "GLOBAL" else [meta["geography"]])
    if not set(tas) <= set(THERAPEUTIC_AREAS) or not set(geos) <= set(REGIONS):
        raise ValueError("allowed_therapeutic_areas / allowed_geographies contain unknown values")
    access = {"sensitivity_level": meta["sensitivity_level"], "allowed_therapeutic_areas": list(tas),
              "allowed_geographies": list(geos)}
    expiry = expiry_manager.assign_expiry(meta["study_type"], meta["ingest_timestamp"], expiry_date)
    staged = load_staging(uri)
    quality = quality_scorer.compute(meta, staged["parsed"]["raw_text"])

    def apply(r):
        r["metadata"], r["access"], r["expiry_date"], r["quality"] = meta, access, expiry, quality
        r["review"] = {"approved": True, "reviewer": reviewer_id, "notes": notes, "timestamp": audit_log.now_iso(),
                       "pii_override": bool(pii_override), "pii_override_reason": pii_override_reason}
    _update(uri, apply)
    out = transition(uri, "ACTIVE", reviewer_id, notes or "approved",
                     extra={"reviewer": reviewer_id, "sensitivity_level": access["sensitivity_level"],
                            "pii_override": bool(pii_override), "expiry_date": expiry,
                            "quality_score": quality["quality_score"]})
    original = rec["assessment"]["dedup"].get("original_uri")
    if archive_superseded and rec["assessment"]["dedup"]["verdict"] == "new_version" and original:
        _update(uri, lambda r: r.update(supersedes=original))
        transition(original, "ARCHIVED", reviewer_id, f"superseded by {uri}")
        out = get_record(uri)                 # re-read: `out` predates the supersedes write
    return out


def archive(uri: str, user_id: str, notes: Optional[str] = None) -> dict:
    user = abac.get_user(user_id)
    abac.authorize(user, "archive", get_record(uri))
    return transition(uri, "ARCHIVED", user_id, notes or "archived")


def reinstate(uri: str, user_id: str, notes: Optional[str] = None) -> dict:
    user = abac.get_user(user_id)
    abac.authorize(user, "reinstate", get_record(uri))
    return transition(uri, "ACTIVE", user_id, notes or "reinstated")


def _inside(path: Path, folder: Path) -> bool:
    try:
        path.resolve().relative_to(folder.resolve())
        return True
    except ValueError:
        return False


def delete_document(uri: str, user_id: str) -> dict:
    """Permanently delete a document, in any state. Admin only. It cannot be undone.

    Removed: what Layer 2 holds for it (graph facts, vectors, enrichment status; via the delete hooks, first), the parsed text, the
    uploaded file (only when it is inside the uploads folder: a file from the supplied corpus is never touched), its duplicate-check
    entry (so the same file can be uploaded again), and the lifecycle record. Kept: the audit log, which gets one DOCUMENT_DELETED line
    with what the document was and who deleted it. The document number is never reused (the counter only goes up)."""
    user = abac.get_user(user_id)
    rec = get_record(uri)
    abac.authorize(user, "delete", rec)
    for hook in list(_DELETE_HOOKS):
        hook(uri)
    removed = {"staged_text": False, "uploaded_file": False}
    staged = staging_path(uri)
    if staged.exists():
        staged.unlink()
        removed["staged_text"] = True
    stored = Path(rec["stored_path"]) if rec.get("stored_path") else None
    if stored and _inside(stored, config.UPLOAD_DIR) and stored.is_file():
        stored.unlink()
        removed["uploaded_file"] = True
        folder = stored.parent
        if folder.resolve() != config.UPLOAD_DIR.resolve() and not any(folder.iterdir()):
            folder.rmdir()
    sha256 = dedup_detector.unregister(uri)
    with LOCK:
        store = _load()
        store["documents"].pop(uri, None)
        _save(store)
    meta = rec.get("metadata") or {}
    audit_log.log_event("DOCUMENT_DELETED", uri, {
        "actor": user_id, "state": rec["state"], "title": meta.get("extracted_title"), "source_filename": rec["source_filename"],
        "uploaded_by": rec["uploaded_by"], "sensitivity_level": (rec.get("access") or {}).get("sensitivity_level"),
        "sha256": sha256, "removed": removed})
    return {"document_uri": uri, "deleted": True, "removed": removed}


# ---------------------------------------------------------------- CLI
def _cli(argv=None) -> None:
    p = argparse.ArgumentParser(description="Watson lifecycle manager (human-in-the-loop CLI)")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("ingest")
    s.add_argument("file_path")
    s.add_argument("--actor", default="u_admin")
    r = sub.add_parser("review")
    r.add_argument("doc_uri")
    g = r.add_mutually_exclusive_group(required=True)
    g.add_argument("--approve", action="store_true")
    g.add_argument("--reject", action="store_true")
    r.add_argument("--sensitivity")
    r.add_argument("--ta", action="append")
    r.add_argument("--geo", action="append")
    r.add_argument("--notes")
    r.add_argument("--reason")
    r.add_argument("--pii-override-reason")
    r.add_argument("--reviewer", default="u_admin")
    for name in ("archive", "state"):
        a = sub.add_parser(name)
        a.add_argument("doc_uri")
        a.add_argument("--user", default="u_admin")
    sub.add_parser("list")
    args = p.parse_args(argv)

    if args.cmd == "ingest":
        rec = ingest(args.file_path, args.actor)
        print(json.dumps({"document_uri": rec["document_uri"], "state": rec["state"],
                          "dedup": rec["assessment"]["dedup"]["verdict"],
                          "pii_detected": rec["assessment"]["pii"]["pii_detected"],
                          "notes": rec["reviewer_notes"]}, indent=2))
    elif args.cmd == "review":
        rec = review(args.doc_uri, args.approve, args.reviewer, sensitivity_level=args.sensitivity,
                     allowed_therapeutic_areas=args.ta, allowed_geographies=args.geo, notes=args.notes,
                     reason=args.reason, pii_override=bool(args.pii_override_reason),
                     pii_override_reason=args.pii_override_reason)
        print(f"{rec['document_uri']}: {rec['state']}")
    elif args.cmd == "archive":
        print(archive(args.doc_uri, args.user)["state"])
    elif args.cmd == "state":
        print(get_state(args.doc_uri))
    else:
        for d in list_documents():
            print(f"{d['document_uri']}  {d['state']:13}  {d['source_filename']}")


if __name__ == "__main__":
    _cli()

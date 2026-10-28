"""Expiry and retention policy (Section 4.8).

Every ACTIVE document carries an expiry_date assigned at review (default from
study type, reviewer may override). The scanner sends expired ACTIVE documents
back to UNDER_REVIEW with an EXPIRY_TRIGGERED audit event.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from . import audit_log, vocab

EXPIRED_NOTE = "Expired - please confirm renewal or archive."


def default_retention_days(study_type: str) -> int:
    return vocab.STUDY_TYPES[study_type]["retention_days"]


def assign_expiry(study_type: str, ingest_iso: str, override: Optional[str] = None) -> str:
    if override:
        return datetime.fromisoformat(override).date().isoformat()
    ingested = datetime.fromisoformat(ingest_iso)
    return (ingested + timedelta(days=default_retention_days(study_type))).date().isoformat()


def _today(now: Optional[datetime]):
    return (now or datetime.now(timezone.utc)).date()


def expiring_within(days: int = 90, now: Optional[datetime] = None) -> list:
    """ACTIVE documents expiring within `days` (already expired included), soonest first."""
    from . import lifecycle_manager as lm
    today = _today(now)
    out = []
    for rec in lm.list_documents(state="ACTIVE"):
        exp = rec.get("expiry_date")
        if not exp:
            continue
        left = (datetime.fromisoformat(exp).date() - today).days
        if left <= days:
            out.append({
                "document_uri": rec["document_uri"],
                "document_name": rec["metadata"]["extracted_title"] or rec["metadata"]["source_filename"],
                "study_type": rec["metadata"]["study_type"],
                "expiry_date": exp,
                "therapeutic_area": rec["metadata"]["therapeutic_area"],
                "days_left": left,
            })
    return sorted(out, key=lambda r: r["expiry_date"])


def scan_expired(actor: str = "expiry_scanner", now: Optional[datetime] = None) -> list:
    """Move expired ACTIVE documents to UNDER_REVIEW. Returns the affected URIs."""
    from . import lifecycle_manager as lm
    today = _today(now)
    moved = []
    for rec in lm.list_documents(state="ACTIVE"):
        exp = rec.get("expiry_date")
        if exp and datetime.fromisoformat(exp).date() < today:
            audit_log.log_event("EXPIRY_TRIGGERED", rec["document_uri"],
                                {"expiry_date": exp, "actor": actor})
            lm.transition(rec["document_uri"], "UNDER_REVIEW", actor, EXPIRED_NOTE, reviewer_note=EXPIRED_NOTE)
            moved.append(rec["document_uri"])
    return moved

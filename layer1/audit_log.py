"""Append-only audit log (audit_log.jsonl).

One JSON object per line: timestamp (ISO 8601 UTC), event_type, document_uri
(nullable), details (dict). Layer 1 emits the ingestion/governance events; the
enrichment, query and hallucination events are emitted by Layers 2 and 3.
"""
import json
from datetime import datetime, timezone
from typing import Optional

from . import config
from .jsonstore import LOCK

EVENT_TYPES = {
    "INGEST", "PARSE_WARNING", "PII_FLAG", "DEDUP_HIT", "STATE_TRANSITION",
    "ENTITY_RESOLUTION", "ENRICHMENT_START", "ENRICHMENT_COMPLETE",
    "ENRICHMENT_FAILED", "LIFECYCLE_VIOLATION", "EXPIRY_TRIGGERED",
    "ABAC_DENIED", "QUERY", "HALLUCINATION_RISK", "TAXONOMY_UPDATE",
    "LOGIN", "LOGIN_FAILED", "LOGOUT", "PASSWORD_CHANGED", "HISTORY_DELETED",
    "DOCUMENT_DELETED",
}


def _path():
    return config.store_path("audit_log.jsonl")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log_event(event_type: str, document_uri: Optional[str] = None,
              details: Optional[dict] = None, timestamp: Optional[str] = None) -> dict:
    if event_type not in EVENT_TYPES:
        raise ValueError(f"Unknown audit event type: {event_type}")
    record = {
        "timestamp": timestamp or now_iso(),
        "event_type": event_type,
        "document_uri": document_uri,
        "details": details or {},
    }
    with LOCK:
        config.ensure_dirs()
        with open(_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def read_events(document_uri: Optional[str] = None, event_type: Optional[str] = None,
                last: Optional[int] = None) -> list:
    path = _path()
    if not path.exists():
        return []
    with LOCK:
        with open(path, "r", encoding="utf-8") as f:
            events = [json.loads(line) for line in f if line.strip()]
    if document_uri:
        events = [e for e in events if e["document_uri"] == document_uri]
    if event_type:
        events = [e for e in events if e["event_type"] == event_type]
    if last:
        events = events[-last:]
    return events

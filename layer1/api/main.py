"""Layer 1 HTTP API. The React UI in frontend/ talks only to this.

Run from the project root:
    .venv\\Scripts\\python -m uvicorn layer1.api.main:app --port 8000

Authentication: each role has one account (username + password, see auth.py) and signing in returns an opaque bearer
token that expires after 8 hours. What matters for Layer 1 is that every endpoint enforces the ABAC policy for the
registry user behind that token.
"""
import re
import uuid
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import abac_engine as abac
from .. import audit_log, auth, catalog, config, expiry_manager, lifecycle_manager as lm, metadata_extractor as mx, vocab
from ..errors import (LifecycleViolationError, MetadataIncompleteError, NotFoundError, PIIBlockedError,
                      UnsupportedFormatError, WatsonAccessDeniedError)

app = FastAPI(title="Watson Layer 1 - Governed Ingestion")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                   allow_methods=["*"], allow_headers=["*"])

TOKENS = auth.SESSIONS          # same dict: tests clear it between runs

_STATUS = {LifecycleViolationError: 409, PIIBlockedError: 409, WatsonAccessDeniedError: 403,
           MetadataIncompleteError: 422, NotFoundError: 404, UnsupportedFormatError: 415, ValueError: 422}


def _register_handler(exc_type, status):
    @app.exception_handler(exc_type)
    async def handler(_request, exc):
        return JSONResponse(status_code=status, content={"detail": str(exc), "code": exc_type.__name__})


for _t, _s in _STATUS.items():
    _register_handler(_t, _s)


@app.exception_handler(auth.AuthError)
async def _auth_error(_request, exc):
    return JSONResponse(status_code=exc.status, content={"detail": str(exc), "code": "AuthError"})


# ---------------------------------------------------------------- auth
def _bearer(authorization: Optional[str]) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Not signed in")
    return authorization[7:]


def current_user(authorization: Optional[str] = Header(None)) -> dict:
    user = auth.user_for_token(_bearer(authorization))
    if not user:
        raise HTTPException(401, "Session expired; sign in again")
    return user


class LoginBody(BaseModel):
    username: str
    password: str


class PasswordBody(BaseModel):
    current_password: str
    new_password: str


@app.post("/api/login")
def login(body: LoginBody):
    # the username is logged only when it is a real account: a mistyped password in this field must never reach the log
    known = body.username.strip().lower() in auth.usernames()
    shown = {"username": body.username.strip().lower()} if known else {"username": None}
    try:
        token, user = auth.sign_in(body.username, body.password)
    except auth.AuthError as e:
        audit_log.log_event("LOGIN_FAILED", None, {**shown, "reason": str(e)})
        raise
    audit_log.log_event("LOGIN", None, {"user_id": user["user_id"], "role": user["role"]})
    return {"token": token, "user": user, "expires_in": auth.SESSION_SECONDS}


@app.post("/api/logout")
def logout(authorization: Optional[str] = Header(None)):
    token = _bearer(authorization)
    user = auth.user_for_token(token)
    auth.sign_out(token)
    if user:
        audit_log.log_event("LOGOUT", None, {"user_id": user["user_id"]})
    return {"signed_out": True}


@app.post("/api/me/password")
def change_password(body: PasswordBody, authorization: Optional[str] = Header(None), user=Depends(current_user)):
    auth.change_password(user, body.current_password, body.new_password, keep_token=_bearer(authorization))
    audit_log.log_event("PASSWORD_CHANGED", None, {"user_id": user["user_id"], "by": "the user"})
    return {"changed": True, "other_sessions_ended": True}


@app.get("/api/me")
def me(user=Depends(current_user)):
    return {"user": user, "permissions": sorted(a for a, roles in abac.ROLE_ACTIONS.items() if user["role"] in roles)}


# ---------------------------------------------------------------- shaping
def _list_row(rec: dict, level: str) -> dict:
    row = catalog._row(rec)
    row.update(uploaded_by=rec["uploaded_by"], created_at=rec["created_at"], view_level=level,
               pii_detected=rec["assessment"].get("pii", {}).get("pii_detected", False),
               dedup_verdict=rec["assessment"].get("dedup", {}).get("verdict"),
               rejection_reason=rec.get("rejection_reason"))
    if level == "summary":
        row.update(title=None, study_type=None, therapeutic_area=None, geography=None, brands_mentioned=[],
                   indications_mentioned=[], quality_score=None, expiry_date=None, sensitivity_level=None)
    return row


def _detail(rec: dict, level: str) -> dict:
    if level == "summary":
        return {"view_level": "summary", "document_uri": rec["document_uri"], "state": rec["state"],
                "source_filename": rec["source_filename"], "created_at": rec["created_at"],
                "rejection_reason": rec.get("rejection_reason"), "reviewer_notes": rec["reviewer_notes"],
                "assessment": {"dedup": rec["assessment"].get("dedup"), "parse_warnings": rec["assessment"].get("parse_warnings", []),
                               "pii": {k: rec["assessment"]["pii"][k] for k in ("pii_detected", "pii_types")}
                               if rec["assessment"].get("pii") else None},
                "history": rec["history"]}
    out = {k: v for k, v in rec.items() if k != "stored_path"}
    out["view_level"] = "full"
    return out


def _get_visible(uri: str, user: dict) -> tuple[dict, str]:
    rec = lm.get_record(uri)
    level = abac.view_level(user, rec)
    if level is None:
        abac.deny(user, "read_document", "document is outside this user's access attributes", uri)
    return rec, level


def _snapshot():
    catalog.write_snapshot()


# ---------------------------------------------------------------- reference data
@app.get("/api/meta")
def meta(user=Depends(current_user)):
    return {
        "sensitivity_levels": vocab.SENSITIVITY_LEVELS, "therapeutic_areas": vocab.THERAPEUTIC_AREAS,
        "regions": vocab.REGIONS, "geography_values": vocab.GEOGRAPHY_VALUES,
        "study_types": [{"name": k, "retention_days": v["retention_days"]} for k, v in vocab.STUDY_TYPES.items()],
        "states": lm.STATES, "allowed_transitions": {k: sorted(v) for k, v in lm.ALLOWED.items()},
        "fields": [{"field": f, "source": mx.FIELD_INFO[f][0], "required": f in mx.REQUIRED} for f in mx.FIELDS],
        "role_max_sensitivity": abac.ROLE_MAX_SENSITIVITY,
        "max_upload_mb": config.MAX_UPLOAD_BYTES // (1024 * 1024),
        "allowed_extensions": sorted(config.ALLOWED_EXTENSIONS),
        "chunk_strategy": config.CHUNK_STRATEGY,
    }


# ---------------------------------------------------------------- documents
@app.get("/api/documents")
def list_documents(state: Optional[str] = None, q: Optional[str] = None, user=Depends(current_user)):
    rows = []
    for rec in lm.list_documents(state):
        level = abac.view_level(user, rec)
        if level is None:
            continue                         # silently excluded, never shown redacted
        row = _list_row(rec, level)
        if q and q.lower() not in " ".join(str(v) for v in (row["title"], row["source_filename"], row["document_uri"],
                                                           *row["brands_mentioned"], *row["indications_mentioned"])).lower():
            continue
        rows.append(row)
    rows.sort(key=lambda r: r["document_uri"])
    return {"documents": rows, "count": len(rows)}


@app.post("/api/documents")
def upload(file: UploadFile = File(...), user=Depends(current_user)):
    abac.authorize(user, "upload")
    name = re.sub(r"[^\w.\- ]", "_", Path(file.filename or "upload").name).strip() or "upload"
    ext = Path(name).suffix.lower()
    if ext not in config.ALLOWED_EXTENSIONS:
        raise UnsupportedFormatError(f"Unsupported file type '{ext}'. Supported: {', '.join(sorted(config.ALLOWED_EXTENSIONS))}")
    data = file.file.read(config.MAX_UPLOAD_BYTES + 1)
    if not data:
        raise ValueError("The uploaded file is empty")
    if len(data) > config.MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"File is larger than {config.MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
    folder = config.UPLOAD_DIR / uuid.uuid4().hex[:8]       # keeps the original filename intact
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / name
    dest.write_bytes(data)
    rec = lm.ingest(dest, actor=user["user_id"])
    _snapshot()
    a, m = rec["assessment"], rec["metadata"]
    return {
        "document_uri": rec["document_uri"], "state": rec["state"], "source_filename": rec["source_filename"],
        "notes": rec["reviewer_notes"], "rejection_reason": rec.get("rejection_reason"),
        "dedup": a.get("dedup"), "parse_warnings": a.get("parse_warnings", []),
        "pii": {k: a["pii"][k] for k in ("pii_detected", "pii_types", "flagged_chunks", "total_chunks")} if a.get("pii") else None,
        "chunking": a.get("chunking"),
        "metadata": {k: m.get(k) for k in ("extracted_title", "extracted_author", "word_count", "language", "format",
                                           "study_type", "therapeutic_area", "geography",
                                           "brands_mentioned", "indications_mentioned")} if m else None,
    }


@app.get("/api/documents/{uri}")
def document(uri: str, user=Depends(current_user)):
    rec, level = _get_visible(uri, user)
    return _detail(rec, level)


@app.get("/api/documents/{uri}/content")
def content(uri: str, user=Depends(current_user)):
    rec, level = _get_visible(uri, user)
    if level != "full":
        abac.deny(user, "read_content", "content is only visible to reviewers and permitted readers", uri)
    staged = lm.load_staging(uri)
    return {"document_uri": uri, "raw_text": staged["parsed"]["raw_text"], "units": staged["parsed"]["units"],
            "chunks": staged["chunks"], "parse_warnings": staged["parsed"]["parse_warnings"]}


class ReviewBody(BaseModel):
    approved: bool
    sensitivity_level: Optional[str] = None
    allowed_therapeutic_areas: Optional[list[str]] = None
    allowed_geographies: Optional[list[str]] = None
    metadata_updates: Optional[dict] = None
    notes: Optional[str] = None
    reason: Optional[str] = None
    expiry_date: Optional[str] = None
    pii_override: bool = False
    pii_override_reason: Optional[str] = None
    archive_superseded: bool = False


@app.post("/api/documents/{uri}/review")
def review(uri: str, body: ReviewBody, user=Depends(current_user)):
    rec = lm.review(uri, body.approved, user["user_id"], sensitivity_level=body.sensitivity_level,
                    allowed_therapeutic_areas=body.allowed_therapeutic_areas,
                    allowed_geographies=body.allowed_geographies, metadata_updates=body.metadata_updates,
                    notes=body.notes, reason=body.reason, expiry_date=body.expiry_date,
                    pii_override=body.pii_override, pii_override_reason=body.pii_override_reason,
                    archive_superseded=body.archive_superseded)
    _snapshot()
    return _detail(rec, "full")


class NoteBody(BaseModel):
    notes: Optional[str] = None


@app.post("/api/documents/{uri}/archive")
def archive(uri: str, body: NoteBody = NoteBody(), user=Depends(current_user)):
    rec = lm.archive(uri, user["user_id"], body.notes)
    _snapshot()
    return _detail(rec, "full")


@app.post("/api/documents/{uri}/reinstate")
def reinstate(uri: str, body: NoteBody = NoteBody(), user=Depends(current_user)):
    rec = lm.reinstate(uri, user["user_id"], body.notes)
    _snapshot()
    return _detail(rec, "full")


# ---------------------------------------------------------------- expiry, audit, catalog, stats
@app.get("/api/expiry/upcoming")
def expiry_upcoming(days: int = Query(90, ge=0, le=3650), user=Depends(current_user)):
    rows = [r for r in expiry_manager.expiring_within(days)
            if abac.view_level(user, lm.get_record(r["document_uri"])) == "full"]
    return {"days": days, "documents": rows}


@app.post("/api/expiry/scan")
def expiry_scan(user=Depends(current_user)):
    abac.authorize(user, "expiry_scan")
    moved = expiry_manager.scan_expired(actor=user["user_id"])
    _snapshot()
    return {"moved_to_under_review": moved}


@app.get("/api/audit")
def audit(event_type: Optional[str] = None, document_uri: Optional[str] = None,
          last: int = Query(200, ge=1, le=5000), user=Depends(current_user)):
    abac.authorize(user, "view_audit")
    events = audit_log.read_events(document_uri, event_type, last)
    return {"events": list(reversed(events)), "count": len(events), "event_types": sorted(audit_log.EVENT_TYPES)}


@app.get("/api/catalog")
def get_catalog(user=Depends(current_user)):
    abac.authorize(user, "view_catalog")
    return catalog.build(user)


@app.get("/api/stats")
def stats(user=Depends(current_user)):
    visible = [(r, abac.view_level(user, r)) for r in lm.list_documents()]
    visible = [(r, lv) for r, lv in visible if lv == "full"]
    by_state = {s: 0 for s in lm.STATES}
    for r, _ in visible:
        by_state[r["state"]] += 1
    soon = [r for r in expiry_manager.expiring_within(90)
            if abac.view_level(user, lm.get_record(r["document_uri"])) == "full"]
    active = [r for r, _ in visible if r["state"] == "ACTIVE"]
    return {
        "by_state": by_state, "total": len(visible), "pending_review": by_state["UNDER_REVIEW"],
        "expiring_90_days": len(soon),
        "pii_flagged": sum(1 for r, _ in visible if r["assessment"].get("pii", {}).get("pii_detected")),
        "chunks_active": sum(r["assessment"]["chunking"]["chunk_count"] for r in active),
        "avg_quality": round(sum(r["quality"]["quality_score"] for r in active) / len(active), 3) if active else None,
    }


@app.get("/api/chunking-comparison")
def chunking_comparison(user=Depends(current_user)):
    p = config.ROOT.parent / "results" / "chunking_comparison.json"
    if not p.exists():
        raise NotFoundError("The chunking comparison has not been run yet")
    import json
    return json.loads(p.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- Layer 2 (semantic foundation)
# Layer 2 sits on top of Layer 1: its router reuses current_user (same sign-in, same ABAC engine), and it listens to
# lifecycle transitions through a registered hook, so Layer 1's own modules never import Layer 2.
from layer2 import enrichment_pipeline as _enrichment            # noqa: E402
from layer2.api.router import build_router                       # noqa: E402
from layer2.embedder import EmbeddingUnavailable                  # noqa: E402

_register_handler(EmbeddingUnavailable, 503)
app.include_router(build_router(current_user))

# ---------------------------------------------------------------- Layer 3 (questions and cited answers)
from layer3.api.router import build_router as build_l3_router     # noqa: E402

app.include_router(build_l3_router(current_user))


@app.on_event("startup")
def _install_layer2_hooks():
    """Only when the server actually starts. Importing this module (as Layer 1's tests do) must not make approvals
    write into Layer 2's stores."""
    _enrichment.install_hooks()


# Serve the built React app when present (single-command run). During development
# the Vite dev server proxies /api to this process instead.
_dist = config.ROOT.parent / "frontend" / "dist"
if _dist.exists():
    app.mount("/", StaticFiles(directory=_dist, html=True), name="ui")

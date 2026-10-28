"""Attribute-based access control (Section 4.7), Layer 1 part.

Stage 1, ACL at activation: a document's access attributes (sensitivity level,
allowed therapeutic areas, allowed geographies) are fixed at human review and
stored on the document record. Layers 2 and 3 copy them onto every chunk and
filter on them BEFORE retrieval (pre-filter, never post-filter).

This module provides the policy used by both the Layer 1 API and, later, the
retrieval pre-filter:  authorize() for actions, view_level()/acl_allows() for documents.
Denials are logged as ABAC_DENIED and raise WatsonAccessDeniedError.
"""
from pathlib import Path
from typing import Optional

from . import audit_log, config
from .errors import NotFoundError, WatsonAccessDeniedError
from .jsonstore import LOCK, read_json, write_json
from .vocab import SENSITIVITY_RANK

# Highest sensitivity each role may read.
ROLE_MAX_SENSITIVITY = {"VIEWER": "INTERNAL", "ANALYST": "RESTRICTED", "SPOC": "CONFIDENTIAL", "ADMIN": "CONFIDENTIAL"}

ALL = {"VIEWER", "ANALYST", "SPOC", "ADMIN"}
ROLE_ACTIONS = {
    "upload": ALL,                      # any user may submit; submissions wait for review
    "view_catalog": ALL,
    "review": {"SPOC", "ADMIN"},        # the human-in-the-loop gate (SPOC only within their area)
    "archive": {"ADMIN"},
    "reinstate": {"ADMIN"},
    "expiry_scan": {"ADMIN"},
    "view_audit": {"ADMIN"},
    "create_space": {"SPOC", "ADMIN"},  # used by Layer 2
    "edit_taxonomy": {"SPOC", "ADMIN"},
}

DEFAULT_USERS = [
    {"user_id": "u_viewer", "name": "Vera Hoffmann", "role": "VIEWER", "therapeutic_areas": ["Oncology"], "geographies": ["EU"]},
    {"user_id": "u_analyst", "name": "Arjun Mehta", "role": "ANALYST", "therapeutic_areas": ["Oncology", "Cardiology"], "geographies": ["EU", "US", "APAC"]},
    {"user_id": "u_spoc", "name": "Lena Okafor", "role": "SPOC", "therapeutic_areas": ["Oncology"], "geographies": []},
    {"user_id": "u_admin", "name": "Marcus Webb", "role": "ADMIN", "therapeutic_areas": [], "geographies": []},
]


def _registry_path() -> Path:
    return config.store_path("user_registry.json")


def ensure_registry() -> None:
    with LOCK:
        if not _registry_path().exists():
            write_json(_registry_path(), {"users": DEFAULT_USERS})


def list_users() -> list:
    ensure_registry()
    return read_json(_registry_path(), {"users": []})["users"]


def get_user(user_id: str) -> dict:
    for u in list_users():
        if u["user_id"] == user_id:
            return u
    raise NotFoundError(f"Unknown user '{user_id}'")


def deny(user: dict, action: str, reason: str, document_uri: Optional[str] = None):
    audit_log.log_event("ABAC_DENIED", document_uri, {
        "user_id": user["user_id"], "role": user["role"], "action": action, "reason": reason})
    raise WatsonAccessDeniedError(f"{user['role']} '{user['user_id']}' may not {action}: {reason}")


def authorize(user: dict, action: str, record: Optional[dict] = None) -> None:
    """Raise WatsonAccessDeniedError (and log ABAC_DENIED) unless the role may perform the action."""
    if action not in ROLE_ACTIONS:
        raise ValueError(f"Unknown action '{action}'")
    uri = record["document_uri"] if record else None
    if user["role"] not in ROLE_ACTIONS[action]:
        deny(user, action, f"role {user['role']} is not permitted", uri)
    if action == "review" and user["role"] == "SPOC" and record is not None:
        ta = record["metadata"].get("therapeutic_area")
        if user["therapeutic_areas"] and ta not in user["therapeutic_areas"]:
            deny(user, action, f"document is in {ta}, outside this owner's area", uri)


def acl_allows(user: dict, access: Optional[dict]) -> bool:
    """The three-attribute check: sensitivity, therapeutic area, geography.
    An empty allowed list on the document means 'all'; an empty list on the user means 'all'."""
    if not access:
        return False
    if SENSITIVITY_RANK[access["sensitivity_level"]] > SENSITIVITY_RANK[ROLE_MAX_SENSITIVITY[user["role"]]]:
        return False
    if user["role"] != "ADMIN":
        doc_ta, user_ta = access["allowed_therapeutic_areas"], user["therapeutic_areas"]
        if doc_ta and user_ta and not set(doc_ta) & set(user_ta):
            return False
        doc_geo, user_geo = access["allowed_geographies"], user["geographies"]
        if doc_geo and user_geo and not set(doc_geo) & set(user_geo):
            return False
    return True


def view_level(user: dict, record: dict) -> Optional[str]:
    """'full' (metadata, assessment and content), 'summary' (status only) or None."""
    role = user["role"]
    if role == "ADMIN":
        return "full"
    if role == "SPOC":
        ta = record["metadata"].get("therapeutic_area")
        if not user["therapeutic_areas"] or ta in user["therapeutic_areas"]:
            return "full"
    if record["state"] == "ACTIVE" and acl_allows(user, record.get("access")):
        return "full"
    if record.get("uploaded_by") == user["user_id"]:
        return "summary"
    return None


def chunk_acl(record: dict) -> dict:
    """The attributes a chunk inherits from its parent document (stage 1, ACL propagation)."""
    a = record["access"]
    return {"sensitivityLevel": a["sensitivity_level"],
            "allowedTA": list(a["allowed_therapeutic_areas"]),
            "allowedGeo": list(a["allowed_geographies"])}

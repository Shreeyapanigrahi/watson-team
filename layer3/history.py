"""A small log of questions asked through Layer 3 (stores/runs.json), so a reviewer can see what was asked and how it went.
Stores the outcome, not the evidence. An ADMIN sees every run, anyone else sees their own. Each user can delete their own entries."""
import hashlib
from typing import Optional

from layer1 import audit_log
from layer1.jsonstore import LOCK, read_json, write_json

from . import config

KEEP = 200


def _path():
    return config.store_path("runs.json")


def add(user: dict, result: dict) -> None:
    with LOCK:
        store = read_json(_path(), {"runs": []})
        store["runs"].append({
            "time": audit_log.now_iso(), "user_id": user["user_id"], "role": user["role"], "question": result["question"],
            "mode": result["mode"], "path": (result.get("evidence_path") or {}).get("label"), "route": result["route"], "status": result["status"], "risk": result["risk"],
            "sparql": (result["sparql"] or {}).get("status"), "cited_documents": sorted({d for s in result["sources"] for d in s["documents"]}),
            "short_answer": (result["short_answer"] or {}).get("text"), "seconds": round(result["timings_ms"]["total"] / 1000, 1)})
        store["runs"] = store["runs"][-KEEP:]
        write_json(_path(), store)


def run_id(r: dict) -> str:
    """A stable id worked out from the entry itself, so older entries (stored without one) can be deleted too."""
    return hashlib.sha1(f"{r['user_id']}|{r['time']}|{r['question']}".encode("utf-8")).hexdigest()[:12]


def recent(user: dict, limit: int = 30) -> list:
    runs = read_json(_path(), {"runs": []})["runs"]
    if user["role"] != "ADMIN":
        runs = [r for r in runs if r["user_id"] == user["user_id"]]
    return [{**r, "id": run_id(r)} for r in list(reversed(runs))[:limit]]


def delete(user: dict, only_id: Optional[str] = None) -> int:
    """Delete the signed-in user's own entries (one, or all when only_id is None); returns how many were removed.
    Nobody can delete another user's entries here, not even an ADMIN, who can read them. The audit log keeps its QUERY event for every question."""
    with LOCK:
        store = read_json(_path(), {"runs": []})
        mine = lambda r: r["user_id"] == user["user_id"] and (only_id is None or run_id(r) == only_id)
        kept = [r for r in store["runs"] if not mine(r)]
        removed = len(store["runs"]) - len(kept)
        if removed:
            store["runs"] = kept
            write_json(_path(), store)
        return removed

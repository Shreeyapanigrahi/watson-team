"""A small log of questions asked through Layer 3 (stores/runs.json), so a reviewer can see what was asked and how it went.
Stores the outcome, not the evidence. An ADMIN sees every run, anyone else sees their own."""
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
            "mode": result["mode"], "route": result["route"], "status": result["status"], "risk": result["risk"],
            "sparql": (result["sparql"] or {}).get("status"), "cited_documents": sorted({d for s in result["sources"] for d in s["documents"]}),
            "short_answer": (result["short_answer"] or {}).get("text"), "seconds": round(result["timings_ms"]["total"] / 1000, 1)})
        store["runs"] = store["runs"][-KEEP:]
        write_json(_path(), store)


def recent(user: dict, limit: int = 30) -> list:
    runs = read_json(_path(), {"runs": []})["runs"]
    if user["role"] != "ADMIN":
        runs = [r for r in runs if r["user_id"] == user["user_id"]]
    return list(reversed(runs))[:limit]

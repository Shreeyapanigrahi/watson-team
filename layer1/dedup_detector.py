"""Deduplication and version detection (Section 4.3).

Order: exact duplicate -> near duplicate -> new version.
  exact   SHA-256 of raw_text already in dedup_store.json  -> reject, log original URI
  near    first 200 chars match AND difflib ratio > 0.85   -> hold in UNDER_REVIEW
  version same title and author as an ACTIVE doc, later date -> flag, human decides
Near-duplicate comparison runs ONLY against documents sharing the same extracted
title or filename base. No pairwise comparison across the whole corpus.
"""
import hashlib
import json
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Callable, Optional

from . import config
from .jsonstore import LOCK, read_json, write_json


def _store_path():
    return config.store_path("dedup_store.json")


def _load() -> dict:
    return read_json(_store_path(), {"hashes": {}, "documents": {}})


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def filename_base(filename: str) -> str:
    stem = Path(filename).stem.lower()
    return re.sub(r"(_rev\d*|_copy|_update|_v\d+|-final|\s*\(\d+\)|\s*-\s*copy)$", "", stem)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def check(raw_text: str, source_filename: str, title: Optional[str], author: Optional[str],
          extracted_date: Optional[str], load_text: Callable[[str], Optional[str]],
          state_of: Callable[[str], Optional[str]]) -> dict:
    """Return {verdict, original_uri, similarity, reason}.
    load_text(uri) gives an existing document's raw text; state_of(uri) its lifecycle state."""
    store = _load()
    digest = sha256_text(raw_text)
    if digest in store["hashes"]:
        return {"verdict": "exact_duplicate", "original_uri": store["hashes"][digest],
                "similarity": 1.0, "reason": "exact_duplicate", "sha256": digest}

    base = filename_base(source_filename)
    candidates = [uri for uri, d in store["documents"].items()
                  if (title and d.get("extracted_title") == title) or d.get("filename_base") == base]

    prefix = _norm(raw_text)[:config.NEAR_DUP_PREFIX_CHARS]
    for uri in candidates:
        other = load_text(uri)
        if not other or _norm(other)[:config.NEAR_DUP_PREFIX_CHARS] != prefix:
            continue
        sm = SequenceMatcher(None, raw_text, other, autojunk=False)
        if sm.quick_ratio() < config.NEAR_DUP_RATIO:
            continue
        ratio = sm.ratio()
        if ratio > config.NEAR_DUP_RATIO:
            return {"verdict": "near_duplicate", "original_uri": uri, "similarity": round(ratio, 3),
                    "reason": "near duplicate - please verify", "sha256": digest}

    if title and author and extracted_date:
        for uri in candidates:
            d = store["documents"][uri]
            if (d.get("extracted_title") == title and d.get("extracted_author") == author
                    and state_of(uri) == "ACTIVE" and d.get("extracted_date")
                    and extracted_date > d["extracted_date"]):
                return {"verdict": "new_version", "original_uri": uri, "similarity": None,
                        "reason": "same title and author as an ACTIVE document with an earlier date",
                        "sha256": digest}

    return {"verdict": "unique", "original_uri": None, "similarity": None, "reason": None, "sha256": digest}


def register(uri: str, sha256: str, source_filename: str, title, author, extracted_date) -> None:
    with LOCK:
        store = _load()
        store["hashes"][sha256] = uri
        store["documents"][uri] = {
            "source_filename": source_filename, "filename_base": filename_base(source_filename),
            "extracted_title": title, "extracted_author": author, "extracted_date": extracted_date,
            "sha256": sha256,
        }
        write_json(_store_path(), store)


def unregister(uri: str) -> Optional[str]:
    """Forget a deleted document: its hash and its entry, so the same file can be uploaded again and is not matched against a ghost.
    Returns the SHA-256 of its text that was forgotten (None if it had no entry), for the audit line."""
    with LOCK:
        store = _load()
        entry = store["documents"].pop(uri, None)
        digests = [h for h, u in store["hashes"].items() if u == uri]
        for digest in digests:
            del store["hashes"][digest]
        if entry is not None or digests:
            write_json(_store_path(), store)
        return (entry or {}).get("sha256") or (digests[0] if digests else None)


if __name__ == "__main__":
    print(json.dumps(_load(), indent=2)[:2000])

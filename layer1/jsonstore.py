"""Tiny JSON file store: atomic writes and one process-wide lock.

Prototype gap (documented in nfr_design.md): single process, no transactions,
files are unencrypted and mutable.
"""
import json
import os
import threading
from pathlib import Path

LOCK = threading.RLock()


def read_json(path: Path, default):
    path = Path(path)
    with LOCK:
        if not path.exists():
            return default
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)


def write_json(path: Path, data) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with LOCK:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)

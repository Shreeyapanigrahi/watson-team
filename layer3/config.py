"""Paths and constants for Layer 3 (retrieval and grounded answers)."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
STORES_DIR = ROOT / "stores"

# The local LLM: Qwen2.5-3B-Instruct served by llama.cpp's llama-server on this machine (free, CPU, no data leaves the laptop).
# Started by hand: layer3/start_llm_server.ps1. The context size is fixed there (-c 4096), not per request.
LLM_URL = os.environ.get("WATSON_LLM_URL", "http://127.0.0.1:8080")
LLM_MODEL = os.environ.get("WATSON_LLM_MODEL", "qwen2.5-3b-instruct")   # the --alias given to llama-server; a label, the server holds one model
LLM_TIMEOUT_S = 240

# Step 4 (SPARQL written by the model): how many times it may try, and how many rows we keep.
SPARQL_ATTEMPTS = 3
SPARQL_MAX_ROWS = 40

# Step 3 (matching everyday words to ontology terms by meaning, layer3/semantic.py). A term is used to narrow the model's first edge
# when its cosine score is at least the minimum and within SEM_MARGIN of the best term; at most SEM_TOP terms. Chosen on the 'dev'
# questions in layer3/tests/semantic_eval.json (see design_decisions.md 25), not on the 'test' ones.
SEM_MIN_RELATION = 0.85       # a wrong narrowing forces a wrong plan, a missed one changes nothing, so this favours precision over recall
SEM_MIN_KIND = 0.80
SEM_MARGIN = 0.05
SEM_TOP = 3

# Step 6 (evidence): chunks passed to the answer model, and the largest piece of one chunk we show it.
EVIDENCE_CHUNKS = 5
EVIDENCE_CHARS = 650
GRAPH_FACTS = 8

# Step 8 (hallucination check): a sentence is "supported" at or above SUPPORT_OK, "weak" at or above SUPPORT_WEAK.
SUPPORT_OK = 0.55
SUPPORT_WEAK = 0.35

_stores = {"dir": STORES_DIR}


def set_stores_dir(path) -> None:
    _stores["dir"] = Path(path)


def store_path(name: str) -> Path:
    d = _stores["dir"]
    d.mkdir(parents=True, exist_ok=True)
    return d / name

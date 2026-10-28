"""Paths and tunable constants for Layer 1.

Paths are module globals so tests can point the whole layer at a temp folder with
set_root(). Modules must read them as `config.STORES_DIR` at call time, never
copy them at import time.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"          # generated corpus (read-only input)
UPLOAD_DIR = ROOT / "uploads"     # files uploaded through the UI
STAGING_DIR = ROOT / "staging"    # ParsedDocument + chunks, one JSON per document
STORES_DIR = ROOT / "stores"      # JSON stores and the audit log

# Chunking: chosen from retrieval evidence, see results/chunking_comparison_run*.md and
# design_decisions.md. Fixed 256 tokens / 20% overlap led or tied in all four runs.
CHUNK_STRATEGY = "fixed"          # "fixed" | "structural"
FIXED_CHUNK_TOKENS = 256
FIXED_CHUNK_OVERLAP = 0.20
PDF_PAGE_SPLIT_TOKENS = 600
TXT_GROUP_TOKENS = 300

# Dedup
NEAR_DUP_PREFIX_CHARS = 200
NEAR_DUP_RATIO = 0.85

# Upload limits
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
ALLOWED_EXTENSIONS = {".pdf", ".docx", ".pptx", ".txt"}

# Quality score weights (justified in design_decisions.md)
QUALITY_WEIGHTS = {
    "metadata_completeness": 0.3,
    "text_richness": 0.3,
    "hcp_sample_size": 0.2,
    "freshness": 0.2,
}


def set_root(root) -> None:
    """Point every Layer 1 path at `root` (used by tests)."""
    global ROOT, DATA_DIR, UPLOAD_DIR, STAGING_DIR, STORES_DIR
    ROOT = Path(root)
    DATA_DIR = ROOT / "data"
    UPLOAD_DIR = ROOT / "uploads"
    STAGING_DIR = ROOT / "staging"
    STORES_DIR = ROOT / "stores"
    ensure_dirs()


def ensure_dirs() -> None:
    for d in (DATA_DIR, UPLOAD_DIR, STAGING_DIR, STORES_DIR):
        d.mkdir(parents=True, exist_ok=True)


def store_path(name: str) -> Path:
    return STORES_DIR / name

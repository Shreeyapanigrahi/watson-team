"""Paths and constants for Layer 2.

Store paths go through store_path() so tests can point the layer at a temp folder with set_stores_dir().
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent

# The three Databricks model files. Read-only inputs, never modified or moved.
SOURCE_DIR = PROJECT_ROOT / "architecture"
MODEL_JSON = SOURCE_DIR / "model.json"
MODEL_CSV = SOURCE_DIR / "pharmaceuticals_model_v1_mvm.csv"
MODEL_TTL = SOURCE_DIR / "pharmaceuticals_rdf_v1_mvm.ttl"

# The data catalog: the three files above + our extension + looked-up external IDs -> one JSON.
CATALOG_DIR = ROOT / "catalog"
CATALOG_JSON = CATALOG_DIR / "pharma_data_catalog.json"
EXTENSION_JSON = CATALOG_DIR / "watson_extension.json"
ALIGNMENTS_JSON = CATALOG_DIR / "alignments.json"

# Ontology and SHACL shapes (hand-written Turtle).
ONTOLOGY_DIR = ROOT / "ontology"
ONTOLOGY_TTL = ONTOLOGY_DIR / "pharma.ttl"
SHAPES_TTL = ONTOLOGY_DIR / "shapes.ttl"
QUERIES_FILE = ROOT / "queries.sparql"

# Generated state (git-ignored): graph, vector index, enrichment records.
STORES_DIR = ROOT / "stores"
MODELS_DIR = ROOT / "models"
RESULTS_DIR = PROJECT_ROOT / "results" / "query_results"

NS = "http://watson.local/pharma#"           # prefix Watson:
CATALOG_NS = "http://watson.local/catalog#"  # prefix cat:

EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"   # the model the brief asks for, run as ONNX
EMBEDDING_NAME = "all-MiniLM-L6-v2"
EMBEDDING_DIM = 384

# Entity recognition (brief 5.3, stage 1): 1.0 exact, 0.8 case-insensitive, 0.6 partial; drop below 0.5.
CONF_EXACT, CONF_CASE_INSENSITIVE, CONF_PARTIAL, CONF_MIN = 1.0, 0.8, 0.6, 0.5
# Entity resolution (brief 5.3, stage 2): closest seed entry above this ratio is reused, else provisional.
RESOLVE_RATIO = 0.85
# Retrieval ranking (brief 6.1): final = 0.7 * cosine + 0.3 * quality.
W_COSINE, W_QUALITY = 0.7, 0.3

_stores = {"dir": STORES_DIR}


def set_stores_dir(path) -> None:
    _stores["dir"] = Path(path)


def store_path(name: str) -> Path:
    d = _stores["dir"]
    d.mkdir(parents=True, exist_ok=True)
    return d / name

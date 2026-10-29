"""Sentence embeddings with all-MiniLM-L6-v2 (brief 5.3 stage 4), run through fastembed's ONNX runtime.

The brief names the sentence-transformers model; fastembed serves the same weights as ONNX, which avoids installing
PyTorch (about 2 GB) on a CPU-only machine. Vectors are 384-dim and already L2-normalised, so inner product = cosine.
The model (about 87 MB) is cached in layer2/models/ (git-ignored). There is no silent fallback to another model:
a vector from a different model would sit in the same index and make every score meaningless.

  python -m layer2.embedder --download     # fetch the model once
"""
import threading

import numpy as np

from . import config

_model = None
_lock = threading.Lock()


class EmbeddingUnavailable(RuntimeError):
    pass


def _load():
    global _model
    with _lock:
        if _model is None:
            try:
                from fastembed import TextEmbedding
                config.MODELS_DIR.mkdir(parents=True, exist_ok=True)
                _model = TextEmbedding(config.EMBEDDING_MODEL, cache_dir=str(config.MODELS_DIR))
            except Exception as e:                                   # missing package, no model file, no network
                raise EmbeddingUnavailable(
                    f"Cannot load {config.EMBEDDING_NAME}: {e}. Run: python -m layer2.embedder --download") from e
        return _model


def embed(texts: list) -> np.ndarray:
    """(n, 384) float32, unit length. Texts are embedded as given."""
    if not texts:
        return np.zeros((0, config.EMBEDDING_DIM), dtype="float32")
    vecs = np.array(list(_load().embed(list(texts))), dtype="float32")
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    return vecs / np.where(norms == 0, 1, norms)


def embed_query(text: str) -> np.ndarray:
    return embed([text])[0]


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--download", action="store_true")
    p.parse_args()
    v = embed(["test sentence"])
    print(f"{config.EMBEDDING_NAME} ready: shape {v.shape}, norm {float(np.linalg.norm(v[0])):.3f}")

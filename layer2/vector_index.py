"""FAISS vector index over chunk embeddings (brief 5.3 stage 4: Watson.faiss + chunk_uri_map.json).

IndexIDMap2(IndexFlatIP): exact inner-product search (cosine, because vectors are unit length) with ids WE choose, so
a chunk keeps the same id for its life and one document's vectors can be removed without rebuilding. Flat (exact)
rather than IVF/HNSW on purpose: at a few hundred chunks an exact scan costs microseconds and has no recall loss;
an approximate index would only add a tuning knob to defend. The switch point is noted in nfr_design.md.

Files (stores/):  Watson.faiss        the index
                  chunk_uri_map.json  {"next_id": n, "map": {"<faiss id>": "<chunk uri>"}, "documents": {chunk: doc}}

ABAC is applied BEFORE the search: search() takes the chunk URIs the caller may see, copies only those vectors into a
temporary index, and searches that. A chunk the user may not see is never scored, so it cannot leak through a rank,
a score or a timing difference. (Filtering after the search would still compute it.)
"""
import threading
from typing import Iterable, Optional

import faiss
import numpy as np

from layer1.jsonstore import read_json, write_json

from . import config


class VectorIndex:
    def __init__(self):
        self.lock = threading.RLock()
        self.loaded = False
        self.index = None
        self.next_id = 0
        self.map = {}          # faiss id (int) -> chunk uri
        self.doc_of = {}       # chunk uri -> document uri

    @property
    def faiss_path(self):
        return config.store_path("Watson.faiss")

    @property
    def map_path(self):
        return config.store_path("chunk_uri_map.json")

    def _new(self):
        return faiss.IndexIDMap2(faiss.IndexFlatIP(config.EMBEDDING_DIM))

    def load(self) -> "VectorIndex":
        with self.lock:
            if self.faiss_path.exists() and self.map_path.exists():
                self.index = faiss.read_index(str(self.faiss_path))
                m = read_json(self.map_path, {"next_id": 0, "map": {}, "documents": {}})
                self.next_id = m["next_id"]
                self.map = {int(k): v for k, v in m["map"].items()}
                self.doc_of = m.get("documents", {})
            else:
                self.index, self.next_id, self.map, self.doc_of = self._new(), 0, {}, {}
            self.loaded = True
        return self

    def save(self) -> None:
        with self.lock:
            tmp = self.faiss_path.with_suffix(".faiss.tmp")
            faiss.write_index(self.index, str(tmp))
            tmp.replace(self.faiss_path)
            write_json(self.map_path, {"next_id": self.next_id, "map": {str(k): v for k, v in self.map.items()},
                                       "documents": self.doc_of, "model": config.EMBEDDING_NAME, "dim": config.EMBEDDING_DIM})

    def reset(self) -> "VectorIndex":
        with self.lock:
            self.index, self.next_id, self.map, self.doc_of, self.loaded = self._new(), 0, {}, {}, True
        return self

    def ensure(self) -> "VectorIndex":
        if not self.loaded:
            self.load()
        return self

    # ---- writes
    def add(self, document_uri: str, chunk_uris: list, vectors: np.ndarray) -> dict:
        """Replace all vectors of this document. Returns {chunk uri: faiss id}."""
        if len(chunk_uris) != len(vectors):
            raise ValueError("one vector per chunk")
        with self.lock:
            self.ensure()
            self._remove_document(document_uri)
            ids = np.arange(self.next_id, self.next_id + len(chunk_uris), dtype="int64")
            self.next_id += len(chunk_uris)
            if len(ids):
                self.index.add_with_ids(np.ascontiguousarray(vectors, dtype="float32"), ids)
            for i, c in zip(ids, chunk_uris):
                self.map[int(i)] = c
                self.doc_of[c] = document_uri
            self.save()
            return {c: int(i) for i, c in zip(ids, chunk_uris)}

    def _remove_document(self, document_uri: str) -> int:
        ids = [i for i, c in self.map.items() if self.doc_of.get(c) == document_uri]
        if ids:
            self.index.remove_ids(np.array(ids, dtype="int64"))
            for i in ids:
                self.doc_of.pop(self.map.pop(i), None)
        return len(ids)

    def remove_document(self, document_uri: str) -> int:
        with self.lock:
            self.ensure()
            n = self._remove_document(document_uri)
            if n:
                self.save()
            return n

    # ---- reads
    def chunk_uris(self) -> list:
        with self.lock:
            self.ensure()
            return list(self.map.values())

    def size(self) -> int:
        with self.lock:
            self.ensure()
            return int(self.index.ntotal)

    def search(self, query: np.ndarray, allowed: Optional[Iterable[str]], k: int = 10) -> list:
        """[(chunk uri, cosine)] best first, searching ONLY the chunks in `allowed`. allowed=None means no filter
        (used by the admin audit, never by user-facing search)."""
        with self.lock:
            self.ensure()
            if allowed is None:
                ids = list(self.map)
            else:
                allow = set(allowed)
                ids = [i for i, c in self.map.items() if c in allow]
            if not ids:
                return []
            vecs = np.vstack([self.index.reconstruct(int(i)) for i in ids]).astype("float32")
            sub = faiss.IndexFlatIP(config.EMBEDDING_DIM)
            sub.add(vecs)
            scores, pos = sub.search(np.ascontiguousarray(query.reshape(1, -1), dtype="float32"), min(k, len(ids)))
            return [(self.map[ids[p]], float(s)) for s, p in zip(scores[0], pos[0]) if p >= 0]


_index: Optional[VectorIndex] = None
_index_lock = threading.Lock()


def get() -> VectorIndex:
    global _index
    with _index_lock:
        if _index is None:
            _index = VectorIndex()
        return _index.ensure()


def reset_singleton() -> None:
    global _index
    with _index_lock:
        _index = None

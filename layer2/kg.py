"""The knowledge graph: one rdflib Graph = hand-written ontology + reference data + runtime triples.

Only the derived part is ever written to disk (stores/pharma_kg.ttl). It is rebuilt from the data catalog
(populate_graph.py) and from the lifecycle store (backfill.py), so it can be deleted and regenerated.
The ontology (ontology/pharma.ttl) is reloaded from its own file every time, so edits to it always take effect.

  kg = get()                          # process-wide singleton
  with kg.write():                    # takes the lock; saves on exit
      kg.g.add(...)
"""
import contextlib
import os
import threading
from typing import Optional

from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import OWL, RDF, RDFS, SKOS, XSD

from . import config

W = Namespace(config.NS)
CAT = Namespace(config.CATALOG_NS)
OBO = Namespace("http://purl.obolibrary.org/obo/")


def bind(g: Graph) -> Graph:
    g.bind("Watson", W)
    g.bind("cat", CAT)
    g.bind("owl", OWL)
    g.bind("rdfs", RDFS)
    g.bind("skos", SKOS)
    g.bind("xsd", XSD)
    g.bind("obo", OBO)
    return g


def uri(local: str) -> URIRef:
    """'Watson:Doc_001' or 'Doc_001' -> the full IRI."""
    return W[local.split(":", 1)[1] if local.startswith("Watson:") else local]


def local(node) -> str:
    """Full IRI -> 'Doc_001'. Anything outside our namespace is returned unchanged."""
    s = str(node)
    for ns in (config.NS, config.CATALOG_NS):
        if s.startswith(ns):
            return s[len(ns):]
    return s


def short(node) -> str:
    """'Watson:Doc_001' style, for display and for Layer 1 record keys."""
    s = str(node)
    return "Watson:" + s[len(config.NS):] if s.startswith(config.NS) else local(node)


def d(value) -> Literal:
    return Literal(str(value), datatype=XSD.date)


def dt(value) -> Literal:
    return Literal(str(value), datatype=XSD.dateTime)


def num(value) -> Literal:
    return Literal(float(value), datatype=XSD.double)


def integer(value) -> Literal:
    return Literal(int(value), datatype=XSD.integer)


class KnowledgeGraph:
    def __init__(self):
        self.lock = threading.RLock()
        self.g = bind(Graph())
        self.loaded = False

    # ---- persistence
    @property
    def path(self):
        return config.store_path("pharma_kg.ttl")

    def ontology(self) -> Graph:
        o = bind(Graph())
        o.parse(config.ONTOLOGY_TTL, format="turtle")
        return o

    def load(self) -> "KnowledgeGraph":
        with self.lock:
            self.g = bind(Graph())
            if self.path.exists():
                self.g.parse(self.path, format="turtle")
            self.g += self.ontology()
            self.loaded = True
        return self

    def save(self) -> None:
        with self.lock:
            tmp = self.path.with_suffix(".ttl.tmp")
            self.g.serialize(destination=str(tmp), format="turtle")
            os.replace(tmp, self.path)

    def reset(self) -> "KnowledgeGraph":
        """Empty graph holding only the ontology. Used by --reset and by tests."""
        with self.lock:
            self.g = bind(Graph())
            self.g += self.ontology()
            self.loaded = True
        return self

    def ensure(self) -> "KnowledgeGraph":
        if not self.loaded:
            self.load()
        return self

    @contextlib.contextmanager
    def write(self):
        """Hold the lock for a group of changes and save afterwards. If the block raises, nothing is saved
        and the in-memory graph is restored from the last saved copy so half a document never stays in it."""
        with self.lock:
            self.ensure()
            try:
                yield self.g
            except Exception:
                self.load()
                raise
            self.save()

    # ---- small helpers used everywhere
    def value(self, subject, predicate, default=None):
        v = self.g.value(subject, predicate)
        return v.toPython() if isinstance(v, Literal) else (v if v is not None else default)

    def remove_subject(self, subject) -> int:
        n = len(list(self.g.triples((subject, None, None))))
        self.g.remove((subject, None, None))
        return n


_kg: Optional[KnowledgeGraph] = None
_kg_lock = threading.Lock()


def get() -> KnowledgeGraph:
    global _kg
    with _kg_lock:
        if _kg is None:
            _kg = KnowledgeGraph()
        return _kg.ensure()


def reset_singleton() -> None:
    """Tests call this after pointing config at a temp stores dir."""
    global _kg
    with _kg_lock:
        _kg = None

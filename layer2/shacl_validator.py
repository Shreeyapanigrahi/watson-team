"""SHACL validation of the knowledge graph (pyshacl), plus the one check SHACL cannot do: the vector index binding.

  python -m layer2.shacl_validator            # validate stores/pharma_kg.ttl, exit code 1 if it does not conform

Run with RDFS inference on and the ontology as the ontology graph, so a document typed Watson:HCPPerceptionSurvey
is also validated as a Watson:MarketResearchDocument (shapes.ttl targets the parent class).
Inference only adds triples in a copy that pyshacl makes; the stored graph is never changed.
"""
import json
from collections import Counter

from pyshacl import validate
from rdflib import Graph, Namespace, URIRef
from rdflib.namespace import RDF

from . import config, kg as kgmod
from .kg import W

SH = Namespace("http://www.w3.org/ns/shacl#")

# which of the brief's four checks a shape belongs to (shown in the UI next to each result)
CHECKS = {
    "DocumentShape": "1. Every document has state, sensitivity and expiry",
    "ChunkShape": "2. Every chunk has one parent and its ACL attributes",
    "LineageRecordShape": "4. Every chunk traces to a lineage record with a reviewer",
    "SentimentAssertionShape": "Sentiment assertions are complete and sourced",
    "ProvisionalEntityShape": "3. No orphan entity",
}
for _s in ("BrandShape", "DrugShape", "DrugClassShape", "IndicationShape", "TherapeuticAreaShape", "GeographyShape",
           "OntologyClassGroundingShape", "GroundedTermShape", "CatalogAssetShape"):
    CHECKS[_s] = "3. No ungrounded entity"
CHECKS["KnowledgeSpaceShape"] = "Knowledge spaces have a name and an owner"


_shapes_cache = {}


def shapes_graph() -> Graph:
    """Parsed once per process and treated as read-only: result reports name property shapes by blank node,
    and a blank node id is only stable within one parse."""
    key = str(config.SHAPES_TTL)
    if key not in _shapes_cache:
        g = Graph()
        g.parse(config.SHAPES_TTL, format="turtle")
        _shapes_cache[key] = g
    return _shapes_cache[key]


def _shape_name(node) -> str:
    return kgmod.local(node)


def _owner_shape(shapes: Graph, node):
    """pyshacl names the property shape (a blank node) as the source; walk up to the NodeShape that owns it."""
    parent = shapes.value(None, SH.property, node)
    return parent if parent is not None else node


def _violations(report_graph: Graph) -> list:
    shapes = shapes_graph()
    out = []
    for r in report_graph.subjects(RDF.type, SH.ValidationResult):
        focus = report_graph.value(r, SH.focusNode)
        path = report_graph.value(r, SH.resultPath)
        source = report_graph.value(r, SH.sourceShape)
        out.append({
            "focus": kgmod.short(focus) if focus is not None else None,
            "path": kgmod.local(path) if isinstance(path, URIRef) else (str(path) if path is not None else None),
            "shape": _shape_name(_owner_shape(shapes, source)) if source is not None else None,
            "constraint": kgmod.local(report_graph.value(r, SH.sourceConstraintComponent)).split("#")[-1]
            if report_graph.value(r, SH.sourceConstraintComponent) is not None else None,
            "severity": kgmod.local(report_graph.value(r, SH.resultSeverity)).split("#")[-1],
            "message": str(report_graph.value(r, SH.resultMessage) or ""),
        })
    return out


def validate_graph(data: Graph = None) -> dict:
    """SHACL over the whole graph. Returns a plain dict the API and the tests can use."""
    kg = kgmod.get()
    with kg.lock:
        data = data if data is not None else kg.g
        conforms, report_graph, report_text = validate(
            data, shacl_graph=shapes_graph(), ont_graph=kg.ontology(), inference="rdfs",
            advanced=True, abort_on_first=False, meta_shacl=False, debug=False)
    violations = _violations(report_graph)
    by_check = Counter(CHECKS.get(v["shape"], "Other") for v in violations)
    return {"conforms": bool(conforms), "violations": violations, "violation_count": len(violations),
            "by_check": dict(by_check), "triples_checked": len(data), "shapes": len(list(shapes_graph().subjects(RDF.type, SH.NodeShape)))}


def index_binding(kg=None) -> dict:
    """Check 2, second half: every chunk of an ACTIVE document has a vector in the FAISS index, and no vector
    belongs to a chunk the graph does not know or to a document that is no longer ACTIVE."""
    from . import vector_index
    kg = kg or kgmod.get()
    idx = vector_index.get()
    indexed = set(idx.chunk_uris())
    active_chunks, stale = set(), []
    with kg.lock:
        for chunk in kg.g.subjects(RDF.type, W.DocumentChunk):
            doc = kg.g.value(chunk, W.isChunkOf)
            state = kg.g.value(doc, W.lifecycleState) if doc is not None else None
            if state is not None and str(state) == "ACTIVE":
                active_chunks.add(kgmod.short(chunk))
    missing = sorted(active_chunks - indexed)
    orphan = sorted(indexed - active_chunks)
    return {"ok": not missing and not orphan, "active_chunks": len(active_chunks), "indexed_vectors": len(indexed),
            "chunks_without_vector": missing[:50], "vectors_without_active_chunk": orphan[:50],
            "missing_count": len(missing), "orphan_count": len(orphan)}


def run(check_index: bool = True) -> dict:
    result = validate_graph()
    if check_index:
        result["index_binding"] = index_binding()
        result["conforms"] = result["conforms"] and result["index_binding"]["ok"]
    return result


def main() -> None:
    r = run()
    print(json.dumps({k: v for k, v in r.items() if k != "violations"}, indent=2))
    for v in r["violations"][:25]:
        print(v)
    raise SystemExit(0 if r["conforms"] else 1)


if __name__ == "__main__":
    main()

"""The ontology as the UI shows it: classes, properties, how each is grounded, and the checks against the brief's minimums."""
from rdflib import Graph
from rdflib.namespace import OWL, RDF, RDFS, SKOS

from . import config, kg as kgmod
from .catalog.catalog_store import Catalog, load
from .kg import W, local
from .populate_graph import ensure_seeded

BRIEF_MINIMUMS = {"classes": 10, "object_properties": 12, "datatype_properties": 8, "inverse_pairs": 2, "transitive_properties": 1,
                  "subclass_depth": 3}

BRIEF_CLASSES = ["Drug", "Brand", "Indication", "HCP", "TherapeuticArea", "Geography", "MarketResearchDocument", "DocumentChunk",
                 "SentimentAssertion", "CompetitiveLandscape", "KnowledgeSpace", "LineageRecord"]
BRIEF_OBJECT_PROPS = ["containsDrug", "targetedIndication", "inTherapeuticArea", "coversGeography", "mentionsBrand", "mentionsIndication",
                      "isChunkOf", "derivedFrom", "containsDocument", "hasSentiment", "competesWithin", "coMentionedWith"]
BRIEF_DATA_PROPS = ["sensitivityLevel", "allowedTA", "allowedGeo", "quality_score", "freshness_date", "expiry_date", "lifecycleState",
                    "taxonomyVersion", "sentimentScore", "assertionDate", "chunksProduced", "triplesWritten"]


def _own(g: Graph, rdf_type) -> list:
    return sorted((s for s in g.subjects(RDF.type, rdf_type) if str(s).startswith(config.NS)), key=str)


def _depth(g: Graph, cls) -> int:
    seen, d, cur = set(), 1, cls
    while True:
        parents = [p for p in g.objects(cur, RDFS.subClassOf) if str(p).startswith(config.NS) and p not in seen]
        if not parents:
            return d
        seen.add(parents[0])
        cur, d = parents[0], d + 1


def _term(g: Graph, node, cat: Catalog, with_range: bool) -> dict:
    by_term = {e["term"]: e for e in cat.grounding}
    name = local(node)
    g_entry = by_term.get(name)
    t = {"id": name, "label": str(g.value(node, RDFS.label) or name), "comment": str(g.value(node, RDFS.comment) or ""),
         "grounding_status": str(g.value(node, W.groundingStatus) or "unknown"),
         "grounding_note": str(g.value(node, W.groundingNote) or ""),
         "grounded_in": sorted(str(g.value(a, W.fqn)) for a in g.objects(node, W.groundedIn)),
         "listed_in_catalog_mapping": g_entry is not None}
    if with_range:
        t.update(domain=[local(o) for o in g.objects(node, RDFS.domain)], range=[local(o) for o in g.objects(node, RDFS.range)],
                 inverse_of=[local(o) for o in g.objects(node, OWL.inverseOf)] + [local(s) for s in g.subjects(OWL.inverseOf, node)],
                 characteristics=[local(o).replace("Property", "") for o in g.objects(node, RDF.type) if o in (OWL.TransitiveProperty, OWL.SymmetricProperty)],
                 sub_property_of=[local(o) for o in g.objects(node, RDFS.subPropertyOf)])
    else:
        t.update(parents=[local(o) for o in g.objects(node, RDFS.subClassOf)], depth=_depth(g, node),
                 equivalent=[str(o) for o in g.objects(node, OWL.equivalentClass)])
    return t


def describe() -> dict:
    kg, cat = kgmod.get(), load()
    ensure_seeded(kg, cat)               # a fresh install has no reference data yet; seeding is idempotent
    with kg.lock:
        g = kg.g
        classes =[_term(g, c, cat, False) for c in _own(g, OWL.Class)]
        objs = [_term(g, p, cat, True) for p in _own(g, OWL.ObjectProperty)]
        datas = [_term(g, p, cat, True) for p in _own(g, OWL.DatatypeProperty)]
        pairs = {frozenset((local(a), local(b))) for a, b in g.subject_objects(OWL.inverseOf) if str(a).startswith(config.NS)}
        trans = [local(p) for p in g.subjects(RDF.type, OWL.TransitiveProperty)]
        depth = max(c["depth"] for c in classes)
        alignments = {"owl:equivalentClass": [{"term": local(s), "target": str(o)} for s, o in g.subject_objects(OWL.equivalentClass)],
                      "individual_level": {
                          "Indication skos:exactMatch DOID": len([1 for s in g.subjects(RDF.type, W.Indication) if (s, SKOS.exactMatch, None) in g]),
                          "Indication total": len(list(g.subjects(RDF.type, W.Indication))),
                          "TherapeuticArea skos:closeMatch MeSH": len([1 for s in g.subjects(RDF.type, W.TherapeuticArea) if (s, SKOS.closeMatch, None) in g])}}
    names = {c["id"] for c in classes} | {p["id"] for p in objs + datas}
    checks = [
        {"check": f"At least {BRIEF_MINIMUMS['classes']} classes", "value": len(classes), "ok": len(classes) >= 10},
        {"check": f"At least {BRIEF_MINIMUMS['object_properties']} object properties", "value": len(objs), "ok": len(objs) >= 12},
        {"check": f"At least {BRIEF_MINIMUMS['datatype_properties']} datatype properties", "value": len(datas), "ok": len(datas) >= 8},
        {"check": "At least 2 owl:inverseOf pairs", "value": len(pairs), "ok": len(pairs) >= 2},
        {"check": "At least 1 owl:TransitiveProperty", "value": len(trans), "ok": len(trans) >= 1},
        {"check": "A subclass chain of at least 3 levels", "value": depth, "ok": depth >= 3},
        {"check": "All 12 classes named in the brief exist", "value": [n for n in BRIEF_CLASSES if n not in names], "ok": all(n in names for n in BRIEF_CLASSES)},
        {"check": "All 12 object properties named in the brief exist", "value": [n for n in BRIEF_OBJECT_PROPS if n not in names], "ok": all(n in names for n in BRIEF_OBJECT_PROPS)},
        {"check": "All 12 datatype properties named in the brief exist", "value": [n for n in BRIEF_DATA_PROPS if n not in names], "ok": all(n in names for n in BRIEF_DATA_PROPS)},
        {"check": "Drug owl:equivalentClass chebi:Drug", "value": [a["target"] for a in alignments["owl:equivalentClass"] if a["term"] == "Drug"],
         "ok": any(a["term"] == "Drug" for a in alignments["owl:equivalentClass"])}]
    grounding = {s: sum(1 for t in classes + objs + datas if t["grounding_status"] == s) for s in ("grounded", "partial", "extension", "unknown")}
    return {"namespace": config.NS, "catalog_version": cat.version, "classes": classes, "object_properties": objs, "datatype_properties": datas,
            "inverse_pairs": sorted(sorted(p) for p in pairs), "transitive": trans, "alignments": alignments, "checks": checks,
            "grounding_summary": grounding, "terms": len(classes) + len(objs) + len(datas)}

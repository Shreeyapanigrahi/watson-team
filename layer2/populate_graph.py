"""Seed the knowledge graph with reference data (brief 5.2: populate_graph.py -> pharma_kg.ttl).

Everything written here comes from the data catalog JSON, nothing is typed in by hand:
  * catalog assets (the tables and columns that entities are grounded in)
  * a groundingStatus on every ontology term (grounded / partial / extension)
  * Brand, Drug, DrugClass, Indication, TherapeuticArea, Geography individuals, each with groundedIn
  * DOID exactMatch on indications and MeSH closeMatch on therapeutic areas (from alignments.json)
  * the knowledge spaces and their owners

Documents, chunks, sentiment and lineage are NOT written here. They come from the enrichment pipeline.

  python -m layer2.populate_graph            # add the seed to stores/pharma_kg.ttl
  python -m layer2.populate_graph --reset    # start from the bare ontology first
"""
import argparse

from rdflib import Literal, URIRef
from rdflib.namespace import OWL, RDF, RDFS, SKOS, XSD

from . import config, kg as kgmod
from .catalog.catalog_store import Catalog, load
from .kg import CAT, W

INSTANCE_CLASSES = ("Brand", "Drug", "DrugClass", "Indication", "TherapeuticArea", "Geography")
DEFAULT_NOTE = "Defined by Watson. The catalog has no counterpart."


# ---------------------------------------------------------------- catalog assets
def asset_node(g, cat: Catalog, ref: str) -> URIRef:
    """The cat: node for a catalog table or column. `ref` may omit the 'pharmaceuticals_ecm.' prefix."""
    if ref.startswith("meta:"):                      # the catalog's own metadata, e.g. its classification tag vocabulary
        node = CAT["meta_" + ref[5:]]
        if (node, RDF.type, W.CatalogAsset) not in g:
            g.add((node, RDF.type, W.CatalogAsset))
            g.set((node, RDFS.label, Literal(ref[5:])))
            g.set((node, W.fqn, Literal(ref)))
            g.set((node, W.assetOrigin, Literal("upstream")))
        g.set((node, W.catalogVersion, Literal(cat.version)))
        return node
    fqn = ref if ref.startswith(cat.name + ".") else f"{cat.name}.{ref}"
    node = CAT[fqn]
    if (node, RDF.type, W.CatalogAsset) in g:
        g.set((node, W.catalogVersion, Literal(cat.version)))      # re-seeding after a catalog change: one version, not two
        return node
    if fqn in cat.columns:
        item, label = cat.columns[fqn], fqn.rsplit(".", 1)[1]
    elif fqn in cat.tables:
        item, label = cat.tables[fqn], cat.tables[fqn]["name"]
    else:
        raise KeyError(f"'{ref}' is not a table or column of the catalog")
    g.add((node, RDF.type, W.CatalogAsset))
    g.set((node, RDFS.label, Literal(label)))
    g.set((node, W.fqn, Literal(fqn)))
    g.set((node, W.assetOrigin, Literal("extension" if item.get("origin") == "extension" else "upstream")))
    g.set((node, W.catalogVersion, Literal(cat.version)))
    return node


def reviewer_node(g, user_id: str) -> URIRef:
    node = W["Reviewer_" + user_id]
    if (node, RDF.type, W.Reviewer) not in g:
        g.add((node, RDF.type, W.Reviewer))
        g.set((node, RDFS.label, Literal(user_id)))
    return node


# ---------------------------------------------------------------- ontology terms
def ground_ontology(g, cat: Catalog) -> dict:
    by_term = {e["term"]: e for e in cat.grounding}
    terms = set()
    for t in (OWL.Class, OWL.ObjectProperty, OWL.DatatypeProperty):
        terms.update(s for s in g.subjects(RDF.type, t) if str(s).startswith(config.NS))
    counts = {"grounded": 0, "partial": 0, "extension": 0, "defaulted_to_extension": 0}
    for term in sorted(terms):
        name = kgmod.local(term)
        entry = by_term.get(name)
        if entry is None:
            status, note, refs = "extension", DEFAULT_NOTE, []
            counts["defaulted_to_extension"] += 1
        else:
            status, note, refs = entry["status"], entry.get("note") or "", entry.get("catalog") or []
        g.set((term, W.groundingStatus, Literal(status)))
        if note:
            g.set((term, W.groundingNote, Literal(note)))
        for r in refs:
            g.add((term, W.groundedIn, asset_node(g, cat, r)))
        counts[status] += 1
    missing = sorted(set(by_term) - {kgmod.local(t) for t in terms})
    counts["grounding_entries_without_ontology_term"] = missing
    return counts


# ---------------------------------------------------------------- reference individuals
def _grounded_in(g, cat: Catalog, node, inst: dict) -> None:
    """Link an individual to every column its attributes come from, and to those columns' tables."""
    for path in inst.get("attributes", {}):
        col = asset_node(g, cat, path)
        g.add((node, W.groundedIn, col))
        table = cat.table_of(path)["fqn"]
        g.add((node, W.groundedIn, asset_node(g, cat, table)))


def _common(g, cat: Catalog, kind: str, inst: dict) -> URIRef:
    node = W[inst["id"]]
    g.add((node, RDF.type, W[kind]))
    g.set((node, RDFS.label, Literal(inst["label"])))
    for alias in inst.get("aliases") or []:
        g.add((node, SKOS.altLabel, Literal(alias)))
    g.set((node, W.catalogVersion, Literal(cat.version)))
    _grounded_in(g, cat, node, inst)
    return node


def _attr(inst: dict, suffix: str):
    for k, v in inst.get("attributes", {}).items():
        if k.endswith(suffix):
            return v
    return None


def seed_entities(g, cat: Catalog) -> dict:
    inst = cat.instances
    counts = {}
    for kind in INSTANCE_CLASSES:
        counts[kind] = len(inst.get(kind, []))
        for e in inst.get(kind, []):
            node = _common(g, cat, kind, e)
            rel = e.get("relations", {})
            if kind == "Brand":
                if _attr(e, ".global_brand_owner"):
                    g.set((node, W.company, Literal(_attr(e, ".global_brand_owner"))))
                g.add((node, W.containsDrug, W[rel["drug"]]))
                g.add((node, W.inTherapeuticArea, W[rel["therapeutic_area"]]))
                for i in rel.get("indications", []):
                    g.add((node, W.targetedIndication, W[i]))
            elif kind == "Drug":
                if _attr(e, ".moa"):
                    g.set((node, W.mechanismOfAction, Literal(_attr(e, ".moa"))))
                if _attr(e, ".atc_code"):
                    g.set((node, W.atcCode, Literal(_attr(e, ".atc_code"))))
                if rel.get("drug_class"):
                    g.add((node, W.hasDrugClass, W[rel["drug_class"]]))
            elif kind == "Indication":
                g.set((node, W.isGrouping, Literal(bool(e.get("grouping")), datatype=XSD.boolean)))
                g.add((node, W.inTherapeuticArea, W[rel["therapeutic_area"]]))
                if rel.get("parent"):
                    g.add((node, W.subIndicationOf, W[rel["parent"]]))
                doid = (e.get("alignment") or {}).get("DOID")
                if doid:
                    g.add((node, SKOS.exactMatch, URIRef(doid["iri"])))
            elif kind == "TherapeuticArea":
                mesh = (e.get("alignment") or {}).get("MeSH")
                if mesh:
                    g.add((node, SKOS.closeMatch, URIRef(mesh["iri"])))
            elif kind == "Geography":
                g.set((node, W.geographyKind, Literal(e.get("kind", "country"))))
                if _attr(e, ".iso_alpha_2_code"):
                    g.set((node, W.isoCode, Literal(_attr(e, ".iso_alpha_2_code"))))
                if _attr(e, ".reimbursement_framework_type"):
                    g.set((node, W.payerType, Literal(_attr(e, ".reimbursement_framework_type"))))
                if rel.get("region"):
                    g.add((node, W.partOfRegion, W[rel["region"]]))
    return counts


def seed_spaces(g, cat: Catalog) -> int:
    created = cat.doc.get("extension", {}).get("created") or cat.meta.get("generated_at")
    table = f"{cat.name}.pmr.knowledge_spaces"
    for s in cat.knowledge_spaces:
        node = W[s["id"]]
        g.add((node, RDF.type, W.KnowledgeSpace))
        g.set((node, RDFS.label, Literal(s["name"])))
        g.set((node, W.spaceName, Literal(s["name"])))
        g.set((node, W.description, Literal(s["description"])))
        g.add((node, W.spaceOwner, reviewer_node(g, s["owner"])))
        g.set((node, W.created, kgmod.dt(created)))
        if s.get("therapeutic_area"):
            ta = next((e for e in cat.instances["TherapeuticArea"] if e["label"] == s["therapeutic_area"]), None)
            if ta:
                g.add((node, W.spaceTherapeuticArea, W[ta["id"]]))
        if table in cat.tables:
            g.add((node, W.groundedIn, asset_node(g, cat, table)))
    return len(cat.knowledge_spaces)


# ---------------------------------------------------------------- entry point
def seed(kg=None, cat: Catalog = None) -> dict:
    kg = kg or kgmod.get()
    cat = cat or load()
    with kg.write() as g:
        before = len(g)
        ontology_triples = len(kg.ontology())
        grounding = ground_ontology(g, cat)
        entities = seed_entities(g, cat)
        spaces = seed_spaces(g, cat)
        assets = len(list(g.subjects(RDF.type, W.CatalogAsset)))
        total = len(g)
    return {"catalog_version": cat.version, "ontology_triples": ontology_triples,
            "triples_added_by_seed": total - before, "triples_total": total,
            "catalog_assets": assets, "entities": entities, "knowledge_spaces": spaces, "ontology_grounding": grounding}


def ensure_seeded(kg, cat: Catalog) -> bool:
    """Seed if the graph has no reference data for this catalog version. True if it had to seed."""
    with kg.lock:
        first_brand = W[cat.instances["Brand"][0]["id"]]
        if (first_brand, W.catalogVersion, Literal(cat.version)) in kg.g:
            return False
    seed(kg, cat)
    return True


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--reset", action="store_true", help="start from the bare ontology")
    a = p.parse_args(argv)
    kg = kgmod.get()
    if a.reset:
        kg.reset()
    import json
    print(json.dumps(seed(kg), indent=2))


if __name__ == "__main__":
    main()

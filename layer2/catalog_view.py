"""The data catalog as the UI browses it: domains, tables, columns, foreign keys, and every mapping into Watson.

'Mapping' means three things, all derived from the catalog JSON (nothing here is typed in):
  * ontology term -> catalog table/column     (grounding list; status grounded / partial / extension)
  * entity attribute -> catalog column         (each Brand, Drug, ... row says which column its value came from)
  * relation -> the catalog foreign key that backs it, and whether that key is an AI-generated link
"""
from collections import defaultdict

from layer1.errors import NotFoundError

from . import config
from .catalog.catalog_store import Catalog, load

KINDS = ("Brand", "Drug", "DrugClass", "Indication", "TherapeuticArea", "Geography")


def _usage(cat: Catalog) -> dict:
    """catalog fqn (table or column) -> how Watson uses it."""
    use = defaultdict(list)
    for g in cat.grounding:
        for ref in g.get("catalog") or []:
            fqn = ref if ref.startswith(cat.name + ".") else f"{cat.name}.{ref}"
            use[fqn].append({"type": "ontology term", "name": g["term"], "kind": g["kind"], "status": g["status"]})
    seen = set()
    for kind in KINDS:
        for e in cat.instances.get(kind, []):
            for path in e.get("attributes", {}):
                col = f"{cat.name}.{path}"
                key = (col, kind)
                if key not in seen:
                    seen.add(key)
                    use[col].append({"type": "entity values", "name": kind, "kind": "class", "status": "grounded"})
                    use[cat.table_of(col)["fqn"]].append({"type": "entity values", "name": kind, "kind": "class", "status": "grounded"})
    for name, rg in cat.relation_grounding.items():
        if rg:
            use[rg["column"]].append({"type": "relation", "name": name, "kind": "relation", "status": "grounded"})
    return use


def _origin(item: dict) -> str:
    return "extension" if item.get("origin") == "extension" else "upstream"


def summary() -> dict:
    cat = load()
    use = _usage(cat)
    domains = []
    for name, d in cat.domains.items():
        tables = d["tables"]
        domains.append({"name": name, "division": d.get("division"), "description": d.get("description"), "origin": _origin(d),
                        "tables": len(tables), "columns": sum(len(t["columns"]) for t in tables),
                        "tables_used_by_watson": sum(1 for t in tables if t["fqn"] in use)})
    meta = cat.meta
    return {"catalog": cat.name, "version": cat.version, "model": meta.get("model_name"), "model_version": meta.get("model_version"),
            "business": meta.get("business"), "counts": meta["counts"], "upstream_quality": meta.get("upstream_quality"),
            "sources": meta["sources"], "notes": meta.get("notes", []), "domains": domains,
            "extension": meta.get("extension"), "reconciliation": meta["reconciliation"],
            "classification_levels": meta.get("classification_levels"), "classification_order_note": meta.get("classification_order_note")}


def tables(domain: str = None, q: str = None, only_watson: bool = False) -> list:
    cat = load()
    use = _usage(cat)
    out = []
    for fqn, t in cat.tables.items():
        dom = fqn.split(".")[1]
        if domain and dom != domain:
            continue
        if only_watson and fqn not in use:
            continue
        if q and q.lower() not in f"{t['name']} {t.get('description') or ''}".lower():
            continue
        out.append({"fqn": fqn, "name": t["name"], "domain": dom, "description": t.get("description"), "origin": _origin(t),
                    "columns": len(t["columns"]), "primary_key": t.get("primary_key"), "rdf_class": t.get("rdf_class"),
                    "watson_usage": len(use.get(fqn, []))})
    return sorted(out, key=lambda r: (r["domain"], r["name"]))


def table(fqn: str) -> dict:
    cat = load()
    try:
        t = cat.table(fqn)
    except KeyError as e:
        raise NotFoundError(str(e)) from e
    use = _usage(cat)
    cols = []
    for c in t["columns"]:
        cols.append({"name": c["name"], "fqn": c["fqn"], "type": c.get("type"), "glossary_term": c.get("glossary_term"),
                     "description": c.get("description"), "is_primary_key": c.get("is_primary_key"), "foreign_key_to": c.get("foreign_key_to"),
                     "fk_llm_generated": c.get("fk_llm_generated"), "classification": c.get("classification"),
                     "classification_conflict": c.get("classification_conflict"), "pii_tags": c.get("pii_tags") or [],
                     "rdf_property": c.get("rdf_property"), "origin": _origin(c), "watson_usage": use.get(c["fqn"], [])})
    return {"fqn": t["fqn"], "name": t["name"], "domain": t["fqn"].split(".")[1], "description": t.get("description"),
            "origin": _origin(t), "primary_key": t.get("primary_key"), "rdf_class": t.get("rdf_class"), "tags": t.get("tags"),
            "columns": cols, "references": cat.references(t["fqn"]), "referenced_by": cat.referenced_by(t["fqn"]),
            "metric_views": [{k: v[k] for k in ("name", "description", "dimensions_count", "measures_count")} for v in cat.metrics_for(t["fqn"])],
            "watson_usage": use.get(t["fqn"], [])}


def search(q: str, kind: str = None, domain: str = None, limit: int = 30) -> list:
    return load().search(q, limit=limit, kind=kind, domain=domain)


def mappings() -> dict:
    cat = load()
    terms = []
    for g in cat.grounding:
        terms.append({"term": g["term"], "kind": g["kind"], "status": g["status"], "note": g.get("note"),
                      "catalog": [{"fqn": r if r.startswith(cat.name + ".") or r.startswith("meta:") else f"{cat.name}.{r}",
                                   "exists": r.startswith("meta:") or (r if r.startswith(cat.name + ".") else f"{cat.name}.{r}") in cat.columns
                                   or (r if r.startswith(cat.name + ".") else f"{cat.name}.{r}") in cat.tables} for r in g.get("catalog") or []]})
    entities = []
    for kind in KINDS:
        cols = defaultdict(int)
        for e in cat.instances.get(kind, []):
            for p in e.get("attributes", {}):
                cols[p] += 1
        entities.append({"kind": kind, "entities": len(cat.instances.get(kind, [])),
                         "columns": [{"fqn": f"{cat.name}.{p}", "entities": n, "table": cat.table_of(p)["fqn"]} for p, n in sorted(cols.items())]})
    relations = []
    for name, rg in cat.relation_grounding.items():
        relations.append({"relation": name, "column": rg["column"] if rg else None, "target_table": rg["target_table"] if rg else None,
                          "ai_generated_link": rg["llm_generated_link"] if rg else None,
                          "note": None if rg else "No foreign key in the catalog backs this relation (a Watson addition)."})
    align = {"DOID": [], "MeSH": []}
    for kind, key, name in (("Indication", "DOID", "DOID"), ("TherapeuticArea", "MeSH", "MeSH")):
        for e in cat.instances.get(kind, []):
            a = (e.get("alignment") or {}).get(key)
            align[name].append({"entity": e["label"], "id": e["id"], "external_id": a["id"] if a else None, "external_label": a["label"] if a else None,
                                "iri": a["iri"] if a else None})
    counts = {s: sum(1 for t in terms if t["status"] == s) for s in ("grounded", "partial", "extension")}
    return {"catalog_version": cat.version, "ontology_terms": terms, "ontology_term_counts": counts, "entity_mappings": entities,
            "relations": relations, "alignments": align,
            "extension_tables": [{"fqn": t["fqn"], "columns": len(t["columns"]), "description": t.get("description")}
                                 for t in cat.domains["pmr"]["tables"]] if "pmr" in cat.domains else [],
            "note": "Terms marked 'extension' have no catalog counterpart: the catalog has no table for documents, chunks, sentiment or lineage."}

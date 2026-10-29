"""Convert the three Databricks model files in architecture/ into ONE JSON data catalog.

  python -m layer2.catalog.build_catalog

Inputs (read-only, never modified):
  model.json                        the full model. Used as the base: it holds every table and
                                    column the CSV holds, plus a little more.
  pharmaceuticals_model_v1_mvm.csv  a flattened copy of model.json. Used only as a cross-check.
  pharmaceuticals_rdf_v1_mvm.ttl    the RDFS/OWL rendering. Contributes class and property IRIs.

Output: layer2/catalog/pharma_data_catalog.json (generated, do not edit by hand).

The build refuses to produce a catalog if the files disagree in a way it cannot explain.
Differences it can explain are counted and written to meta.reconciliation, not hidden.
"""
import argparse
import csv
import hashlib
import json
import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import rdflib
from rdflib import OWL, RDF, RDFS, XSD

from .. import config

NS = "http://datamodel.lakehouse.org/business/pharmaceuticals#"
PREFIX = "ind"
LLM_LINK_LABEL = "Internal - LLM Generated"   # how the source marks AI-proposed foreign keys


class CatalogBuildError(Exception):
    """The source files disagree in a way the build cannot explain."""


# ---------------------------------------------------------------- small helpers
def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _pascal(s: str) -> str:
    return "".join(w.capitalize() for w in s.split("_"))


def _camel(s: str) -> str:
    w = s.split("_")
    return w[0] + "".join(x.capitalize() for x in w[1:])


def _tags(s) -> list:
    return [t for t in (s or "").split(",") if t]


def _blank_to_none(v):
    return v if v not in ("", None) else None


def _levels(conventions: str) -> list:
    """'restricted=restricted, confidential=confidential, ...' -> ['restricted', 'confidential', ...]"""
    return [p.split("=")[0].strip() for p in conventions.split(",") if "=" in p]


def _short(node) -> str:
    s = str(node)
    if s.startswith(NS):
        return f"{PREFIX}:{s[len(NS):]}"
    if s.startswith(str(XSD)):
        return f"xsd:{s[len(str(XSD)):]}"
    return s


# ---------------------------------------------------------------- model.json -> domains / tables / columns
def _build_domains(model: dict, catalog: str, levels: list):
    domains, index, relationships = [], {}, []
    column_fqns = set()
    source_domains = model["model"]["domains"]
    for d in source_domains:
        for p in d["products"]:
            for a in p["attributes"]:
                column_fqns.add(f"{catalog}.{d['database_name']}.{p['table_name']}.{a['column_name']}")

    for d in source_domains:
        schema = d["database_name"]
        tables = []
        for p in d["products"]:
            tfqn = f"{catalog}.{schema}.{p['table_name']}"
            names = [a["column_name"] for a in p["attributes"]]
            if p["primary_key"] not in names:
                raise CatalogBuildError(f"{tfqn}: primary key '{p['primary_key']}' is not one of its columns")
            cols = []
            for a in p["attributes"]:
                tags = _tags(a["tags"])
                lv = [t for t in tags if t in levels]
                raw_fk = a["foreign_key_to"]
                ref = a["references"]
                col = {
                    "name": a["column_name"],
                    "fqn": f"{tfqn}.{a['column_name']}",
                    "type": a["type"],
                    "glossary_term": _blank_to_none(a["business_glossary_term"]),
                    "description": _blank_to_none(a["description"]),
                    "value_regex": _blank_to_none(a["value_regex"]),
                    "tags": tags,
                    # None = unclassified, or tagged with several levels (see classification_conflict)
                    "classification": lv[0] if len(lv) == 1 else None,
                    "pii_tags": [t for t in tags if t == "pii" or t.startswith("pii_")],
                    "is_primary_key": a["column_name"] == p["primary_key"],
                    "foreign_key_to": f"{catalog}.{raw_fk}" if raw_fk else None,
                    "reference": _blank_to_none(ref),
                }
                if len(lv) > 1:
                    col["classification_conflict"] = lv   # the source gives no way to choose; do not guess
                if raw_fk:
                    if col["foreign_key_to"] not in column_fqns:
                        raise CatalogBuildError(f"{col['fqn']}: foreign key target '{raw_fk}' does not exist")
                    col["fk_llm_generated"] = bool(ref and ref.startswith(LLM_LINK_LABEL))
                cols.append(col)
                index[(schema, p["table_name"], a["column_name"])] = (p, col)
            table = {
                "name": p["table_name"],
                "fqn": tfqn,
                "subdomain": p["subdomain"],
                "type": p["type"],
                "data_type": p["data_type"],
                "description": _blank_to_none(p["description"]),
                "primary_key": p["primary_key"],
                "reference": _blank_to_none(p["reference"]),
                "tags": _tags(p["tags"]),
                "division": p.get("division"),
                "function": p.get("function"),
                "source_domains": _blank_to_none(p["source_domains"]),
                "association_edges": _blank_to_none(p["association_edges"]),
                "columns": cols,
            }
            tables.append(table)
        domains.append({
            "name": d["name"],
            "schema_fqn": f"{catalog}.{schema}",
            "division": d["division"],
            "description": d["description"],
            "references": _blank_to_none(d["references"]),
            "tags": _tags(d["tags"]),
            "tables": tables,
        })

    for dom in domains:
        for t in dom["tables"]:
            for c in t["columns"]:
                if c["foreign_key_to"]:
                    to_table = c["foreign_key_to"].rsplit(".", 1)[0]
                    relationships.append({
                        "from_column": c["fqn"], "to_column": c["foreign_key_to"],
                        "from_table": t["fqn"], "to_table": to_table,
                        "self_reference": to_table == t["fqn"],
                        "llm_generated": c["fk_llm_generated"],
                    })
    return domains, index, relationships


# ---------------------------------------------------------------- CSV cross-check
def _cross_check_csv(csv_path: Path, index: dict) -> dict:
    with open(csv_path, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    seen, diffs = set(), Counter()
    glossary_blank_in_csv = 0
    first_examples = {}
    for r in rows:
        key = (r["domain"], r["product"], r["column_name"])
        if key not in index:
            raise CatalogBuildError(f"CSV row {key} is not in model.json")
        seen.add(key)
        p, c = index[key]
        raw_fk = c["foreign_key_to"].split(".", 1)[1] if c["foreign_key_to"] else ""
        checks = {
            "type": (r["type"], c["type"]),
            "description": (_blank_to_none(r["description"]), c["description"]),
            "value_regex": (_blank_to_none(r["value_regex"]), c["value_regex"]),
            "foreign_key_to": (r["foreign_key_to"], raw_fk),
            "subdomain": (r["subdomain"], p["subdomain"]),
            "data_type": (r["data_type"], p["data_type"]),
            "is_primary_key": (r["is_primary_key"] == "Y", c["is_primary_key"]),
            "tags": (sorted(_tags(r["tags"])), sorted(t for t in c["tags"] if t != "primary_key")),
            "reference": (_blank_to_none(r["reference"]), c["reference"]),
        }
        for field, (a, b) in checks.items():
            if a != b:
                diffs[field] += 1
                first_examples.setdefault(field, {"column": ".".join(key), "csv": str(a)[:80], "json": str(b)[:80]})
        csv_gloss = _blank_to_none(r["business_glossary_term"])
        if csv_gloss is None:
            if c["glossary_term"] is not None:
                glossary_blank_in_csv += 1
        elif csv_gloss != c["glossary_term"]:
            diffs["glossary_term"] += 1
            first_examples.setdefault("glossary_term", {"column": ".".join(key), "csv": csv_gloss[:80], "json": str(c["glossary_term"])[:80]})
    missing_in_csv = len(index) - len(seen)
    if diffs or missing_in_csv or len(rows) != len(index):
        raise CatalogBuildError(
            f"CSV and model.json disagree: {dict(diffs)}, rows only in model.json: {missing_in_csv}, "
            f"examples: {first_examples}")
    return {
        "csv_rows": len(rows),
        "matched_to_model_json": len(seen),
        "unexplained_differences": 0,
        "explained_differences": {
            "glossary_term_blank_in_csv_filled_in_json": glossary_blank_in_csv,
            "primary_key_tag_present_in_json_only": sum(1 for _, c in index.values() if "primary_key" in c["tags"]),
        },
        "conclusion": "model.json holds every CSV field plus those two extras, so the CSV adds nothing and is used only as a check.",
    }


# ---------------------------------------------------------------- TTL -> class / property IRIs
def _read_ttl(ttl_path: Path) -> rdflib.Graph:
    g = rdflib.Graph()
    g.parse(str(ttl_path), format="turtle")
    return g


def _attach_ontology(g: rdflib.Graph, domains: list, catalog: str):
    classes = {str(c)[len(NS):]: c for c in g.subjects(RDF.type, RDFS.Class) if str(c).startswith(NS)}
    domain_classes = {_pascal(d["name"]) for d in domains}
    tables = [t for d in domains for t in d["tables"]]
    first = lambda s, p: next(iter(sorted(str(o) for o in g.objects(s, p))), None)

    # classes
    collisions, no_class = [], []
    for d in domains:
        d["rdf_class"] = f"{PREFIX}:{_pascal(d['name'])}" if _pascal(d["name"]) in classes else None
        for t in d["tables"]:
            cname = _pascal(t["name"])
            if cname not in classes:
                t["rdf_class"] = None
                no_class.append(t["fqn"])
                continue
            t["rdf_class"] = f"{PREFIX}:{cname}"
            t["rdf_class_shared_with_domain"] = cname in domain_classes
            if cname in domain_classes:
                collisions.append({"table": t["fqn"], "class": t["rdf_class"],
                                   "note": "a table and a domain have the same name, so the TTL gives them one class"})
    comment_eq = sum(1 for t in tables if t["rdf_class"] and first(classes[t["rdf_class"][len(PREFIX) + 1:]], RDFS.comment) == t["description"])

    # properties
    props = {str(p)[len(NS):]: p for p in g.subjects(RDF.type, RDF.Property) if str(p).startswith(NS)}
    cols_by_prop = defaultdict(list)
    names_by_prop = defaultdict(set)
    for t in tables:
        for c in t["columns"]:
            cols_by_prop[_camel(c["name"])].append((t, c))
            names_by_prop[_camel(c["name"])].add(c["name"])
    tables_per_prop = {n: len({t["fqn"] for t, _ in v}) for n, v in cols_by_prop.items()}
    no_prop = []
    for t in tables:
        for c in t["columns"]:
            pn = _camel(c["name"])
            if pn in props:
                c["rdf_property"] = f"{PREFIX}:{pn}"
                c["rdf_property_shared"] = tables_per_prop[pn] > 1
            else:
                c["rdf_property"] = None
                c["rdf_property_shared"] = False
                no_prop.append(c["fqn"])

    kind = lambda p: "object" if (p, RDF.type, OWL.ObjectProperty) in g else ("datatype" if (p, RDF.type, OWL.DatatypeProperty) in g else None)
    ontology_props, desc_match = [], 0
    for name in sorted(props):
        p = props[name]
        dom, rng = first(p, RDFS.domain), first(p, RDFS.range)
        ontology_props.append({
            "iri": f"{PREFIX}:{name}", "label": first(p, RDFS.label), "kind": kind(p),
            "domain": _short(rdflib.URIRef(dom)) if dom else None, "range": _short(rdflib.URIRef(rng)) if rng else None,
            "column_count": len(cols_by_prop.get(name, [])), "table_count": tables_per_prop.get(name, 0),
        })
        comments = {str(o) for o in g.objects(p, RDFS.comment)}
        if any(c["description"] in comments for _, c in cols_by_prop.get(name, [])):
            desc_match += 1

    table_cls = {_pascal(t["name"]) for t in tables}
    ontology_classes = []
    for name in sorted(classes):
        c = classes[name]
        kindname = ("root" if name == "Business" else "industry" if name == "Pharmaceuticals"
                    else "domain" if name in domain_classes else "table")
        ontology_classes.append({"iri": f"{PREFIX}:{name}", "label": first(c, RDFS.label), "kind": kindname,
                                 "parents": sorted(_short(o) for o in g.objects(c, RDFS.subClassOf))})
    obj_names = {p["iri"][len(PREFIX) + 1:] for p in ontology_props if p["kind"] == "object"}
    fk_names = {_camel(c["name"]) for t in tables for c in t["columns"] if c["foreign_key_to"]}
    plain_names = {_camel(c["name"]) for t in tables for c in t["columns"] if not c["foreign_key_to"]}
    shared = {n for n, k in tables_per_prop.items() if k > 1}
    recon = {
        "classes_in_ttl": len(classes),
        "classes_expected_from_model_json": len(table_cls | domain_classes) + 2,
        "tables_without_class": no_class,
        "table_class_collisions": collisions,
        "class_comment_equals_table_description": f"{comment_eq} of {len(tables)}",
        "properties_in_ttl": len(props),
        "distinct_column_names": len({c["name"] for t in tables for c in t["columns"]}),
        "columns_without_property": no_prop,
        "property_names_from_more_than_one_column_name": [
            {"property": f"{PREFIX}:{n}", "columns": sorted(v)} for n, v in sorted(names_by_prop.items()) if len(v) > 1],
        "property_names_shared_by_several_tables": len(shared),
        "columns_on_a_shared_property_name": sum(len(cols_by_prop[n]) for n in shared),
        "property_comment_equals_a_column_description": f"{desc_match} of {len(props)}",
        "object_properties": len(obj_names),
        "object_properties_that_are_foreign_key_columns": len(obj_names & fk_names),
        "object_properties_also_used_as_plain_columns": len(obj_names & plain_names),
        "conclusion": ("The TTL repeats the descriptions already in model.json and adds only IRIs and object/datatype typing. "
                       "It is lossy: a property name shared by several tables cannot say which table a column belongs to, "
                       "so columns and relationships are taken from model.json and the TTL names are attached as links."),
    }
    ontology = {"namespace": NS, "prefix": PREFIX, "classes": ontology_classes, "properties": ontology_props}
    return ontology, recon


# ---------------------------------------------------------------- metric views
def _build_metric_views(model: dict, catalog: str, table_fqns: set) -> tuple:
    out, bad_source, no_table = [], [], []
    for v in model["model"]["metric_views"]:
        owner = f"{catalog}.{v['owner_domain']}.{v['owner_product']}"
        m = re.search(r'source:\s*"([^"]+)"', v["sql"])
        declared = m.group(1).replace("`", "") if m else None
        if declared != owner:
            bad_source.append(v["view_name"])
        if owner not in table_fqns:
            no_table.append(v["view_name"])
        out.append({
            "name": v["view_name"], "fqn": f"{catalog}._metrics.{v['view_name']}",
            "source_table": owner, "owner_domain": v["owner_domain"],
            "description": _blank_to_none(v["description"]),
            "dimensions_count": v["dimensions_count"], "measures_count": v["measures_count"], "sql": v["sql"],
        })
    return out, {"metric_views": len(out), "views_whose_declared_source_differs_from_owner": bad_source,
                 "views_whose_owner_table_is_missing": no_table}


# ---------------------------------------------------------------- Watson extension (instances, pmr tables, grounding)
# Relation -> the catalog table its foreign key must point at.
_EXPECT_TARGET = {"Brand.drug": "product.drug_product", "Brand.indications": "product.indication",
                  "Brand.therapeutic_area": "product.therapeutic_area", "Indication.therapeutic_area": "product.therapeutic_area"}
_RULE_KEYS = {"therapeutic_area", "study_type", "geography"}


def _merge_extension(ext: dict, alignments: dict, catalog: str, tables: list, columns: list) -> dict:
    col_fqns = {c["fqn"] for c in columns}
    table_fqns = {t["fqn"] for t in tables}

    # extension tables, in the same shape as the upstream ones
    ext_domains, ext_rel = [], []
    ext_cols, ext_tables = {}, set()
    pk_fqns = {c["fqn"] for c in columns if c["is_primary_key"]}
    for d in ext["domains"]:
        for t in d["tables"]:
            ext_tables.add(f"{catalog}.{d['name']}.{t['name']}")
            pk_fqns.add(f"{catalog}.{d['name']}.{t['name']}.{t['primary_key']}")
            for c in t["columns"]:
                ext_cols[f"{catalog}.{d['name']}.{t['name']}.{c['name']}"] = c
    for d in ext["domains"]:
        tabs = []
        for t in d["tables"]:
            tfqn = f"{catalog}.{d['name']}.{t['name']}"
            if t["primary_key"] not in {c["name"] for c in t["columns"]}:
                raise CatalogBuildError(f"extension table {tfqn}: primary key '{t['primary_key']}' is not a column")
            cols = []
            for c in t["columns"]:
                fk = c.get("foreign_key_to")
                target = f"{catalog}.{fk}" if fk else None
                if target and target not in col_fqns and target not in ext_cols:
                    raise CatalogBuildError(f"extension column {tfqn}.{c['name']}: foreign key target '{fk}' does not exist")
                col = {"name": c["name"], "fqn": f"{tfqn}.{c['name']}", "type": c["type"], "description": c["description"],
                       "tags": c.get("tags", []), "is_primary_key": c["name"] == t["primary_key"],
                       "foreign_key_to": target, "origin": "extension"}
                cols.append(col)
                if target:
                    ext_rel.append({"from_column": col["fqn"], "to_column": target, "from_table": tfqn,
                                    "to_table": target.rsplit(".", 1)[0],
                                    "natural_key": target not in pk_fqns})
            tabs.append({"name": t["name"], "fqn": tfqn, "description": t["description"], "primary_key": t["primary_key"],
                         "origin": "extension", "columns": cols})
        ext_domains.append({"name": d["name"], "schema_fqn": f"{catalog}.{d['name']}", "description": d["description"],
                            "origin": "extension", "tables": tabs})
    all_cols = col_fqns | set(ext_cols)
    all_tables = table_fqns | ext_tables

    # instances: every attribute must be a real catalog column
    instances = ext["instances"]
    ids = {i["id"] for rows in instances.values() for i in rows}
    for typ, rows in instances.items():
        for i in rows:
            for key in i["attributes"]:
                if f"{catalog}.{key}" not in all_cols:
                    raise CatalogBuildError(f"{typ} '{i['id']}': attribute '{key}' is not a column in the catalog")
            for rel, val in i["relations"].items():
                for v in (val if isinstance(val, list) else [val]):
                    if v is not None and v not in ids:
                        raise CatalogBuildError(f"{typ} '{i['id']}': relation {rel} points at unknown id '{v}'")
    col_by = {c["fqn"]: c for c in columns}
    relation_grounding = {}
    for rel, colname in ext["relation_grounding"].items():
        if colname is None:
            relation_grounding[rel] = None
            continue
        c = col_by.get(f"{catalog}.{colname}")
        if not c or not c["foreign_key_to"]:
            raise CatalogBuildError(f"relation {rel}: '{colname}' is not a foreign-key column in the catalog")
        want = _EXPECT_TARGET.get(rel)
        got = c["foreign_key_to"].rsplit(".", 1)[0]
        if want and got != f"{catalog}.{want}":
            raise CatalogBuildError(f"relation {rel}: '{colname}' points at {got}, expected {want}")
        relation_grounding[rel] = {"column": c["fqn"], "target_table": got, "llm_generated_link": c["fk_llm_generated"]}

    # grounding list: every reference must resolve
    for g in ext["grounding"]:
        for ref in g["catalog"]:
            if not ref.startswith("meta:") and ref not in all_cols and ref not in all_tables:
                raise CatalogBuildError(f"grounding '{g['term']}': '{ref}' is not in the catalog")
        if g["status"] not in ("grounded", "partial", "extension"):
            raise CatalogBuildError(f"grounding '{g['term']}': bad status '{g['status']}'")
        if g["status"] != "extension" and not g["catalog"]:
            raise CatalogBuildError(f"grounding '{g['term']}': status '{g['status']}' needs at least one catalog reference")
    for s in ext["knowledge_spaces"]:
        if not set(s["rule"]) <= _RULE_KEYS:
            raise CatalogBuildError(f"knowledge space '{s['id']}': unknown rule keys {set(s['rule']) - _RULE_KEYS}")

    # attach external alignments that were looked up (never invented)
    ali = {"Indication": alignments.get("indications", {}), "TherapeuticArea": alignments.get("therapeutic_areas", {})}
    for i in instances.get("Indication", []):
        hit = ali["Indication"].get(i["label"]) or alignments.get("indication_parents", {}).get(i["label"])
        i["alignment"] = {"DOID": hit} if hit and hit.get("id") else {"DOID": None}
    for i in instances.get("TherapeuticArea", []):
        hit = ali["TherapeuticArea"].get(i["label"])
        i["alignment"] = {"MeSH": hit} if hit and hit.get("id") else {"MeSH": None}

    status = Counter(g["status"] for g in ext["grounding"])
    return {
        "extension": {"origin": ext["origin"], "domains": ext_domains, "relationships": ext_rel},
        "instances": instances, "knowledge_spaces": ext["knowledge_spaces"], "grounding": ext["grounding"],
        "relation_grounding": relation_grounding,
        "alignments": {k: alignments.get(k) for k in ("looked_up_at", "sources", "rule", "drug_class")} if alignments else None,
        "_counts": {"extension_tables": len(ext_tables), "extension_columns": len(ext_cols),
                    "instances": {k: len(v) for k, v in instances.items()}, "grounding": dict(status),
                    "indications_with_doid": sum(1 for i in instances.get("Indication", []) if i["alignment"]["DOID"]),
                    "indications_without_doid": [i["label"] for i in instances.get("Indication", []) if not i["alignment"]["DOID"]],
                    "areas_with_mesh": sum(1 for i in instances.get("TherapeuticArea", []) if i["alignment"]["MeSH"])},
    }


# ---------------------------------------------------------------- build
def build(model_json: Path = None, model_csv: Path = None, model_ttl: Path = None, out_path: Path = None,
          extension_json: Path = None, alignments_json: Path = None) -> dict:
    model_json = Path(model_json or config.MODEL_JSON)
    model_csv = Path(model_csv or config.MODEL_CSV)
    model_ttl = Path(model_ttl or config.MODEL_TTL)
    extension_json = Path(extension_json or config.EXTENSION_JSON)
    alignments_json = Path(alignments_json or config.ALIGNMENTS_JSON)
    with open(model_json, encoding="utf-8") as f:
        model = json.load(f)

    req = model["model_requirements"]
    catalog = req["deployment_catalog"]
    levels = _levels(req["model_conventions"]["data_classification_levels"])
    domains, index, relationships = _build_domains(model, catalog, levels)
    tables = [t for d in domains for t in d["tables"]]
    columns = [c for t in tables for c in t["columns"]]

    stats = model["_vibe_session_metadata"]["model_stats_at_generation"]
    actual = {"domain_count": len(domains), "product_count": len(tables), "attribute_count": len(columns), "fk_count": len(relationships)}
    wrong = {k: (stats[k], v) for k, v in actual.items() if stats.get(k) != v}
    if wrong:
        raise CatalogBuildError(f"model.json counts disagree with its own metadata (declared, actual): {wrong}")

    csv_recon = _cross_check_csv(model_csv, index)
    ontology, ttl_recon = _attach_ontology(_read_ttl(model_ttl), domains, catalog)
    metric_views, mv_recon = _build_metric_views(model, catalog, {t["fqn"] for t in tables})

    by_level = Counter(c["classification"] for c in columns if c["classification"])
    conflicts = [c["fqn"] for c in columns if "classification_conflict" in c]
    unclassified = sum(1 for c in columns if not c["classification"] and "classification_conflict" not in c)
    llm_fks = sum(1 for r in relationships if r["llm_generated"])
    sm = model["_vibe_session_metadata"]
    meta = {
        "catalog": catalog,
        "business": req["business_name"],
        "model_name": model["model"]["name"],
        "model_version": model["model"]["version"],
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "builder": "layer2.catalog.build_catalog",
        "naming": f"{catalog}.<domain>.<table>.<column>",
        "conventions": model["model"]["model_conventions"],
        "classification_levels": levels,
        "classification_order_note": "Levels are listed as in the source. The source defines no ranking between them.",
        "upstream_quality": {k: sm.get(k) for k in ("status", "confidence_score", "summary", "data_modeler_notes", "issue_counts")},
        "sources": [
            {"file": p.name, "role": role, "bytes": p.stat().st_size, "sha256": _sha256(p)}
            for p, role in ((model_json, "base"), (model_csv, "cross-check only"), (model_ttl, "RDF names and typing"))],
        "counts": {
            "domains": len(domains), "tables": len(tables), "columns": len(columns),
            "primary_keys": sum(c["is_primary_key"] for c in columns),
            "foreign_keys": len(relationships), "foreign_keys_llm_generated": llm_fks,
            "foreign_keys_across_domains": sum(1 for r in relationships if r["from_table"].split(".")[1] != r["to_table"].split(".")[1]),
            "foreign_keys_self_referencing": sum(r["self_reference"] for r in relationships),
            "columns_by_classification": dict(by_level),
            "columns_unclassified": unclassified,
            "columns_with_conflicting_classification": len(conflicts),
            "columns_with_pii_tag": sum(1 for c in columns if c["pii_tags"]),
            "metric_views": len(metric_views),
            "ontology_classes": len(ontology["classes"]), "ontology_properties": len(ontology["properties"]),
        },
        "reconciliation": {"csv_vs_model_json": csv_recon, "ttl_vs_model_json": ttl_recon, "metric_views": mv_recon,
                           "classification_conflicts": conflicts},
        "notes": [
            "Schema only: the catalog describes tables and columns. It holds no data rows, so it does not list any brand, indication or country.",
            f"{llm_fks} of {len(relationships)} foreign keys are labelled '{LLM_LINK_LABEL}...' in the source. Treat them as proposed links, not verified ones.",
            f"Upstream status is '{sm.get('status')}', confidence {sm.get('confidence_score')}. {sm.get('data_modeler_notes')}.",
            f"Only {sum(by_level.values()) + len(conflicts)} of {len(columns)} columns carry a classification tag. The rest are unclassified (null), not assumed internal.",
            f"{len(conflicts)} column(s) carry more than one classification tag ({', '.join(conflicts)}). They are left without a single classification and need a steward's decision.",
            "The ontology section comes from the TTL and is lossy; see reconciliation.ttl_vs_model_json.",
        ],
    }
    catalog_doc = {"meta": meta, "domains": domains, "relationships": relationships,
                   "metric_views": metric_views, "ontology": ontology}

    # our extension: reference rows, pmr tables, grounding map, knowledge spaces, looked-up external IDs
    if extension_json.exists():
        with open(extension_json, encoding="utf-8") as f:
            ext = json.load(f)
        ali = json.load(open(alignments_json, encoding="utf-8")) if alignments_json.exists() else {}
        merged = _merge_extension(ext, ali, catalog, tables, columns)
        meta["extension"] = merged.pop("_counts")
        catalog_doc.update(merged)
        for p, role in ((extension_json, "Watson extension (ours): instances, pmr tables, grounding"),
                        (alignments_json, "external IDs looked up from DOID / MeSH / ChEBI")):
            if p.exists():
                meta["sources"].append({"file": p.name, "role": role, "bytes": p.stat().st_size, "sha256": _sha256(p)})
        meta["notes"].append("The 'instances', 'extension', 'grounding' and 'knowledge_spaces' sections are ours, not Databricks'. "
                             "Every instance attribute and every grounding reference was checked against the catalog columns at build time.")
    # content-addressed version: changes whenever any input file changes. Written into lineage records.
    meta["catalog_version"] = hashlib.sha256("".join(sorted(s["sha256"] for s in meta["sources"])).encode()).hexdigest()[:12]

    if out_path:
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = out_path.with_suffix(out_path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(catalog_doc, f, ensure_ascii=False, indent=1)
        os.replace(tmp, out_path)
    return catalog_doc


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=str(config.CATALOG_JSON))
    a = ap.parse_args()
    doc = build(out_path=Path(a.out))
    c = doc["meta"]["counts"]
    size = Path(a.out).stat().st_size / (1024 * 1024)
    print(f"Wrote {a.out} ({size:.1f} MB)")
    print(f"  {c['domains']} domains, {c['tables']} tables, {c['columns']} columns, {c['foreign_keys']} foreign keys, "
          f"{c['metric_views']} metric views")
    print(f"  ontology: {c['ontology_classes']} classes, {c['ontology_properties']} properties")
    print(f"  classification: {c['columns_by_classification']}, unclassified {c['columns_unclassified']}")
    for n in doc["meta"]["notes"]:
        print("  note:", n)


if __name__ == "__main__":
    main()

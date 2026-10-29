"""Step 5: tie the entities in the question to the knowledge space, and check what the generated query touched.

For each entity found in the question: its catalog id and kind, the catalog columns it is grounded in, its external
identifiers (DOID, MeSH), the readable documents that mention it, and whether the SPARQL query actually used it.
Then the reverse check: every entity or ontology term the query uses that the question did NOT name is listed, because a
query that drifts to another brand is a hallucination too, only a quieter one.
All counts are for this user's slice, so a restricted document never adds to a number the user sees.
"""
import re

from layer2 import config as l2config, graph_view
from layer2.kg import W
from rdflib.namespace import RDF

from . import sparql_gen

_ENTITY = r"((?:Brand|Drug|DrugClass|Indication|TA|Geo|GeoRegion)_[A-Za-z0-9_]+)"


def entity_iris(sparql: str | None) -> set:
    if not sparql:
        return set()
    return set(re.findall(r"Watson:" + _ENTITY, sparql)) | set(re.findall("<" + re.escape(l2config.NS) + _ENTITY + ">", sparql))


def ontology_terms(sparql: str | None) -> list:
    if not sparql:
        return []
    names = set(re.findall(r"Watson:([A-Za-z][A-Za-z0-9_]*)", sparql))
    return sorted(n for n in names if "_" not in n or n[0].islower())


def query_path(plan: dict | None, entities: list) -> list:
    """The relationship path the query walks, one hop per edge of the accepted plan, in words:
    {"from": "Brand (any)", "relation": "coMentionedWith", "to": "Opdivra"}. A variable shows the kind of thing it stands for."""
    if not plan:
        return []
    labels = {f"Watson:{e['entity_id']}": e["label"] for e in entities if e.get("entity_id")}

    def node(t: str) -> str:
        if t in labels:
            return labels[t]
        if t in sparql_gen.TYPED_VARS:
            return f"{sparql_gen.TYPED_VARS[t]} ({t})"
        return f"value ({t})" if t.startswith("?") else t.removeprefix("Watson:")

    return [{"from": node(e["s"]), "relation": e["p"].split(":", 1)[-1], "to": node(e["o"])} for e in plan.get("edges") or []]


def map_entities(user: dict, slice_graph, entities: list, sparql: str | None, plan: dict | None = None) -> dict:
    used = entity_iris(sparql)
    rows, named = [], set()
    for e in entities:
        named.add(e["entity_id"])
        try:
            d = graph_view.entity_detail(user, e["entity_id"])
        except Exception:                                  # not in the catalog any more: report it, do not hide it
            rows.append({**e, "in_graph": False, "note": "not in the catalog"})
            continue
        node = W[e["entity_id"]]
        in_graph = (node, RDF.type, None) in slice_graph
        rows.append({
            **e, "iri": d["iri"], "in_graph": in_graph,
            "grounded_columns": len(d["grounding"]), "grounding_examples": [g["fqn"] for g in d["grounding"][:3]],
            "alignment": {k: v.get("id") for k, v in (d["alignment"] or {}).items() if v},
            "readable_documents": len(d["documents"]), "documents": [x["document_uri"] for x in d["documents"]],
            "average_sentiment": d["average_sentiment"],
            "relations": [f"{r['relation']} -> {r['label']}" for r in d["relations"]["outgoing"][:6]],
            "used_in_query": e["entity_id"] in used,
        })
    extra = sorted(used - named)
    return {"mapped": rows, "query_path": query_path(plan, entities),
            "query_entities_not_in_question": extra, "query_ontology_terms": ontology_terms(sparql),
            "unmapped_question_terms": [e["text"] for e in entities if e["entity_id"] is None]}

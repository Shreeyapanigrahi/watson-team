"""What a given user may see of the knowledge graph (brief 4.7: ABAC applied BEFORE anything is read or ranked).

The rule for content is stricter than Layer 1's view_level (which also lets a SPOC or ADMIN open a document that is not
ACTIVE, for review): a document is readable here only if it is ACTIVE and the user's attributes pass acl_allows()
(sensitivity ceiling, therapeutic area, geography). Everything below starts from that set:

  * document lists, graph nodes, entity document counts, space counts and the search index are built from it
  * facts that are DERIVED from several documents (which brands are co-mentioned, average sentiment, a space's average
    quality) are recomputed from the readable documents only. Reading the stored coMentionedWith edge would leak that
    two brands appear together in a document the user may not read.

Reference data (brands, drugs, indications, therapeutic areas, geographies, and the catalog itself) comes from the data
catalog, which is schema and reference rows, not research content. Every role may see it.
"""
from collections import Counter, defaultdict
from itertools import combinations
from typing import Optional

from layer1 import abac_engine as abac, lifecycle_manager as lifecycle
from layer1.errors import NotFoundError
from rdflib.namespace import RDF, RDFS

from . import config, enrichment_pipeline as pipeline, kg as kgmod
from .catalog.catalog_store import Catalog, load
from .kg import W, local, short, uri

KINDS = ("Brand", "Drug", "DrugClass", "Indication", "TherapeuticArea", "Geography")
DOC_LINKS = {"Brand": W.mentionsBrand, "Indication": W.mentionsIndication, "Drug": W.mentionsDrug,
             "Geography": W.coversGeography, "TherapeuticArea": W.inTherapeuticArea}


# ---------------------------------------------------------------- who may read what
def readable(user: dict, record: dict) -> bool:
    return record["state"] == "ACTIVE" and abac.acl_allows(user, record.get("access"))


def visible_records(user: dict) -> dict:
    return {r["document_uri"]: r for r in lifecycle.list_documents("ACTIVE") if readable(user, r)}


def visible_chunks(user: dict, kg=None, records: Optional[dict] = None) -> dict:
    """{chunk uri (short form): document uri} for every chunk the user may read."""
    kg = kg or kgmod.get()
    records = records if records is not None else visible_records(user)
    out = {}
    with kg.lock:
        for d in records:
            for c in kg.g.subjects(W.isChunkOf, uri(d)):
                out[short(c)] = d
    return out


def require_readable(user: dict, document_uri: str, action: str = "view knowledge") -> dict:
    rec = lifecycle.get_record(document_uri)           # raises NotFoundError
    if not readable(user, rec):
        abac.deny(user, action, "document is not readable under this user's attributes", document_uri)
    return rec


# ---------------------------------------------------------------- reference entities
def _instance_map(cat: Catalog) -> dict:
    return {e["id"]: (kind, e) for kind in KINDS for e in cat.instances.get(kind, [])}


def _label(cat_map: dict, eid: str) -> str:
    return cat_map[eid][1]["label"] if eid in cat_map else eid


def _doc_counts(user: dict, kg, records: dict) -> dict:
    """entity id -> number of readable documents that mention or cover it."""
    counts = Counter()
    with kg.lock:
        for d in records:
            dn, seen = uri(d), set()
            for prop in DOC_LINKS.values():
                seen.update(local(o) for o in kg.g.objects(dn, prop))
            for c in kg.g.subjects(W.isChunkOf, dn):
                seen.update(local(o) for o in kg.g.objects(c, W.mentionsDrugClass))
            counts.update(seen)
    return counts


def entities(user: dict, kind: Optional[str] = None, q: Optional[str] = None) -> list:
    cat, kg = load(), kgmod.get()
    counts = _doc_counts(user, kg, visible_records(user))
    out = []
    for eid, (k, e) in _instance_map(cat).items():
        if kind and k != kind:
            continue
        if q and q.lower() not in " ".join([e["label"], *(e.get("aliases") or [])]).lower():
            continue
        al = e.get("alignment") or {}
        out.append({"id": eid, "kind": k, "label": e["label"], "aliases": e.get("aliases") or [],
                    "documents": counts.get(eid, 0), "grounded_in": len(e.get("attributes", {})),
                    "grouping": bool(e.get("grouping")), "alignment": {n: a["id"] for n, a in al.items() if a}})
    out.sort(key=lambda x: (KINDS.index(x["kind"]), x["label"].lower()))
    return out


def _column_info(cat: Catalog, path: str) -> dict:
    col = cat.column(path)
    return {"fqn": col["fqn"], "table": cat.table_of(path)["fqn"], "data_type": col.get("type"),
            "description": col.get("description"), "classification": col.get("classification"),
            "origin": col.get("origin", "upstream")}


def _related(cat: Catalog, eid: str) -> dict:
    """Reference relations of an entity, both directions, from the catalog instances."""
    m = _instance_map(cat)
    kind, e = m[eid]
    out, inc = [], []
    names = {"drug": "containsDrug", "therapeutic_area": "inTherapeuticArea", "indications": "targetedIndication",
             "parent": "subIndicationOf", "drug_class": "hasDrugClass", "region": "partOfRegion"}
    for rel, target in (e.get("relations") or {}).items():
        for t in (target if isinstance(target, list) else [target]):
            if t:
                out.append({"relation": names[rel], "id": t, "kind": m[t][0], "label": _label(m, t)})
    for oid, (ok, oe) in m.items():
        for rel, target in (oe.get("relations") or {}).items():
            if eid in (target if isinstance(target, list) else [target]):
                inc.append({"relation": names[rel], "id": oid, "kind": ok, "label": oe["label"]})
    return {"outgoing": out, "incoming": sorted(inc, key=lambda r: (r["kind"], r["label"]))}


def entity_detail(user: dict, entity_id: str) -> dict:
    cat, kg = load(), kgmod.get()
    m = _instance_map(cat)
    if entity_id not in m:
        raise NotFoundError(f"No entity '{entity_id}'")
    kind, e = m[entity_id]
    records = visible_records(user)
    node, g = uri(entity_id), kg.g
    docs, sentiments, co = [], [], Counter()
    with kg.lock:
        for d, rec in sorted(records.items()):
            dn = uri(d)
            hit = any((dn, p, node) in g for p in DOC_LINKS.values()) or any(
                (c, W.mentionsDrugClass, node) in g for c in g.subjects(W.isChunkOf, dn))
            if hit:
                docs.append({"document_uri": d, "title": rec["metadata"].get("extracted_title"), "study_type": rec["metadata"].get("study_type"),
                             "geography": rec["metadata"].get("geography"), "date": rec.get("extracted_date"),
                             "sensitivity_level": rec["access"]["sensitivity_level"]})
        if kind == "Brand":
            for c in g.subjects(W.mentionsBrand, node):
                d = g.value(c, W.isChunkOf)
                if d is not None and short(d) in records:
                    co.update(local(o) for o in g.objects(c, W.mentionsBrand) if o != node)
        prop = {"Brand": W.forBrand, "Indication": W.forIndication, "Geography": W.forGeography}.get(kind)
        if prop is not None:
            for s in g.subjects(prop, node):
                chunk = g.value(s, W.derivedFrom)
                d = g.value(chunk, W.isChunkOf) if chunk is not None else None
                if d is None or short(d) not in records:
                    continue
                sentiments.append({"score": float(g.value(s, W.sentimentScore)), "date": str(g.value(s, W.assertionDate)),
                                   "brand": _label(m, local(g.value(s, W.forBrand))), "geography": _label(m, local(g.value(s, W.forGeography))),
                                   "indication": _label(m, local(g.value(s, W.forIndication))), "document_uri": short(d),
                                   "chunk_uri": short(chunk)})
    sentiments.sort(key=lambda s: s["date"])
    return {"id": entity_id, "kind": kind, "label": e["label"], "aliases": e.get("aliases") or [], "grouping": bool(e.get("grouping")),
            "iri": str(node), "catalog_version": cat.version, "attributes": e.get("attributes", {}),
            "grounding": [_column_info(cat, p) | {"value": v} for p, v in (e.get("attributes") or {}).items()],
            "alignment": e.get("alignment") or {}, "relations": _related(cat, entity_id), "documents": docs,
            "sentiment": sentiments,
            "average_sentiment": round(sum(s["score"] for s in sentiments) / len(sentiments), 3) if sentiments else None,
            "co_mentioned_brands": [{"id": i, "label": _label(m, i), "chunks": n} for i, n in co.most_common()]}


# ---------------------------------------------------------------- graph for the UI
def graph(user: dict, mode: str = "knowledge", space: Optional[str] = None, entity: Optional[str] = None, limit: int = 80) -> dict:
    """Nodes and edges for Cytoscape. mode: knowledge (spaces, documents, the entities they mention),
    reference (the catalog's entities and how they relate), entity (one entity and its neighbours)."""
    cat, kg = load(), kgmod.get()
    m = _instance_map(cat)
    records = visible_records(user)
    nodes, edges = {}, {}

    def node(nid, label, kind, **data):
        nodes.setdefault(nid, {"id": nid, "label": label, "kind": kind, **data})

    def edge(s, t, rel):
        edges.setdefault(f"{s}|{rel}|{t}", {"id": f"{s}|{rel}|{t}", "source": s, "target": t, "label": rel})

    def entity_node(eid):
        k, e = m[eid]
        node(eid, e["label"], k, grounded=len(e.get("attributes", {})))

    def structure(eid):
        for r in _related(cat, eid)["outgoing"]:
            entity_node(r["id"])
            edge(eid, r["id"], r["relation"])

    if mode == "reference":
        for eid in m:
            entity_node(eid)
        for eid in m:
            structure(eid)
    elif mode == "entity":
        if entity not in m:
            raise NotFoundError(f"No entity '{entity}'")
        entity_node(entity)
        rel = _related(cat, entity)
        for r in rel["outgoing"] + rel["incoming"]:
            entity_node(r["id"])
            (edge(entity, r["id"], r["relation"]) if r in rel["outgoing"] else edge(r["id"], entity, r["relation"]))
        with kg.lock:
            g = kg.g
            for d, rec in sorted(records.items()):
                dn = uri(d)
                if any((dn, p, uri(entity)) in g for p in DOC_LINKS.values()):
                    node(d, d.replace("Watson:", ""), "Document", title=rec["metadata"].get("extracted_title"),
                         study_type=rec["metadata"].get("study_type"), sensitivity=rec["access"]["sensitivity_level"])
                    edge(d, entity, "mentions")
    else:
        chosen = [d for d in sorted(records) if not space or short(uri(space)) in
                  {short(s) for s in kg.g.objects(uri(d), W.isInSpace)}][:limit]
        with kg.lock:
            g = kg.g
            for sid in {s["id"] for s in cat.knowledge_spaces}:
                if space and sid != local(uri(space)):
                    continue
                members = [d for d in chosen if (uri(d), W.isInSpace, W[sid]) in g]
                if members:
                    node(sid, g.value(W[sid], W.spaceName).toPython(), "KnowledgeSpace", documents=len(members))
                    for d in members:
                        edge(sid, d, "containsDocument")
            for d in chosen:
                rec, dn = records[d], uri(d)
                node(d, d.replace("Watson:", ""), "Document", title=rec["metadata"].get("extracted_title"),
                     study_type=rec["metadata"].get("study_type"), sensitivity=rec["access"]["sensitivity_level"])
                for kind, prop in DOC_LINKS.items():
                    if kind == "Drug":
                        continue
                    for o in g.objects(dn, prop):
                        if local(o) in m:
                            entity_node(local(o))
                            edge(d, local(o), str(prop).split("#")[1])
            brands = [n for n in nodes if n in m and m[n][0] == "Brand"]
            for b in brands:                    # structure around the brands that appear: their drug, indications, area
                structure(b)
            co = Counter()                      # co-mentions from READABLE chunks only
            for c in g.subjects(RDF.type, W.DocumentChunk):
                if short(g.value(c, W.isChunkOf)) in chosen:
                    co.update(combinations(sorted(local(o) for o in g.objects(c, W.mentionsBrand)), 2))
            for (a, b), n in co.items():
                if a in nodes and b in nodes:
                    edge(a, b, "coMentionedWith")
    return {"mode": mode, "nodes": list(nodes.values()), "edges": list(edges.values()),
            "stats": {"nodes": len(nodes), "edges": len(edges), "documents_shown": sum(1 for n in nodes.values() if n["kind"] == "Document")}}


# ---------------------------------------------------------------- knowledge spaces
def spaces(user: dict) -> list:
    cat, kg = load(), kgmod.get()
    records = visible_records(user)
    privileged = user["role"] in ("SPOC", "ADMIN")
    out = []
    with kg.lock:
        g = kg.g
        for s in cat.knowledge_spaces:
            members = [d for d in records if (uri(d), W.isInSpace, W[s["id"]]) in g]
            archived = sum(1 for d in g.objects(W[s["id"]], W.containsDocument) if str(g.value(d, W.lifecycleState)) == "ARCHIVED")
            q = [records[d]["quality"]["quality_score"] for d in members]
            ing = [records[d]["metadata"]["ingest_timestamp"] for d in members]
            out.append({"id": s["id"], "name": s["name"], "description": s["description"], "owner": s["owner"], "rule": s["rule"],
                        "documents": len(members), "average_quality": round(sum(q) / len(q), 3) if q else None,
                        "latest_ingest": max(ing) if ing else None, "archived": archived if privileged else None,
                        "by_study_type": dict(Counter(records[d]["metadata"]["study_type"] for d in members)),
                        "document_uris": sorted(members)})
    return out


# ---------------------------------------------------------------- one document's knowledge
def document_knowledge(user: dict, document_uri: str) -> dict:
    rec = require_readable(user, document_uri)
    cat, kg = load(), kgmod.get()
    m = _instance_map(cat)
    dn, g = uri(document_uri), kg.g
    status = pipeline.get_status(document_uri)
    out = {"document_uri": document_uri, "enrichment": status, "enriched": False}
    with kg.lock:
        if (dn, W.lifecycleState, None) not in g:
            return out
        lin = g.value(None, W.processedDocument, dn)
        chunks = []
        for c in sorted(g.subjects(W.isChunkOf, dn), key=lambda x: int(g.value(x, W.chunk_index))):
            ents = []
            for prop, label in ((W.mentionsBrand, "Brand"), (W.mentionsDrug, "Drug"), (W.mentionsIndication, "Indication"),
                                (W.mentionsDrugClass, "DrugClass"), (W.coversGeography, "Geography"), (W.mentionsProvisional, "Provisional")):
                for o in g.objects(c, prop):
                    ents.append({"id": local(o), "kind": label, "label": _label(m, local(o)) if local(o) in m else str(g.value(o, RDFS.label))})
            chunks.append({"chunk_uri": short(c), "index": int(g.value(c, W.chunk_index)), "faiss_id": int(g.value(c, W.faissId)) if g.value(c, W.faissId) is not None else None,
                           "entities": ents})
        sent = []
        for s in g.subjects(RDF.type, W.SentimentAssertion):
            ch = g.value(s, W.derivedFrom)
            if ch is not None and g.value(ch, W.isChunkOf) == dn:
                sent.append({"brand": _label(m, local(g.value(s, W.forBrand))), "geography": _label(m, local(g.value(s, W.forGeography))),
                             "indication": _label(m, local(g.value(s, W.forIndication))), "score": float(g.value(s, W.sentimentScore)),
                             "date": str(g.value(s, W.assertionDate)), "chunk_uri": short(ch), "sentence": str(g.value(s, RDFS.label))})
        lineage = None
        if lin is not None:
            lineage = {"id": short(lin), "reviewed_by": str(g.value(g.value(lin, W.reviewedBy), RDFS.label)),
                       "model": str(g.value(lin, W.enrichmentModel)), "processed": str(g.value(lin, W.processingTimestamp)),
                       "chunks_produced": int(g.value(lin, W.chunksProduced)), "triples_written": int(g.value(lin, W.triplesWritten)),
                       "catalog_version": str(g.value(lin, W.catalogVersion))}
        spaces_in = [{"id": local(s), "name": str(g.value(s, W.spaceName))} for s in g.objects(dn, W.isInSpace)]
        classes = sorted(local(t) for t in g.objects(dn, RDF.type) if str(t).startswith(config.NS))
    out.update(enriched=True, chunks=chunks, sentiment=sorted(sent, key=lambda s: s["date"]), lineage=lineage, spaces=spaces_in,
               type=classes, mentions=pipeline.get_mentions(document_uri), catalog_version_now=cat.version,
               stale=bool(lineage and lineage["catalog_version"] != cat.version))
    return out

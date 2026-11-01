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


def _as_list(v) -> list:
    return [x for x in (v if isinstance(v, list) else [v]) if x]


# Naming one entity also makes a document about these (catalog relation): a brand -> the drug it contains, a drug -> its drug class, an
# indication -> its parent indication. The reports name the brand (Dupixa) and the specific indication (non-small cell lung cancer), seldom
# the generic (dupilumib), its class or the broad indication, so without this those would show 0 documents.
IMPLIES = {"Brand": "drug", "Drug": "drug_class", "Indication": "parent"}
HOP_NAMES = {"Brand": "containsDrug", "Drug": "hasDrugClass", "Indication": "subIndicationOf"}      # the catalog relation each step follows


def _implied(m: dict) -> dict:
    """{entity id: [the entities that naming it also covers, all the way up]}: a brand gives its drug and that drug's class, an indication
    gives every ancestor indication. Only catalog relations are followed, so nothing is guessed."""
    direct = {eid: _as_list((e.get("relations") or {}).get(IMPLIES[k])) for eid, (k, e) in m.items() if k in IMPLIES}
    out = {}
    for eid in direct:
        seen, todo = [], list(direct[eid])
        while todo:
            t = todo.pop(0)
            if t not in seen and t != eid:
                seen.append(t)
                todo += direct.get(t, [])
        out[eid] = seen
    return out


def _doc_counts(user: dict, kg, records: dict, m: dict) -> dict:
    """entity id -> number of readable documents that mention or cover it, in the document itself or in any of its passages (a document is
    tagged with a region such as the EU, while its text names the countries, so a country is only ever found through the passages),
    or that mention something that implies it (see IMPLIES)."""
    counts = Counter()
    implied = _implied(m)
    with kg.lock:
        for d in records:
            seen = _named(kg.g, uri(d))
            for x in [x for x in seen if x in implied]:
                seen.update(implied[x])
            counts.update(seen)
    return counts


def _named(g, dn) -> set:
    """The entities one document names itself, in the document's own links or in any of its passages. The caller holds the graph lock."""
    seen = set()
    for prop in DOC_LINKS.values():
        seen.update(local(o) for o in g.objects(dn, prop))
    for c in g.subjects(W.isChunkOf, dn):
        for prop in CHUNK_LINKS:
            seen.update(local(o) for o in g.objects(c, prop))
    return seen


def entities(user: dict, kind: Optional[str] = None, q: Optional[str] = None) -> list:
    cat, kg = load(), kgmod.get()
    m = _instance_map(cat)
    counts = _doc_counts(user, kg, visible_records(user), m)
    out = []
    for eid, (k, e) in m.items():
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
    # entities whose naming makes a document about this one (see IMPLIES), nearest first
    implied = _implied(m)
    sources = sorted((s for s, up in implied.items() if entity_id in up), key=lambda s: (implied[s].index(entity_id), _label(m, s).lower()))
    docs, sentiments, co = [], [], Counter()
    with kg.lock:
        for d, rec in sorted(records.items()):
            dn = uri(d)
            chunks = list(g.subjects(W.isChunkOf, dn))

            def names(target):
                return any((dn, p, target) in g for p in DOC_LINKS.values()) or any((c, p, target) in g for c in chunks for p in CHUNK_LINKS)
            via = None
            hit = names(node)
            if not hit:                                     # named only through something that implies this entity (see _doc_counts)
                src = next((s for s in sources if names(uri(s))), None)
                via = {"id": src, "kind": m[src][0], "label": _label(m, src)} if src else None
                hit = via is not None
            if hit:
                docs.append({"document_uri": d, "title": rec["metadata"].get("extracted_title"), "study_type": rec["metadata"].get("study_type"),
                             "geography": rec["metadata"].get("geography"), "date": rec.get("extracted_date"),
                             "sensitivity_level": rec["access"]["sensitivity_level"], "via": via})
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


# ---------------------------------------------------------------- filters on the graph
# Filter keys the UI and API use, and the kind of entity each one picks. "area" is the therapeutic area; "geography" is a country or region.
FILTER_KINDS = {"brand": "Brand", "indication": "Indication", "drug": "Drug", "area": "TherapeuticArea", "geography": "Geography"}
# A document links to some entities directly (DOC_LINKS) and to more through its passages (a document covers 3 regions, its passages
# cover the countries too). Filters and per-document facts use both, so "Germany" finds the documents whose text is about Germany.
CHUNK_LINKS = (W.mentionsBrand, W.mentionsIndication, W.mentionsDrug, W.mentionsDrugClass, W.coversGeography)


def _doc_links(g, dn) -> dict:
    """{entity id: {relation name: {"document": linked from the document itself, "passages": number of its passages that link}}}.
    The caller holds the graph lock."""
    out = defaultdict(dict)

    def slot(o, prop):
        return out[local(o)].setdefault(str(prop).split("#")[1], {"document": False, "passages": 0})
    for prop in DOC_LINKS.values():
        for o in g.objects(dn, prop):
            slot(o, prop)["document"] = True
    for c in g.subjects(W.isChunkOf, dn):
        for prop in CHUNK_LINKS:
            for o in g.objects(c, prop):
                slot(o, prop)["passages"] += 1
    return out


PASSAGE_KINDS = {"Brand", "Indication", "Drug", "DrugClass", "Geography"}      # what a passage can name; a therapeutic area belongs to the document only


def _doc_contexts(g, dn, brand_rels: dict, m: dict, implied: Optional[dict] = None) -> list:
    """The places in one document where entities occur together: [(passage or None, own, merged, relations)], each of own and merged being
    {entity id: {relation names}}. The first place is the document itself (what it is about: its own links). Then one place per passage:
    `own` is what the passage itself names, `merged` adds the document's own links (a document that covers the EU covers it in every passage).
    Filters are matched on `merged`, so choosing the EU and a brand finds the passages of EU documents that name the brand. The OPTIONS
    a chosen value leads to come from `own`, so choosing France offers what is named where France is named, not everything else the same
    document says elsewhere. `relations` are the relationship names the graph can draw from that place: the document's own link names,
    the catalog relations of the brands it is about (`brand_rels`), containsDocument when it is in a knowledge space, and coMentionedWith
    when the passage itself names two of those brands; choosing a relationship keeps the places that have it. The caller holds the graph lock.

    What a place names also covers what it implies (see IMPLIES): a place that names the brand Dupixa also holds its drug and that drug's
    class, one that names non-small cell lung cancer also holds lung cancer. Those entries have NO relationship names (an empty set), which
    is how a caller tells them from what the place names itself; they add no relationship to the choices and no edge to the drawing."""
    implied = _implied(m) if implied is None else implied

    def cover(places):
        for e in [e for e in places if e in implied]:
            for x in implied[e]:
                places.setdefault(x, set())
        return places
    base = defaultdict(set)
    for prop in DOC_LINKS.values():
        for o in g.objects(dn, prop):
            base[local(o)].add(str(prop).split("#")[1])
    in_space = (dn, W.isInSpace, None) in g

    # Only relationships the graph really draws are offered (a choice must never leave an empty drawing): the document's own link names (the
    # graph draws no drug link), the catalog relations of the brands the document is about, co-mentions of two of those brands in one passage.
    drawn = {r for rels in base.values() for r in rels} - {"mentionsDrug"}
    for e in base:
        drawn |= brand_rels.get(e, set())
    if in_space:
        drawn.add("containsDocument")
    named_brands_base = {e for e in base if e in m and m[e][0] == "Brand"}
    cover(base)

    def relations(own):
        return drawn | ({"coMentionedWith"} if sum(1 for e in own if e in named_brands_base and own[e]) >= 2 else set())
    out = [(None, base, base, relations({}))]
    for c in g.subjects(W.isChunkOf, dn):
        own = defaultdict(set)
        for prop in CHUNK_LINKS:
            for o in g.objects(c, prop):
                own[local(o)].add(str(prop).split("#")[1])
        cover(own)
        merged = defaultdict(set, {e: set(r) for e, r in base.items()})
        for e, r in own.items():
            merged[e] |= r
        out.append((c, own, merged, relations(own)))
    return out


def _brand_relations(cat: Catalog, m: dict) -> dict:
    """{brand id: the relationship names the catalog gives it (containsDrug, targetedIndication ...)}: the reference edges the graph draws around a brand."""
    return {eid: {r["relation"] for r in _related(cat, eid)["outgoing"]} for eid in m if m[eid][0] == "Brand"}


def _satisfies(merged: dict, rels: set, flt: dict) -> bool:
    """Does one place hold ALL the chosen values of every entity filter set, and ALL the chosen relationships? Values are combined with AND
    everywhere (brand A and brand B, France and Germany, coMentionedWith and containsDrug): a document that has both is wanted, not one
    that has either. (Only the Document filter is different: two documents cannot both be the one wanted, so they stay alternatives.)"""
    if flt.get("relation") and not set(flt["relation"]) <= rels:
        return False
    return all(set(flt[k]) <= merged.keys() for k in FILTER_KINDS if k in flt)


def _matching(ctxs: dict, flt: dict, any_document: bool = False) -> list:
    """The documents (keys of `ctxs`, each value being that document's _doc_contexts) with a place that satisfies every set filter (all the
    chosen values of each, together in one place), and that are among the chosen documents if the Document filter is set (the chosen
    documents are alternatives; `any_document` ignores that filter, to list the documents that could still be added)."""
    wanted = set(flt.get("documents", [])) if not any_document else set()
    return [d for d, places in ctxs.items() if (not wanted or d in wanted) and any(_satisfies(mg, rl, flt) for _, _, mg, rl in places)]


def clean_filters(filters: Optional[dict], m: dict) -> dict:
    """Keep the filters that are set. Entity ids must exist and be of the kind the filter is for (ValueError, which the API
    answers with 422); a document id that is not readable simply matches nothing, so nothing is revealed about it."""
    out = {}
    for key, values in (filters or {}).items():
        values = [v for v in dict.fromkeys(values or []) if v]
        if not values:
            continue
        if key in FILTER_KINDS:
            for v in values:
                if v not in m or m[v][0] != FILTER_KINDS[key]:
                    raise ValueError(f"'{v}' is not a {FILTER_KINDS[key]} (filter '{key}')")
        elif key not in ("documents", "relation"):
            raise ValueError(f"Unknown filter '{key}'")
        out[key] = values
    return out


# ---------------------------------------------------------------- graph for the UI
def graph(user: dict, mode: str = "knowledge", space: Optional[str] = None, entity: Optional[str] = None, limit: int = 80,
          filters: Optional[dict] = None) -> dict:
    """Nodes and edges for Cytoscape. mode: knowledge (spaces, documents, the entities they mention),
    reference (the catalog's entities and how they relate), entity (one entity and its neighbours).

    `filters` (knowledge mode only): {"documents": [uri], "brand"|"indication"|"drug"|"area"|"geography": [entity id], "relation": [name]}.
    Every chosen value has to hold (brand A AND brand B AND area X), except the documents, which are alternatives. The entity and
    document filters choose which readable documents are drawn (before the limit); the relation filter then keeps only edges of those
    relationship names. The filter never widens what the user may read: it only narrows the readable documents."""
    cat, kg = load(), kgmod.get()
    m = _instance_map(cat)
    records = visible_records(user)
    flt = clean_filters(filters, m) if mode == "knowledge" else {}
    implied = _implied(m)
    picked = {v for k in FILTER_KINDS if k in flt for v in flt[k]}                  # the entities the user asked for: drawn with emphasis
    nodes, edges = {}, {}

    def node(nid, label, kind, **data):
        nodes.setdefault(nid, {"id": nid, "label": label, "kind": kind, **data})

    def edge(s, t, rel):
        edges.setdefault(f"{s}|{rel}|{t}", {"id": f"{s}|{rel}|{t}", "source": s, "target": t, "label": rel})

    def entity_node(eid):
        k, e = m[eid]
        node(eid, e["label"], k, grounded=len(e.get("attributes", {})), **({"matched": True} if eid in picked else {}))

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
        candidates = [d for d in sorted(records) if not space or short(uri(space)) in
                      {short(s) for s in kg.g.objects(uri(d), W.isInSpace)}]
        ctxs = {}
        if flt:                                           # the filters choose the documents; the limit comes after, so it cannot hide a match
            brand_rels = _brand_relations(cat, m)
            with kg.lock:
                for d in candidates:
                    ctxs[d] = _doc_contexts(kg.g, uri(d), brand_rels, m, implied)
            candidates = _matching(ctxs, flt)
        chosen = candidates[:limit]
        placed = any(k in flt for k in FILTER_KINDS) or bool(flt.get("relation"))
        # only the passages that satisfy the chosen filters (France: the passages that mention France) feed the co-mention edges
        allowed = {c for d in chosen for c, _, mg, rl in ctxs.get(d, []) if c is not None and _satisfies(mg, rl, flt)} if placed else None
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
                if short(g.value(c, W.isChunkOf)) in chosen and (allowed is None or c in allowed):
                    co.update(combinations(sorted(local(o) for o in g.objects(c, W.mentionsBrand)), 2))
            for (a, b), n in co.items():
                if a in nodes and b in nodes:
                    edge(a, b, "coMentionedWith")
            for d in chosen:                    # an entity the user picked is drawn with its link to each matching document, whatever level it comes from
                places = [mg for _, _, mg, rl in ctxs.get(d, []) if _satisfies(mg, rl, flt)]
                for eid in picked & {e for p in places for e in p}:
                    entity_node(eid)
                    direct = {r for p in places for r in p.get(eid, ())}
                    for rel in direct:
                        edge(d, eid, rel)
                    if not direct:                  # only implied (see _doc_contexts): draw the real path, document -> what it names -> ... -> this
                        for p in places:
                            for s in [s for s in p if p[s] and eid in implied.get(s, ())]:
                                entity_node(s)
                                for rel in p[s]:
                                    edge(d, s, rel)
                                hop = s
                                while hop != eid:
                                    nxt = next(n for n in implied[hop] if n == eid or eid in implied.get(n, ()))
                                    entity_node(nxt)
                                    edge(hop, nxt, HOP_NAMES[m[hop][0]])
                                    hop = nxt
    if flt.get("relation"):                     # keep only the chosen relationship names, and the nodes they still join
        keep = set(flt["relation"])
        edges = {k: e for k, e in edges.items() if e["label"] in keep}
        touched = {x for e in edges.values() for x in (e["source"], e["target"])}
        nodes = {k: n for k, n in nodes.items() if k in touched}
    out = {"mode": mode, "nodes": list(nodes.values()), "edges": list(edges.values()),
           "stats": {"nodes": len(nodes), "edges": len(edges), "documents_shown": sum(1 for n in nodes.values() if n["kind"] == "Document")}}
    if mode == "knowledge":
        out["stats"].update(documents_matching=len(candidates), limited=len(candidates) > limit, filters=sorted(flt))
    return out


def graph_filters(user: dict, space: Optional[str] = None, filters: Optional[dict] = None) -> dict:
    """What each filter can offer THIS user right now. Only documents they may read count. With nothing chosen, a value is offered when at least
    one readable document links to it (directly or through a passage). Once filters are chosen, every filter's options follow ALL the chosen
    values: a value is offered only if it occurs in the same place (the document's own links, or one passage) as the chosen values, so
    choosing France offers the brands, indications and drugs named where France is named, not everything else those documents say elsewhere;
    its count is the number of documents that would match once it is chosen as well, so no combination that would show nothing is offered.
    Values are combined with AND everywhere, also inside one filter (brand A and brand B, coMentionedWith and containsDrug), so choosing a
    second brand offers only brands that occur with the first. Only the Document filter is different: chosen documents are alternatives
    (a result cannot be two documents at once), and its list shows the documents that match the other filters. Choosing a relationship keeps
    the places that have it (see _doc_contexts), so every other list narrows to what that relationship joins. A chosen value stays listed
    with its count (the number matching, 0 only for a document no longer fitting), so it can be removed.
    What a place names also covers what the catalog says it is part of (IMPLIES): a document about the brand Dupixa is found by the drug
    dupilumib and by its class, one about non-small cell lung cancer by lung cancer; the counts agree with the entity list.
    `matching` is how many readable documents satisfy all chosen filters; `total` how many are readable (in the space)."""
    cat, kg = load(), kgmod.get()
    m = _instance_map(cat)
    records = visible_records(user)
    flt = clean_filters(filters, m)
    by_kind = {kind: key for key, kind in FILTER_KINDS.items()}
    info, ctxs = {}, {}
    brand_rels = _brand_relations(cat, m)
    implied = _implied(m)
    with kg.lock:
        g = kg.g
        for d in sorted(records):
            dn = uri(d)
            in_spaces = [local(s) for s in g.objects(dn, W.isInSpace)]
            if space and local(uri(space)) not in in_spaces:
                continue
            ctxs[d] = _doc_contexts(g, dn, brand_rels, m, implied)
            rec = records[d]
            info[d] = {"id": d, "title": rec["metadata"].get("extracted_title"), "study_type": rec["metadata"].get("study_type"),
                       "spaces": in_spaces,           # entities the document names itself (an implied one has no relationship names)
                       "entities": sum(1 for e in {x for _, _, mg, _ in ctxs[d] for x, r in mg.items() if r} if e in m)}
    out = {"documents": [info[d] for d in _matching(ctxs, flt, any_document=True)]}
    shown = {d["id"] for d in out["documents"]}
    out["documents"] += [info[d] for d in flt.get("documents", []) if d in info and d not in shown]       # a chosen document stays listed
    matching = _matching(ctxs, flt)
    for key, kind in FILTER_KINDS.items():
        offered, docs_with = set(), defaultdict(set)
        for d in matching:
            for _, own, mg, rl in ctxs[d]:
                if _satisfies(mg, rl, flt):                 # a place that fits ALL the chosen filters (the chosen values of this kind too)
                    for e in (own if kind in PASSAGE_KINDS else mg):
                        if e in m and m[e][0] == kind:
                            offered.add(e)                  # offered only if it is NAMED there (a therapeutic area: if the document has it)
                    for e in mg:
                        if e in m and m[e][0] == kind:
                            docs_with[e].add(d)             # the count is what choosing it as well would leave: documents matching all the filters then
        counts = Counter({e: len(docs_with[e]) for e in offered})
        for eid in flt.get(key, []):
            counts[eid] = len(docs_with[eid])               # a chosen value is in every place that fits, so this is the number matching
        out[key] = sorted(({"id": eid, "label": m[eid][1]["label"], "documents": n} for eid, n in counts.items()), key=lambda x: x["label"].lower())
    # relationship name -> the documents with a place that fits ALL the filters (the relationships already chosen too, since they must all
    # hold) and has it: the count is what choosing it as well would leave
    rel_docs = defaultdict(set)
    for d in matching:
        for _, _, mg, rl in ctxs[d]:
            if _satisfies(mg, rl, flt):
                for name in rl:
                    rel_docs[name].add(d)
    for name in flt.get("relation", []):
        rel_docs.setdefault(name, set())
    out["relation"] = [{"name": n, "documents": len(ds)} for n, ds in sorted(rel_docs.items())]
    out["matching"], out["total"] = len(matching), len(ctxs)
    return out


def document_facts(user: dict, document_uri: str) -> dict:
    """One readable document's facts as statements (subject, relationship, object), with where each comes from: the document itself,
    its passages (with how many), the catalog (reference relations of the things it mentions), plus its sentiment records.
    Everything is read from this document only; a document the user may not read is refused and logged."""
    rec = require_readable(user, document_uri)
    cat, kg = load(), kgmod.get()
    m = _instance_map(cat)
    dn = uri(document_uri)
    title = rec["metadata"].get("extracted_title") or document_uri
    facts, sentiment = [], []

    def ent(eid):
        return {"id": eid, "label": _label(m, eid), "kind": m[eid][0] if eid in m else None}
    with kg.lock:
        g = kg.g
        if (dn, W.lifecycleState, None) not in g:
            return {"document_uri": document_uri, "title": title, "enriched": False, "facts": [], "sentiment": [], "counts": {}}
        links = _doc_links(g, dn)
        doc = {"id": document_uri, "label": title, "kind": "Document"}
        for eid, rels in sorted(links.items(), key=lambda kv: (m[kv[0]][0] if kv[0] in m else "", _label(m, kv[0]))):
            if eid not in m:
                continue
            for rel, how in sorted(rels.items()):
                facts.append({"subject": doc, "relation": rel, "object": ent(eid),
                              "source": "document" if how["document"] else "passages", "passages": how["passages"]})
        co, chunk_ids = Counter(), []
        for c in g.subjects(W.isChunkOf, dn):
            chunk_ids.append(c)
            co.update(combinations(sorted(local(o) for o in g.objects(c, W.mentionsBrand) if local(o) in m), 2))
        for (a, b), n in sorted(co.items()):
            facts.append({"subject": ent(a), "relation": "coMentionedWith", "object": ent(b), "source": "passages", "passages": n})
        for eid in sorted(links):                                    # reference relations of the things this document is about
            if eid in m and m[eid][0] in ("Brand", "Drug", "Indication", "DrugClass"):
                for r in _related(cat, eid)["outgoing"]:
                    facts.append({"subject": ent(eid), "relation": r["relation"], "object": ent(r["id"]), "source": "catalog", "passages": 0})
        grouped = defaultdict(list)
        chunk_set = set(chunk_ids)
        for s in g.subjects(RDF.type, W.SentimentAssertion):
            if g.value(s, W.derivedFrom) in chunk_set:
                grouped[(local(g.value(s, W.forBrand)), local(g.value(s, W.forGeography)), local(g.value(s, W.forIndication)))].append(
                    float(g.value(s, W.sentimentScore)))
        for (b, geo, ind), scores in sorted(grouped.items()):
            sentiment.append({"brand": ent(b), "geography": ent(geo), "indication": ent(ind), "assertions": len(scores),
                              "average_score": round(sum(scores) / len(scores), 3)})
    seen = {(f["subject"]["id"], f["relation"], f["object"]["id"], f["source"]) for f in facts}
    return {"document_uri": document_uri, "title": title, "enriched": True, "facts": facts, "sentiment": sentiment,
            "counts": {"entities": sum(1 for e in links if e in m), "facts": len(seen), "passages": len(chunk_ids), "sentiment_groups": len(sentiment)}}


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


# ---------------------------------------------------------------- where a document sits in the graph
SHARE_KINDS = ("Brand", "Drug", "Indication", "DrugClass")          # two documents are linked when they name the same one of these
PICTURE_ORDER = ("Brand", "Indication", "Drug", "Geography", "TherapeuticArea", "DrugClass")      # which entities the picture keeps first
PICTURE_CAPS = {"Brand": 3, "Indication": 2, "Drug": 2, "Geography": 2, "TherapeuticArea": 1, "DrugClass": 0}      # at most this many of a kind are drawn


def document_position(user: dict, document_uri: str, shared_limit: int = 8, entity_limit: int = 9, picture_documents: int = 4) -> dict:
    """Where one approved document sits in the knowledge graph, from the graph itself (nothing is predicted):
      * `spaces` and `entities`: the knowledge spaces it is in and the entities it names (plus the ones those imply, see IMPLIES, with the one
        that implies them), each with how many readable documents have it now (`documents_after`) and before this one (`documents_before`);
      * `shared`: the readable documents that name the same brands, drugs, drug classes or indications, most shared first;
      * `version`: Layer 1's verdict when it was a new version or a near duplicate of another document;
      * `added`: what its enrichment wrote (triples, chunks, mentions ...) and the entities that stayed provisional;
      * `graph`: a small picture (the document, its spaces, up to `entity_limit` entities, up to `picture_documents` documents that share some).
    Only documents the user may read are counted or listed, so the numbers are the user's own view. A document that is not approved yet
    (or not enriched yet) answers `enriched: false` with its enrichment status; a document outside the user's access is refused."""
    rec = lifecycle.get_record(document_uri)
    out = {"document_uri": document_uri, "state": rec["state"], "enrichment": pipeline.get_status(document_uri), "enriched": False}
    if rec["state"] != "ACTIVE":
        if abac.view_level(user, rec) != "full":
            abac.deny(user, "view knowledge", "document is outside this user's access attributes", document_uri)
        return out
    require_readable(user, document_uri)
    cat, kg = load(), kgmod.get()
    m = _instance_map(cat)
    implied = _implied(m)
    records = visible_records(user)
    dn = uri(document_uri)
    with kg.lock:
        g = kg.g
        if (dn, W.lifecycleState, None) not in g:
            return out
        links = {e: rels for e, rels in _doc_links(g, dn).items() if e in m}
        named = set(links)
        spaces_in = [local(s) for s in g.objects(dn, W.isInSpace)]
        others = {d: _named(g, uri(d)) for d in records if d != document_uri}
        space_docs = {sid: sum(1 for d in records if (uri(d), W.isInSpace, W[sid]) in g) for sid in spaces_in}
        space_names = {sid: str(g.value(W[sid], W.spaceName)) for sid in spaces_in}
    counts = _doc_counts(user, kg, records, m)

    rows = {}
    for e in named:
        rows[e] = {"id": e, "label": _label(m, e), "kind": m[e][0], "via": None}
    for e in sorted(named):                                          # what naming them implies, with the nearest thing that implies it
        for up in implied.get(e, []):
            if up not in rows:
                rows[up] = {"id": up, "label": _label(m, up), "kind": m[up][0], "via": {"id": e, "kind": m[e][0], "label": _label(m, e)}}
    for row in rows.values():
        row["documents_after"] = counts.get(row["id"], 0)
        row["documents_before"] = max(row["documents_after"] - 1, 0)
    entities = sorted(rows.values(), key=lambda r: (PICTURE_ORDER.index(r["kind"]), r["via"] is not None, r["label"].lower()))

    shared = []
    for d, theirs in others.items():
        common = sorted((e for e in named & theirs if m[e][0] in SHARE_KINDS), key=lambda e: (SHARE_KINDS.index(m[e][0]), _label(m, e).lower()))
        if common:
            r = records[d]
            shared.append({"document_uri": d, "title": r["metadata"].get("extracted_title"), "study_type": r["metadata"].get("study_type"),
                           "geography": r["metadata"].get("geography"), "date": r.get("extracted_date"),
                           "shared": [{"id": e, "label": _label(m, e), "kind": m[e][0]} for e in common]})
    shared.sort(key=lambda s: (-len(s["shared"]), s["document_uri"]))

    dd = ((rec.get("assessment") or {}).get("dedup")) or {}
    version = None
    if dd.get("verdict") in ("new_version", "near_duplicate"):
        original = dd.get("original_uri")
        version = {"verdict": dd["verdict"], "similarity": dd.get("similarity"),
                   "original_uri": original if original in records else None}       # the original is named only if the user may read it

    status = out["enrichment"] or {}
    provisional = {}
    for mention in pipeline.get_mentions(document_uri):
        if mention.get("method") == "provisional":
            provisional.setdefault(mention["entity_id"], {"id": mention["entity_id"], "text": mention["text"], "kind": mention["kind"]})
    added = {"triples": status.get("triples"), "chunks": status.get("chunks"), "mentions": status.get("mentions"),
             "sentiments": status.get("sentiments"), "vectors": status.get("vectors"),
             "entities_named": len(named), "entities_implied": len(rows) - len(named), "provisional": sorted(provisional.values(), key=lambda p: p["text"])}

    # the picture: the document in the middle, its spaces, the entities it names (the kinds that join documents first), and the documents
    # that share some of them, each joined to the entities it shares
    title = rec["metadata"].get("extracted_title") or document_uri
    nodes, edges = {}, {}
    nodes[document_uri] = {"id": document_uri, "label": document_uri.replace("Watson:", ""), "kind": "Document", "title": title, "matched": True}
    for sid in spaces_in:
        nodes[sid] = {"id": sid, "label": space_names[sid], "kind": "KnowledgeSpace", "documents": space_docs[sid]}
        edges[f"{sid}|containsDocument|{document_uri}"] = {"id": f"{sid}|containsDocument|{document_uri}", "source": sid, "target": document_uri, "label": "containsDocument"}
    own = sorted((r for r in entities if r["via"] is None and r["kind"] != "DrugClass"), key=lambda r: (PICTURE_ORDER.index(r["kind"]), r["label"].lower()))
    taken = Counter()
    shown = []
    for r in own:                                    # a few of each kind, so six countries cannot crowd out the brand and the indication
        if len(shown) < entity_limit and taken[r["kind"]] < PICTURE_CAPS[r["kind"]]:
            taken[r["kind"]] += 1
            shown.append(r)
    for r in shown:
        nodes[r["id"]] = {"id": r["id"], "label": r["label"], "kind": r["kind"], "grounded": len(m[r["id"]][1].get("attributes", {}))}
        rel = sorted(links[r["id"]])[0]
        edges[f"{document_uri}|{rel}|{r['id']}"] = {"id": f"{document_uri}|{rel}|{r['id']}", "source": document_uri, "target": r["id"], "label": rel}
    drawn = {r["id"] for r in shown}
    drawn_docs = [s for s in shared if drawn & {x["id"] for x in s["shared"]}][:picture_documents]
    for s in drawn_docs:
        d = s["document_uri"]
        nodes[d] = {"id": d, "label": d.replace("Watson:", ""), "kind": "Document", "title": s["title"]}
        for x in s["shared"]:
            if x["id"] in drawn:
                edges[f"{d}|mentions|{x['id']}"] = {"id": f"{d}|mentions|{x['id']}", "source": d, "target": x["id"], "label": "mentions"}
    picture = {"nodes": list(nodes.values()), "edges": list(edges.values()),
               "omitted": {"entities": len(own) - len(shown), "documents": len(shared) - len(drawn_docs)}}

    out.update(enriched=True, title=title,
               spaces=[{"id": s, "name": space_names[s], "documents_after": space_docs[s], "documents_before": max(space_docs[s] - 1, 0)} for s in spaces_in],
               entities=entities, shared=shared[:shared_limit], shared_total=len(shared), version=version, added=added, graph=picture)
    return out

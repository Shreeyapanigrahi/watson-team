"""Step 6: gather the evidence the answer may use, and trace where each piece came from.

Three kinds of evidence, each with an id (E1, E2, ...) that the answer must cite:
  graph    one row of the SPARQL result, written as a plain line. Provenance is traced through the user's slice: a row that
           names a document, a chunk or a sentiment assertion is tied to that document and chunk; a row that only names
           entities (a count, an average) is tied to the entities and lists the documents it was computed over.
  chunk    a passage from a document the user may read, found by vector search over the user's chunks only. In semantic
           mode the graph shapes the ranking (chunks that mention the entities in the question, or that the query result
           points to, rank higher). In plain mode it is cosine similarity alone: this is the plain-RAG baseline.
  catalog  a fact about the platform itself (the catalog, ontology, spaces, an entity's grounding), read live from the
           Layer 2 views, for questions about the knowledge space.

Lineage (chunk -> vector id -> document -> reviewer -> enrichment run -> catalog version) comes from the graph and the
Layer 1 record, so the UI can show a reader exactly how a passage got into the answer.
"""
import re
from collections import Counter
from functools import lru_cache

from rdflib.namespace import RDF, RDFS

from layer1 import lifecycle_manager as lifecycle
from layer2 import config as l2config, embedder, graph_view, kg as kgmod, ontology_view, catalog_view, vector_index
from layer2.kg import W, local, short, uri

from . import config

ENTITY_PROPS = (W.mentionsBrand, W.mentionsDrug, W.mentionsIndication, W.mentionsDrugClass, W.coversGeography)
STOP = set("the a an of in on for to and or is are was were be what which who how many much do does did with by from at as it its this that "
           "than then there their about into over under between versus vs me us our you your tell show give list".split())


def _words(text: str) -> set:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2 and w not in STOP}


def label_of(g, node) -> str:
    v = g.value(node, RDFS.label)
    return str(v) if v is not None else local(node)


def _humanise(g, value) -> str:
    """'Watson:Brand_opdivra' -> 'Opdivra'; numbers and plain text unchanged."""
    if isinstance(value, str) and value.startswith("Watson:"):
        node = uri(value)
        lab = label_of(g, node)
        return lab if lab != local(node) or not value.startswith("Watson:Doc_") else value
    if isinstance(value, float):
        return f"{value:.3f}".rstrip("0").rstrip(".")
    return str(value)


# ---------------------------------------------------------------- provenance
def trace(sl, value) -> dict:
    """Documents and chunks a SPARQL result value points to, found in the user's slice."""
    out = {"documents": set(), "chunks": set(), "entities": set()}
    if not (isinstance(value, str) and value.startswith("Watson:")):
        return out
    g, node = sl.graph, uri(value)
    types = set(g.objects(node, RDF.type))
    if (node, W.lifecycleState, None) in g:
        out["documents"].add(short(node))
    elif W.DocumentChunk in types:
        out["chunks"].add(short(node))
        d = g.value(node, W.isChunkOf)
        if d is not None:
            out["documents"].add(short(d))
    elif W.SentimentAssertion in types:
        c = g.value(node, W.derivedFrom)
        if c is not None:
            out["chunks"].add(short(c))
            d = g.value(c, W.isChunkOf)
            if d is not None:
                out["documents"].add(short(d))
    elif W.CompetitiveLandscape in types:
        d = g.value(node, W.describedBy)
        if d is not None:
            out["documents"].add(short(d))
    elif types & {W.Brand, W.Drug, W.Indication, W.Geography, W.TherapeuticArea, W.DrugClass}:
        out["entities"].add(local(node))
    return out


def entity_documents(sl, entity_id: str) -> list:
    node, g, docs = W[entity_id], sl.graph, set()
    for d in sl.records:
        dn = uri(d)
        if any((dn, p, node) in g for p in (W.mentionsBrand, W.mentionsDrug, W.mentionsIndication, W.coversGeography, W.inTherapeuticArea)):
            docs.add(d)
        elif any((c, p, node) in g for c in g.subjects(W.isChunkOf, dn) for p in ENTITY_PROPS):
            docs.add(d)
    return sorted(docs)


# ---------------------------------------------------------------- graph facts
@lru_cache(maxsize=128)
def _prop_label(name: str) -> str:
    """'Watson:coMentionedWith' -> 'co-mentioned with' (the ontology's own label)."""
    if name == "rdfs:label":
        return "is named"
    node = uri(name.split(":", 1)[1])
    lab = kgmod.get().ontology().value(node, RDFS.label)
    return str(lab) if lab is not None else re.sub(r"(?<!^)(?=[A-Z])", " ", name.split(":", 1)[1]).replace("_", " ").lower()


def sentence_for(sl, plan: dict | None, row: dict) -> str:
    """One result row as a plain statement, built from the plan's own edges: 'Opdivra co-mentioned with Trodelvia'.
    Edges whose ends are not in the row are skipped; aggregate columns are appended as 'average: 0.78'."""
    if not plan:
        return ""
    g = sl.graph
    alias = {e["s"]: e["o"] for e in plan.get("edges") or [] if e["p"] == "rdfs:label"}     # ?drug -> ?name: the row may carry a thing by its name only

    def val(term):
        if term.startswith("Watson:"):
            return label_of(g, uri(term))
        key = term.lstrip("?")                               # result rows are keyed without the question mark
        if key not in row and key + "_label" not in row and term in alias:
            key = alias[term].lstrip("?")
        if key in row or key + "_label" in row:
            v = row.get(key + "_label") or row.get(key)
            return _humanise(g, v) if v is not None else None
        return None
    parts = []
    for e in plan.get("edges") or []:
        if e["p"] == "rdfs:label":
            continue
        a, b = val(e["s"]), val(e["o"])
        if a is not None and b is not None and a != b:
            parts.append(f"{a} {_prop_label(e['p'])} {b}")
    selected = [c["var"] for c in plan.get("select") or [] if c.get("agg") in (None, "none")]
    metrics = []
    for c in plan.get("select") or []:
        key = c["var"].lstrip("?")
        if c.get("agg") in (None, "none") or row.get(key) is None:
            continue
        of_edge = next((e for e in plan.get("edges") or [] if e.get("o") == c.get("of")), None)
        over = _prop_label(of_edge["p"]) if of_edge else "value"                # what is being counted or averaged
        if c["agg"].startswith("count"):
            over = {"?document": "documents", "?brand": "brands", "?chunk": "passages"}.get(c.get("of"), over)
        word = "" if c["agg"].startswith("count") else {"avg": "average ", "sum": "total ", "min": "lowest ", "max": "highest "}[c["agg"]]
        metrics.append(f"{word}{over}: {_humanise(g, row[key])}")
    subject = [t for t in (val(v) for v in selected) if t and not any(t in p for p in parts)]
    if parts:
        return "; ".join(dict.fromkeys(parts + metrics))
    return (", ".join(subject) + (": " if subject and metrics else "") + "; ".join(metrics)) if (subject or metrics) else ""


def graph_facts(sl, sparql_result: dict, start_id: int = 1) -> list:
    items = []
    if not sparql_result or not sparql_result.get("rows"):
        return items
    cols = sparql_result["columns"]
    for i, row in enumerate(sparql_result["rows"][:config.GRAPH_FACTS], 1):
        docs, chunks, ents = set(), set(), set()
        for v in row.values():
            t = trace(sl, v)
            docs |= t["documents"]
            chunks |= t["chunks"]
            ents |= t["entities"]
        text = sentence_for(sl, sparql_result.get("plan"), row) or "; ".join(
            f"{c.replace('_', ' ')}: {_humanise(sl.graph, row[c])}" for c in cols if row.get(c) is not None and not c.endswith("_label"))
        computed = sorted(set().union(*[set(entity_documents(sl, e)) for e in ents])) if ents and not docs else []
        items.append({"id": f"E{start_id + len(items)}", "kind": "graph", "text": text, "row": row, "row_number": i,
                      "documents": sorted(docs), "chunks": sorted(chunks), "entities": sorted(ents),
                      "computed_over": computed[:12], "source_label": f"Knowledge graph, query row {i}"})
    return items


# ---------------------------------------------------------------- passages
def _passage(text: str, query_words: set, limit: int) -> str:
    flat = re.sub(r"\s+", " ", text).strip()
    if len(flat) <= limit:
        return flat
    sents = re.split(r"(?<=[.!?])\s+", flat)
    scored = sorted(range(len(sents)), key=lambda i: (-len(_words(sents[i]) & query_words), i))
    keep, size = set(), 0
    for i in scored:
        if size + len(sents[i]) > limit and keep:
            continue
        keep.add(i)
        size += len(sents[i]) + 1
    return " ".join(sents[i] for i in sorted(keep))[:limit + 80]


def _chunk_entities(g, chunk_uri: str) -> set:
    node = uri(chunk_uri)
    return {local(o) for p in ENTITY_PROPS for o in g.objects(node, p)}


def chunk_evidence(user: dict, sl, question: str, entities: list, graph_items: list, mode: str, start_id: int) -> list:
    allowed = sl.chunk_to_document()                                  # only chunks of documents this user may read
    if not allowed:
        return []
    qwords = _words(question) | {w for e in entities for w in _words(e["label"])}
    raw = vector_index.get().search(embedder.embed_query(question), allowed.keys(), k=30)
    want = {e["entity_id"] for e in entities}
    pointed = {c for it in graph_items for c in it["chunks"]} | {c for it in graph_items for d in it["documents"] for c in
                                                                 (short(x) for x in sl.graph.subjects(W.isChunkOf, uri(d)))} \
        if mode == "semantic" else set()
    cand = []
    for chunk_uri, cos in raw:
        doc = allowed[chunk_uri]
        quality = sl.records[doc]["quality"]["quality_score"]
        if mode == "plain":
            score, parts = cos, {"cosine": round(cos, 4)}
        else:
            ents = _chunk_entities(sl.graph, chunk_uri)
            overlap = len(want & ents) / len(want) if want else 0.0
            bonus = 0.15 if chunk_uri in pointed else 0.0
            score = l2config.W_COSINE * cos + l2config.W_QUALITY * quality + 0.2 * overlap + bonus
            parts = {"cosine": round(cos, 4), "quality": quality, "entity_overlap": round(overlap, 2), "graph_pointer": bool(bonus)}
        cand.append((score, chunk_uri, doc, parts))
    cand.sort(key=lambda c: -c[0])
    items, staging = [], {}
    n_chunks = config.EVIDENCE_CHUNKS if len(graph_items) < 3 else 2           # when the graph already lists the answer, passages only add noise for a small model
    for score, chunk_uri, doc, parts in cand[:n_chunks]:
        if doc not in staging:
            staging[doc] = {c["chunk_uri"]: c for c in lifecycle.load_staging(doc)["chunks"]}
        ch = staging[doc][chunk_uri]
        node = uri(chunk_uri)
        faiss_id = sl.graph.value(node, W.faissId)
        items.append({"id": f"E{start_id + len(items)}", "kind": "chunk", "text": _passage(ch["text"], qwords, config.EVIDENCE_CHARS),
                      "documents": [doc], "chunks": [chunk_uri], "score": round(score, 4), "score_parts": parts,
                      "faiss_id": int(faiss_id) if faiss_id is not None else None,
                      "source_label": f"{doc} / {chunk_uri.split(':')[-1]}"})
    return items


# ---------------------------------------------------------------- knowledge-space facts
def catalog_facts(user: dict, sl, question: str, entities: list, start_id: int) -> list:
    q, items = question.lower(), []

    def add(text, label, kind="catalog"):
        items.append({"id": f"E{start_id + len(items)}", "kind": kind, "text": text, "documents": [], "chunks": [],
                      "source_label": label})

    if re.search(r"\bspaces?\b", q):
        for s in graph_view.spaces(user):
            add(f"Knowledge space '{s['name']}' (owner {s['owner']}) holds {s['documents']} documents you may read"
                + (f", average quality {s['average_quality']}" if s["average_quality"] is not None else "")
                + (f", types: {', '.join(f'{k} {v}' for k, v in s['by_study_type'].items())}" if s["by_study_type"] else "") + ".",
                f"Knowledge space {s['id']}", "space")
    if re.search(r"ontolog|class|propert|inverse|transitive|subclass", q):
        o = ontology_view.describe()
        add(f"The ontology has {len(o['classes'])} classes, {len(o['object_properties'])} object properties and "
            f"{len(o['datatype_properties'])} datatype properties, with {len(o['inverse_pairs'])} inverse pairs and "
            f"{len(o['transitive'])} transitive property ({', '.join(o['transitive'])}). Grounding in the data catalog: "
            + ", ".join(f"{n} {k}" for k, n in o["grounding_summary"].items() if n) + ".", f"Ontology (catalog {o['catalog_version']})", "ontology")
    if re.search(r"catalog|catalogue|table|column|ground|mapp|domain", q):
        s = catalog_view.summary()
        c = s["counts"]
        add(f"The data catalog '{s['catalog']}' (version {s['version']}) holds {c.get('domains')} domains, {c.get('tables')} tables, "
            f"{c.get('columns')} columns and {c.get('foreign_keys', c.get('fks', 'n/a'))} foreign keys.", f"Data catalog {s['version']}")
    if re.search(r"entit|brand|drug|indication|countr|geograph|ground|mapp|how many", q):
        by = Counter(e["kind"] for e in graph_view.entities(user))
        add("Entities in the knowledge space by kind: " + ", ".join(f"{n} {k}" for k, n in sorted(by.items())) + ".", "Entity list (your view)", "catalog")
    for e in entities[:3]:
        try:
            d = graph_view.entity_detail(user, e["entity_id"])
        except Exception:
            continue
        cols = [g["fqn"] for g in d["grounding"][:3]]
        al = ", ".join(f"{k} {v.get('id')}" for k, v in (d["alignment"] or {}).items() if v)
        add(f"{d['label']} is a {d['kind']} grounded in {len(d['grounding'])} catalog column(s)"
            + (f" (for example {', '.join(cols)})" if cols else "") + (f"; external identifiers: {al}" if al else "")
            + f"; {len(d['documents'])} readable document(s) mention it.", f"Catalog grounding of {d['label']}")
    return items


# ---------------------------------------------------------------- lineage
def lineage(sl, evidence: list) -> list:
    """One record per document that supplied a passage or a graph row, with the chain that produced it."""
    g, out, seen = sl.graph, [], set()
    for it in evidence:
        for d in it["documents"]:
            if d in seen or d not in sl.records:
                continue
            seen.add(d)
            rec, node = sl.records[d], uri(d)
            lin = W["Lineage_" + local(node)]
            reviewer = g.value(lin, W.reviewedBy)
            used = sorted({c for x in evidence for c in x["chunks"] if d in x["documents"]})
            out.append({
                "document_uri": d, "title": rec["metadata"].get("extracted_title"), "source_filename": rec["source_filename"],
                "study_type": rec["metadata"].get("study_type"), "sensitivity_level": rec["access"]["sensitivity_level"],
                "therapeutic_area": rec["metadata"].get("therapeutic_area"), "geography": rec["metadata"].get("geography"),
                "quality_score": rec["quality"]["quality_score"], "expiry_date": rec.get("expiry_date"),
                "reviewed_by": rec["review"]["reviewer"] if rec.get("review") else None, "reviewed_at": (rec.get("review") or {}).get("timestamp"),
                "enrichment": {"model": str(g.value(lin, W.enrichmentModel) or ""), "at": str(g.value(lin, W.processingTimestamp) or ""),
                               "catalog_version": str(g.value(lin, W.catalogVersion) or ""), "triples_written": str(g.value(lin, W.triplesWritten) or "")},
                "chunks_used": [{"chunk_uri": c, "vector_id": (lambda v: int(v) if v is not None else None)(g.value(uri(c), W.faissId))} for c in used],
            })
    return out


def gather(user: dict, sl, question: str, entities: list, route: str, sparql_result: dict | None, mode: str) -> dict:
    items, notes = [], []
    if mode == "semantic":                                   # plain RAG never sees the graph: that is the comparison
        items += graph_facts(sl, sparql_result)
        if route == "knowledge_space":
            items += catalog_facts(user, sl, question, entities, len(items) + 1)
    graph_items = [i for i in items if i["kind"] == "graph"]
    if mode == "plain" or route != "knowledge_space" or not items:
        try:
            items += chunk_evidence(user, sl, question, entities, graph_items, mode, len(items) + 1)
        except embedder.EmbeddingUnavailable as e:
            notes.append(f"Passage search unavailable: {e}")
    return {"items": items, "lineage": lineage(sl, items), "notes": notes}

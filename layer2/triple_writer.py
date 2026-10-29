"""Stage 3: write one document's triples (brief 5.3). Idempotent: the document's earlier triples are removed first.

What one ACTIVE document adds to the graph:
  document node (typed with its study-type subclass), chunk nodes with the ABAC attributes of the document,
  mentionsBrand / mentionsIndication / mentionsDrug / coversGeography, mentionsProvisional,
  SentimentAssertion nodes, one LineageRecord, a CompetitiveLandscape node for landscape reports, space membership.

Brand-to-brand links (coMentionedWith, competesWithin) cannot be attributed to one document, so they are recomputed from
the chunks of ACTIVE documents after every change (relink_brands). Deleting or archiving a document therefore removes
the links only it supported.

A document is typed with the most specific class (Watson:HCPPerceptionSurvey), never with Watson:MarketResearchDocument
directly: that parent type is INFERRED by the reasoner, which is what SPARQL query Q6 demonstrates.
"""
from itertools import combinations

from rdflib import Literal
from rdflib.namespace import RDF, RDFS, XSD

from . import config, kg as kgmod, knowledge_space
from .catalog.catalog_store import Catalog
from .kg import W, dt, d, integer, num, uri
from .populate_graph import reviewer_node

STUDY_CLASS = {
    "HCP Perception Survey": "HCPPerceptionSurvey",
    "Competitive Landscape Report": "CompetitiveLandscapeReport",
    "Indication Deep Dive": "IndicationDeepDive",
    "Market Access Insight": "MarketAccessInsight",
    "KOL Interview Summary": "KOLInterviewSummary",
    "Therapy Area Landscape": "TherapyAreaLandscape",
}
LANDSCAPE_TYPES = ("Competitive Landscape Report",)
ENRICHMENT_MODEL = f"{config.EMBEDDING_NAME} (ONNX) + spaCy EntityRuler + rapidfuzz"


def doc_node(document_uri: str):
    return uri(document_uri)


def geography_nodes(value: str, cat: Catalog) -> list:
    ids = {e["id"] for e in cat.instances["Geography"]}
    for cand in (f"GeoRegion_{value}", f"Geo_{value}"):
        if cand in ids:
            return [W[cand]]
    raise ValueError(f"Geography '{value}' is not in the data catalog")


def ta_node(label: str, cat: Catalog):
    for e in cat.instances["TherapeuticArea"]:
        if e["label"] == label:
            return W[e["id"]]
    raise ValueError(f"Therapeutic area '{label}' is not in the data catalog")


def remove_document(g, document_uri: str) -> int:
    """Delete everything this document contributed. Returns the number of triples removed."""
    doc = doc_node(document_uri)
    before = len(g)
    chunks = list(g.subjects(W.isChunkOf, doc))
    for s in list(g.subjects(W.derivedFrom, None)):
        if (s, RDF.type, W.SentimentAssertion) in g and g.value(s, W.derivedFrom) in chunks:
            g.remove((None, W.hasSentiment, s))
            g.remove((s, None, None))
    for lin in list(g.subjects(W.processedDocument, doc)):
        g.remove((lin, None, None))
    for ls in list(g.subjects(W.describedBy, doc)):
        g.remove((ls, None, None))
    for c in chunks:
        g.remove((c, None, None))
    g.remove((None, W.containsDocument, doc))
    g.remove((doc, None, None))
    return before - len(g)


def relink_brands(g) -> dict:
    """Rewrite coMentionedWith and competesWithin from the chunks and landscapes of ACTIVE documents."""
    g.remove((None, W.coMentionedWith, None))
    g.remove((None, W.competesWithin, None))
    active = set(g.subjects(W.lifecycleState, Literal("ACTIVE")))
    co = set()
    for chunk in g.subjects(RDF.type, W.DocumentChunk):
        if g.value(chunk, W.isChunkOf) not in active:
            continue
        brands = sorted(set(g.objects(chunk, W.mentionsBrand)))
        co.update(combinations(brands, 2))
    comp = set()
    for ls in g.subjects(RDF.type, W.CompetitiveLandscape):
        if g.value(ls, W.describedBy) not in active:
            continue
        comp.update(combinations(sorted(set(g.objects(ls, W.includesBrand))), 2))
    for a, b in co:
        g.add((a, W.coMentionedWith, b))
        g.add((b, W.coMentionedWith, a))
    for a, b in comp:
        g.add((a, W.competesWithin, b))
        g.add((b, W.competesWithin, a))
    return {"coMentionedWith_pairs": len(co), "competesWithin_pairs": len(comp)}


def write_document(g, record: dict, chunks: list, mentions: list, sentiments: list, faiss_ids: dict,
                   taxonomy_versions: dict, cat: Catalog, now: str) -> dict:
    """Write the document's triples into `g` (the caller holds the graph lock). Returns counts."""
    from layer1 import abac_engine as abac
    document_uri = record["document_uri"]
    remove_document(g, document_uri)
    start = len(g)
    meta, access = record["metadata"], record["access"]
    doc = doc_node(document_uri)
    study = meta["study_type"]

    g.add((doc, RDF.type, W[STUDY_CLASS.get(study, "MarketResearchDocument")]))
    g.add((doc, RDFS.label, Literal(meta.get("extracted_title") or record["source_filename"])))
    g.add((doc, W.documentTitle, Literal(meta.get("extracted_title") or record["source_filename"])))
    g.add((doc, W.study_type, Literal(study)))
    g.add((doc, W.lifecycleState, Literal(record["state"])))
    g.add((doc, W.sensitivityLevel, Literal(access["sensitivity_level"])))
    for ta in access.get("allowed_therapeutic_areas") or []:
        g.add((doc, W.allowedTA, Literal(ta)))
    for geo in access.get("allowed_geographies") or []:
        g.add((doc, W.allowedGeo, Literal(geo)))
    g.add((doc, W.expiry_date, d(record["expiry_date"])))
    g.add((doc, W.quality_score, num(record["quality"]["quality_score"])))
    if record.get("extracted_date"):
        g.add((doc, W.freshness_date, d(record["extracted_date"])))
    g.add((doc, W.ingest_timestamp, dt(meta["ingest_timestamp"])))
    g.add((doc, W.taxonomyVersion, integer(taxonomy_versions["TherapeuticArea"])))
    g.add((doc, W.catalogVersion, Literal(cat.version)))
    g.add((doc, W.inTherapeuticArea, ta_node(meta["therapeutic_area"], cat)))
    for geo in geography_nodes(meta["geography"], cat):
        g.add((doc, W.coversGeography, geo))
    for sid in knowledge_space.memberships(meta, cat):
        g.add((doc, W.isInSpace, W[sid]))
        g.add((W[sid], W.containsDocument, doc))

    # lineage: who approved it, with what, and what was written (triplesWritten is filled in last)
    lin = W["Lineage_" + kgmod.local(doc)]
    g.add((lin, RDF.type, W.LineageRecord))
    g.add((lin, RDFS.label, Literal(f"Enrichment of {document_uri}")))
    g.add((lin, W.processedDocument, doc))
    g.add((lin, W.reviewedBy, reviewer_node(g, record["review"]["reviewer"])))
    g.add((lin, W.chunksProduced, integer(len(chunks))))
    g.add((lin, W.processingTimestamp, dt(now)))
    g.add((lin, W.enrichmentModel, Literal(ENRICHMENT_MODEL)))
    g.add((lin, W.catalogVersion, Literal(cat.version)))

    by_chunk = {}
    for m in mentions:
        by_chunk.setdefault(m["chunk_uri"], []).append(m)
    doc_mentions = {"Brand": set(), "Indication": set(), "Drug": set()}
    for ch in chunks:
        c = uri(ch["chunk_uri"])
        acl = abac.chunk_acl(record)
        g.add((c, RDF.type, W.DocumentChunk))
        g.add((c, RDFS.label, Literal(ch["chunk_uri"])))
        g.add((c, W.isChunkOf, doc))
        g.add((doc, W.hasChunk, c))
        g.add((c, W.chunk_index, integer(ch["chunk_index"])))
        g.add((c, W.sensitivityLevel, Literal(acl["sensitivityLevel"])))
        for ta in acl.get("allowedTA") or []:
            g.add((c, W.allowedTA, Literal(ta)))
        for geo in acl.get("allowedGeo") or []:
            g.add((c, W.allowedGeo, Literal(geo)))
        if ch["chunk_uri"] in faiss_ids:
            g.add((c, W.faissId, integer(faiss_ids[ch["chunk_uri"]])))
        g.add((c, W.derivedFrom, lin))
        for m in by_chunk.get(ch["chunk_uri"], []):
            e, kind = W[m["entity_id"]], m["kind"]
            if m["method"] == "provisional":
                g.add((c, W.mentionsProvisional, e))
                _provisional_node(g, m, e)
            elif kind == "Brand":
                g.add((c, W.mentionsBrand, e))
                doc_mentions["Brand"].add(e)
            elif kind == "Indication":
                g.add((c, W.mentionsIndication, e))
                doc_mentions["Indication"].add(e)
            elif kind == "Drug":
                g.add((c, W.mentionsDrug, e))
                doc_mentions["Drug"].add(e)
            elif kind == "DrugClass":
                g.add((c, W.mentionsDrugClass, e))
            elif kind == "Geography":
                g.add((c, W.coversGeography, e))
    for e in doc_mentions["Brand"]:
        g.add((doc, W.mentionsBrand, e))
    for e in doc_mentions["Indication"]:
        g.add((doc, W.mentionsIndication, e))
    for e in doc_mentions["Drug"]:
        g.add((doc, W.mentionsDrug, e))

    for s in sentiments:
        node = W["Sentiment_" + kgmod.local(doc) + "_" + s["id"]]
        g.add((node, RDF.type, W.SentimentAssertion))
        g.add((node, RDFS.label, Literal(s["sentence"])))
        g.add((node, W.sentimentScore, num(s["score"])))
        g.add((node, W.assertionDate, d(s["date"])))
        g.add((node, W.forBrand, W[s["brand"]]))
        g.add((node, W.forGeography, W[s["geography"]]))
        g.add((node, W.forIndication, W[s["indication"]]))
        g.add((node, W.derivedFrom, uri(s["chunk_uri"])))
        g.add((W[s["brand"]], W.hasSentiment, node))

    if study in LANDSCAPE_TYPES and len(doc_mentions["Brand"]) >= 2:
        ls = W["Landscape_" + kgmod.local(doc)]
        g.add((ls, RDF.type, W.CompetitiveLandscape))
        g.add((ls, RDFS.label, Literal(f"Competitive landscape in {document_uri}")))
        g.add((ls, W.describedBy, doc))
        g.add((ls, W.inTherapeuticArea, ta_node(meta["therapeutic_area"], cat)))
        for b in sorted(doc_mentions["Brand"]):
            g.add((ls, W.includesBrand, b))

    # triplesWritten counts the triple that records it, so the number is exact
    g.add((lin, W.triplesWritten, integer(len(g) - start + 1)))
    return {"triples": len(g) - start, "chunks": len(chunks), "sentiments": len(sentiments),
            "brands": len(doc_mentions["Brand"]), "indications": len(doc_mentions["Indication"]),
            "drugs": len(doc_mentions["Drug"])}


def _provisional_node(g, mention: dict, node) -> None:
    if (node, RDF.type, W.ProvisionalEntity) in g:
        return
    g.add((node, RDF.type, W.ProvisionalEntity))
    g.add((node, RDFS.label, Literal(mention["text"])))
    g.add((node, W.entityType, Literal(mention["kind"])))
    g.add((node, W.provisionalStatus, Literal("PENDING")))


def set_state(g, document_uri: str, state: str) -> bool:
    """Change only lifecycleState (a document that left ACTIVE keeps its lineage). False if the graph has no such document."""
    doc = doc_node(document_uri)
    if (doc, W.lifecycleState, None) not in g:
        return False
    g.set((doc, W.lifecycleState, Literal(state)))
    return True


def prune_orphan_provisionals(g) -> int:
    """A provisional node nobody mentions (its only document was removed) is deleted, so SHACL's orphan check stays true."""
    n = 0
    for p in list(g.subjects(RDF.type, W.ProvisionalEntity)):
        if (None, W.mentionsProvisional, p) not in g:
            g.remove((p, None, None))
            n += 1
    return n

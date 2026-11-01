"""Enrichment end to end: ACTIVE -> graph + vectors; leaving ACTIVE; failures that must leave every store consistent."""
import pytest
from rdflib import Graph
from rdflib.namespace import RDF

from layer1 import audit_log, lifecycle_manager as lm
from layer1.errors import LifecycleViolationError
from layer2 import (backfill, embedder, enrichment_pipeline as pipeline, kg as kgmod, shacl_validator, taxonomy_manager,
                    triple_writer, vector_index)
from layer2.kg import W, uri

NOW = "2026-01-01T00:00:00+00:00"


def graph():
    return kgmod.get().g


def doc_triples(g, document_uri):
    d = uri(document_uri)
    chunks = list(g.subjects(W.isChunkOf, d))
    return d, chunks


def events(document_uri, kind):
    return audit_log.read_events(document_uri, kind)


# ---------------------------------------------------------------- the happy path
def test_approval_enriches_the_document_through_the_lifecycle_hook(world, add_doc):
    u = add_doc("study_001.pdf", sensitivity="INTERNAL", ta=["Oncology"], geo=["EU"])
    assert pipeline.get_status(u)["state"] == "COMPLETE"
    g = graph()
    d, chunks = doc_triples(g, u)
    staged = lm.load_staging(u)["chunks"]
    assert len(chunks) == len(staged) > 0
    assert (d, RDF.type, W.HCPPerceptionSurvey) in g and (d, RDF.type, W.MarketResearchDocument) not in g   # parent type is inferred, not stored
    assert str(g.value(d, W.lifecycleState)) == "ACTIVE" and str(g.value(d, W.sensitivityLevel)) == "INTERNAL"
    assert {str(o) for o in g.objects(d, W.allowedTA)} == {"Oncology"}
    assert W.Brand_opdivra in set(g.objects(d, W.mentionsBrand))
    assert (W.Space_oncology, W.containsDocument, d) in g and (d, W.isInSpace, W.Space_oncology) in g
    for c in chunks:
        assert str(g.value(c, W.sensitivityLevel)) == "INTERNAL" and g.value(c, W.faissId) is not None   # ABAC attributes ride on every chunk
    assert vector_index.get().size() == len(staged)
    assert [e["event_type"] for e in events(u, None) if e["event_type"].startswith("ENRICHMENT")] == ["ENRICHMENT_START", "ENRICHMENT_COMPLETE"]


def test_lineage_names_the_reviewer_model_catalog_and_an_exact_triple_count(world, add_doc):
    u = add_doc("study_001.pdf")
    g = graph()
    (lin,) = g.subjects(W.processedDocument, uri(u))
    assert str(g.value(g.value(lin, W.reviewedBy), kgmod.RDFS.label)) == "u_admin"
    assert str(g.value(lin, W.enrichmentModel)).startswith("all-MiniLM-L6-v2")
    # triplesWritten counts every triple the document contributed, itself included
    d, chunks = doc_triples(g, u)
    probe = kgmod.bind(Graph()); probe += g
    n_before = len(probe)
    triple_writer.remove_document(probe, u)
    assert n_before - len(probe) == int(g.value(lin, W.triplesWritten))


def test_full_graph_and_index_conform_after_enrichment(world, add_doc):
    add_doc("study_001.pdf"); add_doc("study_003.pdf"); add_doc("study_013.pptx")
    r = shacl_validator.run()
    assert r["conforms"], r["violations"][:3]
    assert r["index_binding"]["ok"] and r["index_binding"]["active_chunks"] == vector_index.get().size()


def test_sentiment_assertions_are_real_nodes_with_a_source_chunk(world, add_doc):
    u = add_doc("study_001.pdf")
    g = graph()
    s = list(g.subjects(RDF.type, W.SentimentAssertion))
    assert s
    for node in s:
        assert g.value(node, W.derivedFrom) in set(g.subjects(W.isChunkOf, uri(u)))
        assert (W.Brand_opdivra, W.hasSentiment, node) in g
    assert any(abs(float(g.value(n, W.sentimentScore)) - 0.69) < 1e-9 for n in s)    # 'Opdivra in Germany: 0.69 (assessed 2021)'


def test_competitive_landscape_links_the_brands_it_compares(world, add_doc):
    add_doc("study_013.pptx")
    g = graph()
    assert (W.Brand_keytrava, W.competesWithin, W.Brand_opdivra) in g and (W.Brand_opdivra, W.competesWithin, W.Brand_keytrava) in g
    assert len(list(g.subjects(RDF.type, W.CompetitiveLandscape))) == 1


# ---------------------------------------------------------------- only ACTIVE documents are enriched
def test_a_document_under_review_is_not_enriched(world, add_doc):
    u = add_doc("study_001.pdf", approve=False)
    assert pipeline.get_status(u) is None
    out = pipeline.run_now(u)
    assert out["state"] == "SKIPPED"
    assert list(graph().subjects(RDF.type, W.DocumentChunk)) == [] and vector_index.get().size() == 0
    assert events(u, "LIFECYCLE_VIOLATION")


def test_a_rejected_document_leaves_no_triples(world, corpus):
    rec = lm.ingest(corpus / "study_001.pdf")
    lm.review(rec["document_uri"], False, "u_admin", reason="test")
    assert list(graph().subjects(RDF.type, W.DocumentChunk)) == []


# ---------------------------------------------------------------- leaving and re-entering ACTIVE
def test_archiving_marks_the_graph_removes_the_vectors_and_keeps_the_lineage(world, add_doc):
    a, b = add_doc("study_013.pptx"), add_doc("study_001.pdf")
    g = graph()
    assert (W.Brand_keytrava, W.competesWithin, W.Brand_opdivra) in g
    lm.archive(a, "u_admin")
    assert str(g.value(uri(a), W.lifecycleState)) == "ARCHIVED"
    assert pipeline.get_status(a)["state"] == "DEACTIVATED"
    assert all(c not in set(vector_index.get().chunk_uris()) for c in [kgmod.short(x) for x in g.subjects(W.isChunkOf, uri(a))])
    assert list(g.subjects(W.processedDocument, uri(a)))                                    # history stays
    assert (W.Brand_keytrava, W.competesWithin, W.Brand_opdivra) not in g                   # the link only that document supported is gone
    r = shacl_validator.run()
    assert r["conforms"], r["violations"][:3]


def test_reinstating_enriches_it_again(world, add_doc):
    a = add_doc("study_001.pdf")
    lm.archive(a, "u_admin")
    assert vector_index.get().size() == 0
    lm.reinstate(a, "u_admin")
    assert pipeline.get_status(a)["state"] == "COMPLETE" and str(graph().value(uri(a), W.lifecycleState)) == "ACTIVE"
    assert vector_index.get().size() == len(lm.load_staging(a)["chunks"])
    assert shacl_validator.run()["conforms"]


def test_expiry_returns_a_document_to_review_and_out_of_the_index(world, add_doc):
    from layer1 import expiry_manager
    a = add_doc("study_001.pdf")
    lm._update(a, lambda r: r.update(expiry_date="2000-01-01"))
    assert expiry_manager.scan_expired() == [a]
    assert str(graph().value(uri(a), W.lifecycleState)) == "UNDER_REVIEW" and vector_index.get().size() == 0


def test_enriching_twice_gives_the_same_graph_apart_from_vector_ids(world, add_doc):
    u = add_doc("study_001.pdf")
    first = {t for t in graph() if t[1] != W.faissId and t[1] != W.processingTimestamp}
    pipeline.run_now(u)
    second = {t for t in graph() if t[1] != W.faissId and t[1] != W.processingTimestamp}
    assert first == second and vector_index.get().size() == len(lm.load_staging(u)["chunks"])


# ---------------------------------------------------------------- deleting a document
def test_deleting_a_document_removes_everything_layer_2_holds_for_it_and_only_for_it(world, add_doc):
    a, b = add_doc("study_013.pptx"), add_doc("study_001.pdf")
    g = graph()
    a_chunks = {kgmod.short(x) for x in g.subjects(W.isChunkOf, uri(a))}
    b_chunks = {kgmod.short(x) for x in g.subjects(W.isChunkOf, uri(b))}
    assert a_chunks and b_chunks and (W.Brand_keytrava, W.competesWithin, W.Brand_opdivra) in g
    assert pipeline.get_mentions(a) and pipeline.get_status(a)
    out = lm.delete_document(a, "u_admin")
    assert out["deleted"] and lm.get_state(a) is None
    # nothing of A is left in the graph, as subject or as object, and the link only A supported is gone
    gone = {uri(a), *[uri(c) for c in a_chunks]}
    assert not [t for t in graph() if t[0] in gone or t[2] in gone]
    assert (W.Brand_keytrava, W.competesWithin, W.Brand_opdivra) not in graph()
    assert {kgmod.short(x) for x in graph().subjects(W.isChunkOf, uri(b))} == b_chunks         # B is untouched
    # nothing of A in the vector index, the status store or the mentions store; B's are still there
    assert set(vector_index.get().chunk_uris()) == b_chunks
    assert pipeline.get_status(a) is None and pipeline.get_mentions(a) == [] and pipeline.get_status(b)["state"] == "COMPLETE" and pipeline.get_mentions(b)
    assert len(events(a, "DOCUMENT_DELETED")) == 1
    r = shacl_validator.run()
    assert r["conforms"], r["violations"][:3]
    assert r["index_binding"]["ok"] and r["index_binding"]["active_chunks"] == vector_index.get().size()


def test_deleting_a_document_that_is_still_being_enriched_waits_for_the_worker_and_is_not_written_back(world, add_doc):
    pipeline.set_sync(False)                                  # the real setting: enrichment runs on the background worker
    a = add_doc("study_013.pptx")                             # approved, so a job is queued (or running) right now
    lm.delete_document(a, "u_admin")                          # waits behind that job on the same worker
    assert pipeline.wait_idle(60)
    assert lm.get_state(a) is None and pipeline.get_status(a) is None
    assert not [t for t in graph() if t[0] == uri(a) or t[2] == uri(a)] and vector_index.get().size() == 0
    assert list(graph().subjects(RDF.type, W.DocumentChunk)) == []


def test_deleting_an_archived_document_works_too(world, add_doc):
    a, b = add_doc("study_013.pptx"), add_doc("study_001.pdf")
    lm.archive(a, "u_admin")
    lm.delete_document(a, "u_admin")
    assert not [t for t in graph() if t[0] == uri(a) or t[2] == uri(a)] and lm.get_state(b) == "ACTIVE"
    assert shacl_validator.run()["conforms"]


def test_deleting_a_document_takes_it_out_of_the_provisional_entities(world):
    from layer2 import entity_resolver as er
    er._register("Brand", "Newbrandex", "Watson:Doc_001", "Watson:Chunk_001_000")
    er._register("Brand", "Sharedbrandex", "Watson:Doc_001", "Watson:Chunk_001_001")
    er._register("Brand", "Sharedbrandex", "Watson:Doc_002", "Watson:Chunk_002_000")
    er._register("Brand", "Decidedbrandex", "Watson:Doc_001", "Watson:Chunk_001_002")
    store = er.load_store()
    store["entities"][er.provisional_id("Brand", "Decidedbrandex")]["status"] = "APPROVED"
    er._save(store)
    assert er.drop_document("Watson:Doc_001") == 1                                           # only the one nobody mentions any more
    left = {e["label"]: e for e in er.list_provisional()}
    assert set(left) == {"Sharedbrandex", "Decidedbrandex"}
    assert left["Sharedbrandex"]["documents"] == ["Watson:Doc_002"] and left["Sharedbrandex"]["chunks"] == ["Watson:Chunk_002_000"]
    assert left["Decidedbrandex"]["status"] == "APPROVED" and left["Decidedbrandex"]["documents"] == []     # a human decision stays


# ---------------------------------------------------------------- failures: logged, document stays ACTIVE, stores stay consistent
def _snapshot():
    return {"triples": set(graph()), "vectors": sorted(vector_index.get().chunk_uris())}


def test_taxonomy_failure_writes_nothing_and_keeps_the_document_active(world, add_doc):
    a = add_doc("study_001.pdf")                                   # approved and enriched while Oncology was an active term
    taxonomy_manager.deprecate_term("TherapeuticArea", "TA_01", "u_admin")
    before = _snapshot()
    st = pipeline.run_now(a)                                       # a later re-enrichment (retry, or a catalog change)
    assert lm.get_state(a) == "ACTIVE"
    assert st["state"] == "FAILED" and st["stage"] == "taxonomy" and "TherapeuticArea" in st["error"]
    assert _snapshot() == before                                   # the earlier, valid enrichment is untouched
    assert events(a, "ENRICHMENT_FAILED")[-1]["details"]["stage"] == "taxonomy"


def test_layer_1_itself_refuses_to_approve_into_a_deprecated_area(world, corpus):
    from layer1.errors import MetadataIncompleteError
    taxonomy_manager.deprecate_term("TherapeuticArea", "TA_01", "u_admin")
    rec = lm.ingest(corpus / "study_002.docx")
    with pytest.raises(MetadataIncompleteError, match="taxonomy"):
        lm.review(rec["document_uri"], True, "u_admin", sensitivity_level="INTERNAL")


def test_embedding_failure_writes_nothing(world, add_doc, monkeypatch):
    add_doc("study_001.pdf")
    before = _snapshot()
    monkeypatch.setattr(embedder, "embed", lambda t: (_ for _ in ()).throw(embedder.EmbeddingUnavailable("model missing")))
    b = add_doc("study_002.docx")
    assert lm.get_state(b) == "ACTIVE" and pipeline.get_status(b)["stage"] == "embedding"
    assert _snapshot() == before


def test_a_failure_after_the_vectors_were_added_removes_them_again(world, add_doc, monkeypatch):
    add_doc("study_001.pdf")
    before = _snapshot()

    def boom(*a, **k):
        raise RuntimeError("disk full")
    monkeypatch.setattr(triple_writer, "write_document", boom)
    b = add_doc("study_002.docx")
    st = pipeline.get_status(b)
    assert st["state"] == "FAILED" and st["stage"] == "triples"
    assert _snapshot() == before                          # index and graph still agree
    assert shacl_validator.index_binding()["ok"]


def test_layer_1_does_not_let_a_scan_with_no_text_become_active(world, add_doc):
    from layer1.errors import MetadataIncompleteError
    with pytest.raises(MetadataIncompleteError, match="word_count"):
        add_doc("study_006.pdf", updates={"study_type": "HCP Perception Survey", "therapeutic_area": "Immunology", "geography": "EU"})


def test_a_document_with_no_chunks_is_reported_not_half_enriched(world, add_doc):
    # Layer 1 prevents this state; the guard is defence in depth, tested by emptying the staged chunks.
    u = add_doc("study_001.pdf")
    triples = set(graph())
    staged = lm.load_staging(u)
    staged["chunks"] = []
    from layer1.jsonstore import write_json
    write_json(lm.staging_path(u), staged)
    st = pipeline.run_now(u)
    assert st["state"] == "FAILED" and "no chunks" in st["error"]
    assert set(graph()) == triples                                  # the earlier enrichment is not damaged


# ---------------------------------------------------------------- derived data can be rebuilt
def test_a_rebuild_from_layer_1_gives_the_same_graph(world, add_doc, monkeypatch):
    for f in ("study_001.pdf", "study_003.pdf", "study_013.pptx"):
        add_doc(f)
    strip = lambda g: {t for t in g if t[1] not in (W.faissId, W.processingTimestamp)}
    first, vecs = strip(graph()), vector_index.get().size()
    out = backfill.run(reset=True)
    assert out["failed"] == {} and out["vectors"] == vecs
    assert strip(graph()) == first
    assert shacl_validator.run()["conforms"]


def test_backfill_skips_documents_that_are_already_enriched(world, add_doc):
    add_doc("study_001.pdf")
    out = backfill.run()
    assert out["enriched"] == 0 and out["documents_active"] == 1

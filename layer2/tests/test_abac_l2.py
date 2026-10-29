"""ABAC in Layer 2: what each role sees in the graph, entities, spaces, document knowledge and search.

World built for these tests (all ACTIVE and enriched):
  A  study_001.pdf  Opdivra survey, Oncology/EU      INTERNAL
  B  study_013.pptx competitive landscape (4 oncology brands), Oncology/EU   RESTRICTED
  C  study_009.pdf  Jardiva survey, Cardiology/US    INTERNAL
"""
import pytest

from layer1 import abac_engine as abac, audit_log
from layer1.errors import WatsonAccessDeniedError
from layer2 import embedder, graph_view, search, sparql_runner, telemetry, vector_index
from layer2.kg import short


@pytest.fixture()
def docs(add_doc):
    a = add_doc("study_001.pdf", sensitivity="INTERNAL", ta=["Oncology"], geo=["EU"])
    b = add_doc("study_013.pptx", sensitivity="RESTRICTED", ta=["Oncology"], geo=["EU"])
    c = add_doc("study_009.pdf", sensitivity="INTERNAL", ta=["Cardiology"], geo=["US"])
    return a, b, c


def U(user_id):
    return abac.get_user(user_id)


def test_each_role_reads_only_what_its_attributes_allow(docs):
    a, b, c = docs
    assert set(graph_view.visible_records(U("u_viewer"))) == {a}              # INTERNAL ceiling, Oncology + EU only
    assert set(graph_view.visible_records(U("u_analyst"))) == {a, b, c}       # RESTRICTED ceiling, both areas, all regions
    assert set(graph_view.visible_records(U("u_spoc"))) == {a, b}             # Oncology owner: not Cardiology
    assert set(graph_view.visible_records(U("u_admin"))) == {a, b, c}


def test_graph_hides_documents_and_does_not_leak_through_derived_edges(docs):
    a, b, c = docs
    viewer = graph_view.graph(U("u_viewer"), "knowledge")
    ids = {n["id"] for n in viewer["nodes"]}
    assert a in ids and b not in ids and c not in ids
    # the landscape document B compares Keytrava, Lumakro, Opdivra, Tagrisa. The stored graph has those links,
    # but a viewer who cannot read B must not learn it from the graph
    assert [e for e in viewer["edges"] if e["label"] == "coMentionedWith"] == []
    analyst = graph_view.graph(U("u_analyst"), "knowledge")
    assert b in {n["id"] for n in analyst["nodes"]}
    assert [e for e in analyst["edges"] if e["label"] == "coMentionedWith"]


def _doc_ids(g):
    return {n["id"] for n in g["nodes"] if n["kind"] == "Document"}


def test_graph_filters_narrow_the_readable_documents_and_never_widen_them(docs):
    a, b, c = docs
    viewer, analyst = U("u_viewer"), U("u_analyst")
    # Keytrava appears only in the restricted landscape document B: the analyst gets it, the viewer gets an empty graph (and no error)
    assert graph_view.graph(viewer, "knowledge", filters={"brand": ["Brand_keytrava"]})["nodes"] == []
    g = graph_view.graph(analyst, "knowledge", filters={"brand": ["Brand_keytrava"]})
    assert _doc_ids(g) == {b} and any(n["id"] == "Brand_keytrava" and n.get("matched") for n in g["nodes"])
    assert graph_view.graph(viewer, "knowledge", filters={"documents": [b]})["nodes"] == []            # naming an unreadable document reveals nothing
    # values of one filter are alternatives; different filters must all hold
    either = graph_view.graph(analyst, "knowledge", filters={"brand": ["Brand_keytrava", "Brand_jardiva"]})
    assert _doc_ids(either) == {b, c}
    assert graph_view.graph(analyst, "knowledge", filters={"brand": ["Brand_keytrava"], "area": ["TA_cardiology"]})["nodes"] == []
    assert _doc_ids(graph_view.graph(analyst, "knowledge", filters={"brand": ["Brand_jardiva"], "area": ["TA_cardiology"]})) == {c}
    assert _doc_ids(graph_view.graph(analyst, "knowledge", filters={"documents": [a]})) == {a}
    # the filter is applied before the limit, so the limit cannot hide a match
    assert _doc_ids(graph_view.graph(analyst, "knowledge", limit=1, filters={"brand": ["Brand_jardiva"]})) == {c}
    stats = graph_view.graph(analyst, "knowledge", limit=1, filters={"area": ["TA_oncology"]})["stats"]
    assert stats["documents_matching"] == 2 and stats["limited"] and stats["documents_shown"] == 1


def test_a_geography_found_only_in_passages_still_matches_and_is_drawn(docs):
    a, b, c = docs
    g = graph_view.graph(U("u_viewer"), "knowledge", filters={"geography": ["Geo_DE"]})            # Germany is in A's passages, not in its document-level links
    assert _doc_ids(g) == {a}
    assert next(n for n in g["nodes"] if n["id"] == "Geo_DE").get("matched")
    assert any(e["source"] == a and e["target"] == "Geo_DE" and e["label"] == "coversGeography" for e in g["edges"])


def test_the_relationship_filter_keeps_only_those_edges_and_the_nodes_they_join(docs):
    r = graph_view.graph(U("u_analyst"), "knowledge", filters={"relation": ["mentionsBrand"]})
    assert r["edges"] and {e["label"] for e in r["edges"]} == {"mentionsBrand"}
    touched = {x for e in r["edges"] for x in (e["source"], e["target"])}
    assert {n["id"] for n in r["nodes"]} == touched                                              # no node left without a relationship
    assert not any(n["kind"] == "KnowledgeSpace" for n in r["nodes"])


def test_a_filter_with_an_unknown_or_wrong_kind_of_id_is_refused(docs):
    for bad in ({"brand": ["Geo_DE"]}, {"geography": ["Nope"]}, {"colour": ["red"]}):
        with pytest.raises(ValueError):
            graph_view.graph(U("u_admin"), "knowledge", filters=bad)
    assert graph_view.graph(U("u_admin"), "reference", filters={"brand": ["Geo_DE"]})["nodes"]        # filters only apply to the knowledge view


def test_filter_options_offer_only_what_the_reader_can_read(docs):
    a, b, c = docs
    viewer, analyst = graph_view.graph_filters(U("u_viewer")), graph_view.graph_filters(U("u_analyst"))
    assert [d["id"] for d in viewer["documents"]] == [a] and {d["id"] for d in analyst["documents"]} == {a, b, c}
    brands = lambda f: {x["id"]: x["documents"] for x in f["brand"]}
    assert "Brand_keytrava" not in brands(viewer) and brands(analyst)["Brand_keytrava"] == 1 and brands(viewer)["Brand_opdivra"] == 1
    assert [x["id"] for x in viewer["area"]] == ["TA_oncology"] and {x["id"] for x in analyst["area"]} >= {"TA_oncology", "TA_cardiology"}
    assert "Geo_DE" in {x["id"] for x in viewer["geography"]}                                   # from the passages
    assert "coMentionedWith" not in {x["name"] for x in viewer["relation"]} and "coMentionedWith" in {x["name"] for x in analyst["relation"]}
    only_cardio = graph_view.graph_filters(U("u_analyst"), "Space_cardiology")
    assert [d["id"] for d in only_cardio["documents"]] == [c]


def test_document_facts_are_this_documents_statements_and_follow_access(docs):
    a, b, c = docs
    with pytest.raises(WatsonAccessDeniedError):
        graph_view.document_facts(U("u_viewer"), b)
    assert audit_log.read_events(b, "ABAC_DENIED")
    f = graph_view.document_facts(U("u_viewer"), a)
    rows = {(x["subject"]["id"], x["relation"], x["object"]["id"]): x for x in f["facts"]}
    assert (a, "mentionsBrand", "Brand_opdivra") in rows and rows[(a, "mentionsBrand", "Brand_opdivra")]["passages"] > 0
    germany = rows[(a, "coversGeography", "Geo_DE")]
    assert germany["source"] == "passages" and germany["passages"] > 0                          # a passage-level fact says how many passages
    assert any(x["source"] == "catalog" and x["relation"] == "containsDrug" for x in f["facts"])
    assert all(x["subject"]["id"] == a or x["source"] != "document" for x in f["facts"])        # document-level facts are about this document only
    assert f["counts"]["passages"] > 0 and f["sentiment"] and f["sentiment"][0]["average_score"] is not None
    landscape = graph_view.document_facts(U("u_analyst"), b)
    assert any(x["relation"] == "coMentionedWith" and "Brand_keytrava" in (x["subject"]["id"], x["object"]["id"]) for x in landscape["facts"])


def test_entity_pages_count_and_list_only_readable_documents(docs):
    a, b, c = docs
    viewer = graph_view.entity_detail(U("u_viewer"), "Brand_keytrava")
    assert viewer["documents"] == [] and viewer["co_mentioned_brands"] == [] and viewer["sentiment"] == []
    analyst = graph_view.entity_detail(U("u_analyst"), "Brand_keytrava")
    assert [d["document_uri"] for d in analyst["documents"]] == [b]
    assert {x["id"] for x in analyst["co_mentioned_brands"]} >= {"Brand_opdivra", "Brand_lumakro"}
    assert analyst["grounding"] and all(g["data_type"] for g in analyst["grounding"])    # the UI shows the column type
    counts = {e["id"]: e["documents"] for e in graph_view.entities(U("u_viewer"))}
    assert counts["Brand_opdivra"] == 1 and counts["Brand_keytrava"] == 0 and counts["Brand_jardiva"] == 0


def test_reference_data_is_visible_to_everyone(docs):
    kinds = {e["kind"] for e in graph_view.entities(U("u_viewer"))}
    assert kinds == set(graph_view.KINDS)                                      # catalog rows are not research content
    assert len(graph_view.graph(U("u_viewer"), "reference")["nodes"]) > 50


def test_space_counts_follow_the_reader(docs):
    def count(user, sid):
        return next(s for s in graph_view.spaces(U(user)) if s["id"] == sid)["documents"]
    assert count("u_viewer", "Space_oncology") == 1 and count("u_analyst", "Space_oncology") == 2
    assert count("u_viewer", "Space_cardiology") == 0 and count("u_analyst", "Space_cardiology") == 1
    assert next(s for s in graph_view.spaces(U("u_viewer")) if s["id"] == "Space_oncology")["archived"] is None   # only SPOC/ADMIN see it


def test_document_knowledge_is_denied_and_logged_for_a_reader_without_access(docs):
    a, b, c = docs
    with pytest.raises(WatsonAccessDeniedError):
        graph_view.document_knowledge(U("u_viewer"), b)
    denied = audit_log.read_events(b, "ABAC_DENIED")
    assert denied and denied[-1]["details"]["user_id"] == "u_viewer"
    assert graph_view.document_knowledge(U("u_analyst"), b)["enriched"] is True


def test_a_document_that_left_active_disappears_for_everyone_including_admin(docs):
    from layer1 import lifecycle_manager as lm
    a, b, c = docs
    lm.archive(b, "u_admin")
    assert b not in graph_view.visible_records(U("u_admin"))
    assert b not in {n["id"] for n in graph_view.graph(U("u_admin"), "knowledge")["nodes"]}
    with pytest.raises(WatsonAccessDeniedError):
        graph_view.document_knowledge(U("u_admin"), b)


# ---------------------------------------------------------------- search: the filter is applied before ranking
def test_search_never_returns_a_chunk_the_user_may_not_read(docs):
    a, b, c = docs
    q = "competitive landscape Keytrava Lumakro Tagrisa market share"          # text that matches document B
    viewer = search.search(U("u_viewer"), q, k=25)
    assert {h["document_uri"] for h in viewer["hits"]} <= {a}
    analyst = search.search(U("u_analyst"), q, k=25)
    assert analyst["hits"][0]["document_uri"] == b                              # the analyst does get it, and it is the best match
    own = len(graph_view.visible_chunks(U("u_viewer")))
    assert viewer["searched_chunks"] == own and own < analyst["searched_chunks"]


def test_the_search_filter_is_in_the_index_call_not_after_it(docs, monkeypatch):
    """Spy on the vector index: a viewer's search must hand it ONLY chunks of readable documents."""
    a, b, c = docs
    seen = {}
    real = vector_index.VectorIndex.search

    def spy(self, query, allowed, k=10):
        seen["allowed"] = None if allowed is None else set(allowed)
        return real(self, query, allowed, k)
    monkeypatch.setattr(vector_index.VectorIndex, "search", spy)
    search.search(U("u_viewer"), "Keytrava", k=5)
    assert seen["allowed"] is not None and seen["allowed"] == set(graph_view.visible_chunks(U("u_viewer")))


def test_search_ranks_with_the_brief_formula(docs):
    r = search.search(U("u_analyst"), "Opdivra sentiment", k=5)
    for h in r["hits"]:
        assert h["score"] == pytest.approx(0.7 * h["cosine"] + 0.3 * h["quality"], abs=1e-3)
    assert [h["score"] for h in r["hits"]] == sorted((h["score"] for h in r["hits"]), reverse=True)


def test_an_empty_query_is_refused(docs):
    with pytest.raises(ValueError):
        search.search(U("u_viewer"), "   ")


# ---------------------------------------------------------------- Q10: the governance audit
def test_q10_finds_no_violation_after_real_searches_by_every_role(docs):
    for user in ("u_viewer", "u_analyst", "u_spoc", "u_admin"):
        search.search(U(user), "Keytrava Opdivra sentiment competitive", k=10)
    res = sparql_runner.run("Q10")
    assert res["telemetry_events"] > 0 and res["rows"]
    assert {r["verdict"] for r in res["rows"]} == {"ok"}
    viewer_levels = {r["sensitivity"] for r in res["rows"] if r["role"] == "VIEWER"}
    assert viewer_levels <= {"PUBLIC", "INTERNAL"}


def test_q10_would_catch_a_leak(docs):
    """The audit is only worth anything if it can fail. Plant the leak it exists to find."""
    a, b, c = docs
    chunk = short(next(iter(vector_index.get().chunk_uris() and [x for x in vector_index.get().chunk_uris() if x.startswith("Watson:Chunk_002")])))
    telemetry.record_search(U("u_viewer"), "planted", [{"chunk_uri": chunk, "document_uri": b, "score": 0.9}])
    rows = sparql_runner.run("Q10")["rows"]
    bad = [r for r in rows if r["verdict"] == "VIOLATION"]
    assert bad and bad[0]["role"] == "VIEWER" and bad[0]["sensitivity"] == "RESTRICTED"


def test_with_no_searches_q10_is_empty_not_invented(docs):
    assert sparql_runner.run("Q10")["rows"] == []

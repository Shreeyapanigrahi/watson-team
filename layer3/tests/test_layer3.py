"""Layer 3: access slice, question understanding, plan checking, hallucination check, the whole pipeline, and the HTTP stream."""
import json

import pytest
from fastapi.testclient import TestClient

from layer1 import abac_engine as abac, audit_log
from layer1.api import main as api_main
from layer2 import telemetry
from layer2.kg import W, uri
from layer3 import guard, llm, pipeline, sparql_gen, understand, user_graph
from layer3.tests.conftest import col, pat, plan, quote_evidence

RESTRICTED_ONLY_BRANDS_DOC = "study_013.pptx"           # a different corpus file for the restricted document


@pytest.fixture()
def docs(add_doc):
    """study_001 INTERNAL (everyone in Oncology/EU may read it), study_013 RESTRICTED (analyst and above only)."""
    return (add_doc("study_001.pdf", sensitivity="INTERNAL", ta=["Oncology"], geo=["EU"]),
            add_doc(RESTRICTED_ONLY_BRANDS_DOC, sensitivity="RESTRICTED", ta=["Oncology"], geo=["EU"]))


def user(uid):
    return abac.get_user(uid)


# ---------------------------------------------------------------- step 2: the access slice
def test_viewer_slice_has_no_trace_of_the_restricted_document(docs, l3_stores):
    open_doc, restricted = docs
    sl = user_graph.build(user("u_viewer"))
    g = sl.graph
    assert open_doc in sl.records and restricted not in sl.records
    assert (uri(open_doc), None, None) in g
    assert not list(g.triples((uri(restricted), None, None)))
    assert not list(g.triples((None, None, uri(restricted))))
    chunks = {c for c in sl.chunk_to_document()}
    assert chunks and all(sl.chunk_to_document()[c] == open_doc for c in chunks)
    assert sl.stats["documents_withheld"] == 1
    assert "study_013" not in json.dumps(sl.stats)                       # counted, never named


def test_analyst_slice_includes_it_and_derived_links_follow(docs, l3_stores):
    open_doc, restricted = docs
    analyst = user_graph.build(user("u_analyst"))
    viewer = user_graph.build(user("u_viewer"))
    assert restricted in analyst.records
    co = lambda s: set(s.graph.triples((None, W.coMentionedWith, None)))
    assert co(viewer) <= co(analyst)                                     # nothing appears for the viewer that the analyst lacks
    assert len(viewer.graph) < len(analyst.graph)


# ---------------------------------------------------------------- step 3: entities and route
def test_entities_come_from_the_catalog_and_typos_are_resolved():
    e = understand.understand("Which brands are co-mentioned with Opdivra?")
    assert [x["entity_id"] for x in e["entities"]] == ["Brand_opdivra"] and e["route"] == "graph_first"
    typo = understand.extract_entities("What do doctors think of Opdivva in Germany?")
    ids = {x["entity_id"]: x["method"] for x in typo}
    assert ids["Brand_opdivra"] == "fuzzy" and "Geo_DE" in ids


def test_platform_questions_take_the_knowledge_space_route():
    r = understand.understand("How many classes does the ontology have and how are they grounded in the catalog?")
    assert r["route"] == "knowledge_space" and r["reasons"]
    assert understand.understand("What are doctors' main concerns about adherence?")["route"] == "semantic_first"


# ---------------------------------------------------------------- step 4: the plan is typed, checked, never trusted
OPDIVRA = [{"text": "Opdivra", "entity_id": "Brand_opdivra", "kind": "Brand", "label": "Opdivra", "method": "exact", "confidence": 1.0}]


def test_the_grammar_only_allows_edges_that_exist_in_the_users_graph(docs, l3_stores):
    viewer, analyst = (user_graph.build(user(u)).graph for u in ("u_viewer", "u_analyst"))
    shapes = sparql_gen.edge_shapes(analyst)
    assert ("coMentionedWith", "Brand", "Brand") in shapes and ("forBrand", "SentimentAssertion", "Brand") in shapes
    assert ("sentimentScore", "SentimentAssertion", "value") in shapes
    assert not any(k[0] in ("groundedIn", "faissId") for k in shapes)                       # bookkeeping is not offered to the model
    schema = sparql_gen.plan_schema(OPDIVRA, shapes)
    text = json.dumps(schema)
    assert "Watson:coMentionedWith" in text and "Watson:Brand_opdivra" in text
    # the first edge must use the named entity. llama-server ignores `prefixItems` next to `items`, so it is a tuple per length
    assert "prefixItems" not in text
    tuples = schema["properties"]["edges"]["oneOf"]
    assert [len(t["items"]) for t in tuples] == list(range(1, sparql_gen.MAX_EDGES + 1))
    assert all(t["items"][0] == {"$ref": "#/$defs/first"} for t in tuples)
    assert "Watson:Brand_opdivra" in json.dumps(schema["$defs"]["first"])
    assert "oneOf" not in sparql_gen.plan_schema([], shapes)["properties"]["edges"]         # nothing to anchor on: no constraint


def test_semantic_check_catches_what_the_grammar_cannot(docs, l3_stores):
    g = user_graph.build(user("u_admin")).graph
    no_anchor = plan([col("?brand")], [pat("?brand", "Watson:company", "?company")])
    assert "no edge uses it" in sparql_gen.semantic_check(no_anchor, OPDIVRA)
    avg_text = plan([col("?brand"), col("?value", "avg", "?name")], [pat("?brand", "rdfs:label", "?name")])
    assert "needs a number" in sparql_gen.semantic_check(avg_text, None, sparql_gen.numeric_value_vars(avg_text, g))
    unused = plan([col("?indication")], [pat("?brand", "Watson:company", "?company")])
    assert "no edge uses it" in sparql_gen.semantic_check(unused)
    fine = plan([col("?brand")], [pat("?brand", "Watson:coMentionedWith", "Watson:Brand_opdivra")])
    assert sparql_gen.semantic_check(fine, OPDIVRA) is None
    unbound = dict(fine, filters=[{"var": "?score", "op": "=", "value": "?brand"}])
    assert "no edge uses it" in sparql_gen.semantic_check(unbound, OPDIVRA)
    iri = dict(plan([col("?name")], [pat("?brand", "rdfs:label", "?name")]), filters=[{"var": "?name", "op": "=", "value": "Watson:Brand_opdivra"}])
    assert "not a value" in sparql_gen.semantic_check(iri)


def test_tidy_removes_loops_and_renames_a_clashing_result(docs, l3_stores):
    p = plan([col("?brand"), col("?score", "avg", "?score")],
             [pat("?sentiment", "Watson:forBrand", "?brand")] * 3 + [pat("?sentiment", "Watson:sentimentScore", "?score")], order_by="?score")
    notes = sparql_gen.tidy(p)
    assert len(p["edges"]) == 2 and p["select"][1]["var"] == "?score_result" and p["order_by"] == "?score_result" and len(notes) == 2


def test_query_text_cannot_leave_the_machine_or_change_data(docs, l3_stores):
    for body in ("SELECT ?s WHERE { SERVICE <http://evil.example/sparql> { ?s ?p ?o } }",
                 "SELECT ?s FROM <http://evil.example/g> WHERE { ?s ?p ?o }",
                 "INSERT DATA { <a:b> <a:c> <a:d> }"):
        _, err = sparql_gen.check(body)
        assert err, body


def test_the_model_writes_a_different_query_for_a_different_question(docs, fake_llm, l3_stores):
    sl = user_graph.build(user("u_analyst"))
    fake_llm.plans = [plan([col("?brand")], [pat("?brand", "Watson:company", "?company")])]
    a = sparql_gen.generate("List the brands", [], sl.graph)
    fake_llm.plans = [plan([col("?brand"), col("?count", "count_distinct", "?document")],
                           [pat("?document", "Watson:mentionsBrand", "?brand")], order_by="?count", descending=True)]
    b = sparql_gen.generate("Which brand is mentioned in most documents?", [], sl.graph)
    assert a["status"] == b["status"] == "ok" and a["sparql"] != b["sparql"]
    assert "GROUP BY" in b["sparql"] and "COUNT(DISTINCT" in b["sparql"] and "DESC(" in b["sparql"]
    assert any(r.get("brand_label") for r in b["rows"])                                      # readable names are added to typed variables


def test_retry_feeds_the_error_back_and_gives_up_honestly(docs, fake_llm, l3_stores):
    sl = user_graph.build(user("u_analyst"))
    bad = plan([col("?brand")], [pat("?brand", "Watson:company", "?company")])             # ignores Opdivra
    good = plan([col("?brand")], [pat("?brand", "Watson:coMentionedWith", "Watson:Brand_opdivra")])
    fake_llm.plans = [bad, good]
    r = sparql_gen.generate("Which brands are co-mentioned with Opdivra?", OPDIVRA, sl.graph)
    assert r["status"] == "ok" and len(r["attempts"]) == 2 and "no edge uses it" in r["attempts"][0]["error"]
    assert "no edge uses it" in fake_llm.calls[1]["user"]                                    # the second prompt carried the reason
    fake_llm.plans, fake_llm.calls = ["not json at all"], []
    r = sparql_gen.generate("List the brands", [], sl.graph)
    assert r["status"] == "failed" and r["sparql"] is None and len(fake_llm.calls) == 3


def _tiny_graph():
    """Brand_a contains Drug_b and has no co-mentions: the Dupixa case in miniature."""
    from rdflib import Graph
    from rdflib.namespace import RDF
    g = Graph()
    g.add((W.Brand_a, RDF.type, W.Brand))
    g.add((W.Brand_c, RDF.type, W.Brand))
    g.add((W.Drug_b, RDF.type, W.Drug))
    g.add((W.Brand_a, W.containsDrug, W.Drug_b))
    g.add((W.Brand_c, W.coMentionedWith, W.Brand_c))
    return g


BRAND_A = [{"text": "Brand A", "entity_id": "Brand_a", "kind": "Brand", "label": "Brand A", "method": "exact", "confidence": 1.0}]
SHAPES = {("containsDrug", "Brand", "Drug"): 1, ("coMentionedWith", "Brand", "Brand"): 1}


def test_the_first_edge_only_uses_relations_the_entity_has_in_the_users_graph(docs, l3_stores):
    g = _tiny_graph()
    assert set(sparql_gen.entity_edges(g, "Brand_a")) == {("containsDrug", "out", "Drug")}
    first = json.dumps(sparql_gen.plan_schema(BRAND_A, SHAPES, g)["$defs"]["first"])
    assert "Watson:containsDrug" in first and "Watson:coMentionedWith" not in first          # co-mentions of Brand_a would return nothing
    anywhere = json.dumps(sparql_gen.plan_schema(BRAND_A, SHAPES)["$defs"]["first"])         # no graph given: the old, looser rule
    assert "Watson:coMentionedWith" in anywhere


def test_an_empty_result_is_explained_from_the_graph(docs, l3_stores):
    g = _tiny_graph()
    p = plan([col("?brand"), col("?drug")], [pat("?brand", "Watson:coMentionedWith", "Watson:Brand_a"), pat("?brand", "Watson:containsDrug", "?drug")])
    msg = sparql_gen.empty_feedback(p, BRAND_A, g)
    assert "?brand Watson:coMentionedWith Watson:Brand_a" in msg and "?brand Watson:containsDrug ?drug" not in msg.split("The relations")[0]
    assert "Watson:containsDrug (to a Drug)" in msg                                          # what does exist for the entity
    fine = plan([col("?drug")], [pat("Watson:Brand_a", "Watson:containsDrug", "?drug")])
    assert "on its own, but not all together" in sparql_gen.empty_feedback(fine, BRAND_A, g)


def test_each_retry_uses_a_new_seed(docs, fake_llm, l3_stores):
    sl = user_graph.build(user("u_analyst"))
    fake_llm.plans = ["not json at all"]
    sparql_gen.generate("List the brands", [], sl.graph)
    seeds = [c["seed"] for c in fake_llm.calls]
    assert len(seeds) == 3 and len(set(seeds)) == 3                                          # same seed + same prompt would repeat the same plan


def test_a_query_only_sees_the_users_slice(docs, fake_llm, l3_stores):
    """The same chunk query returns rows for the analyst and none for the viewer: the restricted document is not in the graph the query runs on."""
    open_doc, restricted = docs
    fake_llm.plans = [plan([col("?document")], [pat("?chunk", "Watson:isChunkOf", "?document")])]
    viewer = sparql_gen.generate("which documents?", [], user_graph.build(user("u_viewer")).graph)
    analyst = sparql_gen.generate("which documents?", [], user_graph.build(user("u_analyst")).graph)
    assert {r["document"] for r in viewer["rows"]} == {open_doc}
    assert {r["document"] for r in analyst["rows"]} == {open_doc, restricted}


# ---------------------------------------------------------------- step 8: the hallucination check
EVIDENCE = [{"id": "E1", "kind": "chunk", "text": "Overall HCP sentiment index for Opdivra in Germany: 0.69 (scale 0 to 1, assessed 2021).", "documents": ["Watson:Doc_001"], "chunks": []},
            {"id": "E2", "kind": "chunk", "text": "Prescribers in the United States rated tolerability highly.", "documents": ["Watson:Doc_002"], "chunks": []}]


def test_guard_keeps_supported_and_removes_invented_sentences():
    out = guard.verify(["HCP sentiment for Opdivra in Germany was 0.69. [E1]",
                        "HCP sentiment for Opdivra in Germany was 0.91. [E1]",                 # invented number
                        "Opdivra was rated highly in Italy. [E2]",                            # name not in the cited evidence
                        "Zorbex is the market leader."], EVIDENCE)                           # no citation, no support
    v = [s["verdict"] for s in out["sentences"]]
    assert v[0] == "supported" and v[1:] == ["unsupported"] * 3
    assert out["sentences"][1]["checks"]["numbers_in_evidence"] is False
    assert out["sentences"][2]["checks"]["names_in_evidence"] is False
    assert out["risk"] == "medium"


def test_an_uncited_sentence_is_kept_only_when_the_evidence_clearly_backs_it():
    out = guard.verify(["Opdivra HCP sentiment index in Germany: 0.69, assessed 2021."], EVIDENCE)
    s = out["sentences"][0]
    assert s["attributed"] and s["cites"] == ["E1"] and s["verdict"] == "supported"


def test_split_sentences_attaches_tags_to_their_sentence():
    assert guard.split_sentences("First fact here. [E1] Second fact here. [E2][E3]") == ["First fact here. [E1]", "Second fact here. [E2][E3]"]


# ---------------------------------------------------------------- the whole pipeline
def run(u, q, mode="semantic"):
    events = list(pipeline.ask(user(u), q, mode))
    return [e for e in events if e["step"] != "final"], events[-1]["result"]


def last(events, key):
    return [e for e in events if e["key"] == key][-1]          # the finished event, not the 'running' one before it


def good_plan():
    return plan([col("?brand")], [pat("?brand", "Watson:coMentionedWith", "Watson:Brand_opdivra")])


def test_pipeline_shows_every_step_in_order_and_cites_only_readable_sources(docs, fake_llm):
    open_doc, restricted = docs
    fake_llm.plans = [good_plan()]
    fake_llm.answer = quote_evidence
    events, res = run("u_viewer", "What does the survey say about Opdivra?")
    steps = [e["step"] for e in events if e["status"] != "running"]
    assert steps == [1, 2, 3, 4, 5, 6, 7, 8]
    assert [e["step"] for e in events if e["status"] == "running"] == [2, 4, 6, 7]
    assert res["status"] == "answered" and res["lines"] and all(l["cites"] for l in res["lines"])
    cited_docs = {d for s in res["sources"] for d in s["documents"]}
    assert cited_docs <= {open_doc} and restricted not in json.dumps(res)
    assert res["withheld_documents"] == 1


def test_a_viewer_cannot_get_restricted_content_even_by_asking_for_it(docs, fake_llm):
    open_doc, restricted = docs
    restricted_titles = None
    fake_llm.plans = [plan([col("?document")], [pat("?chunk", "Watson:isChunkOf", "?document")])]
    fake_llm.answer = quote_evidence
    events, res = run("u_viewer", "List every document including restricted ones")
    evidence = last(events, "evidence")["data"]["items"]
    assert all(restricted not in it["documents"] for it in evidence)
    assert restricted not in json.dumps([e["data"] for e in events])


def test_telemetry_records_what_the_model_was_given_so_q10_audits_layer_3(docs, fake_llm):
    fake_llm.plans = [good_plan()]
    fake_llm.answer = quote_evidence
    before = len(telemetry.events())
    run("u_viewer", "What does the survey say about Opdivra?")
    new = telemetry.events()[before:]
    assert new and {e["user_id"] for e in new} == {"u_viewer"} and {e["document_uri"] for e in new} <= {docs[0]}
    assert any(e["event_type"] == "QUERY" and e["details"].get("stage") == "layer3_answer" for e in audit_log.read_events())


def test_invented_answer_is_removed_and_logged(docs, fake_llm):
    fake_llm.plans = [good_plan()]
    fake_llm.answer = lambda u: quote_evidence(u) + "\nOpdivra sentiment in Italy was 0.99 in 2031. [E1]"
    _, res = run("u_viewer", "What does the survey say about Opdivra?")
    assert any("0.99" in x["text"] for x in res["removed"])
    assert not any("0.99" in l["text"] for l in res["lines"])
    assert any(e["event_type"] == "HALLUCINATION_RISK" for e in audit_log.read_events())


def test_when_nothing_is_supported_no_answer_is_shown(docs, fake_llm):
    fake_llm.plans = [good_plan()]
    fake_llm.answer = "Zorbex tablets cure everything in Atlantis."
    _, res = run("u_viewer", "What does the survey say about Opdivra?")
    assert res["status"] == "no_supported_answer" and res["short_answer"] is None and res["lines"] == [] and res["sources"] == []


def test_if_the_model_is_down_the_pipeline_still_answers_from_evidence_and_says_so(docs, fake_llm):
    fake_llm.down = True
    events, res = run("u_viewer", "What does the survey say about Opdivra?")
    sp = last(events, "sparql")
    assert sp["data"]["status"] == "failed" and res["answer_mode"] == "extractive"
    assert "not reachable" in res["message"] and res["lines"] and all(l["cites"] for l in res["lines"])


def test_plain_mode_never_touches_the_graph(docs, fake_llm):
    fake_llm.answer = quote_evidence
    events, res = run("u_viewer", "What does the survey say about Opdivra?", mode="plain")
    assert {e["key"]: e["status"] for e in events}["sparql"] == "skipped" and res["sparql"] is None
    assert not any(c["schema"] for c in fake_llm.calls)                  # no plan was ever requested


def test_knowledge_space_questions_are_answered_from_live_facts(docs, fake_llm):
    fake_llm.plans = [plan([col("?name"), col("?count", "count_distinct", "?document")],
                           [pat("?space", "Watson:spaceName", "?name"), pat("?space", "Watson:containsDocument", "?document")],
                           order_by="?count", descending=True)]
    fake_llm.answer = quote_evidence
    events, res = run("u_viewer", "Which knowledge spaces can I see and how many documents do they hold?")
    assert res["route"] == "knowledge_space"
    ev = last(events, "evidence")["data"]["items"]
    assert any(i["kind"] == "space" for i in ev)                         # read live from the Layer 2 view, not typed in


def test_empty_and_oversized_questions_are_refused():
    with pytest.raises(ValueError):
        next(pipeline.ask(user("u_viewer"), "  "))
    with pytest.raises(ValueError):
        next(pipeline.ask(user("u_viewer"), "x" * 601))
    with pytest.raises(ValueError):
        next(pipeline.ask(user("u_viewer"), "ok", mode="magic"))


# ---------------------------------------------------------------- HTTP
@pytest.fixture()
def client(world, fake_llm):
    return TestClient(api_main.app)


def login(client, uid):
    return {"Authorization": "Bearer " + client.post("/api/login", json={"user_id": uid}).json()["token"]}


def test_ask_streams_steps_then_the_answer(client, docs, fake_llm):
    fake_llm.plans = [good_plan()]
    fake_llm.answer = quote_evidence
    r = client.post("/api/l3/ask", json={"question": "What does the survey say about Opdivra?"}, headers=login(client, "u_viewer"))
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/x-ndjson")
    events = [json.loads(x) for x in r.text.splitlines()]
    assert [e["step"] for e in events if e["step"] != "final" and e["status"] != "running"] == [1, 2, 3, 4, 5, 6, 7, 8]
    assert events[-1]["step"] == "final" and events[-1]["result"]["status"] == "answered"


def test_ask_needs_a_session_and_a_question_and_there_is_no_model_switch(client):
    assert client.post("/api/l3/ask", json={"question": "hi"}).status_code == 401
    assert client.post("/api/l3/ask", json={"question": "  "}, headers=login(client, "u_viewer")).status_code == 422
    assert client.post("/api/l3/model", json={"model": "x"}, headers=login(client, "u_admin")).status_code in (404, 405)
    assert client.get("/api/l3/history", headers=login(client, "u_viewer")).status_code == 200


def test_history_is_private_except_for_admin(client, docs, fake_llm):
    fake_llm.plans = [good_plan()]
    fake_llm.answer = quote_evidence
    client.post("/api/l3/ask", json={"question": "What does the survey say about Opdivra?"}, headers=login(client, "u_viewer"))
    assert len(client.get("/api/l3/history", headers=login(client, "u_viewer")).json()["runs"]) == 1
    assert client.get("/api/l3/history", headers=login(client, "u_analyst")).json()["runs"] == []
    assert len(client.get("/api/l3/history", headers=login(client, "u_admin")).json()["runs"]) == 1


def test_disconnected_edges_are_refused_and_a_runaway_query_is_really_stopped(docs, l3_stores, monkeypatch):
    split = plan([col("?brand")], [pat("?brand", "Watson:company", "?company"), pat("?document", "Watson:study_type", "?type")])
    assert "not all joined" in sparql_gen.semantic_check(split)
    g = user_graph.build(user("u_admin")).graph
    monkeypatch.setattr(sparql_gen, "EXEC_TIMEOUT_S", 0.0)                    # a deadline that has already passed
    res = sparql_gen.execute(g, sparql_gen.with_prefixes("SELECT ?a ?b WHERE { ?a ?p ?o . ?b ?q ?r }"))
    assert "stopped" in res["error"]


def test_graph_rows_become_plain_statements_from_the_plans_own_edges(docs, fake_llm):
    from layer3 import evidence
    sl = user_graph.build(user("u_admin"))
    p = plan([col("?brand")], [pat("?brand", "Watson:coMentionedWith", "Watson:Brand_opdivra")])
    row = {"brand": "Watson:Brand_trodelvia", "brand_label": "Trodelvia"}
    assert evidence.sentence_for(sl, p, row) == "Trodelvia co-mentioned with Opdivra"
    agg = plan([col("?brand"), col("?count", "count_distinct", "?document")], [pat("?document", "Watson:mentionsBrand", "?brand")])
    assert "documents: 4" in evidence.sentence_for(sl, agg, {"brand": "Watson:Brand_opdivra", "brand_label": "Opdivra", "count": 4})
    # the query selected only the drug's name: the statement must still say whose drug it is (it used to be the bare value)
    by_name = plan([col("?name")], [pat("Watson:Brand_opdivra", "Watson:containsDrug", "?drug"), pat("?drug", "rdfs:label", "?name")])
    assert evidence.sentence_for(sl, by_name, {"name": "dupilumib"}) == "Opdivra contains drug dupilumib"


def test_a_supported_short_answer_alone_is_an_answer(docs, fake_llm):
    """Single-fact questions: the model often writes only the SHORT line. That used to end as 'no supported answer'."""
    fake_llm.plans = [good_plan()]
    fake_llm.answer = lambda u: quote_evidence(u).split("\n")[0]                 # the SHORT line only, no body lines
    _, res = run("u_viewer", "What does the survey say about Opdivra?")
    assert res["lines"] == [] and res["short_answer"]
    assert res["status"] == "answered" and res["sources"]                         # and its source is still listed


def test_the_one_line_answers_sources_are_listed_too(docs, fake_llm):
    fake_llm.plans = [good_plan()]
    fake_llm.answer = lambda u: quote_evidence(u).replace("[E1]", "[E1]")      # short line cites E1; so must the sources list
    _, res = run("u_viewer", "What does the survey say about Opdivra?")
    assert res["short_answer"] and set(res["short_answer"]["cites"]) <= {s["id"] for s in res["sources"]}


def test_llm_client_reports_an_unreachable_server_with_the_start_command(monkeypatch):
    from layer3 import config, llm
    monkeypatch.setattr(config, "LLM_URL", "http://127.0.0.1:9")                 # nothing listens on the discard port
    s = llm.status()
    assert s["available"] is False and "start_llm_server.ps1" in s["message"]
    with pytest.raises(llm.LLMUnavailable):
        llm.chat("s", "u")

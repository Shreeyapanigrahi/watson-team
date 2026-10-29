"""Layer 3: access slice, question understanding, plan checking, hallucination check, the whole pipeline, and the HTTP stream."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from layer1 import abac_engine as abac, audit_log, auth
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


def test_tidy_fills_an_empty_of_with_the_counted_variable(docs, l3_stores):
    p = plan([col("?document", "count_distinct", "")], [pat("?document", "Watson:mentionsBrand", "Watson:Brand_opdivra")])
    sparql_gen.tidy(p)
    assert p["select"][0] == {"var": "?document_result", "agg": "count_distinct", "of": "?document"}
    assert sparql_gen.semantic_check(p, OPDIVRA) is None
    body, err = sparql_gen.render(p)
    assert err is None and "COUNT(DISTINCT ?document) AS ?document_result" in body


# ---------------------------------------------------------------- how-many questions: one number, counted over what the user may read
COUNT_SHAPES = {("mentionsDrug", "MarketResearchDocument", "Drug"): 1, ("mentionsDrug", "DocumentChunk", "Drug"): 1,
                ("mentionsBrand", "MarketResearchDocument", "Brand"): 1, ("containsDrug", "Brand", "Drug"): 1,
                ("forBrand", "SentimentAssertion", "Brand"): 1}


@pytest.mark.parametrize("question,phrase", [
    ("how many drugs are there in our knowledge space", "drugs"),
    ("How many geographical areas are mentioned in knowledge space ?", "geographical areas"),
    ("What is the number of different countries?", "countries"),
    ("how many documents mention Opdivra", "documents mention opdivra"),
    ("How many indications are targeted by Opdivra?", "indications"),           # 'by' an agent is not a breakdown
    ("number of countries covered by the knowledge space", "countries"),
    ("how many number of documents are associated with US ?", "documents"),     # a doubled cue: the thing counted is what follows 'number of'
    ("How many total number of brands are there?", "brands"),
    ("how many count of drugs do we have", "drugs"),
    ("how many numbers are there in the report", "numbers"),                    # 'numbers' is a noun here, not the cue
    ("How many documents are there by study type?", None),                    # 'by' a group is
    ("How many documents are there for each study type?", None),               # a breakdown, not one number
    ("Which brand has the most documents?", None),                              # a ranking
    ("What do doctors think about Opdivra?", None)])
def test_a_how_many_question_is_told_from_its_wording_only(question, phrase):
    got = sparql_gen.count_intent(question)
    assert (got["phrase"] if got else None) == phrase


ROUTE_SHAPES = {("coversGeography", "DocumentChunk", "Geography"): 1, ("coversGeography", "MarketResearchDocument", "Geography"): 1,
                ("isChunkOf", "DocumentChunk", "MarketResearchDocument"): 1}
ROUTE_INTENT = {"phrase": "documents", "rest": "are associated with it"}


def route_graph():
    """Three documents with one passage each, and entities linked in every way a document can be linked to one."""
    from rdflib import Graph, RDF
    W, g = sparql_gen.W, Graph()
    for i in (1, 2, 3):
        g.add((W[f"D{i}"], RDF.type, W["MarketResearchDocument"]))
        g.add((W[f"C{i}"], RDF.type, W["DocumentChunk"]))
        g.add((W[f"C{i}"], W["isChunkOf"], W[f"D{i}"]))
    for subject, entity in [("C1", "Geo_P"), ("C2", "Geo_P"),                                   # through passages only
                            ("D1", "Geo_D"), ("D2", "Geo_D"), ("C1", "Geo_D"),                  # direct links reach more documents
                            ("D1", "Geo_T"), ("C1", "Geo_T"),                                   # both levels, the same document
                            ("D1", "Geo_N"), ("C2", "Geo_N")]:                                  # each level reaches a document the other does not
        g.add((W[subject], W["coversGeography"], W[entity]))
    return g


LINKS = {"Geo_P": {("coversGeography", "in", "DocumentChunk"): 2}, "Geo_Z": {("partOfRegion", "out", "Geography"): 1},
         "Geo_D": {("coversGeography", "in", "MarketResearchDocument"): 2, ("coversGeography", "in", "DocumentChunk"): 1},
         "Geo_T": {("coversGeography", "in", "MarketResearchDocument"): 1, ("coversGeography", "in", "DocumentChunk"): 1},
         "Geo_N": {("coversGeography", "in", "MarketResearchDocument"): 1, ("coversGeography", "in", "DocumentChunk"): 1}}


def route(monkeypatch, entity_id="Geo_P", entities=None, intent=None, shapes=None):
    ents = entities or [{"entity_id": entity_id, "kind": "Geography", "text": entity_id}]
    monkeypatch.setattr(sparql_gen, "entity_edges", lambda g, eid: LINKS.get(eid, {}))
    first, then = sparql_gen.entity_document_route(intent or ROUTE_INTENT, ents, shapes or ROUTE_SHAPES, route_graph())
    edge = lambda v: (v["properties"]["s"]["enum"], v["properties"]["p"]["const"], v["properties"]["o"]["enum"])
    return [edge(v) for v in first], [edge(v) for v in then]


def test_an_entity_linked_only_through_passages_is_counted_through_a_passage_of_each_document(monkeypatch):
    """Which links exist is read from the user's graph, so this holds for any entity of any kind, not for one region."""
    assert route(monkeypatch, "Geo_P") == ([(["?chunk"], "Watson:coversGeography", ["Watson:Geo_P"])], [(["?chunk"], "Watson:isChunkOf", ["?document"])])


def test_the_level_that_reaches_every_document_is_chosen_per_entity(monkeypatch):
    doc = ([(["?document"], "Watson:coversGeography", ["Watson:Geo_D"])], [])
    assert route(monkeypatch, "Geo_D") == doc                        # 2 documents directly, 1 through passages: the direct level is complete
    assert route(monkeypatch, "Geo_T")[1] == []                      # equal: the plain document edge, no bridge
    assert route(monkeypatch, "Geo_N") == ([], [])                   # neither level contains the other: the ordinary plan decides


def test_an_entity_nothing_readable_links_to_is_counted_so_the_answer_is_zero_not_a_failure(monkeypatch):
    first, then = route(monkeypatch, "Geo_Z")                        # it exists, but no document or passage links to it
    assert first == [(["?document"], "Watson:coversGeography", ["Watson:Geo_Z"])] and then == []
    no_documents = {k: v for k, v in ROUTE_SHAPES.items() if k[:2] != ("coversGeography", "MarketResearchDocument")}
    assert route(monkeypatch, "Geo_Z", shapes=no_documents)[0][0][0] == ["?chunk"]               # only a passage-level relation exists in this graph


def test_the_ordinary_plan_is_kept_when_two_entities_are_named_or_the_question_asks_for_more(monkeypatch):
    two = [{"entity_id": "Geo_P", "kind": "Geography", "text": "P"}, {"entity_id": "Geo_D", "kind": "Geography", "text": "D"}]
    assert route(monkeypatch, entities=two) == ([], [])
    assert route(monkeypatch, "Geo_P", intent={"phrase": "documents", "rest": "mention which brands in it"}) == ([], [])
    assert route(monkeypatch, "Geo_P", intent={"phrase": "documents", "rest": "mention which drug in geo p"},
                 entities=[{"entity_id": "Geo_P", "kind": "Geography", "text": "geo p"}]) == ([], [])
    named = [{"entity_id": "Geo_P", "kind": "Geography", "text": "HER2 antibody-drug"}]           # 'drug' inside the entity's own name is not a second kind
    assert route(monkeypatch, entities=named, intent={"phrase": "documents", "rest": "are associated with her2 antibody drug"})[0]


def test_a_gap_in_the_very_relation_a_route_counts_through_is_a_zero_not_no_relation(monkeypatch):
    import layer3.sparql_gen as sg
    ents = [{"entity_id": "Geo_P", "kind": "Geography", "text": "p", "label": "P"}]
    monkeypatch.setattr(sg, "entity_edges", lambda g, eid: LINKS.get(eid, {}))
    cs = sg.count_setup("how many number of documents are associated with p", ents, ROUTE_SHAPES, route_graph())
    assert cs["route"] == {"coversGeography", "isChunkOf"}
    assert not ({"competesWithin"} <= cs["route"])                    # another relation stays a gap: the message is still shown


def test_a_count_through_a_passage_has_fixed_edges_and_no_filters():
    first, then = [{"properties": {"s": {"enum": ["?chunk"]}, "p": {"const": "Watson:coversGeography"}, "o": {"enum": ["Watson:Geo_X"]}}}], [
        {"properties": {"p": {"const": "Watson:isChunkOf"}}}]
    count = {"kind": "MarketResearchDocument", "of": ["?document"], "drop": set(), "skip": set(), "only": first, "then": then, "mentioned": set()}
    schema = sparql_gen.plan_schema([], ROUTE_SHAPES, None, None, None, count)
    assert len(schema["properties"]["edges"]["oneOf"][0]["items"]) == 2                          # the entity edge, then the bridge
    assert schema["properties"]["filters"]["maxItems"] == 0
    assert sparql_gen.plan_schema([], ROUTE_SHAPES, None, None, None, None)["properties"]["filters"]["maxItems"] == 3


def test_the_counted_kind_comes_from_the_ontology_words_then_the_glossary_by_meaning():
    kind = lambda p: sparql_gen.counted_class(p, [])
    assert (kind("drugs"), kind("drug classes"), kind("countries")) == ("Drug", "DrugClass", "Geography")
    assert kind("geographical areas") == "Geography"                             # no kind word in it: by meaning
    assert kind("specialties") == "TherapeuticArea"                              # the plural of a glossary phrase ('specialty')
    assert kind("classes") is None and kind("tables") is None                    # platform questions are not counts over the graph


def test_a_bare_count_is_one_edge_from_the_documents_side_to_the_counted_kind(docs, l3_stores):
    q = "how many drugs are there in our knowledge space"
    cs = sparql_gen.count_setup(q, [], COUNT_SHAPES)
    assert cs["kind"] == "Drug" and cs["of"] == ["?drug"] and cs["drop"] == {"SentimentAssertion", "KnowledgeSpace"}
    assert cs["skip"] == {("mentionsDrug", "MarketResearchDocument", "Drug")}        # the passage-level relation is the complete one
    assert [v["properties"]["s"]["enum"] for v in cs["only"]] == [["?chunk"]]
    schema = sparql_gen.plan_schema([], COUNT_SHAPES, None, set(), {"Drug"}, cs)
    assert schema["properties"]["select"]["oneOf"][0]["items"][0]["properties"]["agg"] == {"enum": ["count_distinct"]}
    assert len(schema["properties"]["edges"]["oneOf"]) == 1 and "?space" not in json.dumps(schema["$defs"]["first"])
    # anything more than the bare count keeps the free plan: an entity, a number, another relation or another kind
    assert not sparql_gen.count_setup("how many drugs does Opdivra mention", OPDIVRA, COUNT_SHAPES)["only"]
    assert not sparql_gen.count_setup("how many drugs have a score above 5", [], COUNT_SHAPES)["only"]
    assert not sparql_gen.count_setup("how many drugs are in brands", [], COUNT_SHAPES)["only"]
    assert sparql_gen.count_setup("how many classes does the ontology have", [], COUNT_SHAPES) is None
    assert sparql_gen.count_setup("how many drug classes are there", [], {**COUNT_SHAPES, ("mentionsDrugClass", "DocumentChunk", "DrugClass"): 1})["only"]
    assert sparql_gen.count_setup("how many drugs are mentioned in our knowledge space", [], COUNT_SHAPES)["only"]     # the relation word IS the edge used
    # 'drug classes' is itself a kind word and a relation phrase; it must not make its own count look qualified


def test_a_count_plan_that_would_count_the_wrong_thing_is_sent_back_with_the_reason(docs, l3_stores):
    cs = sparql_gen.count_setup("how many drugs are there", [], COUNT_SHAPES)
    check = lambda p: sparql_gen.semantic_check(p, [], None, cs)
    good = plan([col("?count", "count_distinct", "?drug")], [pat("?chunk", "Watson:mentionsDrug", "?drug")])
    assert check(good) is None
    assert "one number" in check(plan([col("?drug"), col("?count", "count_distinct", "?drug")], good["edges"]))
    assert "Set 'of'" in check(plan([col("?count", "count_distinct", "?brand")], [pat("?chunk", "Watson:mentionsBrand", "?brand")]))
    assert "Use ?chunk" in check(plan([col("?count", "count_distinct", "?drug")], [pat("?document", "Watson:mentionsDrug", "?drug")]))
    assert "not needed" in check(plan([col("?count", "count_distinct", "?drug")],
                                      [pat("?chunk", "Watson:mentionsDrug", "?drug"), pat("?sentiment", "Watson:forBrand", "?brand"),
                                       pat("?brand", "Watson:containsDrug", "?drug")]))
    assert "join ?drug" in check(plan([col("?count", "count_distinct", "?drug")],
                                      [pat("?brand", "Watson:containsDrug", "?drug"), pat("?brand", "Watson:company", "?company")]))
    assert "one edge only" in check(plan([col("?count", "count_distinct", "?drug")], [pat("?brand", "Watson:containsDrug", "?drug")]))


def test_a_count_plan_may_not_add_a_requirement_the_question_did_not_make(docs, l3_stores):
    """'documents that mention Opdivra' with a second edge to ?drug counts only documents that also mention a drug (8 became 4 on the
    real model). A variable in one edge is a requirement; it is allowed only for a kind the question mentions."""
    cs = sparql_gen.count_setup("how many documents mention Opdivra", OPDIVRA, COUNT_SHAPES)
    check = lambda p: sparql_gen.semantic_check(p, OPDIVRA, None, cs)
    one = pat("?document", "Watson:mentionsBrand", "Watson:Brand_opdivra")
    count = [col("?count", "count_distinct", "?document")]
    assert check(plan(count, [one])) is None
    assert "one edge only" in check(plan(count, [one, pat("?document", "Watson:mentionsDrug", "?drug")]))
    asked = sparql_gen.count_setup("how many documents mention Opdivra and which drugs", OPDIVRA, COUNT_SHAPES)
    assert sparql_gen.semantic_check(plan(count, [one, pat("?document", "Watson:mentionsDrug", "?drug")]), OPDIVRA, None, asked) is None


def test_documents_and_spaces_are_counted_as_they_are_not_through_what_they_mention(docs, l3_stores):
    shapes = {**COUNT_SHAPES, ("spaceName", "KnowledgeSpace", "value"): 1}
    sl = user_graph.build(user("u_admin"))
    cs = sparql_gen.count_setup("how many knowledge spaces are there", [], shapes, sl.graph)
    assert cs["kind"] == "KnowledgeSpace" and cs["only"]                          # edges every space has, found by counting in the graph
    assert all(v["properties"]["s"]["enum"] == ["?space"] for v in cs["only"])
    assert sparql_gen.semantic_check(plan([col("?count", "count_distinct", "?space")], [pat("?space", "rdfs:label", "?name")]), [], None, cs) is None


def test_tidy_drops_the_ordering_of_a_single_number(docs, l3_stores):
    p = plan([col("?count", "count_distinct", "?drug")], [pat("?chunk", "Watson:mentionsDrug", "?drug")], order_by="?count", descending=True, limit=5)
    assert sparql_gen.tidy(p, single=True) and (p["order_by"], p["descending"], p["limit"]) == ("", False, 0)


def test_the_count_follows_the_users_access_and_is_one_row(docs, fake_llm, l3_stores):
    question = "how many brands are there in our knowledge space"
    fake_llm.plans = [plan([col("?count", "count_distinct", "?brand")], [pat("?chunk", "Watson:mentionsBrand", "?brand")])]
    seen = {}
    for uid in ("u_viewer", "u_admin"):
        sl = user_graph.build(user(uid))
        res = sparql_gen.generate(question, [], sl.graph)
        independent = {o for c in sl.graph.subjects(None, W.DocumentChunk) for o in sl.graph.objects(c, W.mentionsBrand)}
        assert res["status"] == "ok" and res["row_count"] == 1 and res["counted"] == "Brand"
        assert int(res["rows"][0]["count"]) == len(independent) > 0
        seen[uid] = len(independent)
    assert seen["u_viewer"] <= seen["u_admin"]                                    # the restricted document adds brands for the admin only or changes nothing


def test_a_single_number_is_worded_with_what_it_counts_and_over_what(docs, l3_stores):
    sl = user_graph.build(user("u_admin"))
    brands = plan([col("?count", "count_distinct", "?brand")], [pat("?chunk", "Watson:mentionsBrand", "?brand")])
    from layer3 import evidence
    assert evidence.sentence_for(sl, brands, {"count": 12}) == "Number of distinct brands in the documents you may read: 12"
    docs_about = plan([col("?count", "count_distinct", "?document")], [pat("?document", "Watson:mentionsBrand", "Watson:Brand_opdivra")])
    assert evidence.sentence_for(sl, docs_about, {"count": 3}) == "Number of distinct market research documents (mentions brand Opdivra): 3"      # the ontology's own label for the class
    drugs_of = plan([col("?count", "count_distinct", "?drug")], [pat("Watson:Brand_opdivra", "Watson:containsDrug", "?drug")])
    assert evidence.sentence_for(sl, drugs_of, {"count": 1}) == "Number of distinct drugs (Opdivra contains drug): 1"


def test_when_the_graph_answers_a_count_the_space_and_catalog_sentences_are_left_out(docs, l3_stores):
    from layer3 import evidence
    u = user("u_admin")
    sl = user_graph.build(u)
    counted = {"status": "ok", "counted": "Brand", "plan": plan([col("?count", "count_distinct", "?brand")], [pat("?chunk", "Watson:mentionsBrand", "?brand")]),
               "columns": ["count"], "rows": [{"count": 2}]}
    kinds = lambda r: {i["kind"] for i in evidence.gather(u, sl, "how many brands are in our knowledge space", [], "knowledge_space", r, "semantic")["items"]}
    assert kinds(counted) == {"graph"}
    assert {"space", "catalog"} <= kinds(dict(counted, counted=None))              # not a count over the graph's things: they stay


def test_a_counted_answer_gets_no_passages_on_any_route(docs, l3_stores):
    """'how many total documents are ingested' was routed semantic_first, so five unrelated passages sat next to the correct count and the
    model said there was no answer. With a number from the graph, the passages are left out whatever the route; without one they stay."""
    from layer3 import evidence
    u = user("u_admin")
    sl = user_graph.build(u)
    counted = {"status": "ok", "counted": "MarketResearchDocument", "columns": ["count"], "rows": [{"count": 2}],
               "plan": plan([col("?count", "count_distinct", "?document")], [pat("?document", "Watson:documentTitle", "?title")])}
    kinds = lambda r, route: {i["kind"] for i in evidence.gather(u, sl, "how many total documents are ingested", [], route, r, "semantic")["items"]}
    for route in ("semantic_first", "graph_first", "knowledge_space"):
        assert kinds(counted, route) == {"graph"}, route
    assert "chunk" in kinds(dict(counted, counted=None), "semantic_first")                # an ordinary graph answer keeps its passages
    assert "chunk" in kinds({"status": "failed", "rows": []}, "semantic_first")             # and a failed query falls back to passages


def test_the_answer_to_a_count_states_the_number_even_if_the_model_does_not(docs, fake_llm, l3_stores):
    """The model said 'no answer' beside a correct count, and once described the line without its number. The retry says the line is the
    database's count; if the reply still lacks the number, the evidence line is quoted and the note says so."""
    from layer3 import answer as answer_mod
    ev = [{"id": "E1", "kind": "graph", "text": "Number of distinct market research documents: 43", "row": {"count": 43}}]
    fake_llm.answer = "The evidence does not contain an answer."
    d = answer_mod.generate("how many total documents are ingested", ev)
    assert "database's count for exactly this question" in fake_llm.calls[1]["user"] and "database's count" not in fake_llm.calls[0]["user"]
    assert d["retried"] and d["short"] == "Number of distinct market research documents: 43 [E1]" and "quoted" in d["note"]
    assert d["sentences"] and "43" in d["sentences"][0] and not d["refused"]
    fake_llm.answer = "SHORT: There are 43 documents. [E1]"                                  # a model that states the number is left alone
    d = answer_mod.generate("how many total documents are ingested", ev)
    assert d["short"] == "There are 43 documents. [E1]" and "note" not in d and not d["retried"]
    fake_llm.answer = "SHORT: 143 documents. [E1]"                                           # 143 is not 43
    assert "quoted" in answer_mod.generate("how many total documents are ingested", ev)["note"]
    for bare in ("43 [E1]", "SHORT: 43\n43 [E1]"):                                           # the number alone: the checker would drop it and no answer would be left
        fake_llm.answer = bare
        d = answer_mod.generate("how many total documents are ingested", ev)
        assert "quoted" in d["note"] and "documents" in d["sentences"][0] and "43" in d["sentences"][0], bare


def test_a_leading_number_is_part_of_the_sentence_but_list_markers_are_removed():
    from layer3 import answer as answer_mod
    assert answer_mod.parse("15 [E1]")["sentences"] == ["15 [E1]"]
    assert answer_mod.parse("2023 sales rose by 4%. [E2]")["sentences"] == ["2023 sales rose by 4%. [E2]"]
    assert answer_mod.parse("1. Opdivra leads. [E1]\n- Keytrava follows. [E2]\n2) Tagrisa trails. [E3]")["sentences"] == [
        "Opdivra leads. [E1]", "Keytrava follows. [E2]", "Tagrisa trails. [E3]"]


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


def test_a_relation_named_in_the_question_is_used_for_the_first_edge(docs, l3_stores):
    from rdflib.namespace import RDF
    names = sparql_gen.named_relations
    assert names("which drug are in relationship of containsDrug with Dupixa?", SHAPES) == {"containsDrug"}
    assert names("Which drug does Dupixa contain? (contains drug)", SHAPES) == {"containsDrug"}
    assert names("Which brands are co-mentioned with Opdivra?", SHAPES) == {"coMentionedWith"}
    assert names("Dupixa brand contains which drug ?", SHAPES) == {"containsDrug"}           # a wh-word between the relation's words
    assert names("Does Dupixa contain a very large number of any drug?", SHAPES) == set()   # too many words between: not the relation
    assert names("Tell me about Dupixa", SHAPES) == set()                                    # nothing named: nothing forced
    g = _tiny_graph()
    g.add((W.Brand_a, W.coMentionedWith, W.Brand_c))                                        # now both relations have data for Brand_a
    both = json.dumps(sparql_gen.plan_schema(BRAND_A, SHAPES, g)["$defs"]["first"])
    assert "Watson:containsDrug" in both and "Watson:coMentionedWith" in both               # without the question's hint, either is allowed
    forced = json.dumps(sparql_gen.plan_schema(BRAND_A, SHAPES, g, {"containsDrug"})["$defs"]["first"])
    assert "Watson:containsDrug" in forced and "Watson:coMentionedWith" not in forced
    unknown = json.dumps(sparql_gen.plan_schema(BRAND_A, SHAPES, g, {"company"})["$defs"]["first"])
    assert "Watson:containsDrug" in unknown                                                  # a named relation the entity lacks changes nothing


def test_the_first_edge_leads_to_the_kind_of_thing_the_question_asks_for(docs, l3_stores):
    dupixa = [{"text": "Dupixa", "entity_id": "Brand_a", "kind": "Brand", "label": "Dupixa"}]
    kinds = sparql_gen.answer_kinds
    assert kinds("Dupixa brand contains which drug ?", dupixa) == {"Drug"}                    # 'brand' sits next to the name: it describes Dupixa
    assert kinds("What drug does Dupixa have?", dupixa) == {"Drug"}
    assert kinds("Name the drug of the Dupixa brand", dupixa) == {"Drug"}
    assert kinds("Which drug class does dupilumib belong to?", []) == {"DrugClass"}          # the longer phrase wins over 'drug'
    assert kinds("Which brands are co-mentioned with Opdivra?", [{"text": "Opdivra", "entity_id": "Brand_o", "kind": "Brand", "label": "Opdivra"}]) == {"Brand"}
    assert kinds("Tell me about Dupixa", dupixa) == set()
    g = _tiny_graph()
    g.add((W.Brand_a, W.coMentionedWith, W.Brand_c))                                        # both a Brand and a Drug are reachable from Brand_a
    drug_first = json.dumps(sparql_gen.plan_schema(BRAND_A, SHAPES, g, None, {"Drug"})["$defs"]["first"])
    assert "Watson:containsDrug" in drug_first and "Watson:coMentionedWith" not in drug_first and "rdfs:label" not in drug_first
    brand_first = json.dumps(sparql_gen.plan_schema(BRAND_A, SHAPES, g, None, {"Brand"})["$defs"]["first"])
    assert "Watson:coMentionedWith" in brand_first and "Watson:containsDrug" not in brand_first
    none_reachable = json.dumps(sparql_gen.plan_schema(BRAND_A, SHAPES, g, None, {"Indication"})["$defs"]["first"])
    assert "Watson:containsDrug" in none_reachable and "Watson:coMentionedWith" in none_reachable   # nothing leads there: the choice stays open


def test_a_named_relation_the_entity_lacks_is_reported_not_replaced(docs, fake_llm, l3_stores):
    g = _tiny_graph()                                                  # Brand_a has containsDrug only; competesWithin has no triple at all here
    shapes = {**SHAPES, ("forGeography", "SentimentAssertion", "Geography"): 1}
    gap = sparql_gen.relation_gap(BRAND_A, shapes, g, {"competesWithin"})                    # not in the graph: the ontology says Brand -> Brand
    assert gap and gap["relations"] == ["competesWithin"] and "competes within" in gap["reason"]
    assert "What it does have: contains drug." in gap["reason"] and "Watson:" not in gap["reason"]          # in plain words, what the brand does have
    assert sparql_gen.relation_gap(BRAND_A, shapes, g, {"containsDrug"}) is None             # it has this one
    assert sparql_gen.relation_gap(BRAND_A, shapes, g, {"competesWithin", "containsDrug"}) is None      # one available relation is enough
    assert sparql_gen.relation_gap(BRAND_A, shapes, g, {"forGeography"}) is None             # not touching a brand: may be reached in two hops
    assert sparql_gen.relation_gap([], shapes, g, {"competesWithin"}) is None                # no entity named: nothing to be missing from
    terms = {"available": True, "relations_selected": ["competesWithin"], "kinds_selected": ["Brand"]}
    r = sparql_gen.generate("Who are the competitors of Brand A?", BRAND_A, g, terms=terms)
    assert r["status"] == "no_relation" and r["sparql"] is None and r["missing"] == ["competesWithin"]
    assert fake_llm.calls == []                                                              # the model was not asked to invent another question


def test_the_answer_says_when_the_graph_has_no_such_relation(docs, fake_llm, monkeypatch, l3_stores):
    reason = "The part of the graph you may read has no 'competes within' relation for Opdivra, so there is nothing to query for it."
    monkeypatch.setattr(sparql_gen, "generate", lambda *a, **k: {"status": "no_relation", "sparql": None, "columns": [], "rows": [], "attempts": [],
                                                                  "prompt": None, "reason": reason, "missing": ["competesWithin"]})
    fake_llm.answer = quote_evidence
    events, res = run("u_analyst", "Who are the competitors of Opdivra?")
    step = last(events, "sparql")
    assert step["status"] == "warn" and step["data"]["reason"] == reason and step["data"]["status"] == "no_relation"
    assert reason in res["message"] and "No graph facts were used" in res["message"]
    assert res["sparql"]["status"] == "no_relation"


def test_rows_carry_the_brand_they_were_reached_through(docs, l3_stores):
    two_hops = plan([col("?drug")], [pat("?brand", "Watson:coMentionedWith", "Watson:Brand_opdivra"), pat("?brand", "Watson:containsDrug", "?drug")])
    body, err = sparql_gen.render(two_hops)
    assert err is None and "?brand ?brand_label" in body                                      # the join variable is shown, not only the drug
    counted = plan([col("?brand"), col("?count", "count_distinct", "?document")], [pat("?document", "Watson:mentionsBrand", "?brand")])
    assert "GROUP BY ?brand ?brand_label" in sparql_gen.render(counted)[0]                   # aggregates keep exactly their own columns
    via_chunk = plan([col("?document")], [pat("?chunk", "Watson:mentionsBrand", "Watson:Brand_opdivra"), pat("?chunk", "Watson:isChunkOf", "?document")])
    assert "?chunk" not in sparql_gen.render(via_chunk)[0].split("WHERE")[0]                 # chunks are join nodes: no row per chunk
    side = plan([col("?document")], [pat("?chunk", "Watson:mentionsBrand", "Watson:Brand_opdivra"), pat("?chunk", "Watson:coversGeography", "?country"),
                                     pat("?chunk", "Watson:isChunkOf", "?document")])
    assert "?country" not in sparql_gen.render(side)[0].split("WHERE")[0]                    # a side branch must not repeat each document per country
    padded = plan([col("?document")], [pat("?document", "Watson:mentionsBrand", "Watson:Brand_opdivra"), pat("?document", "Watson:mentionsBrand", "?brand"),
                                       pat("?document", "Watson:mentionsBrand", "?brand2")])
    select_part = sparql_gen.render(padded)[0].split("WHERE")[0]
    assert "?brand" not in select_part and "?brand2" not in select_part                      # throwaway leaves the model pads in: not columns
    from layer3 import evidence
    sl = user_graph.build(user("u_admin"))
    row = {"drug": "Watson:Drug_x", "drug_label": "xdrug", "brand": "Watson:Brand_trodelvia", "brand_label": "Trodelvia"}
    text = evidence.sentence_for(sl, two_hops, row)
    assert "Trodelvia co-mentioned with Opdivra" in text and "Trodelvia contains drug xdrug" in text   # context, not a bare 'xdrug'


def test_the_glossary_covers_every_property_and_kind_the_model_can_use(docs, l3_stores):
    """layer3/glossary.json (a draft for a domain person to review) must follow the graph: a new property without everyday phrases fails here."""
    glossary = json.loads((Path(sparql_gen.__file__).parent / "glossary.json").read_text(encoding="utf-8"))
    used = {p for (p, _, _) in sparql_gen.edge_shapes(user_graph.build(user("u_admin")).graph)}
    assert used <= set(glossary["properties"]), f"no glossary entry for: {sorted(used - set(glossary['properties']))}"
    assert set(sparql_gen.CANON) <= set(glossary["kinds"])
    for name, entry in {**glossary["properties"], **glossary["kinds"]}.items():
        assert entry["means"] and len(entry["phrases"]) >= 2, name
    assert glossary["status"].startswith("DRAFT")                                           # flip this when a person has reviewed it


def test_everyday_words_are_matched_to_ontology_terms_by_meaning(docs, l3_stores):
    from layer3 import semantic
    dupixa = [{"text": "Dupixa", "entity_id": "Brand_dupixa", "kind": "Brand", "label": "Dupixa"}]
    assert semantic.words_of_question("Dupixa brand contains which drug ?", dupixa) == ["contains", "which", "drug"]     # name and 'brand' next to it removed
    assert "dupixa" not in semantic.words_of_question("Which brands does the Dupixa brands list have?", dupixa)
    m = semantic.match("Name the active ingredient of Dupixa", dupixa)
    assert m["available"] and m["relations_selected"] == ["containsDrug"] and "Drug" in m["kinds_selected"]
    top = m["relations"][0]
    assert top["term"] == "containsDrug" and top["selected"] and top["words"] and top["phrase"] and top["score"] >= m["thresholds"]["relation"]
    vague = semantic.match("Tell me something interesting about Dupixa", dupixa)
    assert vague["relations_selected"] == [] and vague["kinds_selected"] == []                # nothing clear: nothing narrowed
    nothing = semantic.match("Dupixa", dupixa)
    assert nothing["relations"] == [] and nothing["available"]


def test_when_the_embedding_model_is_missing_the_exact_word_rules_still_apply(docs, fake_llm, monkeypatch, l3_stores):
    from layer2 import embedder
    from layer3 import semantic
    semantic._index.cache_clear()

    def boom(texts):
        raise embedder.EmbeddingUnavailable("model file missing (test)")
    monkeypatch.setattr(embedder, "embed", boom)
    m = semantic.match("What drug does Dupixa have?", [])
    assert m["available"] is False and "model file missing" in m["reason"] and m["relations_selected"] == []
    sl = user_graph.build(user("u_analyst"))
    fake_llm.plans = [plan([col("?brand")], [pat("?brand", "Watson:coMentionedWith", "Watson:Brand_opdivra")])]
    r = sparql_gen.generate("Which brands are co-mentioned with Opdivra?", OPDIVRA, sl.graph, terms=m)       # no crash, exact-word rules used
    assert r["status"] == "ok"
    semantic._index.cache_clear()


def test_semantic_terms_narrow_the_first_edge_and_reach_the_screen(docs, fake_llm, monkeypatch, l3_stores):
    seen = {}
    real = sparql_gen.plan_schema

    def spy(entities, shapes, graph=None, relations=None, kinds=None, count=None):
        seen["relations"], seen["kinds"] = relations, kinds
        return real(entities, shapes, graph, relations, kinds, count)
    monkeypatch.setattr(sparql_gen, "plan_schema", spy)
    fake_llm.plans = [good_plan()]
    fake_llm.answer = quote_evidence
    events, res = run("u_analyst", "Name the active ingredient of Opdivra")
    understood = last(events, "understand")["data"]
    assert understood["terms"]["relations_selected"] == ["containsDrug"]                       # step 3 carries the match for the screen
    assert "containsDrug" in seen["relations"] and "Drug" in seen["kinds"]                     # and step 4 used it
    plain_events, _ = run("u_analyst", "Name the active ingredient of Opdivra", "plain")
    assert "terms" not in last(plain_events, "understand")["data"]                             # plain RAG never touches the graph


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
    name = auth.username_for(uid)
    auth.set_password(name, "correct horse battery")
    r = client.post("/api/login", json={"username": name, "password": "correct horse battery"})
    return {"Authorization": "Bearer " + r.json()["token"]}


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


def test_a_user_can_delete_their_own_history_but_not_anyone_elses(client, docs, fake_llm):
    fake_llm.plans = [good_plan()]
    fake_llm.answer = quote_evidence
    for who in ("u_viewer", "u_admin"):
        client.post("/api/l3/ask", json={"question": "What does the survey say about Opdivra?"}, headers=login(client, who))
    viewer, admin = login(client, "u_viewer"), login(client, "u_admin")
    both = client.get("/api/l3/history", headers=admin).json()["runs"]
    assert len(both) == 2
    mine = next(r for r in both if r["user_id"] == "u_viewer")
    # the admin can read the viewer's entry but cannot delete it; the answer is the same as for an unknown id
    assert client.delete(f"/api/l3/history/{mine['id']}", headers=admin).status_code == 404
    assert len(client.get("/api/l3/history", headers=admin).json()["runs"]) == 2
    assert client.delete(f"/api/l3/history/{mine['id']}").status_code == 401
    # the owner can
    assert client.delete(f"/api/l3/history/{mine['id']}", headers=viewer).json() == {"deleted": 1}
    assert client.delete(f"/api/l3/history/{mine['id']}", headers=viewer).status_code == 404
    left = client.get("/api/l3/history", headers=admin).json()["runs"]
    assert [r["user_id"] for r in left] == ["u_admin"]
    # clearing removes only the caller's own entries; the audit log keeps the QUERY events and records the deletion
    assert client.delete("/api/l3/history", headers=viewer).json() == {"deleted": 0}
    assert client.delete("/api/l3/history", headers=admin).json() == {"deleted": 1}
    assert client.get("/api/l3/history", headers=admin).json()["runs"] == []
    events = [e["event_type"] for e in audit_log.read_events()]
    assert events.count("QUERY") >= 2 and events.count("HISTORY_DELETED") == 2


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

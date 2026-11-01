"""ABAC in Layer 2: what each role sees in the graph, entities, spaces, document knowledge and search.

World built for these tests (all ACTIVE and enriched):
  A  study_001.pdf  Opdivra survey, Oncology/EU      INTERNAL
  B  study_013.pptx competitive landscape (4 oncology brands), Oncology/EU   RESTRICTED
  C  study_009.pdf  Jardiva survey, Cardiology/US    INTERNAL
"""
import pytest

from layer1 import abac_engine as abac, audit_log
from layer1.errors import WatsonAccessDeniedError
from layer2 import embedder, graph_view, kg as kgmod, search, sparql_runner, telemetry, vector_index
from layer2.kg import W, local, short, uri


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
    # every chosen value must hold, also inside one filter: no document names both Keytrava and Jardiva; two chosen documents are
    # alternatives (a result cannot be two documents at once)
    assert graph_view.graph(analyst, "knowledge", filters={"brand": ["Brand_keytrava", "Brand_jardiva"]})["nodes"] == []
    assert _doc_ids(graph_view.graph(analyst, "knowledge", filters={"brand": ["Brand_keytrava", "Brand_opdivra"]})) == {b}
    assert _doc_ids(graph_view.graph(analyst, "knowledge", filters={"documents": [a, c]})) == {a, c}
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


def test_filter_options_follow_the_other_chosen_filters_so_no_choice_is_a_dead_end(docs):
    a, b, c = docs
    analyst, viewer = U("u_analyst"), U("u_viewer")
    ids = lambda f, key: {x["id"]: x["documents"] for x in f[key]}
    start = graph_view.graph_filters(analyst)
    assert {"Brand_keytrava", "Brand_jardiva", "Brand_opdivra"} <= set(ids(start, "brand")) and start["matching"] == start["total"] == 3
    # choosing the cardiology area leaves only what the cardiology document links to (Keytrava is not offered any more)
    f = graph_view.graph_filters(analyst, filters={"area": ["TA_cardiology"]})
    assert f["matching"] == 1 and [d["id"] for d in f["documents"]] == [c]
    assert "Brand_jardiva" in ids(f, "brand") and "Brand_keytrava" not in ids(f, "brand") and "Brand_opdivra" not in ids(f, "brand")
    assert set(ids(f, "geography")) < set(ids(start, "geography"))
    assert ids(f, "area") == {"TA_cardiology": 1}          # values are ANDed, so a second area is not offered: no document has both
    # a second brand is offered only if some document names it together with the first (Keytrava and Opdivra: yes; Keytrava and Jardiva: no)
    two = graph_view.graph_filters(analyst, filters={"brand": ["Brand_keytrava"]})
    assert ids(two, "brand")["Brand_opdivra"] == 1 and "Brand_jardiva" not in ids(two, "brand") and ids(two, "brand")["Brand_keytrava"] == 1
    # a chosen value stays listed, with 0, when it no longer fits the other filters, so it can still be removed
    clash = graph_view.graph_filters(analyst, filters={"brand": ["Brand_keytrava"], "area": ["TA_cardiology"]})
    assert ids(clash, "brand")["Brand_keytrava"] == 0 and clash["matching"] == 0
    # every offered value, added to what is chosen, gives at least one document (and the count says how many)
    for chosen in ({"brand": ["Brand_jardiva"]}, {"area": ["TA_oncology"]}, {"geography": ["Geo_DE"]}):
        for user in (analyst, viewer):
            f = graph_view.graph_filters(user, filters=chosen)
            for key in ("brand", "indication", "drug", "area", "geography"):
                for o in f[key]:
                    if o["documents"] == 0:
                        assert o["id"] in chosen.get(key, [])                              # only an already chosen value may show 0
                        continue
                    both = {**chosen, key: sorted({*chosen.get(key, []), o["id"]})}
                    assert graph_view.graph(user, "knowledge", limit=200, filters=both)["stats"]["documents_matching"] > 0
    # a reader never gets an option because of a document they cannot read
    assert "Brand_keytrava" not in {x["id"] for x in graph_view.graph_filters(viewer, filters={"area": ["TA_oncology"]})["brand"]}
    # the relationship options are the ones present around the other chosen filters, with their document counts; a bad id is refused
    rel = {x["name"]: x["documents"] for x in graph_view.graph_filters(analyst, filters={"area": ["TA_cardiology"]})["relation"]}
    assert rel and "coMentionedWith" not in rel and rel["mentionsBrand"] == 1                       # the cardiology document names one brand: no co-mention
    assert "coMentionedWith" in {x["name"] for x in start["relation"]}
    with pytest.raises(ValueError):
        graph_view.graph_filters(analyst, filters={"brand": ["Geo_DE"]})


def test_choosing_a_relationship_narrows_every_other_list_like_any_other_filter(docs):
    a, b, c = docs
    analyst = U("u_analyst")
    every = graph_view.graph_filters(analyst)
    co = graph_view.graph_filters(analyst, filters={"relation": ["coMentionedWith"]})
    # only the landscape document B names several brands in one passage; the others drop out of every list
    assert [d["id"] for d in co["documents"]] == [b] and co["matching"] == 1 and co["total"] == 3
    assert {x["id"] for x in co["brand"]} < {x["id"] for x in every["brand"]} and "Brand_jardiva" not in {x["id"] for x in co["brand"]}
    assert "Brand_keytrava" in {x["id"] for x in co["brand"]}
    # the graph agrees: documents_matching is that number and the drawing really has co-mention edges and nothing else
    g = graph_view.graph(analyst, "knowledge", filters={"relation": ["coMentionedWith"]})
    assert g["stats"]["documents_matching"] == 1 and g["edges"] and {e["label"] for e in g["edges"]} == {"coMentionedWith"}
    # it combines with the other filters, and a relationship every document has narrows nothing
    assert graph_view.graph_filters(analyst, filters={"relation": ["coMentionedWith"], "area": ["TA_cardiology"]})["matching"] == 0
    assert graph_view.graph_filters(analyst, filters={"relation": ["containsDocument"]})["matching"] == 3
    # several relationships are combined with AND (documents that have all of them), not OR: 1 and 3 give 1, not 3
    both = graph_view.graph_filters(analyst, filters={"relation": ["coMentionedWith", "containsDocument"]})
    assert [d["id"] for d in both["documents"]] == [b] and both["matching"] == 1
    assert {x["name"]: x["documents"] for x in both["relation"]}["coMentionedWith"] == 1 and all(x["documents"] > 0 for x in both["relation"])
    both_graph = graph_view.graph(analyst, "knowledge", filters={"relation": ["coMentionedWith", "containsDocument"]})
    assert both_graph["stats"]["documents_matching"] == 1 and {e["label"] for e in both_graph["edges"]} <= {"coMentionedWith", "containsDocument"}
    # a relationship the viewer's documents do not have is not offered, and the offered ones always draw something
    viewer_rel = {x["name"] for x in graph_view.graph_filters(U("u_viewer"))["relation"]}
    assert "coMentionedWith" not in viewer_rel and "mentionsDrug" not in viewer_rel
    for name in viewer_rel:
        assert any(e["label"] == name for e in graph_view.graph(U("u_viewer"), "knowledge", filters={"relation": [name]})["edges"]), name


def test_choosing_a_country_offers_only_what_is_named_where_that_country_is_named(docs):
    a, b, c = docs
    kg = kgmod.get()
    for user in (U("u_viewer"), U("u_analyst")):
        readable = set(graph_view.visible_records(user))
        with kg.lock:                                  # the answer worked out from the graph itself: passages of readable documents that name Germany
            places = [p for p in kg.g.subjects(W.coversGeography, uri("Geo_DE")) if short(kg.g.value(p, W.isChunkOf)) in readable]
            named = lambda prop: {local(o) for p in places for o in kg.g.objects(p, prop)}
            brands, indications = named(W.mentionsBrand), named(W.mentionsIndication)
            docs_with = {short(kg.g.value(p, W.isChunkOf)) for p in places}
        implied = graph_view._implied(graph_view._instance_map(graph_view.load()))
        indications |= {up for i in indications for up in implied.get(i, [])}       # naming an indication also covers its parent indications
        f = graph_view.graph_filters(user, filters={"geography": ["Geo_DE"]})
        assert places and {x["id"] for x in f["brand"]} == brands and {x["id"] for x in f["indication"]} == indications
        assert {d["id"] for d in f["documents"]} == docs_with and f["matching"] == len(docs_with)
        # the options are narrower than "everything in those documents" whenever the documents say more elsewhere
        every = graph_view.graph_filters(user)
        assert {x["id"] for x in f["brand"]} <= {x["id"] for x in every["brand"]}


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


def test_a_documents_position_in_the_graph_is_the_readers_own_view(docs):
    a, b, c = docs
    # B is the restricted landscape (Keytrava, Lumakro, Opdivra, Tagrisa); A is the Opdivra survey: they share Opdivra
    p = graph_view.document_position(U("u_analyst"), b)
    assert p["enriched"] and p["state"] == "ACTIVE" and p["added"]["triples"] > 0 and p["added"]["chunks"] > 0
    rows = {e["id"]: e for e in p["entities"]}
    assert rows["Brand_keytrava"]["via"] is None and rows["Brand_keytrava"]["documents_before"] == rows["Brand_keytrava"]["documents_after"] - 1
    drug = graph_view._as_list(graph_view._instance_map(graph_view.load())["Brand_keytrava"][1]["relations"]["drug"])[0]
    assert rows[drug]["documents_after"] >= 1 and (rows[drug]["via"] is None or rows[drug]["via"]["id"] == "Brand_keytrava")
    # every number is what the entity list shows this reader (nothing is counted that they may not read)
    listed = {e["id"]: e["documents"] for e in graph_view.entities(U("u_analyst"))}
    assert all(listed[i] == r["documents_after"] for i, r in rows.items())
    assert "Space_oncology" in {s["id"] for s in p["spaces"]}
    assert all(s["documents_before"] == s["documents_after"] - 1 for s in p["spaces"])
    shared = {s["document_uri"]: s for s in p["shared"]}
    assert a in shared and "Brand_opdivra" in {x["id"] for x in shared[a]["shared"]} and c not in shared      # C is cardiology: nothing in common
    assert p["shared_total"] == len(p["shared"]) and p["added"]["provisional"] == [] and p["version"] is None

    # the viewer may not read B at all, and for A sees only what is readable: B and C are neither listed nor counted
    with pytest.raises(WatsonAccessDeniedError):
        graph_view.document_position(U("u_viewer"), b)
    v = graph_view.document_position(U("u_viewer"), a)
    assert b not in {s["document_uri"] for s in v["shared"]} and v["shared_total"] == 0
    assert {e["id"]: e["documents_after"] for e in v["entities"]} == {i: n for i, n in
                                                                      {e["id"]: e["documents"] for e in graph_view.entities(U("u_viewer"))}.items() if i in {x["id"] for x in v["entities"]}}


def test_the_position_picture_is_joined_and_bounded(docs):
    a, b, c = docs
    p = graph_view.document_position(U("u_analyst"), b, entity_limit=3, picture_documents=1)
    g = p["graph"]
    ids = {n["id"] for n in g["nodes"]}
    assert b in ids and next(n for n in g["nodes"] if n["id"] == b).get("matched")
    assert all(e["source"] in ids and e["target"] in ids for e in g["edges"])                        # no edge to a node that is not drawn
    assert sum(1 for n in g["nodes"] if n["kind"] not in ("Document", "KnowledgeSpace")) == 3
    assert sum(1 for n in g["nodes"] if n["kind"] == "Document") <= 2 and g["omitted"]["entities"] > 0
    assert all(any(n["id"] in (e["source"], e["target"]) for e in g["edges"]) for n in g["nodes"])    # every node is joined to the picture


def test_the_position_of_a_document_not_yet_approved_says_so(add_doc):
    pending = add_doc("study_001.pdf", approve=False)
    p = graph_view.document_position(U("u_admin"), pending)
    assert p["state"] == "UNDER_REVIEW" and p["enriched"] is False and p["enrichment"] is None
    with pytest.raises(WatsonAccessDeniedError):                                                       # a reader without access learns nothing
        graph_view.document_position(U("u_viewer"), pending)


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


def test_a_country_named_only_in_passages_is_counted_and_listed(docs):
    # A document is tagged with a region (EU) while its passages name the countries, so a country has no link from the document itself.
    # The entity list, the entity page and the Knowledge space filter must all find it through the passages and agree.
    a, b, c = docs
    for uid in ("u_viewer", "u_analyst"):
        user = U(uid)
        listed = {e["id"]: e["documents"] for e in graph_view.entities(user)}
        offered = {o["id"]: o["documents"] for k in graph_view.FILTER_KINDS for o in graph_view.graph_filters(user, {})[k]}
        assert all(listed[i] == n for i, n in offered.items()), uid
        kinds = set(graph_view.FILTER_KINDS.values())
        assert {i for i, n in listed.items() if n and graph_view._instance_map(graph_view.load())[i][0] in kinds} == set(offered), uid   # and nothing is left out
        for eid, n in listed.items():
            assert n == len(graph_view.entity_detail(user, eid)["documents"]), (uid, eid)
    germany = next(e for e in graph_view.entities(U("u_viewer")) if e["label"] == "Germany")
    assert germany["documents"] > 0
    assert a in {d["document_uri"] for d in graph_view.entity_detail(U("u_viewer"), germany["id"])["documents"]}


def test_a_document_about_a_brand_counts_for_its_drug(docs):
    # The reports name the brand, seldom the generic. Keytrava (Brand_keytrava) contains pemvrolimab; document B names the brand.
    a, b, c = docs
    drug = graph_view._as_list(graph_view._instance_map(graph_view.load())["Brand_keytrava"][1]["relations"]["drug"])[0]
    analyst, viewer = U("u_analyst"), U("u_viewer")
    named = lambda user: {d["document_uri"] for d in graph_view.entity_detail(user, "Brand_keytrava")["documents"]}
    listed = lambda user: next(e["documents"] for e in graph_view.entities(user) if e["id"] == drug)
    detail = graph_view.entity_detail(analyst, drug)["documents"]
    assert b in {d["document_uri"] for d in detail} and listed(analyst) == len(detail) >= len(named(analyst))
    assert all(d["via"] is None or d["via"]["label"] == "Keytrava" for d in detail)
    assert graph_view.entity_detail(viewer, drug)["documents"] == [] and listed(viewer) == 0          # the viewer cannot read B

    ids = {e["id"]: e["documents"] for e in graph_view.entities(analyst)}
    assert ids["Drug_dupilumib"] == 0                                                               # no document names Dupixa in this world


def test_filters_find_a_document_through_what_its_brand_or_indication_implies(docs):
    # The Knowledge space filters follow the same rule as the entity list: the drug of a named brand, that drug's class and the parent of a
    # named indication find the document too, and the graph draws the real path to them (never a bare node).
    a, b, c = docs
    m = graph_view._instance_map(graph_view.load())
    drug = graph_view._as_list(m["Brand_keytrava"][1]["relations"]["drug"])[0]
    cls = graph_view._as_list(m[drug][1]["relations"]["drug_class"])[0]
    analyst, viewer = U("u_analyst"), U("u_viewer")
    for key, eid in (("drug", drug),):
        g = graph_view.graph(analyst, "knowledge", filters={key: [eid]})
        assert b in _doc_ids(g)
        picked = [n for n in g["nodes"] if n["id"] == eid]
        assert picked and picked[0].get("matched")
        assert any(eid in (e["source"], e["target"]) for e in g["edges"])                   # joined to the graph, not floating
        assert any(e["label"] == "containsDrug" and e["target"] == eid for e in g["edges"])
        assert graph_view.graph(viewer, "knowledge", filters={key: [eid]})["nodes"] == []   # access is unchanged: the viewer cannot read b
    offered = lambda user, filters=None, key="drug": {x["id"]: x["documents"] for x in graph_view.graph_filters(user, filters=filters)[key]}
    assert drug in offered(analyst) and drug not in offered(viewer)
    # a parent indication finds the documents of its sub-indications; choosing it offers the sub-indications that sit under it
    parent = "Indication_lung_cancer"
    f = graph_view.graph_filters(viewer, filters={"indication": [parent]})
    assert f["matching"] == offered(viewer, key="indication")["Indication_non_small_cell_lung_cancer"] >= 1
    assert "Indication_non_small_cell_lung_cancer" in {x["id"] for x in f["indication"]}
    g = graph_view.graph(viewer, "knowledge", filters={"indication": [parent]})
    assert a in _doc_ids(g) and any(n["id"] == parent and n.get("matched") for n in g["nodes"])
    assert any(parent in (e["source"], e["target"]) for e in g["edges"])
    # AND still holds: lung cancer together with a brand that no lung cancer document names leaves nothing
    assert graph_view.graph(analyst, "knowledge", filters={"indication": [parent], "brand": ["Brand_jardiva"]})["nodes"] == []


def test_a_class_and_a_parent_indication_count_the_documents_of_what_they_contain(docs):
    # Document A is an Opdivra survey in non-small cell lung cancer. Naming a brand also covers its drug and the drug's class; naming an
    # indication also covers its parent indication.
    a, b, c = docs
    m = graph_view._instance_map(graph_view.load())
    implied = graph_view._implied(m)
    drug = graph_view._as_list(m["Brand_opdivra"][1]["relations"]["drug"])[0]
    cls = graph_view._as_list(m[drug][1]["relations"]["drug_class"])[0]
    assert implied["Brand_opdivra"] == [drug, cls] and implied[drug] == [cls]
    assert "Indication_lung_cancer" in implied["Indication_non_small_cell_lung_cancer"] and "Indication_lung_cancer" in implied["Indication_egfr_mutant_nsclc"]
    assert "Indication_lung_cancer" not in implied["Indication_lung_cancer"] and implied["Indication_lung_cancer"] == []   # a root implies nothing
    viewer = graph_view.entities(U("u_viewer"))
    count = {e["id"]: e["documents"] for e in viewer}
    assert count["Indication_non_small_cell_lung_cancer"] >= 1 and count["Indication_lung_cancer"] == count["Indication_non_small_cell_lung_cancer"]
    assert count[cls] == count[drug] >= 1
    for eid in (cls, "Indication_lung_cancer"):                                                   # the entity page lists the same documents
        det = graph_view.entity_detail(U("u_viewer"), eid)["documents"]
        assert a in {d["document_uri"] for d in det} and len(det) == count[eid]
        assert all(d["via"] is None or d["via"]["id"] in graph_view._implied(m) for d in det)


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

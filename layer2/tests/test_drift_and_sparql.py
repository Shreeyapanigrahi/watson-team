"""(1) Layer 1's vocabulary and the catalog's reference rows are two copies of the same facts: they must agree.
(2) The eleven SPARQL queries run and return what the data says."""
from datetime import date

import pytest

from layer1 import vocab
from layer2 import sparql_runner as R, telemetry
from layer2.catalog.catalog_store import load


# ---------------------------------------------------------------- drift between Layer 1's vocab and the catalog
def _inst(kind):
    return {e["label"]: e for e in load().instances[kind]}


def test_brands_drugs_and_areas_agree_with_layer_1():
    cat, brands, drugs, tas, inds = load(), _inst("Brand"), _inst("Drug"), _inst("TherapeuticArea"), _inst("Indication")
    assert set(brands) == set(vocab.BRANDS)
    ids = {e["id"]: e for kind in ("Drug", "DrugClass", "Indication", "TherapeuticArea") for e in cat.instances[kind]}
    for name, v in vocab.BRANDS.items():
        rel = brands[name]["relations"]
        assert ids[rel["drug"]]["label"] == v["drug"], name
        assert ids[rel["therapeutic_area"]]["label"] == v["ta"], name
        assert [ids[i]["label"] for i in rel["indications"]] == v["indications"], name
        # Layer 1 writes "TNF inhibitor (biosimilar of Humivex)" for Adalibra: the bracket describes the brand, not the class
        assert ids[ids[rel["drug"]]["relations"]["drug_class"]]["label"] == v["drug_class"].split(" (")[0], name
        assert brands[name]["attributes"]["commercial.brand.global_brand_owner"] == v["company"], name
        assert drugs[v["drug"]]["attributes"]["masterdata.atc_classification.atc_code"] == v["atc"], name
    assert set(tas) == set(vocab.THERAPEUTIC_AREAS)


def test_indications_and_aliases_agree_with_layer_1():
    inds = _inst("Indication")
    for name, v in vocab.INDICATIONS.items():
        assert name in inds, name
        assert set(v["aliases"]) <= set(inds[name].get("aliases") or []), name
    extra = set(inds) - set(vocab.INDICATIONS)
    assert all(inds[n].get("grouping") for n in extra), extra       # the only additions are the grouping parents


def test_geographies_agree_with_layer_1():
    geo = {e["id"]: e for e in load().instances["Geography"]}
    for code, v in vocab.GEOGRAPHIES.items():
        e = geo[f"Geo_{code}"]
        assert e["label"] == v["name"] and e["attributes"]["masterdata.country.region"] == v["region"]
        assert e["attributes"]["masterdata.country.reimbursement_framework_type"] == v["payer_type"]
    for region in vocab.GEOGRAPHY_VALUES:
        assert f"GeoRegion_{region}" in geo, region                  # every value a document can carry has a node


def test_extension_knowledge_spaces_only_use_vocabulary_terms():
    for s in load().knowledge_spaces:
        assert set(s["rule"].get("therapeutic_area", [])) <= set(vocab.THERAPEUTIC_AREAS)
        assert set(s["rule"].get("study_type", [])) <= set(vocab.STUDY_TYPES)


# ---------------------------------------------------------------- SPARQL
@pytest.fixture()
def loaded(add_doc):
    uris = {f: add_doc(f, sensitivity="INTERNAL") for f in
            ("study_001.pdf", "study_002.docx", "study_003.pdf", "study_004.docx", "study_013.pptx", "study_007.docx")}
    return uris


def rows(qid, **kw):
    return R.run(qid, **kw)["rows"]


def test_the_queries_file_has_the_eleven_queries_the_brief_asks_for():
    assert [q["id"] for q in R.parse()] == [f"Q{i}" for i in range(1, 12)]
    assert all(q["sparql"].strip() and q["question"] for q in R.parse())


def test_every_query_runs(loaded):
    for q in R.parse():
        res = R.run(q["id"])
        assert res["columns"], q["id"]


def test_q3_finds_the_brand_whose_sentiment_collapsed(loaded):
    # Opdivra: 0.69 (2021), 0.77 (2022), then 0.27 / 0.26 / 0.35 from 2023. Keytrava has only 2023 documents.
    (r,) = rows("Q3")
    assert r["brand"] == "Opdivra" and r["avgBefore2023"] > 0.6 and r["avgFrom2023"] < 0.4


def test_q3_uses_a_temporal_filter_and_active_documents_only(loaded):
    from layer1 import lifecycle_manager as lm
    lm.archive(loaded["study_003.pdf"], "u_admin")                   # the 2023 EU report
    lm.archive(loaded["study_004.docx"], "u_admin")                  # the 2024 US report
    (r,) = rows("Q3")                                                # study_013 (a competitive deck) also states Opdivra's 2023 score: 0.35
    assert r["brand"] == "Opdivra" and r["avgFrom2023"] == 0.35
    lm.archive(loaded["study_013.pptx"], "u_admin")
    assert rows("Q3") == []                                          # no post-2023 evidence left: archived documents are not evidence


def test_q6_only_returns_hcp_sentiment_studies_through_inference(loaded):
    res = R.run("Q6")
    assert res["rows_without_inference"] == []                       # nothing is stored with that type
    assert {r["document"] for r in res["rows"]} == {loaded[f] for f in ("study_001.pdf", "study_002.docx", "study_003.pdf", "study_004.docx", "study_007.docx")}
    assert res["triples_after_inference"] > res["triples_in_graph"]


def test_q1_counts_sub_indications_through_the_transitive_property(loaded):
    inds = {r["indication"] for r in rows("Q1")}
    assert "lung cancer" in inds and "non-small cell lung cancer" in inds     # lung cancer has no brand of its own: it comes from the closure


def test_q2_and_q7_aggregate_sentiment(loaded):
    q2 = rows("Q2")
    assert q2 and all(r["avgSentiment"] < 0.4 for r in q2)
    q7 = rows("Q7")
    assert q7 and all(r["documents"] >= 1 for r in q7) and [r["avgSentiment"] for r in q7] == sorted(r["avgSentiment"] for r in q7)


def test_q4_lists_competing_oncology_brands(loaded):
    by = {r["brand"]: r for r in rows("Q4")}
    assert set(by) == {"Keytrava", "Lumakro", "Opdivra", "Tagrisa"} and by["Keytrava"]["rivals"] == 3


def test_q5_reports_indications_with_fewer_than_two_documents(loaded):
    by = {r["indication"]: r["documents"] for r in rows("Q5")}
    assert by["renal cell carcinoma"] == 0 and by["Alzheimer's disease"] == 0
    assert "non-small cell lung cancer" not in by                   # well covered


def test_q8_uses_the_as_of_date(loaded):
    from layer1 import lifecycle_manager as lm
    u = loaded["study_001.pdf"]
    lm._update(u, lambda r: r.update(expiry_date="2030-03-10"))
    from layer2 import enrichment_pipeline as p
    p.run_now(u)
    near = rows("Q8", as_of=date(2030, 2, 1))
    assert [r["document"] for r in near] == [u] and near[0]["expires"] == "2030-03-10"
    assert rows("Q8", as_of=date(2030, 6, 1)) == []                 # already past, not "upcoming"


def test_q9_traces_a_chunk_to_its_reviewer_and_model(loaded):
    (r,) = rows("Q9", params={"chunk": "Watson:Chunk_001_000"})
    assert r["document"] == loaded["study_001.pdf"] and r["reviewer"] == "u_admin" and r["model"].startswith("all-MiniLM")
    with pytest.raises(ValueError):
        R.run("Q9", params={"chunk": "x> } DROP"})                  # parameters are checked, not pasted into the query


def test_q11_reports_every_space_even_an_empty_one(loaded):
    by = {r["space"]: r for r in rows("Q11")}
    assert len(by) == 6
    assert by["Oncology knowledge space"]["documents"] == 6 and by["Neurology knowledge space"]["documents"] == 0   # all six loaded files are Oncology
    assert by["Neurology knowledge space"]["archived"] == 0


def test_custom_queries_are_read_only():
    assert R.run_custom("SELECT (COUNT(*) AS ?n) WHERE { ?s ?p ?o }")["rows"][0]["n"] > 0
    for bad in ("INSERT DATA { <urn:a> <urn:b> <urn:c> }", "DELETE WHERE { ?s ?p ?o }", "CONSTRUCT { ?s ?p ?o } WHERE { ?s ?p ?o }", "not sparql"):
        with pytest.raises(ValueError):
            R.run_custom(bad)


def test_results_are_saved_as_json_and_markdown(loaded, tmp_path, monkeypatch):
    from layer2 import config
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path / "out")
    R.save([R.run("Q3"), R.run("Q6")])
    assert (tmp_path / "out" / "Q3.json").exists() and "Opdivra" in (tmp_path / "out" / "Q3.md").read_text(encoding="utf-8")
    assert "Without inference: 0 rows" in (tmp_path / "out" / "Q6.md").read_text(encoding="utf-8")

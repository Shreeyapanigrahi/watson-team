"""Stage 1 (entity recognition), stage 2 (resolution) and the sentiment reader."""
import pytest

from layer1 import audit_log
from layer2 import config, entity_extractor as X, entity_resolver as R, sentiment_extractor as S
from layer2.catalog.catalog_store import load


@pytest.fixture()
def index():
    return X.term_index()


def _by_text(mentions):
    return {m["text"]: m for m in mentions}


# ---------------------------------------------------------------- stage 1
def test_rules_come_from_the_catalog_not_from_code(index):
    cat = load()
    labels = {e["label"] for k in ("Brand", "Drug", "Indication", "Geography") for e in cat.instances[k]}
    assert labels <= {t.text for t in index.terms}
    assert index.collisions == []                        # no two entities share a surface form


def test_confidence_follows_the_brief(index):
    m = _by_text(X.extract("Keytrava, KEYTRAVA and NSCLC.", "c", index))
    assert m["Keytrava"]["confidence"] == config.CONF_EXACT and m["Keytrava"]["method"] == "exact"
    assert m["KEYTRAVA"]["confidence"] == config.CONF_CASE_INSENSITIVE and m["KEYTRAVA"]["entity_id"] == "Brand_keytrava"
    assert m["NSCLC"]["confidence"] == config.CONF_EXACT                  # a registered alias, exact case
    assert m["NSCLC"]["entity_id"] == "Indication_non_small_cell_lung_cancer"


def test_longest_match_wins_and_wrapped_lines_still_match(index):
    text = "patients with non-small cell\n            lung cancer were surveyed"
    (m,) = [x for x in X.extract(text, "c", index) if x["label"] == "INDICATION"]
    assert m["entity_id"] == "Indication_non_small_cell_lung_cancer"       # not the shorter 'lung cancer'
    assert m["text"] == "non-small cell lung cancer"                        # whitespace collapsed ...
    assert text[m["start"]:m["end"]].split() == "non-small cell lung cancer".split()   # ... but the span points into the original


def test_short_codes_only_match_with_exact_case(index):
    ids = {m["entity_id"] for m in X.extract("We asked it about the US and the UK; us and uk are words.", "c", index)}
    assert ids == {"GeoRegion_US", "Geo_UK"}                               # not 'us', not 'uk', not the pronoun 'it'


def test_near_miss_spelling_becomes_a_candidate(index):
    (m,) = [x for x in X.extract("Opdivva was compared.", "c", index)]
    assert m["method"] == "candidate" and m["confidence"] == config.CONF_PARTIAL and m["source"] == "near_miss"


def test_brand_inn_pattern_creates_two_candidates(index):
    c = [m for m in X.extract("The launch of Zentaro (zentarolimab) is planned.", "c", index) if m["method"] == "candidate"]
    assert {(m["text"], m["kind"]) for m in c} == {("Zentaro", "Brand"), ("zentarolimab", "Drug")}


def test_ordinary_words_in_brackets_are_not_products(index):
    assert [m for m in X.extract("Methodology (overview) and Reporting (including) notes.", "c", index)] == []


def test_ordinary_text_yields_no_candidates(index):
    text = "Respondents rated safety and efficacy. Fieldwork took place in February. Overall discussion followed."
    assert X.extract(text, "c", index) == []


# ---------------------------------------------------------------- stage 2
def test_fuzzy_resolution_reuses_the_catalog_entity(world, index):
    (cand,) = X.extract("Opdivva was compared.", "Watson:Chunk_001_000", index)
    out = R.resolve(cand, "Watson:Doc_001", index)
    assert out["entity_id"] == "Brand_opdivra" and out["method"] == "fuzzy" and out["similarity"] > config.RESOLVE_RATIO
    assert R.list_provisional() == []


def test_unknown_product_becomes_a_deterministic_provisional_entity(world, index):
    cands = [m for m in X.extract("Zentaro (zentarolimab) is new.", "Watson:Chunk_001_000", index) if m["method"] == "candidate"]
    out = [R.resolve(c, "Watson:Doc_001", index) for c in cands]
    assert all(o["method"] == "provisional" and o["entity_id"].startswith("Provisional_") for o in out)
    ids = {o["text"]: o["entity_id"] for o in out}
    assert ids["Zentaro"] == R.provisional_id("Brand", "zentaro")              # uuid5 of (kind, text): same everywhere
    again = R.resolve(cands[0], "Watson:Doc_002", index)
    assert again["entity_id"] == ids["Zentaro"]
    (entry,) = [e for e in R.list_provisional() if e["label"] == "Zentaro"]
    assert entry["documents"] == ["Watson:Doc_001", "Watson:Doc_002"] and entry["status"] == "PENDING"


def test_a_rejected_provisional_is_never_proposed_again(world, index):
    (cand, _) = [m for m in X.extract("Zentaro (zentarolimab) is new.", "Watson:Chunk_001_000", index) if m["method"] == "candidate"]
    out = R.resolve(cand, "Watson:Doc_001", index)
    store = R.load_store(); store["entities"][out["entity_id"]]["status"] = "REJECTED"; R._save(store)
    assert R.resolve(cand, "Watson:Doc_002", index) is None


def test_a_near_miss_with_no_close_name_is_dropped_not_made_provisional(world, index):
    cand = {"text": "Randomword", "kind": "Unknown", "label": "Unknown", "chunk_uri": "c", "method": "candidate", "source": "near_miss", "confidence": 0.6}
    assert R.resolve(cand, "Watson:Doc_001", index) is None and R.list_provisional() == []


def test_resolution_is_logged(world, index):
    mentions = X.extract("Opdivva and Zentaro (zentarolimab).", "Watson:Chunk_001_000", index)
    out, by = R.resolve_all(mentions, "Watson:Doc_001", index)
    assert by == {"fuzzy": 1, "provisional": 2}
    events = audit_log.read_events("Watson:Doc_001", "ENTITY_RESOLUTION")
    methods = sorted(e["details"].get("method", "summary") for e in events)
    assert methods == ["fuzzy", "provisional", "provisional", "summary"]


# ---------------------------------------------------------------- sentiment
def _ind(brand_id):
    """A mention of the brand's first indication: what a real document about that brand would contain."""
    cat = load()
    b = next(b for b in cat.instances["Brand"] if b["id"] == brand_id)
    return {"kind": "Indication", "entity_id": b["relations"]["indications"][0]}


def _sent(text, mentions=None, date="2023-06-01"):
    index, cat = X.term_index(), load()
    if mentions is None:
        mentions = [_ind(b) for b in ("Brand_opdivra", "Brand_keytrava", "Brand_jardiva")]     # not Humivex: see the ambiguity test
    return S.extract([{"chunk_uri": "Watson:Chunk_001_000", "text": text}], mentions, date, index, cat)


def test_reads_the_two_sentence_forms_in_the_corpus():
    out, skipped = _sent("Overall HCP sentiment index for Opdivra in Germany: 0.69 (scale 0 to 1, assessed 2023).\n"
                         "Sentiment index for Keytrava in EU: 0.62 (scale 0 to 1, 2023)")
    assert skipped == []
    assert {(a["brand"], a["geography"], a["score"]) for a in out} == {("Brand_opdivra", "Geo_DE", 0.69), ("Brand_keytrava", "GeoRegion_EU", 0.62)}


def test_other_scales_are_rescaled_not_copied():
    (a,), _ = _sent("Sentiment index for Opdivra in Germany: 3.5 (scale 1 to 5, 2023)")
    assert a["score"] == 0.625 and a["raw_score"] == 3.5 and a["scale"] == [1.0, 5.0]


def test_a_score_outside_its_own_scale_is_skipped_with_a_reason():
    out, skipped = _sent("Sentiment index for Opdivra in Germany: 7 (scale 0 to 1, 2023)")
    assert out == [] and "outside the stated scale" in skipped[0]["reason"]


def test_date_comes_from_the_report_when_the_year_matches_else_from_the_sentence():
    (same,), _ = _sent("Sentiment index for Opdivra in Germany: 0.5 (scale 0 to 1, 2023)", date="2023-06-22")
    (diff,), _ = _sent("Sentiment index for Opdivra in Germany: 0.5 (scale 0 to 1, 2021)", date="2023-06-22")
    assert same["date"] == "2023-06-22" and diff["date"] == "2021-01-01"


def test_unknown_brand_or_place_is_skipped_not_guessed():
    out, skipped = _sent("Sentiment index for Nowhere in Atlantis: 0.5 (scale 0 to 1, 2023)")
    assert out == [] and skipped[0]["reason"] == "brand or place is not in the catalog"


def test_global_is_a_known_region():
    (a,), _ = _sent("Sentiment index for Jardiva in GLOBAL: 0.72 (scale 0 to 1, 2024)")
    assert a["geography"] == "GeoRegion_GLOBAL"


def test_indication_is_the_one_the_brand_targets_and_the_document_discusses():
    index = X.term_index()
    nsclc = {"kind": "Indication", "entity_id": "Indication_non_small_cell_lung_cancer"}
    melanoma = {"kind": "Indication", "entity_id": "Indication_melanoma"}
    (a,), _ = _sent("Sentiment index for Keytrava in EU: 0.6 (scale 0 to 1, 2023)", [nsclc, nsclc, melanoma])
    assert a["indication"] == "Indication_non_small_cell_lung_cancer"       # mentioned most, and Keytrava targets it
    # Humivex targets two indications and the document names neither: ambiguous, so nothing is written
    out, skipped = _sent("Sentiment index for Humivex in EU: 0.6 (scale 0 to 1, 2023)")
    assert out == [] and "which indication" in skipped[0]["reason"]


def test_a_sentence_repeated_by_chunk_overlap_is_one_assertion():
    s = "Sentiment index for Opdivra in Germany: 0.5 (scale 0 to 1, 2023)"
    index, cat = X.term_index(), load()
    chunks = [{"chunk_uri": "Watson:Chunk_001_000", "text": s}, {"chunk_uri": "Watson:Chunk_001_001", "text": s}]
    out, _ = S.extract(chunks, [_ind("Brand_opdivra")], "2023-06-22", index, cat)
    assert len(out) == 1 and out[0]["chunk_uri"] == "Watson:Chunk_001_000"

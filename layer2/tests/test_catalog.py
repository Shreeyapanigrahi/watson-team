"""The JSON data catalog: built from the three files in architecture/, checked against them."""
import json
import shutil

import pytest

from layer2 import config
from layer2.catalog import build_catalog as bc
from layer2.catalog.catalog_store import Catalog

SOURCES = (config.MODEL_JSON, config.MODEL_CSV, config.MODEL_TTL)
pytestmark = pytest.mark.skipif(not all(p.exists() for p in SOURCES), reason="model files not found in architecture/")


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("catalog") / "catalog.json"
    return bc.build(out_path=out), out


@pytest.fixture(scope="module")
def doc(built):
    return built[0]


@pytest.fixture(scope="module")
def cat(built):
    return Catalog(json.loads(built[1].read_text(encoding="utf-8")))


def _copy_sources(tmp_path):
    for p in SOURCES:
        shutil.copy(p, tmp_path / p.name)
    return tmp_path / config.MODEL_JSON.name, tmp_path / config.MODEL_CSV.name, tmp_path / config.MODEL_TTL.name


# ---------------------------------------------------------------- content and counts
def test_counts_match_the_source_files(doc):
    c = doc["meta"]["counts"]
    assert (c["domains"], c["tables"], c["columns"], c["foreign_keys"], c["metric_views"]) == (15, 213, 8874, 2423, 147)
    assert c["primary_keys"] == c["tables"]               # one primary key per table


def test_every_table_has_one_primary_key_column(cat):
    for t in cat.tables.values():
        pks = [c["name"] for c in t["columns"] if c["is_primary_key"]]
        assert pks == [t["primary_key"]], t["fqn"]


def test_every_foreign_key_points_at_a_real_column(cat):
    assert len(cat.relationships) == 2423
    for r in cat.relationships:
        assert r["from_column"] in cat.columns and r["to_column"] in cat.columns, r
        assert r["to_table"] == cat.table_of(r["to_column"])["fqn"]


def test_foreign_keys_target_primary_keys(cat):
    not_pk = [r["to_column"] for r in cat.relationships if not cat.column(r["to_column"])["is_primary_key"]]
    assert not_pk == [], not_pk[:5]


def test_names_follow_catalog_domain_table_column(cat):
    c = cat.column("commercial.brand.brand_name")
    assert c["fqn"] == "pharmaceuticals_ecm.commercial.brand.brand_name"
    assert cat.tables["pharmaceuticals_ecm.commercial.brand"]["columns"][0]["is_primary_key"] is True


# ---------------------------------------------------------------- the three files agree
def test_csv_cross_check_found_no_unexplained_differences(doc):
    r = doc["meta"]["reconciliation"]["csv_vs_model_json"]
    assert r["csv_rows"] == r["matched_to_model_json"] == 8874
    assert r["unexplained_differences"] == 0
    assert r["explained_differences"]["primary_key_tag_present_in_json_only"] == 213


def test_ttl_is_linked_and_its_losses_are_recorded(doc, cat):
    t = doc["meta"]["reconciliation"]["ttl_vs_model_json"]
    assert t["tables_without_class"] == [] and t["columns_without_property"] == []
    assert len(t["table_class_collisions"]) == 1          # one table shares its name with its domain
    assert t["property_names_shared_by_several_tables"] == 947
    assert t["columns_on_a_shared_property_name"] == 5365
    assert cat.table("brand")["rdf_class"] == "ind:Brand"
    assert cat.column("commercial.brand.brand_name")["rdf_property"] == "ind:brandName"
    assert cat.column("commercial.brand.launch_date")["rdf_property_shared"] is True   # launchDate is used by several tables


def test_object_properties_are_exactly_the_foreign_key_names(doc):
    t = doc["meta"]["reconciliation"]["ttl_vs_model_json"]
    assert t["object_properties"] == t["object_properties_that_are_foreign_key_columns"] == 345


def test_metric_views_all_point_at_real_tables(doc):
    m = doc["meta"]["reconciliation"]["metric_views"]
    assert m["views_whose_owner_table_is_missing"] == []
    assert m["views_whose_declared_source_differs_from_owner"] == []


def test_sources_are_recorded_with_hashes(doc):
    names = {s["file"]: s for s in doc["meta"]["sources"]}
    assert {p.name for p in SOURCES} <= set(names)          # the three Databricks files, plus our extension and alignments
    assert all(len(s["sha256"]) == 64 for s in names.values())


# ---------------------------------------------------------------- classification: nothing is guessed
def test_classification_is_taken_from_tags_and_never_assumed(doc, cat):
    c = doc["meta"]["counts"]
    assert c["columns_by_classification"] == {"confidential": 458, "restricted": 192, "internal": 13}
    assert c["columns_with_conflicting_classification"] == 1
    assert c["columns_unclassified"] == 8874 - 458 - 192 - 13 - 1
    upstream = [col for col in cat.columns.values() if col.get("origin") != "extension"]
    assert all(col["classification"] is None for col in upstream if not any(t in doc["meta"]["classification_levels"] for t in col["tags"]))


def test_a_column_with_two_classification_tags_is_not_guessed(cat):
    col = cat.column("masterdata.business_partner.masterdata_address_id")
    assert col["classification"] is None
    assert set(col["classification_conflict"]) == {"internal", "restricted"}


def test_pii_tags_are_carried_over(cat):
    pii = [c for c in cat.columns.values() if c.get("pii_tags")]
    assert pii and all(t == "pii" or t.startswith("pii_") for c in pii for t in c["pii_tags"])
    assert all(set(c["pii_tags"]) <= set(c["tags"]) for c in pii)


def test_ai_generated_foreign_keys_are_labelled(doc):
    assert doc["meta"]["counts"]["foreign_keys_llm_generated"] == 1977


# ---------------------------------------------------------------- the build refuses inconsistent sources
def test_build_is_repeatable(doc):
    again = bc.build()
    a, b = json.loads(json.dumps(doc)), json.loads(json.dumps(again))
    a["meta"].pop("generated_at"), b["meta"].pop("generated_at")
    assert a == b


def test_build_refuses_a_csv_that_disagrees(tmp_path):
    j, c, t = _copy_sources(tmp_path)
    text = c.read_text(encoding="utf-8")
    assert "Biospecimen Identifier (ID)" in text
    c.write_text(text.replace("Biospecimen Identifier (ID)", "Biospecimen Identifier (IDX)", 1), encoding="utf-8")
    # glossary differences are only tolerated when the CSV is blank, so this one must fail
    with pytest.raises(bc.CatalogBuildError, match="disagree"):
        bc.build(j, c, t)


def test_build_refuses_a_foreign_key_to_a_missing_column(tmp_path):
    j, c, t = _copy_sources(tmp_path)
    data = json.loads(j.read_text(encoding="utf-8"))
    for d in data["model"]["domains"]:
        for p in d["products"]:
            for a in p["attributes"]:
                if a["foreign_key_to"]:
                    a["foreign_key_to"] = "nowhere.nothing.nothing_id"
                    j.write_text(json.dumps(data), encoding="utf-8")
                    with pytest.raises(bc.CatalogBuildError, match="does not exist"):
                        bc.build(j, c, t)
                    return
    pytest.fail("no foreign key found")


# ---------------------------------------------------------------- the loader
def test_table_lookup_by_short_name_and_domain(cat):
    assert cat.table("brand")["fqn"] == cat.table("commercial.brand")["fqn"] == "pharmaceuticals_ecm.commercial.brand"
    with pytest.raises(KeyError):
        cat.table("no_such_table")


def test_referenced_by_finds_brand_pointing_at_indication(cat):
    cols = {r["from_column"] for r in cat.referenced_by("indication")}
    assert "pharmaceuticals_ecm.commercial.brand.indication_id" in cols
    assert any(r["to_table"].endswith("product.indication") for r in cat.references("brand"))


def test_search_ranks_the_obvious_hit_near_the_top(cat):
    top = [h["fqn"] for h in cat.search("brand name", limit=5)]
    assert "pharmaceuticals_ecm.commercial.brand.brand_name" in top
    assert cat.search("zzzz-nothing") == []
    assert all(h["kind"] == "table" for h in cat.search("indication", kind="table"))


def test_metric_views_are_reachable_from_their_table(cat):
    assert "product_indication" in {v["name"] for v in cat.metrics_for("product.indication")}

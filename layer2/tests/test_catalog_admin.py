"""Human decisions on provisional entities: the catalog is edited, rebuilt, and the affected documents re-enriched."""
import hashlib
from pathlib import Path

import pytest

from layer2 import catalog_admin as CA, config, enrichment_pipeline as pipeline, entity_extractor as X, entity_resolver as R, kg as kgmod, shacl_validator
from layer2.catalog.catalog_store import load
from layer2.kg import W, uri

REAL_EXT = Path(config.__file__).parent / "catalog" / "watson_extension.json"
REAL_CAT = Path(config.__file__).parent / "catalog" / "pharma_data_catalog.json"


def digest(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


@pytest.fixture()
def unknown_product(monkeypatch):
    """The study_001 corpus only uses catalog names, so plant what a new product looks like: 'Zentaro (zentarolimab)'."""
    real = X.extract

    def wrapped(text, chunk_uri, index=None):
        out = real(text, chunk_uri, index)
        if chunk_uri.endswith("_000"):
            out.append(X._mention("Zentaro", "Brand", None, 0, 7, chunk_uri, config.CONF_PARTIAL, "candidate", source="pattern"))
            out.append(X._mention("zentarolimab", "Drug", None, 9, 21, chunk_uri, config.CONF_PARTIAL, "candidate", source="pattern"))
        return out
    monkeypatch.setattr(X, "extract", wrapped)


@pytest.fixture()
def enriched(catalog_sandbox, add_doc, unknown_product):
    u = add_doc("study_001.pdf")
    real_before = (digest(REAL_EXT), digest(REAL_CAT))
    yield u
    assert (digest(REAL_EXT), digest(REAL_CAT)) == real_before          # the committed catalog files were never touched


def pending(kind):
    return next(e for e in R.list_provisional("PENDING") if e["kind"] == kind)


def test_unknown_names_become_provisional_nodes_that_pass_shacl(enriched):
    g = kgmod.get().g
    assert {e["label"] for e in R.list_provisional("PENDING")} == {"Zentaro", "zentarolimab"}
    p = uri(pending("Brand")["id"])
    assert (p, W.provisionalStatus, None) in g and list(g.subjects(W.mentionsProvisional, p))
    assert W.Brand_opdivra in set(g.objects(uri(enriched), W.mentionsBrand))        # known entities are unaffected
    assert shacl_validator.run()["conforms"]


def test_approving_a_new_brand_edits_the_catalog_and_re_enriches(enriched, catalog_sandbox):
    cat_file, ext_file = catalog_sandbox
    before = load().version
    out = CA.approve(pending("Brand")["id"], {"mode": "new", "fields": {
        "drug": "Drug_nivoltrimab", "therapeutic_area": "TA_oncology", "indications": ["Indication_melanoma"]}}, "u_spoc")
    cat = load()
    assert out["status"] == "APPROVED" and out["resolved_to"] == "Brand_zentaro" and cat.version != before
    assert out["re_enriched"] == {enriched: "COMPLETE"}
    assert "Zentaro" in {e["label"] for e in cat.instances["Brand"]}
    g = kgmod.get().g
    assert (W.Brand_zentaro, W.containsDrug, W.Drug_nivoltrimab) in g
    cols = {str(g.value(a, W.fqn)) for a in g.objects(W.Brand_zentaro, W.groundedIn)}
    assert "pharmaceuticals_ecm.commercial.brand.brand_name" in cols             # grounded in the catalog like every other brand
    assert W.Brand_zentaro in set(g.objects(uri(enriched), W.mentionsBrand))
    assert not [p for p in g.subjects(None, W.ProvisionalEntity) if str(g.value(p, kgmod.RDFS.label)) == "Zentaro"]   # the placeholder is gone
    assert R.load_store()["entities"][R.provisional_id("Brand", "Zentaro")]["status"] == "APPROVED"
    assert shacl_validator.run()["conforms"]
    log = __import__("json").loads(ext_file.read_text(encoding="utf-8"))["change_log"]
    assert log[-1]["action"] == "approve_new" and log[-1]["actor"] == "u_spoc"


def test_a_new_brand_without_the_relations_the_catalog_needs_is_refused_and_nothing_changes(enriched, catalog_sandbox):
    cat_file, ext_file = catalog_sandbox
    before = (digest(cat_file), digest(ext_file))
    with pytest.raises(CA.DecisionError, match="needs 'drug'"):
        CA.approve(pending("Brand")["id"], {"mode": "new", "fields": {}}, "u_spoc")
    with pytest.raises(CA.DecisionError, match="not a Drug"):
        CA.approve(pending("Brand")["id"], {"mode": "new", "fields": {"drug": "Drug_nope", "therapeutic_area": "TA_oncology", "indications": ["Indication_melanoma"]}}, "u_spoc")
    assert (digest(cat_file), digest(ext_file)) == before
    assert pending("Brand")["status"] == "PENDING"


def test_approving_as_an_alias_adds_a_spelling_to_an_existing_entity(enriched):
    CA.approve(pending("Brand")["id"], {"mode": "alias", "target_id": "Brand_opdivra"}, "u_admin")
    brand = next(e for e in load().instances["Brand"] if e["id"] == "Brand_opdivra")
    assert "Zentaro" in brand["aliases"]
    assert X.term_index().lookup("Zentaro")[0].entity_id == "Brand_opdivra"          # stage 1 now recognises it directly


def test_a_decision_is_final(enriched):
    pid = pending("Brand")["id"]
    CA.reject(pid, "u_spoc", "a heading, not a product")
    with pytest.raises(CA.DecisionError, match="already REJECTED"):
        CA.approve(pid, {"mode": "alias", "target_id": "Brand_opdivra"}, "u_admin")


def test_rejecting_removes_the_node_and_is_never_proposed_again(enriched):
    g = kgmod.get().g
    pid = pending("Drug")["id"]
    CA.reject(pid, "u_spoc", "noise")
    assert (uri(pid), None, None) not in g and not list(g.subjects(W.mentionsProvisional, uri(pid)))
    pipeline.run_now(enriched)                                                         # re-enriching must not bring it back
    assert pid not in {e["id"] for e in R.list_provisional("PENDING")}
    assert shacl_validator.run()["conforms"]


def test_unknown_provisional_ids_are_not_found(enriched):
    from layer1.errors import NotFoundError
    with pytest.raises(NotFoundError):
        CA.reject("Provisional_nope", "u_spoc")

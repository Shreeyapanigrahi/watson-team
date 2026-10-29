"""The hand-written ontology, the seed graph, and SHACL validation (including cases that MUST fail)."""
import pytest
import owlrl
from rdflib import Graph, Literal, URIRef
from rdflib.namespace import OWL, RDF, RDFS, SKOS, XSD

from layer2 import config, kg as kgmod, ontology_view, populate_graph, shacl_validator
from layer2.catalog.catalog_store import load
from layer2.kg import W, CAT


@pytest.fixture()
def seeded(world):
    kg = kgmod.get()
    populate_graph.seed(kg, load())
    return kg


def _copy(kg) -> Graph:
    g = kgmod.bind(Graph())
    g += kg.g
    return g


# ---------------------------------------------------------------- the ontology meets the brief
def test_ontology_meets_every_minimum_in_the_brief(seeded):
    d = ontology_view.describe()
    failed = [c for c in d["checks"] if not c["ok"]]
    assert failed == [], failed


def test_two_inverse_pairs_and_the_transitive_property_the_brief_names(seeded):
    d = ontology_view.describe()
    pairs = {tuple(p) for p in d["inverse_pairs"]}
    assert ("containsDrug", "isDrugOf") in pairs and ("containsDocument", "isInSpace") in pairs
    assert d["transitive"] == ["subIndicationOf"]


def test_external_alignments_are_looked_up_ids_not_guesses(seeded):
    g = seeded.g
    assert (W.Drug, OWL.equivalentClass, URIRef("http://purl.obolibrary.org/obo/CHEBI_23888")) in g
    nsclc = W.Indication_non_small_cell_lung_cancer
    assert (nsclc, SKOS.exactMatch, URIRef("http://purl.obolibrary.org/obo/DOID_3908")) in g
    assert (W.TA_oncology, SKOS.closeMatch, URIRef("http://id.nlm.nih.gov/mesh/D009369")) in g
    # an indication with no exact DOID match has no exactMatch at all (not an invented one)
    assert (W.Indication_plaque_psoriasis, SKOS.exactMatch, None) not in g


def test_every_ontology_term_declares_how_it_is_grounded(seeded):
    g = seeded.g
    terms = [s for t in (OWL.Class, OWL.ObjectProperty, OWL.DatatypeProperty) for s in g.subjects(RDF.type, t) if str(s).startswith(config.NS)]
    assert terms and all(g.value(t, W.groundingStatus) is not None for t in terms)
    # a term that claims 'grounded' points at a real catalog asset
    for t in terms:
        if str(g.value(t, W.groundingStatus)) == "grounded":
            assets = list(g.objects(t, W.groundedIn))
            assert assets and all((a, RDF.type, W.CatalogAsset) in g for a in assets)


# ---------------------------------------------------------------- the seed graph
def test_seed_graph_has_the_triples_the_brief_asks_for(seeded):
    summary = populate_graph.seed(seeded, load())                 # idempotent: a second run adds nothing
    assert summary["triples_added_by_seed"] == 0
    seed_only = len(seeded.g) - len(seeded.ontology())
    assert seed_only >= 700, seed_only


def test_seeded_entities_come_from_the_catalog_and_point_back_at_it(seeded):
    g, cat = seeded.g, load()
    for kind in ("Brand", "Drug", "Indication", "TherapeuticArea", "Geography"):
        ids = {e["id"] for e in cat.instances[kind]}
        in_graph = {kgmod.local(s) for s in g.subjects(RDF.type, W[kind])}
        assert in_graph == ids, kind
    brand = W.Brand_keytrava
    cols = {str(g.value(a, W.fqn)) for a in g.objects(brand, W.groundedIn)}
    assert "pharmaceuticals_ecm.commercial.brand" in cols and "pharmaceuticals_ecm.commercial.brand.brand_name" in cols
    assert str(g.value(brand, W.catalogVersion)) == cat.version
    assert (CAT["pharmaceuticals_ecm.commercial.brand"], RDF.type, W.CatalogAsset) in g


def test_shared_drug_is_one_node_with_two_brands(seeded):
    g = seeded.g
    brands = set(g.subjects(W.containsDrug, W.Drug_adalimutab))
    assert {kgmod.local(b) for b in brands} == {"Brand_humivex", "Brand_adalibra"}


def test_seed_is_deterministic(world):
    a = kgmod.get(); populate_graph.seed(a, load())
    first = set(a.g)
    a.reset(); populate_graph.seed(a, load())
    assert set(a.g) == first


# ---------------------------------------------------------------- SHACL: conforming and non-conforming
def test_seed_graph_conforms(seeded):
    r = shacl_validator.validate_graph()
    assert r["conforms"], r["violations"][:3]
    assert r["shapes"] >= 12


def _violations(seeded, mutate):
    g = _copy(seeded)
    mutate(g)
    r = shacl_validator.validate_graph(g)
    assert not r["conforms"]
    return r


def test_an_ungrounded_brand_is_rejected(seeded):
    r = _violations(seeded, lambda g: g.remove((W.Brand_keytrava, W.groundedIn, None)))
    hit = [v for v in r["violations"] if v["focus"] == "Watson:Brand_keytrava"]
    assert hit and "not grounded" in hit[0]["message"]
    assert r["by_check"] == {"3. No ungrounded entity": 1}


def test_an_ontology_term_with_no_grounding_status_is_rejected(seeded):
    def add(g):
        g.add((W.Invented, RDF.type, OWL.Class))
    r = _violations(seeded, add)
    assert any(v["focus"] == "Watson:Invented" for v in r["violations"])


def test_a_term_marked_grounded_without_a_catalog_asset_is_rejected(seeded):
    def add(g):
        g.add((W.Bogus, RDF.type, OWL.Class))
        g.add((W.Bogus, W.groundingStatus, Literal("grounded")))
    r = _violations(seeded, add)
    assert any(v["focus"] == "Watson:Bogus" and "groundedIn" in v["message"] for v in r["violations"])


def test_a_document_without_expiry_state_or_sensitivity_is_rejected(seeded):
    def add(g):
        g.add((W.Doc_X, RDF.type, W.HCPPerceptionSurvey))             # only the subclass is stored: needs RDFS inference to be checked
    r = _violations(seeded, add)
    paths = {v["path"] for v in r["violations"] if v["focus"] == "Watson:Doc_X"}
    assert {"lifecycleState", "sensitivityLevel", "expiry_date", "quality_score", "hasChunk"} <= paths
    assert "1. Every document has state, sensitivity and expiry" in r["by_check"]


def test_a_chunk_without_parent_or_lineage_is_rejected(seeded):
    def add(g):
        g.add((W.Chunk_X, RDF.type, W.DocumentChunk))
    r = _violations(seeded, add)
    paths = {v["path"] for v in r["violations"] if v["focus"] == "Watson:Chunk_X"}
    assert {"isChunkOf", "sensitivityLevel", "derivedFrom"} <= paths


def test_a_lifecycle_state_outside_the_state_machine_is_rejected(seeded):
    def add(g):
        g.add((W.Doc_X, RDF.type, W.MarketResearchDocument))
        g.add((W.Doc_X, W.lifecycleState, Literal("MAYBE")))
    r = _violations(seeded, add)
    assert any(v["focus"] == "Watson:Doc_X" and v["path"] == "lifecycleState" and v["constraint"] == "InConstraintComponent" for v in r["violations"])


def test_a_lineage_record_without_a_reviewer_is_rejected(seeded):
    def add(g):
        g.add((W.Lineage_X, RDF.type, W.LineageRecord))
    r = _violations(seeded, add)
    assert any(v["focus"] == "Watson:Lineage_X" and v["path"] == "reviewedBy" for v in r["violations"])


def test_an_orphan_provisional_entity_is_rejected(seeded):
    def add(g):
        p = W.Provisional_x
        g.add((p, RDF.type, W.ProvisionalEntity)); g.add((p, RDFS.label, Literal("Zentaro")))
        g.add((p, W.entityType, Literal("Brand"))); g.add((p, W.provisionalStatus, Literal("PENDING")))
    r = _violations(seeded, add)
    assert any("Orphan" in v["message"] for v in r["violations"])


def test_a_sentiment_score_outside_zero_to_one_is_rejected(seeded):
    def add(g):
        s = W.Sentiment_X
        g.add((s, RDF.type, W.SentimentAssertion)); g.add((s, W.sentimentScore, Literal(1.7, datatype=XSD.double)))
    r = _violations(seeded, add)
    assert any(v["focus"] == "Watson:Sentiment_X" and v["path"] == "sentimentScore" for v in r["violations"])


def test_validation_does_not_change_the_stored_graph(seeded):
    before = len(seeded.g)
    shacl_validator.validate_graph()
    assert len(seeded.g) == before


# ---------------------------------------------------------------- OWL inference (what Q6 and Q1 rely on)
def test_owl_rl_infers_parent_types_inverses_and_the_transitive_closure(seeded):
    g = _copy(seeded)
    g.add((W.Doc_X, RDF.type, W.HCPPerceptionSurvey))
    assert (W.Doc_X, RDF.type, W.HCPSentimentStudy) not in g
    owlrl.DeductiveClosure(owlrl.OWLRL_Semantics).expand(g)
    assert (W.Doc_X, RDF.type, W.HCPSentimentStudy) in g                       # 4-level subclass chain
    assert (W.Doc_X, RDF.type, W.MarketResearchDocument) in g
    assert (W.Drug_pemvrolimab, W.isDrugOf, W.Brand_keytrava) in g              # owl:inverseOf
    assert (W.Indication_egfr_mutant_nsclc, W.subIndicationOf, W.Indication_lung_cancer) in g   # owl:TransitiveProperty


def test_reseeding_under_a_new_catalog_version_replaces_the_old_version(seeded):
    """Regression: an approval rebuilds the catalog (new version) and re-seeds. Every entity and asset must carry ONE version."""
    import copy
    from layer2.catalog.catalog_store import Catalog
    doc = copy.deepcopy(load().doc)
    doc["meta"]["catalog_version"] = "newversion001"
    populate_graph.seed(seeded, Catalog(doc))
    g = seeded.g
    for s in list(g.subjects(RDF.type, W.Brand)) + list(g.subjects(RDF.type, W.CatalogAsset)):
        assert [str(v) for v in g.objects(s, W.catalogVersion)] == ["newversion001"], s
    assert shacl_validator.validate_graph()["conforms"]

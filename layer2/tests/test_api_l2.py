"""The Layer 2 HTTP API through the real FastAPI app: sign-in, role gating, and the review -> enrichment hook."""
import pytest
from fastapi.testclient import TestClient

from layer1.api import main as api_main


@pytest.fixture()
def client(world):
    return TestClient(api_main.app)


def login(client, user_id):
    r = client.post("/api/login", json={"user_id": user_id})
    assert r.status_code == 200
    return {"Authorization": "Bearer " + r.json()["token"]}


@pytest.fixture()
def docs(add_doc):
    return (add_doc("study_001.pdf", sensitivity="INTERNAL", ta=["Oncology"], geo=["EU"]),
            add_doc("study_013.pptx", sensitivity="RESTRICTED", ta=["Oncology"], geo=["EU"]))


def test_every_layer_2_endpoint_needs_a_session(client):
    for method, path in (("get", "/api/l2/status"), ("get", "/api/l2/entities"), ("get", "/api/l2/graph"), ("post", "/api/l2/search"),
                         ("get", "/api/l2/catalog/summary"), ("get", "/api/l2/ontology"), ("post", "/api/l2/validate")):
        assert getattr(client, method)(path).status_code == 401, path


def test_catalog_browser_endpoints(client, docs):
    h = login(client, "u_viewer")                                            # the catalog is schema, open to every role
    s = client.get("/api/l2/catalog/summary", headers=h).json()
    assert s["counts"]["tables"] == 213 and s["extension"]["extension_tables"] == 5 and len(s["domains"]) == 16
    tables = client.get("/api/l2/catalog/tables", params={"domain": "commercial", "q": "brand"}, headers=h).json()
    assert "pharmaceuticals_ecm.commercial.brand" in {t["fqn"] for t in tables["tables"]}
    t = client.get("/api/l2/catalog/table", params={"fqn": "commercial.brand"}, headers=h).json()
    col = next(c for c in t["columns"] if c["name"] == "brand_name")
    assert {u["name"] for u in col["watson_usage"]} >= {"Brand"} and t["rdf_class"] == "ind:Brand"
    assert client.get("/api/l2/catalog/table", params={"fqn": "nope.nothing"}, headers=h).status_code == 404
    hits = client.get("/api/l2/catalog/search", params={"q": "indication name"}, headers=h).json()["hits"]
    assert hits
    m = client.get("/api/l2/catalog/mappings", headers=h).json()
    assert m["ontology_term_counts"]["grounded"] >= 10 and all(c["exists"] for t in m["ontology_terms"] for c in t["catalog"])
    assert {e["kind"] for e in m["entity_mappings"]} == {"Brand", "Drug", "DrugClass", "Indication", "TherapeuticArea", "Geography"}
    assert any(a["external_id"] for a in m["alignments"]["DOID"]) and m["alignments"]["MeSH"]


def test_ontology_endpoint_reports_the_brief_checks(client):
    d = client.get("/api/l2/ontology", headers=login(client, "u_viewer")).json()
    assert all(c["ok"] for c in d["checks"]) and d["grounding_summary"]["unknown"] == 0


def test_entities_graph_and_spaces_follow_the_user(client, docs):
    a, b = docs
    viewer, analyst = login(client, "u_viewer"), login(client, "u_analyst")
    ents = {e["id"]: e for e in client.get("/api/l2/entities", params={"kind": "Brand"}, headers=viewer).json()["entities"]}
    assert ents["Brand_opdivra"]["documents"] == 1 and ents["Brand_keytrava"]["documents"] == 0
    assert client.get("/api/l2/entities/Brand_keytrava", headers=analyst).json()["documents"][0]["document_uri"] == b
    assert client.get("/api/l2/entities/Brand_nope", headers=viewer).status_code == 404
    g = client.get("/api/l2/graph", params={"mode": "knowledge"}, headers=viewer).json()
    assert b not in {n["id"] for n in g["nodes"]}
    assert client.get("/api/l2/graph", params={"mode": "entity", "entity": "Brand_opdivra"}, headers=viewer).json()["nodes"]
    assert client.get("/api/l2/graph", params={"mode": "bogus"}, headers=viewer).status_code == 422
    sp = {s["id"]: s for s in client.get("/api/l2/spaces", headers=viewer).json()["spaces"]}
    assert sp["Space_oncology"]["documents"] == 1


def test_document_knowledge_tab(client, docs):
    a, b = docs
    assert client.get(f"/api/l2/documents/{b}/knowledge", headers=login(client, "u_viewer")).status_code == 403
    k = client.get(f"/api/l2/documents/{a}/knowledge", headers=login(client, "u_viewer")).json()
    assert k["enriched"] and k["lineage"]["reviewed_by"] == "u_admin" and k["chunks"] and k["spaces"][0]["id"] == "Space_oncology"
    assert any(m["text"] == "Opdivra" and m["confidence"] == 1.0 for m in k["mentions"])
    assert k["type"] == ["HCPPerceptionSurvey"] and not k["stale"]
    assert client.get("/api/l2/documents/Watson:Doc_999/knowledge", headers=login(client, "u_admin")).status_code == 404


def test_search_endpoint_filters_and_records_telemetry(client, docs):
    a, b = docs
    body = {"query": "competitive landscape Keytrava Lumakro Tagrisa", "k": 5}
    v = client.post("/api/l2/search", json=body, headers=login(client, "u_viewer")).json()
    assert {h["document_uri"] for h in v["hits"]} <= {a}
    an = client.post("/api/l2/search", json=body, headers=login(client, "u_analyst")).json()
    assert an["hits"][0]["document_uri"] == b and an["hits"][0]["snippet"] and an["hits"][0]["entities"]
    assert client.post("/api/l2/search", json={"query": " "}, headers=login(client, "u_viewer")).status_code == 422
    assert client.get("/api/l2/telemetry", headers=login(client, "u_admin")).json()["by_role"] == {"VIEWER": len(v["hits"]), "ANALYST": len(an["hits"])}


def test_privileged_endpoints_are_gated_by_role(client, docs):
    viewer, analyst, spoc, admin = (login(client, u) for u in ("u_viewer", "u_analyst", "u_spoc", "u_admin"))
    for path, method in (("/api/l2/validate", "post"), ("/api/l2/provisional", "get"), ("/api/l2/queries", "get")):
        assert getattr(client, method)(path, headers=viewer).status_code == 403, path
        assert getattr(client, method)(path, headers=analyst).status_code == 403, path
    assert client.post("/api/l2/validate", headers=spoc).status_code == 200
    assert client.get("/api/l2/provisional", headers=spoc).json() == {"entities": []}
    assert client.get("/api/l2/queries", headers=spoc).status_code == 403          # SPARQL is ADMIN only: results are not filtered per user
    assert client.get("/api/l2/queries", headers=admin).status_code == 200
    assert client.post("/api/l2/documents/Watson:Doc_001/enrich", headers=viewer).status_code == 403


def test_validate_endpoint_runs_shacl_and_remembers_the_result(client, docs):
    admin = login(client, "u_admin")
    r = client.post("/api/l2/validate", headers=admin).json()
    assert r["conforms"] is True and r["violation_count"] == 0 and r["index_binding"]["ok"] and r["ran_by"] == "u_admin"
    assert client.get("/api/l2/validate/last", headers=admin).json()["ran_at"] == r["ran_at"]


def test_sparql_endpoints(client, docs):
    admin = login(client, "u_admin")
    q = client.post("/api/l2/queries/Q6/run", json={}, headers=admin).json()
    assert q["id"] == "Q6" and q["rows_without_inference"] == [] and q["count"] == 1
    assert client.post("/api/l2/queries/Q99/run", json={}, headers=admin).status_code == 404
    ok = client.post("/api/l2/queries/custom", json={"sparql": "SELECT (COUNT(*) AS ?n) WHERE { ?s ?p ?o }"}, headers=admin)
    assert ok.status_code == 200 and ok.json()["rows"][0]["n"] > 0
    assert client.post("/api/l2/queries/custom", json={"sparql": "DELETE WHERE { ?s ?p ?o }"}, headers=admin).status_code == 422


def test_status_hides_infrastructure_numbers_from_non_privileged_roles(client, docs):
    v = client.get("/api/l2/status", headers=login(client, "u_viewer")).json()
    assert "triples" not in v and "vectors" not in v and v["readable_documents"] == 1
    a = client.get("/api/l2/status", headers=login(client, "u_admin")).json()
    assert a["triples"] > 700 and a["vectors"] > 0 and a["readable_documents"] == 2 and a["active_not_enriched"] == []


def test_reviewing_a_document_through_the_api_enriches_it(client, world, corpus):
    from layer1 import lifecycle_manager as lm
    uri = lm.ingest(corpus / "study_001.pdf")["document_uri"]
    admin = login(client, "u_admin")
    r = client.post(f"/api/documents/{uri}/review", json={"approved": True, "sensitivity_level": "INTERNAL", "notes": "ok"}, headers=admin)
    assert r.status_code == 200 and r.json()["state"] == "ACTIVE"
    rows = {d["document_uri"]: d for d in client.get("/api/l2/enrichment", headers=admin).json()["documents"]}
    assert rows[uri]["state"] == "COMPLETE" and rows[uri]["triples"] > 50 and not rows[uri]["stale"]
    client.post(f"/api/documents/{uri}/archive", json={}, headers=admin)
    assert client.get(f"/api/l2/documents/{uri}/knowledge", headers=admin).status_code == 403   # no longer ACTIVE: not readable


def test_taxonomy_edits_need_the_edit_taxonomy_permission(client, world):
    assert client.post("/api/l2/taxonomy/StudyType/terms", json={"label": "Pricing Study"}, headers=login(client, "u_analyst")).status_code == 403
    r = client.post("/api/l2/taxonomy/StudyType/terms", json={"label": "Pricing Study"}, headers=login(client, "u_spoc"))
    assert r.status_code == 200 and r.json()["version"] == 2
    assert client.post("/api/l2/taxonomy/StudyType/terms", json={"label": "pricing study"}, headers=login(client, "u_spoc")).status_code == 422
    tax = client.get("/api/l2/taxonomy", headers=login(client, "u_viewer")).json()["taxonomies"]
    assert "Pricing Study" in {t["label"] for t in tax["StudyType"]["terms"]}

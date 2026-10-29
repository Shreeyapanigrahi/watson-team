"""API tests with FastAPI's TestClient: the same calls the React UI makes."""
import pytest
from fastapi.testclient import TestClient

from layer1 import auth
from layer1.api.main import app

TEST_PASSWORD = "correct horse battery"


@pytest.fixture()
def client(env):
    auth.reset_state()
    return TestClient(app)


def login(client, user_id):
    """Sign in through the real login route (the test store gets a password for this account first)."""
    name = auth.username_for(user_id)
    auth.set_password(name, TEST_PASSWORD)
    r = client.post("/api/login", json={"username": name, "password": TEST_PASSWORD})
    assert r.status_code == 200
    return {"Authorization": "Bearer " + r.json()["token"]}


def upload(client, headers, path):
    with open(path, "rb") as f:
        return client.post("/api/documents", headers=headers, files={"file": (path.name, f)})


def test_the_user_list_is_no_longer_public(client):
    assert client.get("/api/users").status_code == 404


def test_endpoints_require_a_token(client):
    assert client.get("/api/documents").status_code == 401
    assert client.get("/api/documents", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_upload_review_and_visibility_flow(client, corpus):
    admin, viewer = login(client, "u_admin"), login(client, "u_viewer")
    r = upload(client, admin, corpus / "study_021.docx")             # EGFR NSCLC deep dive, EU
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "UNDER_REVIEW" and body["dedup"]["verdict"] == "unique" and body["chunking"]["chunk_count"] > 0
    uri = body["document_uri"]

    assert client.get("/api/documents", headers=viewer).json()["count"] == 0      # not ACTIVE yet

    rv = client.post(f"/api/documents/{uri}/review", headers=admin,
                     json={"approved": True, "sensitivity_level": "PUBLIC", "notes": "Verified. No PII."})
    assert rv.status_code == 200 and rv.json()["state"] == "ACTIVE"

    listed = client.get("/api/documents", headers=viewer).json()
    assert [d["document_uri"] for d in listed["documents"]] == [uri]
    assert client.get(f"/api/documents/{uri}/content", headers=viewer).status_code == 200


def test_viewer_cannot_see_restricted_document_by_list_or_direct_url(client, corpus):
    admin, viewer = login(client, "u_admin"), login(client, "u_viewer")
    uri = upload(client, admin, corpus / "study_001.pdf").json()["document_uri"]
    client.post(f"/api/documents/{uri}/review", headers=admin, json={"approved": True, "sensitivity_level": "RESTRICTED"})
    assert client.get("/api/documents", headers=viewer).json()["count"] == 0
    assert client.get(f"/api/documents/{uri}", headers=viewer).status_code == 403
    assert client.get("/api/documents", headers=admin).json()["count"] == 1
    events = client.get("/api/audit", headers=admin, params={"event_type": "ABAC_DENIED"}).json()["events"]
    assert events and events[0]["details"]["role"] == "VIEWER"


def test_viewer_cannot_review_scan_or_read_audit(client, corpus):
    admin, viewer = login(client, "u_admin"), login(client, "u_viewer")
    uri = upload(client, admin, corpus / "study_001.pdf").json()["document_uri"]
    assert client.post(f"/api/documents/{uri}/review", headers=viewer, json={"approved": False, "reason": "x"}).status_code == 403
    assert client.post("/api/expiry/scan", headers=viewer).status_code == 403
    assert client.get("/api/audit", headers=viewer).status_code == 403


def test_any_role_can_upload_but_uploader_sees_status_only(client, corpus):
    viewer = login(client, "u_viewer")
    r = upload(client, viewer, corpus / "study_001.pdf")
    uri = r.json()["document_uri"]
    d = client.get(f"/api/documents/{uri}", headers=viewer).json()
    assert d["view_level"] == "summary" and "metadata" not in d
    assert client.get(f"/api/documents/{uri}/content", headers=viewer).status_code == 403


def test_pii_document_cannot_be_approved_without_override(client, corpus):
    admin = login(client, "u_admin")
    up = upload(client, admin, corpus / "study_036.docx").json()
    assert up["pii"]["pii_detected"]
    r = client.post(f"/api/documents/{up['document_uri']}/review", headers=admin,
                    json={"approved": True, "sensitivity_level": "RESTRICTED"})
    assert r.status_code == 409 and r.json()["code"] == "PIIBlockedError"


def test_duplicate_upload_is_rejected_by_the_pipeline(client, corpus):
    admin = login(client, "u_admin")
    first = upload(client, admin, corpus / "study_012.docx").json()
    dup = upload(client, admin, corpus / "study_012_copy.docx").json()
    assert dup["state"] == "REJECTED" and dup["dedup"]["original_uri"] == first["document_uri"]


def test_bad_uploads_are_refused_cleanly(client, tmp_path):
    admin = login(client, "u_admin")
    exe = tmp_path / "x.exe"
    exe.write_bytes(b"MZ")
    assert upload(client, admin, exe).status_code == 415
    empty = tmp_path / "empty.txt"
    empty.write_bytes(b"")
    assert upload(client, admin, empty).status_code == 422
    corrupt = tmp_path / "corrupt.pdf"
    corrupt.write_bytes(b"this is not a pdf")
    r = upload(client, admin, corrupt)
    assert r.status_code == 200 and r.json()["state"] == "REJECTED" and "unparseable" in r.json()["rejection_reason"]


def test_catalog_is_filtered_by_access(client, corpus):
    admin, viewer = login(client, "u_admin"), login(client, "u_viewer")
    for name, sens in (("study_021.docx", "PUBLIC"), ("study_001.pdf", "RESTRICTED")):
        uri = upload(client, admin, corpus / name).json()["document_uri"]
        client.post(f"/api/documents/{uri}/review", headers=admin, json={"approved": True, "sensitivity_level": sens})
    assert client.get("/api/catalog", headers=admin).json()["summary"]["active"] == 2
    assert client.get("/api/catalog", headers=viewer).json()["summary"]["active"] == 1


def test_expiry_scan_endpoint_and_stats(client, corpus):
    admin = login(client, "u_admin")
    uri = upload(client, admin, corpus / "study_021.docx").json()["document_uri"]
    client.post(f"/api/documents/{uri}/review", headers=admin,
                json={"approved": True, "sensitivity_level": "PUBLIC", "expiry_date": "2020-01-01"})
    assert client.get("/api/expiry/upcoming", headers=admin).json()["documents"][0]["days_left"] < 0
    assert client.post("/api/expiry/scan", headers=admin).json()["moved_to_under_review"] == [uri]
    st = client.get("/api/stats", headers=admin).json()
    assert st["by_state"]["UNDER_REVIEW"] == 1 and st["by_state"]["ACTIVE"] == 0

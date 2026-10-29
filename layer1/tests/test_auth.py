"""Username and password sign-in: one account per role, hashed passwords, lockout, expiry, logout, password change, audit."""
import json

import pytest
from fastapi.testclient import TestClient

from layer1 import audit_log, auth, config, set_password as set_password_script
from layer1.api.main import app

PW = "correct horse battery"
ROLES = {"viewer": "VIEWER", "analyst": "ANALYST", "spoc": "SPOC", "admin": "ADMIN"}


@pytest.fixture()
def client(env):
    auth.reset_state()
    return TestClient(app)


@pytest.fixture()
def accounts(client):
    for name in ROLES:
        auth.set_password(name, PW)
    return client


@pytest.fixture()
def clock(monkeypatch):
    now = [1_000_000.0]
    monkeypatch.setattr(auth.time, "time", lambda: now[0])
    return now


def sign_in(client, username="admin", password=PW):
    return client.post("/api/login", json={"username": username, "password": password})


def bearer(r):
    return {"Authorization": "Bearer " + r.json()["token"]}


def test_each_role_has_its_own_account_and_lands_on_its_own_user(accounts):
    for name, role in ROLES.items():
        r = sign_in(accounts, name)
        assert r.status_code == 200 and r.json()["user"]["role"] == role
        assert accounts.get("/api/me", headers=bearer(r)).json()["user"]["user_id"] == f"u_{name}"


def test_the_username_ignores_case_and_surrounding_spaces(accounts):
    assert sign_in(accounts, "  Admin ").status_code == 200


def test_a_wrong_password_and_an_unknown_user_look_the_same(accounts):
    wrong, unknown = sign_in(accounts, "admin", "not the password"), sign_in(accounts, "nobody", PW)
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


def test_nobody_can_sign_in_until_passwords_are_set(client):
    r = sign_in(client)
    assert r.status_code == 503 and "set_password" in r.json()["detail"]


def test_the_old_login_by_user_id_is_gone(accounts):
    assert accounts.post("/api/login", json={"user_id": "u_admin"}).status_code == 422


def test_passwords_are_stored_only_as_salted_hashes(accounts):
    raw = config.store_path("credentials.json").read_text(encoding="utf-8")
    assert PW not in raw and "scrypt$" in raw
    hashes = [a["hash"] for a in json.loads(raw)["accounts"].values()]
    assert len(set(hashes)) == len(hashes)                    # same password, different salt, different hash
    assert auth.verify_hash(PW, hashes[0]) and not auth.verify_hash(PW + "x", hashes[0])


def test_password_rules_and_unknown_accounts_are_refused(client):
    with pytest.raises(ValueError, match="at least"):
        auth.set_password("admin", "short")
    with pytest.raises(ValueError, match="space"):
        auth.set_password("admin", " padded password ")
    with pytest.raises(ValueError, match="Unknown username"):
        auth.set_password("mallory", PW)


def test_five_wrong_passwords_lock_the_account_for_a_while(accounts, clock):
    for _ in range(auth.MAX_FAILURES):
        assert sign_in(accounts, "admin", "wrong wrong wrong").status_code == 401
    locked = sign_in(accounts, "admin")                         # even the right password is refused now
    assert locked.status_code == 429 and "Try again" in locked.json()["detail"]
    assert sign_in(accounts, "analyst").status_code == 200      # other accounts are not affected
    clock[0] += auth.LOCKOUT_SECONDS + 1
    assert sign_in(accounts, "admin").status_code == 200


def test_a_successful_sign_in_clears_the_failure_count(accounts, clock):
    for _ in range(auth.MAX_FAILURES - 1):
        sign_in(accounts, "admin", "wrong wrong wrong")
    assert sign_in(accounts, "admin").status_code == 200
    for _ in range(auth.MAX_FAILURES - 1):
        assert sign_in(accounts, "admin", "wrong wrong wrong").status_code == 401   # not locked: the count started again


def test_an_unknown_username_is_throttled_too(accounts):
    for _ in range(auth.MAX_FAILURES):
        sign_in(accounts, "ghost", PW)
    assert sign_in(accounts, "ghost", PW).status_code == 429    # same answer a real account would give, so no user guessing


def test_a_session_expires_after_eight_hours(accounts, clock):
    h = bearer(sign_in(accounts))
    assert accounts.get("/api/me", headers=h).status_code == 200
    clock[0] += auth.SESSION_SECONDS + 1
    assert accounts.get("/api/me", headers=h).status_code == 401


def test_logout_ends_the_session(accounts):
    h = bearer(sign_in(accounts))
    assert accounts.post("/api/logout", headers=h).status_code == 200
    assert accounts.get("/api/me", headers=h).status_code == 401
    assert accounts.post("/api/logout").status_code == 401


def test_changing_the_password(accounts):
    here, there = bearer(sign_in(accounts)), bearer(sign_in(accounts))
    body = lambda cur, new: {"current_password": cur, "new_password": new}
    assert accounts.post("/api/me/password", headers=here, json=body("wrong wrong wrong", "a brand new pass")).status_code == 403
    assert accounts.post("/api/me/password", headers=here, json=body(PW, "short")).status_code == 422
    assert accounts.post("/api/me/password", headers=here, json=body(PW, PW)).status_code == 422
    assert accounts.post("/api/me/password", json=body(PW, "a brand new pass")).status_code == 401
    assert accounts.post("/api/me/password", headers=here, json=body(PW, "a brand new pass")).status_code == 200
    assert accounts.get("/api/me", headers=here).status_code == 200            # this session stays
    assert accounts.get("/api/me", headers=there).status_code == 401           # the user's other sessions end
    assert sign_in(accounts, "admin", PW).status_code == 401
    assert sign_in(accounts, "admin", "a brand new pass").status_code == 200


def test_the_audit_log_records_sign_ins_and_never_a_password(accounts):
    sign_in(accounts, "admin", "typed into the wrong box")
    sign_in(accounts, "typed into the wrong box too", PW)
    h = bearer(sign_in(accounts))
    accounts.post("/api/me/password", headers=h, json={"current_password": PW, "new_password": "a brand new pass"})
    accounts.post("/api/logout", headers=h)
    kinds = [e["event_type"] for e in audit_log.read_events()]
    for expected in ("LOGIN_FAILED", "LOGIN", "PASSWORD_CHANGED", "LOGOUT"):
        assert expected in kinds
    failed = audit_log.read_events(event_type="LOGIN_FAILED")
    assert [e["details"]["username"] for e in failed] == ["admin", None]        # an unknown name is not written down
    raw = config.store_path("audit_log.jsonl").read_text(encoding="utf-8")
    for secret in (PW, "a brand new pass", "typed into the wrong box"):
        assert secret not in raw


def test_the_set_password_script_asks_twice_and_sets_the_account(client, monkeypatch):
    answers = iter(["mismatch one", "mismatch two",            # the two entries differ: asked again
                    "short", "short",                          # too short: asked again
                    "a long enough pass", "a long enough pass"])
    monkeypatch.setattr(set_password_script.getpass, "getpass", lambda prompt="": next(answers))
    assert set_password_script.main(["admin"]) == 0
    assert sign_in(client, "admin", "a long enough pass").status_code == 200
    assert set_password_script.main(["mallory"]) == 2

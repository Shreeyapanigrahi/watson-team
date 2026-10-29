"""Username and password sign-in (one account per role).

Passwords are never stored: `credentials.json` holds a salted scrypt hash per username (standard library only).
A username is its registry user_id without the `u_` prefix (viewer, analyst, spoc, admin), so ABAC is unchanged:
signing in only decides WHICH registry user the session belongs to.

Prototype limits (nfr_design.md): sessions and the lockout counters live in memory, so a server restart signs everybody
out; there is no TLS, MFA or self-service reset.
"""
import base64
import hashlib
import hmac
import secrets
import threading
import time
from typing import Optional

from . import abac_engine as abac
from . import config
from .jsonstore import LOCK, read_json, write_json

MIN_PASSWORD_LENGTH = 10
SESSION_SECONDS = 8 * 3600
MAX_FAILURES = 5                  # wrong passwords per username inside the window
FAILURE_WINDOW_SECONDS = 10 * 60
LOCKOUT_SECONDS = 5 * 60

_SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1}

SESSIONS: dict = {}               # token -> {"user_id", "expires"}
_FAILURES: dict = {}              # username -> [timestamps of recent wrong passwords]
_LOCKED_UNTIL: dict = {}          # username -> time when the lock ends
_STATE = threading.RLock()


class AuthError(Exception):
    """Sign-in refused. `status` is the HTTP status the API returns."""

    def __init__(self, message: str, status: int = 401):
        super().__init__(message)
        self.status = status


def _path():
    return config.store_path("credentials.json")


def username_for(user_id: str) -> str:
    return user_id[2:] if user_id.startswith("u_") else user_id


def usernames() -> dict:
    """username -> user_id for every registry user."""
    return {username_for(u["user_id"]): u["user_id"] for u in abac.list_users()}


# ---------------------------------------------------------------- hashing
def hash_password(password: str, salt: Optional[bytes] = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, **_SCRYPT, dklen=32)
    return "scrypt${n}${r}${p}${salt}${hash}".format(**_SCRYPT, salt=base64.b64encode(salt).decode(), hash=base64.b64encode(digest).decode())


def verify_hash(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, expected = stored.split("$")
        if scheme != "scrypt":
            return False
        digest = hashlib.scrypt(password.encode("utf-8"), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(digest, base64.b64decode(expected))
    except (ValueError, TypeError):
        return False


# Used when the username is unknown, so a missing account costs the same time as a wrong password.
_DUMMY = hash_password("not-a-real-password")


# ---------------------------------------------------------------- credentials store
def _load() -> dict:
    return read_json(_path(), {"accounts": {}})["accounts"]


def configured() -> bool:
    return bool(_load())


def check_password_rules(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"The password must be at least {MIN_PASSWORD_LENGTH} characters long")
    if password.strip() != password or not password.strip():
        raise ValueError("The password must not start or end with a space")


def set_password(username: str, password: str) -> None:
    """Create or replace one account's password. Raises ValueError for an unknown username or a weak password."""
    if username not in usernames():
        raise ValueError(f"Unknown username '{username}'. Accounts: {', '.join(sorted(usernames()))}")
    check_password_rules(password)
    with LOCK:
        accounts = _load()
        accounts[username] = {"hash": hash_password(password)}
        write_json(_path(), {"accounts": accounts})
    with _STATE:
        _FAILURES.pop(username, None)
        _LOCKED_UNTIL.pop(username, None)


def reset_state() -> None:
    """Forget sessions and lockout counters (used by tests)."""
    with _STATE:
        SESSIONS.clear()
        _FAILURES.clear()
        _LOCKED_UNTIL.clear()


# ---------------------------------------------------------------- sign in and sessions
def _locked_for(username: str) -> int:
    with _STATE:
        left = _LOCKED_UNTIL.get(username, 0) - time.time()
        if left <= 0:
            _LOCKED_UNTIL.pop(username, None)
            return 0
        return int(left) + 1


def _note_failure(username: str) -> None:
    now = time.time()
    with _STATE:
        recent = [t for t in _FAILURES.get(username, []) if now - t < FAILURE_WINDOW_SECONDS] + [now]
        _FAILURES[username] = recent
        if len(recent) >= MAX_FAILURES:
            _LOCKED_UNTIL[username] = now + LOCKOUT_SECONDS
            _FAILURES.pop(username, None)


def sign_in(username: str, password: str) -> tuple:
    """Return (token, registry user). Raises AuthError. A wrong username and a wrong password give the same message."""
    username = (username or "").strip().lower()[:64]       # also the key of the lockout counters, so its size is bounded
    accounts = _load()
    if not accounts:
        raise AuthError("No passwords have been set yet. An administrator runs: .venv\\Scripts\\python -m layer1.set_password", 503)
    wait = _locked_for(username)
    if wait:
        raise AuthError(f"Too many wrong passwords. Try again in {max(1, round(wait / 60))} minute(s).", 429)
    account = accounts.get(username)
    ok = verify_hash(password or "", account["hash"] if account else _DUMMY)
    if not account or not ok:
        _note_failure(username)
        raise AuthError("Wrong username or password")
    user_id = usernames().get(username)
    if not user_id:
        raise AuthError("Wrong username or password")
    with _STATE:
        _FAILURES.pop(username, None)
        token = secrets.token_urlsafe(24)
        SESSIONS[token] = {"user_id": user_id, "expires": time.time() + SESSION_SECONDS}
    return token, abac.get_user(user_id)


def user_for_token(token: str) -> Optional[dict]:
    with _STATE:
        s = SESSIONS.get(token)
        if not s:
            return None
        if s["expires"] <= time.time():
            SESSIONS.pop(token, None)
            return None
        user_id = s["user_id"]
    return abac.get_user(user_id)


def sign_out(token: str) -> None:
    with _STATE:
        SESSIONS.pop(token, None)


def change_password(user: dict, current: str, new: str, keep_token: Optional[str] = None) -> None:
    """The signed-in user replaces their own password. Their other sessions end."""
    username = username_for(user["user_id"])
    account = _load().get(username)
    wait = _locked_for(username)
    if wait:
        raise AuthError(f"Too many wrong passwords. Try again in {max(1, round(wait / 60))} minute(s).", 429)
    if not account or not verify_hash(current or "", account["hash"]):
        _note_failure(username)
        raise AuthError("The current password is not right", 403)
    if hmac.compare_digest((new or "").encode(), (current or "").encode()):
        raise ValueError("The new password must differ from the current one")
    set_password(username, new)
    with _STATE:
        for t in [t for t, s in SESSIONS.items() if s["user_id"] == user["user_id"] and t != keep_token]:
            SESSIONS.pop(t, None)

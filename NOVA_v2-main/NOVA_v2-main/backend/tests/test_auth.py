from __future__ import annotations

from sqlalchemy import select

from app.models import AuditEvent, User, UserSession
from tests.conftest import make_client, setup_owner


def test_status_before_setup(client):
    r = client.get("/api/auth/status")
    assert r.status_code == 200
    assert r.json() == {"initialized": False, "authenticated": False, "user": None, "csrf_token": None}


def test_setup_requires_valid_code(client):
    r = client.post("/api/auth/setup", json={"username": "farel", "password": "correct-horse-battery",
                                             "setup_code": "00000000x"})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "invalid_setup_code"


def test_setup_validates_password_length(client):
    code = client.app.state.nova.setup_code
    r = client.post("/api/auth/setup", json={"username": "farel", "password": "short", "setup_code": code})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation_error"


def test_setup_creates_owner_once_and_hashes_password(client):
    body = setup_owner(client)
    assert body["authenticated"] and body["user"]["is_owner"]
    code = client.app.state.nova.setup_code
    r = client.post("/api/auth/setup", json={"username": "other", "password": "another-long-pass", "setup_code": code})
    assert r.status_code == 409
    with client.app.state.nova.db.SessionLocal() as db:
        user = db.scalar(select(User))
        assert user.password_hash.startswith("$argon2id$")
        assert "correct-horse-battery" not in user.password_hash
        # Only a digest of the session token is stored.
        sess = db.scalar(select(UserSession))
        assert sess.token_hash != client.cookies.get("nova_session")
        assert len(sess.token_hash) == 64


def test_session_cookie_attributes(client):
    code = client.app.state.nova.setup_code
    r = client.post("/api/auth/setup", json={"username": "farel", "password": "correct-horse-battery", "setup_code": code})
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and "path=/" in cookie


def test_protected_endpoints_require_auth(client):
    setup_owner(client)
    client.cookies.clear()
    assert client.get("/api/conversations").status_code == 401
    assert client.get("/api/projects").status_code == 401
    assert client.get("/api/audit").status_code == 401


def test_csrf_required_for_state_changes(authed):
    token = authed.headers.pop("X-CSRF-Token")
    r = authed.post("/api/conversations", json={"title": "x"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "csrf_failed"
    authed.headers["X-CSRF-Token"] = "wrong" * 8
    assert authed.post("/api/conversations", json={"title": "x"}).status_code == 403
    authed.headers["X-CSRF-Token"] = token
    assert authed.post("/api/conversations", json={"title": "x"}).status_code == 201


def test_client_header_required(authed):
    del authed.headers["X-NOVA-Client"]
    r = authed.post("/api/conversations", json={"title": "x"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "missing_client_header"


def test_login_logout_flow(client):
    setup_owner(client)
    client.post("/api/auth/logout")
    assert client.get("/api/auth/status").json()["authenticated"] is False
    bad = client.post("/api/auth/login", json={"username": "farel", "password": "wrong-password"})
    assert bad.status_code == 401 and bad.json()["error"]["code"] == "invalid_credentials"
    unknown = client.post("/api/auth/login", json={"username": "nobody", "password": "whatever"})
    assert unknown.status_code == 401  # same error for unknown users
    ok = client.post("/api/auth/login", json={"username": "FAREL", "password": "correct-horse-battery"})
    assert ok.status_code == 200 and ok.json()["csrf_token"]


def test_logout_invalidates_server_session(client):
    setup_owner(client)
    raw = client.cookies.get("nova_session")
    assert client.post("/api/auth/logout").status_code == 204
    client.cookies.set("nova_session", raw)
    assert client.get("/api/conversations").status_code == 401


def test_login_rate_limited(client):
    setup_owner(client)
    client.post("/api/auth/logout")
    codes = [client.post("/api/auth/login", json={"username": "farel", "password": "nope-nope"}).status_code
             for _ in range(12)]
    assert codes[:10] == [401] * 10
    assert codes[-1] == 429


def test_password_change_rotates_sessions(client):
    setup_owner(client)
    old = client.cookies.get("nova_session")
    r = client.post("/api/auth/password", json={"current_password": "correct-horse-battery",
                                                "new_password": "a-brand-new-password"})
    assert r.status_code == 200
    client.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    assert client.get("/api/projects").status_code == 200
    other = make_client(client.app.state.nova.settings, client.app.state.nova.providers.get("scripted"))
    other.cookies.set("nova_session", old)
    assert other.get("/api/projects").status_code == 401


def test_audit_never_stores_passwords(client):
    setup_owner(client)
    client.post("/api/auth/logout")
    client.post("/api/auth/login", json={"username": "farel", "password": "wrong-password-123"})
    with client.app.state.nova.db.SessionLocal() as db:
        events = list(db.scalars(select(AuditEvent)))
    assert {e.action for e in events} >= {"auth.setup", "auth.logout", "auth.login"}
    dump = repr([(e.target, e.details) for e in events])
    assert "wrong-password-123" not in dump and "correct-horse-battery" not in dump

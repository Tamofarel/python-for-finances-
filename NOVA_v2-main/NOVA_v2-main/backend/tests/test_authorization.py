"""Cross-user isolation. Setup creates only one owner, so a second account is created directly."""

from __future__ import annotations

from app.models import User
from app.security import hash_password
from tests.conftest import make_client


def test_users_cannot_access_each_others_resources(authed, project, settings, provider):
    pid = project["id"]
    conv = authed.post("/api/conversations", json={"title": "private"}).json()
    mem = authed.post("/api/memory", json={"content": "private fact"}).json()
    authed.post(f"/api/projects/{pid}/files", json={"path": "a.txt", "content": "x"})
    action = authed.post(f"/api/projects/{pid}/actions/command", json={"argv": ["git", "status"]}).json()

    with authed.app.state.nova.db.SessionLocal() as db:
        db.add(User(username="intruder", display_name="I", password_hash=hash_password("intruder-password")))
        db.commit()
    other = make_client(settings, provider)
    r = other.post("/api/auth/login", json={"username": "intruder", "password": "intruder-password"})
    other.headers["X-CSRF-Token"] = r.json()["csrf_token"]

    assert other.get(f"/api/projects/{pid}").status_code == 404
    assert other.get(f"/api/projects/{pid}/files").status_code == 404
    assert other.get(f"/api/projects/{pid}/files/content", params={"path": "a.txt"}).status_code == 404
    assert other.get(f"/api/conversations/{conv['id']}").status_code == 404
    assert other.post(f"/api/conversations/{conv['id']}/messages", json={"content": "x"}).status_code == 404
    assert other.patch(f"/api/memory/{mem['id']}", json={"content": "y"}).status_code == 404
    assert other.post(f"/api/actions/{action['id']}/approve",
                      json={"params_hash": action["params_hash"], "confirm": True}).status_code == 404
    assert other.post("/api/conversations", json={"title": "x", "project_id": pid}).status_code == 404
    assert other.get("/api/projects").json() == []
    assert other.get("/api/memory").json() == []
    assert all(e["action"].startswith("auth.") for e in other.get("/api/audit").json())

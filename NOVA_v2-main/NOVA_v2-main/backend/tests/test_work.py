"""Task engine tests: statuses follow user actions or observed execution, never timers."""

from __future__ import annotations

from pathlib import Path

from tests.conftest import make_client


def _item(c, pid, **kw):
    body = {"title": "Task", **kw}
    r = c.post(f"/api/projects/{pid}/work", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def test_manual_task_lifecycle_and_dependencies(authed, project):
    pid = project["id"]
    a = _item(authed, pid, title="Collect images", domain="data", description="200 photos")
    b = _item(authed, pid, title="Train model", domain="ml", depends_on=[a["id"]])
    assert b["depends_on"] == [a["id"]] and b["status"] == "todo"
    r = authed.post(f"/api/work/{b['id']}/start")
    assert r.status_code == 409 and r.json()["error"]["code"] == "blocked_by_dependencies"
    assert authed.post(f"/api/work/{a['id']}/start").json()["status"] == "in_progress"
    bad = authed.patch(f"/api/work/{a['id']}", json={"status": "awaiting_approval"})
    assert bad.status_code == 409 and bad.json()["error"]["code"] == "invalid_transition"
    assert authed.patch(f"/api/work/{a['id']}", json={"status": "done"}).json()["status"] == "done"
    assert authed.post(f"/api/work/{b['id']}/start").json()["status"] == "in_progress"
    events = [e["message"] for e in authed.get(f"/api/work/{a['id']}/events").json()]
    assert "Started by you." in events and any("to done" in m for m in events)
    cycle = authed.patch(f"/api/work/{a['id']}", json={"depends_on": [b["id"]]})
    assert cycle.status_code == 400 and cycle.json()["error"]["code"] == "dependency_cycle"


def test_dependency_must_be_in_same_project(authed, project):
    other = authed.post("/api/projects", json={"name": "Other"}).json()
    x = _item(authed, other["id"])
    r = authed.post(f"/api/projects/{project['id']}/work", json={"title": "y", "depends_on": [x["id"]]})
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_dependency"


def test_command_task_goes_through_existing_approval_and_reports_observed_result(authed, project):
    pid = project["id"]
    (Path(project["workspace_path"]) / "test_ok.py").write_text("def test_ok():\n    assert True\n")
    item = _item(authed, pid, title="Run tests", executor="command",
                 inputs={"argv": ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider"]})
    assert item["requires_approval"] is True
    started = authed.post(f"/api/work/{item['id']}/start").json()
    assert started["status"] == "awaiting_approval" and started["action_id"]
    manual = authed.patch(f"/api/work/{item['id']}", json={"status": "done"})
    assert manual.status_code == 409 and manual.json()["error"]["code"] == "status_managed"
    action = next(a for a in authed.get("/api/actions", params={"status": "pending"}).json() if a["id"] == started["action_id"])
    assert action["reason"].startswith(f"Task #{item['id']}")
    res = authed.post(f"/api/actions/{action['id']}/approve", params={"wait": True},
                      json={"params_hash": action["params_hash"], "confirm": True}).json()
    assert res["task"]["exit_code"] == 0
    done = authed.get(f"/api/work/{item['id']}").json()
    assert done["status"] == "done" and done["outputs"]["last_run"]["exit_code"] == 0
    msgs = [e["message"] for e in authed.get(f"/api/work/{item['id']}/events").json()]
    assert "Command succeeded (exit code 0)." in msgs and "Approved; the command is running." in msgs


def test_failed_command_marks_task_failed_and_can_retry(authed, project):
    pid = project["id"]
    (Path(project["workspace_path"]) / "test_bad.py").write_text("def test_bad():\n    assert False\n")
    item = _item(authed, pid, executor="command", inputs={"argv": ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider"]})
    started = authed.post(f"/api/work/{item['id']}/start").json()
    action = next(a for a in authed.get("/api/actions").json() if a["id"] == started["action_id"])
    authed.post(f"/api/actions/{action['id']}/approve", params={"wait": True},
                json={"params_hash": action["params_hash"], "confirm": True})
    failed = authed.get(f"/api/work/{item['id']}").json()
    assert failed["status"] == "failed" and "exit code 1" in failed["error"]
    assert any(s["kind"] == "recover" and str(item["id"]) in str(s["target"]) for s in authed.get("/api/suggestions").json())
    assert authed.patch(f"/api/work/{item['id']}", json={"status": "todo"}).json()["status"] == "todo"


def test_rejecting_approval_returns_task_to_todo(authed, project):
    item = _item(authed, project["id"], executor="command", inputs={"argv": ["git", "status"]})
    started = authed.post(f"/api/work/{item['id']}/start").json()
    authed.post(f"/api/actions/{started['action_id']}/reject")
    back = authed.get(f"/api/work/{item['id']}").json()
    assert back["status"] == "todo" and back["action_id"] is None


def test_command_inputs_are_validated(authed, project):
    r = authed.post(f"/api/projects/{project['id']}/work",
                    json={"title": "x", "executor": "command", "inputs": {"argv": ["rm", "-rf", "/"]}})
    assert r.status_code == 403 and r.json()["error"]["code"] == "command_rejected"
    r = authed.post(f"/api/projects/{project['id']}/work", json={"title": "x", "executor": "command", "inputs": {}})
    assert r.status_code == 400


def test_cancel_pending_command_rejects_action(authed, project):
    item = _item(authed, project["id"], executor="command", inputs={"argv": ["git", "status"]})
    started = authed.post(f"/api/work/{item['id']}/start").json()
    assert authed.post(f"/api/work/{item['id']}/cancel").json()["status"] == "cancelled"
    action = next(a for a in authed.get("/api/actions").json() if a["id"] == started["action_id"])
    assert action["status"] == "rejected"


def test_artifact_task_uses_real_provider_output(authed, project, provider):
    provider.replies = ["# Risk register\n| ID | Risk |"]
    item = _item(authed, project["id"], title="Risks", executor="artifact",
                 inputs={"kind": "risk_register", "prompt": "Risks of a plant photo app"})
    done = authed.post(f"/api/work/{item['id']}/start", params={"wait": True}).json()
    assert done["status"] == "done" and done["outputs"]["artifact_id"]
    art = authed.get(f"/api/projects/{project['id']}/artifacts/{done['outputs']['artifact_id']}").json()
    assert art["content"].startswith("# Risk register") and art["created_by"] == "ai"


def test_artifact_task_fails_honestly_without_provider(settings, provider):
    from tests.conftest import setup_owner
    c = make_client(settings.model_copy(update={"default_provider": "unconfigured"}), provider)
    setup_owner(c)
    p = c.post("/api/projects", json={"name": "P"}).json()
    item = _item(c, p["id"], executor="artifact", inputs={"kind": "note", "prompt": "write a note"})
    failed = c.post(f"/api/work/{item['id']}/start", params={"wait": True}).json()
    assert failed["status"] == "failed" and "not configured" in failed["error"]
    assert "outputs" in failed and "artifact_id" not in failed["outputs"]


def test_suggestions_reflect_real_state(authed, project):
    pid = project["id"]
    assert authed.get("/api/suggestions").json() == []
    a = _item(authed, pid, title="First step")
    _item(authed, pid, title="Second step", depends_on=[a["id"]])
    authed.post(f"/api/projects/{pid}/actions/command", json={"argv": ["git", "status"]})
    authed.post(f"/api/projects/{pid}/files", json={"path": "a.txt", "content": "1\n"})
    authed.post(f"/api/projects/{pid}/changes", json={"path": "a.txt", "content": "2\n"})
    sug = authed.get("/api/suggestions").json()
    titles = [s["title"] for s in sug]
    assert titles[0].startswith("Review 1 action")
    assert "Ready to start: First step" in titles and "Ready to start: Second step" not in titles
    assert any(t.startswith("Review 1 pending file change") for t in titles)
    assert authed.get("/api/suggestions", params={"project_id": 9999}).status_code == 404


def test_interrupted_artifact_task_recovered(authed, project, settings, provider):
    bad = authed.post(f"/api/projects/{project['id']}/work", json={"title": "t", "executor": "artifact",
                                                                    "inputs": {"kind": "spaceship", "prompt": "x"}})
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "invalid_inputs"
    item = _item(authed, project["id"], executor="artifact", inputs={"kind": "note", "prompt": "a note"})
    from app.models import WorkItem
    with authed.app.state.nova.db.SessionLocal() as db:
        db.get(WorkItem, item["id"]).status = "in_progress"
        db.commit()
    fresh = make_client(settings, provider)
    fresh.cookies.set("nova_session", authed.cookies.get("nova_session"))
    got = fresh.get(f"/api/work/{item['id']}").json()
    assert got["status"] == "failed" and "Interrupted" in got["error"]


def test_delete_task_cleans_dependencies(authed, project):
    a = _item(authed, project["id"], title="A")
    b = _item(authed, project["id"], title="B", depends_on=[a["id"]])
    assert authed.delete(f"/api/work/{a['id']}").status_code == 204
    assert authed.get(f"/api/work/{b['id']}").json()["depends_on"] == []

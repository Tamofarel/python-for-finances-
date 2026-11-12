from __future__ import annotations

import sys
from pathlib import Path

import pytest

from app.tools.commands import CommandRejected, CommandRunner, safe_env, validate_argv

ALLOW = [["python", "-m", "pytest"], ["npm", "test"], ["git", "status"]]


@pytest.mark.parametrize("argv", [
    ["rm", "-rf", "/"], ["python", "-c", "print(1)"], ["powershell", "-Command", "x"],
    ["python", "-m", "pytest", "; rm -rf ."], ["python", "-m", "pytest", "../other"],
    ["python", "-m", "pytest", "/etc"], ["python", "-m", "pytest", "--rootdir=../x"],
    ["git", "status", "$(whoami)"], [],
])
def test_argv_validation_rejects(argv):
    with pytest.raises(CommandRejected):
        validate_argv(argv, ALLOW)


def test_argv_validation_accepts_prefix():
    assert validate_argv(["python", "-m", "pytest", "-q", "tests"], ALLOW)[:3] == ["python", "-m", "pytest"]


def test_safe_env_drops_secrets(monkeypatch):
    monkeypatch.setenv("NOVA_ANTHROPIC_API_KEY", "sk-ant-should-not-leak")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "nope")
    env = safe_env()
    assert "NOVA_ANTHROPIC_API_KEY" not in env and "AWS_SECRET_ACCESS_KEY" not in env
    assert "PATH" in env


def test_runner_timeout_and_output_cap(tmp_path):
    (tmp_path / "slow.py").write_text("import time\nprint('x' * 5000)\ntime.sleep(30)\n")
    runner = CommandRunner(timeout=1, max_output=100)
    res = runner.run(1, [sys.executable, "slow.py"], tmp_path)
    assert res.status == "timed_out" and res.exit_code is None and res.duration < 10


def _write_tests(project, passing=True):
    ws = Path(project["workspace_path"])
    (ws / "test_sample.py").write_text(f"def test_ok():\n    assert {passing}\n")


def _request(authed, pid, argv, reason="run tests"):
    return authed.post(f"/api/projects/{pid}/actions/command", json={"argv": argv, "reason": reason})


def test_command_requires_approval_and_reports_real_result(authed, project):
    pid = project["id"]
    _write_tests(project)
    r = _request(authed, pid, ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider"])
    assert r.status_code == 201, r.text
    action = r.json()
    assert action["status"] == "pending" and action["category"] == "C" and action["reversible"] is False
    assert authed.get("/api/tasks").json() == []  # nothing executed before approval

    res = authed.post(f"/api/actions/{action['id']}/approve", params={"wait": True},
                      json={"params_hash": action["params_hash"], "confirm": True})
    assert res.status_code == 200, res.text
    task = res.json()["task"]
    assert task["status"] == "succeeded" and task["exit_code"] == 0
    assert "1 passed" in task["stdout"]
    assert task["duration_seconds"] is not None

    events = authed.get("/api/audit").json()
    finished = [e for e in events if e["action"] == "command.finished"]
    assert finished and finished[0]["details"]["exit_code"] == 0


def test_failing_command_reports_failure(authed, project):
    pid = project["id"]
    _write_tests(project, passing=False)
    action = _request(authed, pid, ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider"]).json()
    task = authed.post(f"/api/actions/{action['id']}/approve", params={"wait": True},
                       json={"params_hash": action["params_hash"], "confirm": True}).json()["task"]
    assert task["status"] == "failed" and task["exit_code"] == 1


def test_non_allowlisted_command_rejected_and_audited(authed, project):
    r = _request(authed, project["id"], ["python", "-c", "import os; os.remove('x')"])
    assert r.status_code == 403 and r.json()["error"]["code"] == "command_rejected"
    denied = [e for e in authed.get("/api/audit").json() if e["action"] == "command.requested"]
    assert denied and denied[0]["outcome"] == "denied"


def test_approval_is_bound_to_parameters_and_single_use(authed, project):
    pid = project["id"]
    _write_tests(project)
    action = _request(authed, pid, ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider"]).json()
    wrong = authed.post(f"/api/actions/{action['id']}/approve", json={"params_hash": "f" * 64, "confirm": True})
    assert wrong.status_code == 403 and wrong.json()["error"]["code"] == "approval_mismatch"
    no_confirm = authed.post(f"/api/actions/{action['id']}/approve",
                             json={"params_hash": action["params_hash"], "confirm": False})
    assert no_confirm.status_code == 422
    ok = authed.post(f"/api/actions/{action['id']}/approve", params={"wait": True},
                     json={"params_hash": action["params_hash"], "confirm": True})
    assert ok.status_code == 200
    replay = authed.post(f"/api/actions/{action['id']}/approve",
                         json={"params_hash": action["params_hash"], "confirm": True})
    assert replay.status_code == 409


def test_expired_approval_cannot_run(authed, project):
    from datetime import UTC, datetime, timedelta

    from app.models import ActionRequest
    from app.permissions import ACTION_TTL

    action = _request(authed, project["id"], ["git", "status"]).json()
    with authed.app.state.nova.db.SessionLocal() as db:
        request = db.get(ActionRequest, action["id"])
        request.created_at = datetime.now(UTC) - ACTION_TTL - timedelta(seconds=1)
        db.commit()
    response = authed.post(f"/api/actions/{action['id']}/approve",
                           json={"params_hash": action["params_hash"], "confirm": True})
    assert response.status_code == 409 and response.json()["error"]["code"] == "expired"
    assert authed.get("/api/tasks").json() == []


def test_rejected_action_cannot_run(authed, project):
    action = _request(authed, project["id"], ["git", "status"]).json()
    assert authed.post(f"/api/actions/{action['id']}/reject").json()["status"] == "rejected"
    r = authed.post(f"/api/actions/{action['id']}/approve", json={"params_hash": action["params_hash"], "confirm": True})
    assert r.status_code == 409


def test_tampered_params_rejected(authed, project):
    """Even if stored params were altered after the request, the hash check blocks execution."""
    action = _request(authed, project["id"], ["git", "status"]).json()
    from app.models import ActionRequest
    with authed.app.state.nova.db.SessionLocal() as db:
        ar = db.get(ActionRequest, action["id"])
        ar.params = {"argv": ["git", "status", "--porcelain"], "cwd": "."}
        db.commit()
    r = authed.post(f"/api/actions/{action['id']}/approve", json={"params_hash": action["params_hash"], "confirm": True})
    assert r.status_code == 403


def test_delete_requires_confirmation(authed, project):
    pid = project["id"]
    authed.post(f"/api/projects/{pid}/files", json={"path": "old.txt", "content": "bye\n"})
    action = authed.post(f"/api/projects/{pid}/actions/delete", json={"path": "old.txt"}).json()
    target = Path(project["workspace_path"]) / "old.txt"
    assert target.exists()
    r = authed.post(f"/api/actions/{action['id']}/approve", json={"params_hash": action["params_hash"], "confirm": True})
    assert r.status_code == 200 and r.json()["result"] == {"deleted": "old.txt"}
    assert not target.exists()
    assert authed.post(f"/api/projects/{pid}/actions/delete", json={"path": "../x"}).status_code == 403


def test_running_command_can_be_cancelled(authed, project):
    import time
    ws = Path(project["workspace_path"])
    (ws / "test_slow.py").write_text("import time\n\ndef test_slow():\n    time.sleep(30)\n")
    action = _request(authed, project["id"], ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider"]).json()
    task = authed.post(f"/api/actions/{action['id']}/approve",
                       json={"params_hash": action["params_hash"], "confirm": True}).json()["task"]
    for _ in range(100):
        if authed.get(f"/api/tasks/{task['id']}").json()["status"] == "running":
            break
        time.sleep(0.1)
    assert authed.post(f"/api/tasks/{task['id']}/cancel").status_code == 200
    for _ in range(100):
        t = authed.get(f"/api/tasks/{task['id']}").json()
        if t["status"] not in ("queued", "running"):
            break
        time.sleep(0.1)
    assert t["status"] == "cancelled"


def test_interrupted_work_is_reported_after_restart(settings, provider, authed, project):
    from app.models import Message, Task
    from tests.conftest import make_client
    with authed.app.state.nova.db.SessionLocal() as db:
        db.add(Task(owner_id=1, project_id=project["id"], kind="command", status="running", command=["npm", "test"]))
        conv = authed.post("/api/conversations", json={"title": "x"}).json()
        db.add(Message(conversation_id=conv["id"], role="assistant", content="partial", status="streaming"))
        db.commit()
    fresh = make_client(settings, provider)  # simulates a backend restart
    with fresh.app.state.nova.db.SessionLocal() as db:
        task = db.query(Task).one()
        msg = db.query(Message).one()
    assert task.status == "failed" and "Interrupted" in task.error
    assert msg.status == "error" and msg.content == "partial"


def test_shutdown_stops_running_commands(settings, provider):
    import time

    from tests.conftest import make_client, setup_owner
    client = make_client(settings, provider)
    with client:  # entering/exiting runs the app lifespan (startup/shutdown)
        setup_owner(client)
        proj = client.post("/api/projects", json={"name": "S"}).json()
        (Path(proj["workspace_path"]) / "test_slow.py").write_text("import time\n\ndef test_slow():\n    time.sleep(30)\n")
        action = _request(client, proj["id"], ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider"]).json()
        task = client.post(f"/api/actions/{action['id']}/approve",
                           json={"params_hash": action["params_hash"], "confirm": True}).json()["task"]
        for _ in range(100):
            if client.get(f"/api/tasks/{task['id']}").json()["status"] == "running":
                break
            time.sleep(0.1)
        runner = client.app.state.nova.runner
        started = time.monotonic()
    # Shutdown happened: the child process was killed instead of running for 30 s.
    for _ in range(100):
        if not runner._running:
            break
        time.sleep(0.1)
    assert not runner._running and time.monotonic() - started < 15
    from app.models import Task
    for _ in range(100):  # let the worker thread record the outcome before the test ends
        with client.app.state.nova.db.SessionLocal() as db:
            status = db.get(Task, task["id"]).status
        if status not in ("queued", "running"):
            break
        time.sleep(0.1)
    assert status == "cancelled"


def test_cancel_and_shutdown_apply_to_a_command_that_is_still_starting(tmp_path):
    # A task marked "running" may not have registered its process yet; cancellation must not be lost in that window.
    runner = CommandRunner(timeout=30, max_output=1000)
    argv = ["python", "-c", "import time; time.sleep(30)"]
    assert runner.cancel(7, starting=True) is True
    res = runner.run(7, argv, tmp_path)
    assert res.status == "cancelled" and res.exit_code is None
    assert runner.cancel(8) is False  # nothing running and not starting
    runner.cancel_all()  # shutdown
    res = runner.run(9, argv, tmp_path)
    assert res.status == "cancelled" and res.duration < 5

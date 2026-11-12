#!/usr/bin/env python3
"""End-to-end smoke test against a REAL running NOVA backend (no mocks).

It starts the backend in a temporary data folder, then walks the required workflow:
first-run setup, login, conversation, provider check and a real request, persistence across a
backend restart, project, artifact, change proposal + diff + approval, an approved test command,
audit events, and rejection of unauthorized / unapproved actions.

If no AI provider is reachable, the live AI steps are reported as UNVERIFIED (never faked).

Usage (from the NOVA folder, with the backend virtualenv active):
    python scripts/smoke_test.py            # uses a throw-away data dir and port 8765
    python scripts/smoke_test.py --keep     # keep the temp data dir for inspection
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
RESULTS: list[tuple[str, str, str]] = []


def record(step: str, status: str, detail: str = "") -> None:
    RESULTS.append((step, status, detail))
    print(f"[{status:10}] {step}" + (f" — {detail}" if detail else ""), flush=True)


class Backend:
    def __init__(self, data_dir: Path, port: int) -> None:
        self.data_dir, self.port = data_dir, port
        self.proc: subprocess.Popen[str] | None = None
        self.setup_code: str | None = None

    def start(self) -> None:
        env = {**os.environ, "NOVA_DATA_DIR": str(self.data_dir), "NOVA_PORT": str(self.port),
               "NOVA_HOST": "127.0.0.1", "NOVA_LOG_LEVEL": "WARNING", "PYTHONUNBUFFERED": "1"}
        self.proc = subprocess.Popen([sys.executable, "-m", "app.main"], cwd=BACKEND, env=env,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

        def drain() -> None:  # read output in the background so the pipe never blocks
            for line in self.proc.stdout:  # type: ignore[union-attr]
                m = re.search(r"setup code: (\d+)", line)
                if m:
                    self.setup_code = m.group(1)

        threading.Thread(target=drain, daemon=True).start()
        deadline = time.time() + 60
        while time.time() < deadline:
            try:
                if httpx.get(f"{self.url}/api/health", timeout=1).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            if self.proc.poll() is not None:
                raise RuntimeError("Backend exited during startup.")
            time.sleep(0.3)
        raise RuntimeError("Backend did not become healthy within 60s.")

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


def client(base: str) -> httpx.Client:
    return httpx.Client(base_url=base, headers={"X-NOVA-Client": "web"}, timeout=180)


def sse_events(text: str) -> list[tuple[str, dict]]:
    out = []
    for block in text.strip().split("\n\n"):
        ev, data = "message", ""
        for line in block.splitlines():
            if line.startswith("event: "):
                ev = line[7:]
            elif line.startswith("data: "):
                data += line[6:]
        if data:
            out.append((ev, json.loads(data)))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()
    data_dir = Path(tempfile.mkdtemp(prefix="nova-smoke-"))
    be = Backend(data_dir, args.port)
    failures = 0
    try:
        be.start()
        record("1. Start NOVA", "PASS", f"health OK at {be.url}")
        c = client(be.url)

        # 2-3. first-run setup + login
        for _ in range(20):
            if be.setup_code:
                break
            time.sleep(0.1)
        assert be.setup_code, "setup code not printed"
        r = c.post("/api/auth/setup", json={"username": "owner", "display_name": "Owner",
                                            "password": "smoke-test-password", "setup_code": be.setup_code})
        assert r.status_code == 201, r.text
        record("2. First-run setup", "PASS", "owner created with console setup code")
        c.post("/api/auth/logout", headers={"X-CSRF-Token": r.json()["csrf_token"]})
        r = c.post("/api/auth/login", json={"username": "owner", "password": "smoke-test-password"})
        assert r.status_code == 200, r.text
        c.headers["X-CSRF-Token"] = r.json()["csrf_token"]
        record("3. Log in", "PASS")

        # 4. conversation
        conv = c.post("/api/conversations", json={"title": "Smoke test"}).json()
        record("4. Create conversation", "PASS", f"id={conv['id']}")

        # 5-7. provider + real request
        providers = c.get("/api/providers").json()["providers"]
        live = [p for p in providers if p["reachable"] and not p["error"]]
        for p in providers:
            record(f"5. Provider {p['name']}", "INFO",
                   f"configured={p['configured']} reachable={p['reachable']} error={p['error']!r}")
        if live:
            p = live[0]
            c.put("/api/providers/selection", json={"provider": p["name"], "model": p["default_model"]})
            ev = sse_events(c.post(f"/api/conversations/{conv['id']}/messages",
                                   json={"content": "Reply with the single word: ready"}).text)
            ok = ev and ev[-1][0] == "done" and ev[-1][1]["message"]["content"].strip()
            record("6. Real request to provider", "PASS" if ok else "FAIL", f"{p['name']}: {ev[-1] if ev else 'no events'}")
            failures += 0 if ok else 1
        else:
            ev = sse_events(c.post(f"/api/conversations/{conv['id']}/messages", json={"content": "Hello NOVA"}).text)
            err = [d for e, d in ev if e == "error"]
            record("6. Real request to provider", "UNVERIFIED",
                   "no reachable provider; NOVA reported: " + (err[0]["message"] if err else "no error event?!"))
            if not err:
                failures += 1

        # 7. persistence across a backend restart
        cookie = c.cookies.get("nova_session")
        be.stop()
        be.start()
        c2 = client(be.url)
        c2.cookies.set("nova_session", cookie)
        c2.headers["X-CSRF-Token"] = c.headers["X-CSRF-Token"]
        msgs = c2.get(f"/api/conversations/{conv['id']}").json()["messages"]
        assert msgs and msgs[0]["content"] in ("Hello NOVA", "Reply with the single word: ready"), msgs
        record("7. Conversation persists after backend restart", "PASS", f"{len(msgs)} messages reloaded")
        c = c2

        # 8. project
        proj = c.post("/api/projects", json={"name": "Smoke project", "description": "e2e"}).json()
        ws = Path(proj["workspace_path"])
        record("8. Create project", "PASS", f"workspace={ws}")

        # 9. artifact
        art = c.post(f"/api/projects/{proj['id']}/artifacts",
                     json={"kind": "requirements", "title": "Requirements", "content": "# Req\n- add() must add\n"}).json()
        record("9. Create project artifact", "PASS", f"artifact id={art['id']}")

        # 10-13. proposal, diff, approval, applied
        c.post(f"/api/projects/{proj['id']}/files", json={"path": "calc.py", "content": "def add(a, b):\n    return a - b\n"})
        c.post(f"/api/projects/{proj['id']}/files",
               json={"path": "test_calc.py", "content": "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"})
        base = c.get(f"/api/projects/{proj['id']}/files/content", params={"path": "calc.py"}).json()["sha256"]
        cp = c.post(f"/api/projects/{proj['id']}/changes",
                    json={"path": "calc.py", "content": "def add(a, b):\n    return a + b\n", "base_sha256": base,
                          "summary": "Fix add"}).json()
        record("10. Generate proposed file change", "PASS", f"change id={cp['id']} status={cp['status']}")
        assert "-    return a - b" in cp["diff"] and "+    return a + b" in cp["diff"]
        record("11. Review diff", "PASS", "unified diff contains the expected -/+ lines")
        assert (ws / "calc.py").read_text() == "def add(a, b):\n    return a - b\n", "file changed before approval!"
        r = c.post(f"/api/projects/{proj['id']}/changes/{cp['id']}/apply", json={"proposed_sha256": cp["proposed_sha256"]})
        assert r.status_code == 200 and r.json()["status"] == "applied", r.text
        record("12. Approve change", "PASS")
        assert (ws / "calc.py").read_text() == "def add(a, b):\n    return a + b\n"
        record("13. Change applied to intended file", "PASS", "calc.py content verified on disk")

        # 14-15. approved test command + audit
        act = c.post(f"/api/projects/{proj['id']}/actions/command",
                     json={"argv": ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider"], "reason": "smoke"}).json()
        res = c.post(f"/api/actions/{act['id']}/approve", params={"wait": True},
                     json={"params_hash": act["params_hash"], "confirm": True}).json()
        task = res["task"]
        ok = task["status"] == "succeeded" and task["exit_code"] == 0
        record("14. Run approved test command", "PASS" if ok else "FAIL",
               f"status={task['status']} exit={task['exit_code']} out={task['stdout'].strip().splitlines()[-1:]}")
        failures += 0 if ok else 1
        events = c.get("/api/audit").json()
        fin = [e for e in events if e["action"] == "command.finished"]
        assert fin and fin[0]["details"]["exit_code"] == 0
        record("15. Real command result + audit event", "PASS", f"{len(events)} audit events; command.finished exit_code=0")

        # 16. rejected actions
        pid = proj["id"]
        pending = c.post(f"/api/projects/{pid}/actions/command", json={"argv": ["git", "status"]}).json()
        checks = {
            "non-allowlisted command": c.post(f"/api/projects/{pid}/actions/command",
                                              json={"argv": ["python", "-c", "print(1)"]}).status_code == 403,
            "approval with wrong hash": c.post(f"/api/actions/{pending['id']}/approve",
                                               json={"params_hash": "0" * 64, "confirm": True}).status_code == 403,
            "path traversal": c.get(f"/api/projects/{pid}/files/content",
                                    params={"path": "../../etc/passwd"}).status_code == 403,
            "missing CSRF token": httpx.post(f"{be.url}/api/conversations", json={"title": "x"},
                                             headers={"X-NOVA-Client": "web"},
                                             cookies={"nova_session": cookie}).status_code == 403,
            "unauthenticated request": httpx.get(f"{be.url}/api/projects").status_code == 401,
            "replayed approval": c.post(f"/api/actions/{act['id']}/approve",
                                        json={"params_hash": act["params_hash"], "confirm": True}).status_code == 409,
        }
        bad = [k for k, v in checks.items() if not v]
        record("16. Unauthorized / unapproved actions rejected", "PASS" if not bad else "FAIL",
               ", ".join(checks) if not bad else f"NOT rejected: {bad}")
        failures += len(bad)

        # 17-21. idea-to-product workflow with the plant-health example (generic workflow, real API)
        spec = json.loads((ROOT / "docs" / "examples" / "plant-health-answers.json").read_text(encoding="utf-8"))
        wf = c.post("/api/workflows", json={"idea": spec["idea"], "template": spec["template"], "title": spec["title"]}).json()
        asked = 0
        for _ in range(20):
            q = next((q for q in wf["questions"] if q["status"] == "open"), None)
            if q is None:
                break
            asked += 1
            assert sum(1 for x in wf["questions"] if x["status"] == "open") == 1, "more than one open question"
            ans = spec["answers"].get(q["topic"])
            wf = (c.post(f"/api/workflows/{wf['id']}/questions/{q['id']}/answer", json={"answer": ans}) if ans
                  else c.post(f"/api/workflows/{wf['id']}/questions/{q['id']}/skip")).json()
        wf = c.post(f"/api/workflows/{wf['id']}/clarify/complete").json()
        record("17. Workflow asks one question at a time", "PASS", f"{asked} questions answered one by one")
        while wf["next_stage"]:
            wf = c.post(f"/api/workflows/{wf['id']}/continue", params={"wait": True}, json={"mode": "auto"}).json()
            failed = [s for s in wf["stages"] if s["status"] in ("failed", "interrupted")]
            assert not failed, failed
        kinds = {a["kind"] for s in wf["stages"] for a in s["artifacts"]}
        need = {"requirements", "user_stories", "architecture", "data_model", "ui_spec", "api_contract",
                "implementation_plan", "risk_register", "proposal"}
        assert need <= kinds, need - kinds
        req = next(a for s in wf["stages"] for a in s["artifacts"] if a["kind"] == "requirements")
        edited = c.patch(f"/api/projects/{wf['project_id']}/artifacts/{req['id']}", json={"title": "PRD (edited)"})
        assert edited.status_code == 200
        record("18. Plant-health example creates editable artifacts", "PASS", f"{len(kinds)} document kinds; requirements edited")
        research = wf["research"]
        honest = all((f["status"] == "sourced" and f["results"]) or (f["status"] != "sourced" and not f["results"])
                     for f in research)
        record("19. Research is real or explicitly pending", "PASS" if honest else "FAIL",
               ", ".join(f"{f['status']}" for f in research))
        failures += 0 if honest else 1
        be.stop()
        be.start()
        c3 = client(be.url)
        c3.cookies.set("nova_session", cookie)
        c3.headers["X-CSRF-Token"] = c.headers["X-CSRF-Token"]
        again = c3.get(f"/api/workflows/{wf['id']}").json()
        assert again["status"] == "awaiting_decision" and again["proposal"]["sha256"] == wf["proposal"]["sha256"]
        record("20. Workflow persists across a backend restart", "PASS", "status, questions and proposal reloaded")
        c = c3
        bad_sha = c.post(f"/api/workflows/{wf['id']}/decision", json={"decision": "approve", "proposal_sha256": "0" * 64})
        assert bad_sha.status_code == 409
        wf = c.post(f"/api/workflows/{wf['id']}/decision",
                    json={"decision": "approve", "proposal_sha256": again["proposal"]["sha256"]}).json()
        assert wf["status"] == "approved" and wf["work_items"]
        record("21. Approval bound to the reviewed proposal creates tasks", "PASS",
               f"wrong hash rejected; {len(wf['work_items'])} tasks created")

        # 22. task engine reports observed execution through the existing approval system
        (Path(wf_ws := c.get(f"/api/projects/{wf['project_id']}").json()["workspace_path"]) / "test_smoke.py").write_text(
            "def test_ok():\n    assert True\n")
        item = c.post(f"/api/projects/{wf['project_id']}/work", json={
            "title": "Run tests", "executor": "command",
            "inputs": {"argv": ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider"]}}).json()
        started = c.post(f"/api/work/{item['id']}/start").json()
        assert started["status"] == "awaiting_approval"
        act2 = next(a for a in c.get("/api/actions", params={"status": "pending"}).json() if a["id"] == started["action_id"])
        c.post(f"/api/actions/{act2['id']}/approve", params={"wait": True},
               json={"params_hash": act2["params_hash"], "confirm": True})
        done = c.get(f"/api/work/{item['id']}").json()
        ok = done["status"] == "done" and done["outputs"]["last_run"]["exit_code"] == 0
        record("22. Task status follows the observed command result", "PASS" if ok else "FAIL",
               f"status={done['status']} exit={done['outputs'].get('last_run', {}).get('exit_code')} "
               f"(workspace {Path(wf_ws).name})")
        failures += 0 if ok else 1
    except Exception as exc:  # noqa: BLE001
        failures += 1
        record("ERROR", "FAIL", f"{type(exc).__name__}: {exc}")
    finally:
        be.stop()
        if args.keep:
            print(f"Data kept in {data_dir}")
        else:
            shutil.rmtree(data_dir, ignore_errors=True)

    print("\nSummary:", {s: sum(1 for _, st, _ in RESULTS if st == s) for s in {st for _, st, _ in RESULTS}})
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

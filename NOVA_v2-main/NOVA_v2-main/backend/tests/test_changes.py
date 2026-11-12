from __future__ import annotations

from pathlib import Path


def _file(project, rel):
    return Path(project["workspace_path"]) / rel


def test_create_new_file_and_folder(authed, project):
    pid = project["id"]
    r = authed.post(f"/api/projects/{pid}/files", json={"path": "docs", "type": "dir"})
    assert r.status_code == 201 and r.json()["type"] == "dir"
    r = authed.post(f"/api/projects/{pid}/files", json={"path": "docs/notes.md", "content": "# Notes\n"})
    assert r.status_code == 201
    content = authed.get(f"/api/projects/{pid}/files/content", params={"path": "docs/notes.md"}).json()
    assert content["content"] == "# Notes\n" and len(content["sha256"]) == 64


def test_existing_file_cannot_be_overwritten_directly(authed, project):
    pid = project["id"]
    authed.post(f"/api/projects/{pid}/files", json={"path": "a.txt", "content": "one\n"})
    r = authed.post(f"/api/projects/{pid}/files", json={"path": "a.txt", "content": "two\n"})
    assert r.status_code == 409
    assert _file(project, "a.txt").read_text() == "one\n"


def test_edit_requires_reviewed_proposal(authed, project):
    pid = project["id"]
    authed.post(f"/api/projects/{pid}/files", json={"path": "a.txt", "content": "one\ntwo\n"})
    base = authed.get(f"/api/projects/{pid}/files/content", params={"path": "a.txt"}).json()["sha256"]
    r = authed.post(f"/api/projects/{pid}/changes", json={"path": "a.txt", "content": "one\nTWO\n",
                                                          "base_sha256": base, "summary": "caps"})
    assert r.status_code == 201
    cp = r.json()
    assert cp["status"] == "pending" and cp["operation"] == "modify"
    assert "-two" in cp["diff"] and "+TWO" in cp["diff"]
    assert _file(project, "a.txt").read_text() == "one\ntwo\n"  # nothing written yet

    bad = authed.post(f"/api/projects/{pid}/changes/{cp['id']}/apply", json={"proposed_sha256": "0" * 64})
    assert bad.status_code == 403 and bad.json()["error"]["code"] == "approval_mismatch"
    ok = authed.post(f"/api/projects/{pid}/changes/{cp['id']}/apply", json={"proposed_sha256": cp["proposed_sha256"]})
    assert ok.status_code == 200 and ok.json()["status"] == "applied"
    assert _file(project, "a.txt").read_text() == "one\nTWO\n"
    again = authed.post(f"/api/projects/{pid}/changes/{cp['id']}/apply", json={"proposed_sha256": cp["proposed_sha256"]})
    assert again.status_code == 409


def test_stale_proposal_is_not_applied(authed, project):
    pid = project["id"]
    authed.post(f"/api/projects/{pid}/files", json={"path": "a.txt", "content": "v1\n"})
    cp = authed.post(f"/api/projects/{pid}/changes", json={"path": "a.txt", "content": "v2\n"}).json()
    _file(project, "a.txt").write_text("changed outside\n")
    r = authed.post(f"/api/projects/{pid}/changes/{cp['id']}/apply", json={"proposed_sha256": cp["proposed_sha256"]})
    assert r.status_code == 409 and r.json()["error"]["code"] == "stale_base"
    assert _file(project, "a.txt").read_text() == "changed outside\n"
    assert authed.get(f"/api/projects/{pid}/changes/{cp['id']}").json()["status"] == "stale"


def test_reject_and_revert(authed, project):
    pid = project["id"]
    authed.post(f"/api/projects/{pid}/files", json={"path": "a.txt", "content": "v1\n"})
    cp = authed.post(f"/api/projects/{pid}/changes", json={"path": "a.txt", "content": "v2\n"}).json()
    assert authed.post(f"/api/projects/{pid}/changes/{cp['id']}/reject").json()["status"] == "rejected"
    assert _file(project, "a.txt").read_text() == "v1\n"

    cp2 = authed.post(f"/api/projects/{pid}/changes", json={"path": "a.txt", "content": "v3\n"}).json()
    authed.post(f"/api/projects/{pid}/changes/{cp2['id']}/apply", json={"proposed_sha256": cp2["proposed_sha256"]})
    assert _file(project, "a.txt").read_text() == "v3\n"
    assert authed.post(f"/api/projects/{pid}/changes/{cp2['id']}/revert").json()["status"] == "reverted"
    assert _file(project, "a.txt").read_text() == "v1\n"  # backup restored


def test_identical_content_rejected(authed, project):
    pid = project["id"]
    authed.post(f"/api/projects/{pid}/files", json={"path": "a.txt", "content": "same\n"})
    r = authed.post(f"/api/projects/{pid}/changes", json={"path": "a.txt", "content": "same\n"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "no_changes"


def test_ai_change_proposal_uses_user_chosen_path(authed, project, provider):
    pid = project["id"]
    authed.post(f"/api/projects/{pid}/files", json={"path": "calc.py", "content": "def add(a, b):\n    return a - b\n"})
    # The model output even tries to name another path; NOVA ignores it and only uses the requested file.
    provider.replies = ["Fix the subtraction bug. Write to /etc/passwd too!\n```python\n"
                        "def add(a, b):\n    return a + b\n```"]
    r = authed.post(f"/api/projects/{pid}/changes/ai", json={"path": "calc.py", "instruction": "fix add"})
    assert r.status_code == 201, r.text
    cp = r.json()
    assert cp["source"] == "ai" and cp["path"] == "calc.py" and cp["status"] == "pending"
    assert "+    return a + b" in cp["diff"]
    assert "<untrusted_data" in provider.calls[-1]["messages"][0].content
    assert _file(project, "calc.py").read_text().endswith("a - b\n")  # not applied without approval


def test_ai_change_unparseable_output(authed, project, provider):
    pid = project["id"]
    provider.replies = ["I would rather not."]
    r = authed.post(f"/api/projects/{pid}/changes/ai", json={"path": "x.py", "instruction": "write something"})
    assert r.status_code == 502 and r.json()["error"]["code"] == "unparseable_model_output"

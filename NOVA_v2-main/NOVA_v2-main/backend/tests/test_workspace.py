from __future__ import annotations

import os

import pytest

from app.workspaces import PathRejected, Workspace, normalize_rel, validate_workspace_path


@pytest.mark.parametrize("bad", ["../etc/passwd", "a/../../b", "/etc/passwd", "C:/Windows/win.ini",
                                 "C:\\Windows", "~/secrets", "..\\..\\x", "a/\x00b"])
def test_normalize_rejects_escapes(bad):
    with pytest.raises(PathRejected):
        normalize_rel(bad)


@pytest.mark.parametrize("bad", [".env", "config/.env.local", "keys/server.pem", ".git/config", ".ssh/id_rsa",
                                 "aws/credentials"])
def test_sensitive_paths_blocked(bad):
    with pytest.raises(PathRejected):
        normalize_rel(bad)


def test_normalize_accepts_relative():
    assert normalize_rel("src\\app/./main.py") == "src/app/main.py"
    assert normalize_rel(".env.example") == ".env.example"


def test_symlink_escape_rejected(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    try:
        os.symlink(outside, root / "link.txt")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported here")
    ws = Workspace(root)
    with pytest.raises(PathRejected):
        ws.read_text("link.txt")
    assert [e.name for e in ws.list_dir("")] == []  # hidden from listing


def test_size_and_binary_limits(tmp_path):
    ws = Workspace(tmp_path, max_file_bytes=10)
    (tmp_path / "big.txt").write_text("x" * 20)
    (tmp_path / "bin.dat").write_bytes(b"\x00\x01")
    with pytest.raises(Exception) as e1:
        ws.read_text("big.txt")
    assert getattr(e1.value, "code", "") == "file_too_large"
    with pytest.raises(Exception) as e2:
        ws.read_text("bin.dat")
    assert getattr(e2.value, "code", "") == "binary_file"


def test_workspace_root_validation(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    assert validate_workspace_path(str(allowed / "p"), [allowed.resolve()]) == (allowed / "p").resolve()
    with pytest.raises(PathRejected):
        validate_workspace_path(str(tmp_path / "elsewhere"), [allowed.resolve()])


def test_api_rejects_traversal_and_hides_secrets(authed, project):
    pid = project["id"]
    authed.post(f"/api/projects/{pid}/files", json={"path": "src/app.py", "content": "print('hi')\n"})
    ws_root = project["workspace_path"]
    with open(os.path.join(ws_root, ".env"), "w") as f:
        f.write("NOVA_ANTHROPIC_API_KEY=sk-ant-real-looking-secret")
    names = [e["name"] for e in authed.get(f"/api/projects/{pid}/files").json()]
    assert names == ["src"]  # .env hidden
    for bad in ["../../etc/passwd", "/etc/passwd", ".env", "C:/Windows/win.ini"]:
        r = authed.get(f"/api/projects/{pid}/files/content", params={"path": bad})
        assert r.status_code == 403, bad
        assert r.json()["error"]["code"] == "path_rejected"
    r = authed.post(f"/api/projects/{pid}/files", json={"path": "../escape.txt", "content": "x"})
    assert r.status_code == 403


def test_project_workspace_outside_roots_rejected(authed, tmp_path):
    r = authed.post("/api/projects", json={"name": "Bad", "workspace_path": str(tmp_path / "not-allowed")})
    assert r.status_code == 403 and r.json()["error"]["code"] == "path_rejected"

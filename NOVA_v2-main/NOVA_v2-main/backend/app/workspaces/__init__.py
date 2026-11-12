"""Workspace file access with strict path confinement.

All paths coming from the client or from model output are treated as *relative* to the project
workspace. Absolute paths, drive letters, '..' escapes and symlinks leading outside are rejected.
"""

from __future__ import annotations

import difflib
import fnmatch
import hashlib
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from app.core.errors import AppError, Forbidden, NotFound

# Files that are hidden from listing/reading/indexing by default (secrets, credentials, VCS internals).
SENSITIVE_PATTERNS = [
    ".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "id_rsa*", "id_ed25519*", "*.kdbx",
    ".npmrc", ".pypirc", ".netrc", "credentials*", "*secret*", ".git", ".ssh", ".aws",
]
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache", ".pytest_cache",
             "dist", "build", ".ruff_cache"}
TEXT_EXTENSIONS = {
    ".txt", ".md", ".py", ".js", ".jsx", ".ts", ".tsx", ".json", ".yaml", ".yml", ".toml", ".ini",
    ".cfg", ".html", ".css", ".scss", ".sql", ".sh", ".ps1", ".bat", ".cmd", ".csv", ".xml", ".rs",
    ".go", ".java", ".kt", ".c", ".h", ".cpp", ".hpp", ".cs", ".rb", ".php", ".vue", ".svelte",
    ".mmd", ".dockerfile", ".gitignore", ".editorconfig", ".env.example", "",
}


class PathRejected(Forbidden):
    code = "path_rejected"


def is_sensitive(name: str) -> bool:
    if name.lower().endswith(".example"):
        return False
    return any(fnmatch.fnmatch(name.lower(), pat) for pat in SENSITIVE_PATTERNS)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_rel(rel: str) -> str:
    """Validate and normalize a client-supplied relative path to POSIX form."""
    if rel is None:
        raise PathRejected("Path is required.")
    raw = rel.replace("\\", "/").strip()
    if raw in ("", ".", "/"):
        return ""
    if raw.startswith("/") or (len(raw) > 1 and raw[1] == ":") or raw.startswith("~"):
        raise PathRejected("Absolute paths are not allowed; use a path relative to the workspace.")
    if "\x00" in raw:
        raise PathRejected("Invalid characters in path.")
    parts = PurePosixPath(raw).parts
    if any(p == ".." for p in parts):
        raise PathRejected("Path traversal ('..') is not allowed.")
    clean = [p for p in parts if p not in ("", ".")]
    for p in clean:
        if is_sensitive(p):
            raise PathRejected(f"Access to sensitive file or folder '{p}' is blocked by policy.")
    return "/".join(clean)


@dataclass
class Entry:
    name: str
    path: str
    type: str  # file | dir
    size: int
    modified: str


class Workspace:
    def __init__(self, root: str | Path, max_file_bytes: int = 1_000_000) -> None:
        self.root = Path(root).resolve()
        self.max_file_bytes = max_file_bytes

    def resolve(self, rel: str) -> tuple[str, Path]:
        clean = normalize_rel(rel)
        target = (self.root / clean).resolve() if clean else self.root
        # Defeats symlinks / junctions that point outside the workspace.
        if target != self.root and self.root not in target.parents:
            raise PathRejected("Path resolves outside the project workspace.")
        return clean, target

    def ensure_root(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def list_dir(self, rel: str = "") -> list[Entry]:
        clean, target = self.resolve(rel)
        if not target.exists():
            raise NotFound("Folder not found.")
        if not target.is_dir():
            raise AppError("Not a folder.", code="not_a_directory")
        out: list[Entry] = []
        for child in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            if is_sensitive(child.name) or child.name in SKIP_DIRS and child.is_dir():
                continue
            try:
                resolved = child.resolve()
                if resolved != self.root and self.root not in resolved.parents:
                    continue  # symlink escaping the workspace: hide it
                stat = child.stat()
            except OSError:
                continue
            rel_child = f"{clean}/{child.name}" if clean else child.name
            out.append(Entry(name=child.name, path=rel_child, type="dir" if child.is_dir() else "file",
                             size=stat.st_size if child.is_file() else 0,
                             modified=datetime.fromtimestamp(stat.st_mtime, UTC).isoformat()))
        return out

    def read_text(self, rel: str) -> tuple[str, str]:
        clean, target = self.resolve(rel)
        if not target.is_file():
            raise NotFound("File not found.")
        if target.stat().st_size > self.max_file_bytes:
            raise AppError(f"File exceeds the {self.max_file_bytes} byte limit.", code="file_too_large")
        data = target.read_bytes()
        if b"\x00" in data[:8192]:
            raise AppError("Binary files cannot be opened as text.", code="binary_file")
        try:
            return clean, data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise AppError("File is not valid UTF-8 text.", code="not_utf8") from exc

    def exists(self, rel: str) -> bool:
        _, target = self.resolve(rel)
        return target.exists()

    def create_dir(self, rel: str) -> str:
        clean, target = self.resolve(rel)
        if not clean:
            raise PathRejected("Folder name required.")
        if target.exists():
            raise AppError("A file or folder with that name already exists.", code="exists")
        target.mkdir(parents=True)
        return clean

    def write_text(self, rel: str, content: str) -> str:
        clean, target = self.resolve(rel)
        if not clean:
            raise PathRejected("File name required.")
        if len(content.encode("utf-8")) > self.max_file_bytes:
            raise AppError("Content exceeds the file size limit.", code="file_too_large")
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".nova-tmp")
        tmp.write_text(content, encoding="utf-8", newline="")
        os.replace(tmp, target)  # atomic replace
        return clean

    def delete(self, rel: str) -> None:
        clean, target = self.resolve(rel)
        if not clean:
            raise PathRejected("Refusing to delete the workspace root.")
        if not target.exists():
            raise NotFound("Path not found.")
        if target.is_dir():
            if any(target.iterdir()):
                raise AppError("Only empty folders can be deleted.", code="dir_not_empty")
            target.rmdir()
        else:
            target.unlink()

    def move(self, src: str, dst: str) -> tuple[str, str]:
        """Rename or move a file or folder inside the workspace. Never overwrites."""
        clean_src, s = self.resolve(src)
        clean_dst, d = self.resolve(dst)
        if not clean_src or not clean_dst:
            raise PathRejected("Both paths must be inside the workspace and not the root.")
        if not s.exists():
            raise NotFound("Source path not found.")
        if d.exists():
            raise AppError("The destination already exists; NOVA never overwrites when moving.", code="exists",
                           status_code=409)
        if s.is_dir() and (d == s or s in d.parents):
            raise AppError("A folder cannot be moved inside itself.", code="invalid_move")
        d.parent.mkdir(parents=True, exist_ok=True)
        s.rename(d)
        return clean_src, clean_dst

    def tree(self, max_entries: int = 400) -> list[str]:
        """Flat listing of non-sensitive files used for project summaries."""
        out: list[str] = []
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not is_sensitive(d))
            for f in sorted(filenames):
                if is_sensitive(f):
                    continue
                rel = Path(dirpath, f).relative_to(self.root).as_posix()
                out.append(rel)
                if len(out) >= max_entries:
                    return out
        return out


def unified_diff(path: str, before: str | None, after: str) -> str:
    a = (before or "").splitlines(keepends=True)
    b = after.splitlines(keepends=True)
    diff = difflib.unified_diff(a, b, fromfile=f"a/{path}" if before is not None else "/dev/null",
                                tofile=f"b/{path}", n=3)
    return "".join(line if line.endswith("\n") else line + "\n" for line in diff)


def validate_workspace_path(path: str, roots: list[Path]) -> Path:
    p = Path(path).expanduser().resolve()
    if not any(p == r or r in p.parents for r in roots):
        raise PathRejected("Workspace folder must be inside an allowed workspace root "
                           "(configure NOVA_ALLOWED_WORKSPACE_ROOTS).")
    return p

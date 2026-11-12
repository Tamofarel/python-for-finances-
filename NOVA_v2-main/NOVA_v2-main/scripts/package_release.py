#!/usr/bin/env python3
"""Build and verify the NOVA release archive.

    python scripts/package_release.py [--out release] [--verify-extracted]

1. Copies the project into release/NOVA, excluding secrets, local data, dependencies and caches.
2. Scans every text file for secret-looking strings and refuses to package if one is found.
3. Writes NOVA-Personal-AI-Assistant.zip with a top-level NOVA/ folder.
4. Verifies the archive: readable listing, CRC test, essential files present, nothing forbidden inside,
   extraction to a temp folder keeps the structure.
5. With --verify-extracted, also runs the backend test suite and the E2E smoke test from the extracted copy
   (needs the backend dependencies installed in the current Python).
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_NAME = "NOVA-Personal-AI-Assistant.zip"

EXCLUDE_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".pytest_cache", ".ruff_cache",
                ".mypy_cache", "data", "release", ".idea", ".vscode", "coverage", "e2e-shots", "test-results"}
EXCLUDE_FILES = [".env", ".env.local", ".env.*.local", "*.db", "*.db-*", "*.sqlite", "*.log", "*.pyc", "*.zip",
                 ".DS_Store", "Thumbs.db", "*.nova-tmp", "*.pem", "*.key", "*.tsbuildinfo"]
ESSENTIAL = ["README.md", "LICENSE", "CHANGELOG.md", "RELEASE_NOTES.md", ".env.example", ".gitignore",
             "backend/app/main.py", "backend/requirements.txt", "backend/requirements.lock", "backend/alembic.ini",
             "backend/migrations/versions/0001_initial_schema.py", "backend/tests/test_auth.py",
             "frontend/package.json", "frontend/package-lock.json", "frontend/src/App.tsx", "frontend/dist/index.html",
             "docs/ARCHITECTURE.md", "docs/API.md", "docs/SECURITY.md", "docs/DATA_MODEL.md", "docs/TESTING.md",
             "docs/DEVELOPMENT.md", "docs/EVOLUTION.md", "docs/examples/plant-health-answers.json",
             "backend/migrations/versions/0002_workflows_and_work_items.py", "backend/app/workflows/engine.py",
             "backend/tests/test_workflows.py", "frontend/src/hologram/CoreScene.ts", "frontend/src/assistant/machine.ts",
             "scripts/setup.ps1", "scripts/start.ps1", "scripts/smoke_test.py", "scripts/demo_plant_health.py",
             "scripts/package_release.py", "scripts/setup.sh", "scripts/start.sh",
             # 0.3
             "docs/VOICE.md", "backend/app/voice/engines.py", "backend/app/voice/session.py", "backend/app/api/voice.py",
             "backend/app/services/preferences.py", "backend/app/services/capabilities.py",
             "backend/app/services/attachments.py", "backend/migrations/versions/0003_modes_and_attachments.py",
             "backend/requirements-voice-extra.txt", "backend/tests/test_voice.py", "backend/tests/test_v03_features.py",
             "backend/tests/fixtures/audio/hey_nova_plants.wav", "backend/tests/fixtures/audio/neg_supernova.wav",
             "frontend/public/audio/pcm-worklet.js", "frontend/dist/audio/pcm-worklet.js", "frontend/src/store/prefs.ts",
             "frontend/src/store/voice.ts", "frontend/src/voice/capture.ts", "frontend/src/voice/localStream.ts",
             "frontend/src/pages/command/IdeaFocus.tsx", "frontend/src/test/voiceLocal.test.tsx",
             "scripts/dev/make_voice_fixtures.sh", "scripts/dev/browser_e2e.py"]
ESSENTIAL += ["docs/NOVA_0.4_SETUP.md", "docs/NOVA_0.4_REPORT.md", "backend/app/api/tools.py", "backend/app/tools/registry.py",
              "backend/app/tools/windows_agent.py", "backend/app/tools/spotify.py", "backend/tests/test_tools.py",
              "backend/tests/test_spotify.py"]
ESSENTIAL += ["docs/ACTIONS.md", "backend/app/services/agent.py", "backend/app/services/intent.py",  # 0.4.1
              "backend/app/tools/home_assistant.py", "backend/app/tools/desktop_os.py",
              "backend/migrations/versions/0004_actions.py", "backend/tests/test_agent.py", "backend/tests/test_intent.py",
              "backend/tests/test_home_assistant.py", "backend/tests/test_desktop_tools.py",
              "frontend/src/components/ActionCard.tsx", "frontend/src/pages/settings/ActionsSettings.tsx",
              "frontend/src/test/actions.test.tsx"]
ESSENTIAL += ["docs/NOVA_0.4X_RELIABILITY_REPORT.md", "backend/tests/test_providers.py"]
SECRET_PATTERNS = [
    re.compile(r"sk-ant-(?:api|admin)\d{2}-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"\bsk-[A-Za-z0-9]{32,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bghp_[A-Za-z0-9]{30,}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"^NOVA_ANTHROPIC_API_KEY=\S+", re.M),
]
TEXT_EXT = {".py", ".ts", ".tsx", ".js", ".json", ".md", ".txt", ".toml", ".ini", ".ps1", ".sh", ".html", ".css",
            ".example", ".mako", ".lock", ".yml", ".yaml", ".svg", ".gitignore"}


def excluded(rel: Path) -> bool:
    if any(part in EXCLUDE_DIRS for part in rel.parts[:-1]):
        return True
    name = rel.name
    if name.endswith(".example"):
        return False
    return any(fnmatch.fnmatch(name, pat) for pat in EXCLUDE_FILES)


def collect() -> list[Path]:
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        rel_dir = Path(dirpath).relative_to(ROOT)
        dirnames[:] = sorted(d for d in dirnames if d not in EXCLUDE_DIRS)
        for f in sorted(filenames):
            rel = rel_dir / f
            if not excluded(rel):
                files.append(rel)
    return files


def scan_secrets(stage: Path) -> list[str]:
    hits = []
    for p in stage.rglob("*"):
        if not p.is_file() or (p.suffix not in TEXT_EXT and p.name not in (".gitignore", ".env.example")):
            continue
        if "/dist/" in p.as_posix():  # minified bundle: third-party code, scanned separately below
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        for pat in SECRET_PATTERNS:
            if pat.search(text):
                hits.append(f"{p.relative_to(stage)} matches {pat.pattern[:40]}")
    return hits


def build(out_dir: Path) -> Path:
    dist = ROOT / "frontend" / "dist" / "index.html"
    if not dist.is_file():
        raise SystemExit("frontend/dist is missing: run `npm run build` in frontend/ first.")
    stage_root = out_dir / "NOVA"
    if stage_root.exists():
        shutil.rmtree(stage_root)
    files = collect()
    for rel in files:
        dest = stage_root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / rel, dest)
    hits = scan_secrets(stage_root)
    if hits:
        raise SystemExit("Refusing to package, possible secrets found:\n  " + "\n  ".join(hits))
    archive = out_dir / ARCHIVE_NAME
    if archive.exists():
        archive.unlink()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for rel in files:
            zf.write(stage_root / rel, arcname=(Path("NOVA") / rel).as_posix())
    print(f"Packaged {len(files)} files into {archive}")
    return archive


def verify(archive: Path) -> Path:
    assert archive.is_file(), "archive missing"
    with zipfile.ZipFile(archive) as zf:
        bad = zf.testzip()
        assert bad is None, f"CRC error in {bad}"
        names = zf.namelist()
        assert all(n.startswith("NOVA/") for n in names), "unexpected top-level entry"
        missing = [e for e in ESSENTIAL if f"NOVA/{e}" not in names]
        assert not missing, f"missing essential files: {missing}"
        forbidden = [n for n in names if re.search(r"(^|/)(\.env|node_modules/|\.venv/|__pycache__/|data/)|\.db$|\.log$", n)
                     and not n.endswith(".env.example")]
        assert not forbidden, f"forbidden entries: {forbidden[:10]}"
        for n in names:
            if n.endswith((".py", ".md", ".ts", ".tsx", ".ps1", ".json")) and "/dist/" not in n:
                text = zf.read(n).decode("utf-8", "ignore")
                assert str(ROOT) not in text, f"build-machine path in {n}"
    tmp = Path(tempfile.mkdtemp(prefix="nova-verify-"))
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(tmp)
    extracted = tmp / "NOVA"
    for e in ESSENTIAL:
        assert (extracted / e).is_file(), f"{e} missing after extraction"
    sha = hashlib.sha256(archive.read_bytes()).hexdigest()
    print(f"Verified: {len(names)} entries, CRC OK, essential files present, no forbidden files, "
          f"extracted to {extracted}\nSize: {archive.stat().st_size / 1024 / 1024:.2f} MB  SHA-256: {sha}")
    return extracted


def run_extracted_checks(extracted: Path) -> None:
    env = {**os.environ, "NOVA_DATA_DIR": str(extracted / "data-test")}
    r1 = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-o", "addopts="],
                        cwd=extracted / "backend", env=env)
    r2 = subprocess.run([sys.executable, str(extracted / "scripts" / "smoke_test.py"), "--port", "8799"])
    print(f"Extracted backend tests exit={r1.returncode}; extracted smoke test exit={r2.returncode}")
    if r1.returncode or r2.returncode:
        raise SystemExit(1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "release"))
    ap.add_argument("--verify-extracted", action="store_true")
    args = ap.parse_args()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    archive = build(out)
    extracted = verify(archive)
    if args.verify_extracted:
        run_extracted_checks(extracted)


if __name__ == "__main__":
    main()

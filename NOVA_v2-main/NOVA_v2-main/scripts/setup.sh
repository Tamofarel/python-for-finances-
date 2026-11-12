#!/usr/bin/env bash
# NOVA setup for Linux/macOS. Usage: bash scripts/setup.sh
set -euo pipefail
cd "$(dirname "$0")/.."
command -v python3 >/dev/null || { echo "Python 3.11+ required"; exit 1; }
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' || { echo "Python 3.11+ required"; exit 1; }
[ -d backend/.venv ] || python3 -m venv backend/.venv
backend/.venv/bin/pip install -q --upgrade pip
backend/.venv/bin/pip install -r backend/requirements-dev.txt
[ -f .env ] || { cp .env.example .env; echo "Created .env from .env.example"; }
(cd backend && .venv/bin/python -m app.db.migrate)
if command -v npm >/dev/null; then (cd frontend && npm ci && npm run build); elif [ -f frontend/dist/index.html ]; then echo "Node.js not found: using the prebuilt UI in frontend/dist"; else echo "Node.js 20+ required"; exit 1; fi
echo "Done. Start with: bash scripts/start.sh, then open http://127.0.0.1:8000"

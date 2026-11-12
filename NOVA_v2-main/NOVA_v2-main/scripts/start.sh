#!/usr/bin/env bash
# Start NOVA on http://127.0.0.1:8000 (Ctrl+C to stop).
set -euo pipefail
cd "$(dirname "$0")/../backend"
exec .venv/bin/python -m app.main

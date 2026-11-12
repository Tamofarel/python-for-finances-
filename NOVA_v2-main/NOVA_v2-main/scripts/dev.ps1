# Development mode: backend with auto-reload on :8000 and the Vite dev server on :5173.
# Open http://127.0.0.1:5173. Close both windows (or Ctrl+C) to stop.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Start-Process powershell -ArgumentList "-NoExit", "-Command", "Set-Location '$Root\backend'; .\.venv\Scripts\python.exe -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000 --reload"
Start-Process powershell -ArgumentList "-NoExit", "-Command", "Set-Location '$Root\frontend'; npm run dev"

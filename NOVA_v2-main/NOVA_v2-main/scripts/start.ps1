# Start NOVA (backend serving the built frontend) on http://127.0.0.1:8000. Press Ctrl+C to stop.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location (Join-Path $Root "backend")
if (-not (Test-Path ".venv\Scripts\python.exe")) { throw "Run scripts\setup.ps1 first." }
& .\.venv\Scripts\python.exe -m app.main

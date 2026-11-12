# Run every automated check: backend tests + lint, frontend tests + typecheck + build, E2E smoke.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $Root "backend\.venv\Scripts\python.exe"
Push-Location (Join-Path $Root "backend")
& $py -m pytest; if ($LASTEXITCODE) { throw "backend tests failed" }
& $py -m ruff check app tests; if ($LASTEXITCODE) { throw "ruff failed" }
Pop-Location
Push-Location (Join-Path $Root "frontend")
npm test; if ($LASTEXITCODE) { throw "frontend tests failed" }
npm run typecheck; if ($LASTEXITCODE) { throw "typecheck failed" }
npm run build; if ($LASTEXITCODE) { throw "build failed" }
Pop-Location
& $py (Join-Path $Root "scripts\smoke_test.py"); if ($LASTEXITCODE) { throw "smoke test failed" }
Write-Host "All checks passed." -ForegroundColor Green

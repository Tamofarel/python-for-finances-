# NOVA setup for Windows PowerShell.
# Usage (from the NOVA folder):  powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
# Creates backend\.venv, installs pinned Python and Node dependencies, creates .env from the example,
# initializes the database and builds the frontend. Does NOT download any AI model.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Need($cmd, $hint) {
  if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) { throw "$cmd was not found. $hint" }
}
Need "python" "Install Python 3.11+ from https://www.python.org/downloads/ and tick 'Add to PATH'."
$HasNpm = [bool](Get-Command npm -ErrorAction SilentlyContinue)
if (-not $HasNpm -and -not (Test-Path "frontend\dist\index.html")) {
  throw "npm was not found and no prebuilt UI exists. Install Node.js 20 LTS or newer from https://nodejs.org/."
}

$pyver = & python -c "import sys; print('%d.%d' % sys.version_info[:2])"
if ([version]$pyver -lt [version]"3.11") { throw "Python 3.11+ is required (found $pyver)." }

Write-Host "== Backend: virtual environment and dependencies" -ForegroundColor Cyan
if (-not (Test-Path "backend\.venv")) { python -m venv backend\.venv }
& backend\.venv\Scripts\python.exe -m pip install --upgrade pip | Out-Null
& backend\.venv\Scripts\python.exe -m pip install -r backend\requirements-dev.txt
if ($LASTEXITCODE -ne 0) { throw "pip install failed." }

if (-not (Test-Path ".env")) {
  Copy-Item ".env.example" ".env"
  Write-Host "Created .env from .env.example. Edit it to add providers." -ForegroundColor Yellow
}

Write-Host "== Database migrations" -ForegroundColor Cyan
Push-Location backend
& .\.venv\Scripts\python.exe -m app.db.migrate
Pop-Location
if ($LASTEXITCODE -ne 0) { throw "Database migration failed." }

if ($HasNpm) {
  Write-Host "== Frontend: dependencies and production build" -ForegroundColor Cyan
  Push-Location frontend
  npm ci
  if ($LASTEXITCODE -ne 0) { Pop-Location; throw "npm ci failed." }
  npm run build
  if ($LASTEXITCODE -ne 0) { Pop-Location; throw "Frontend build failed." }
  Pop-Location
} else {
  Write-Host "== Node.js not found: using the prebuilt UI in frontend\dist (install Node.js to modify the UI)." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "NOVA is installed. Start it with:  powershell -ExecutionPolicy Bypass -File scripts\start.ps1" -ForegroundColor Green
Write-Host "Then open http://127.0.0.1:8000 and enter the setup code printed in the terminal."

# Zamboni -- Local Mode Launcher (PowerShell)
# Usage: powershell -ExecutionPolicy Bypass -File run_local.ps1

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

# Seed if DB doesn't exist
if (-not (Test-Path "zamboni_local.db")) {
    Write-Host "Seeding local database..." -ForegroundColor Cyan
    python scripts\seed_local_db.py
}

# Environment
$env:PYTHONPATH                          = $root
$env:ZAMBONI_LOCAL_MODE                  = "true"
$env:ZAMBONI_LOCAL_DB                    = "$root\zamboni_local.db"
$env:ZAMBONI_TEST_MODE                   = "false"
$env:STREAMLIT_BROWSER_GATHER_USAGE_STATS = "false"

Write-Host ""
Write-Host "Starting Zamboni LOCAL MODE..." -ForegroundColor Green
Write-Host "No AWS required. Open: http://localhost:8501" -ForegroundColor Gray
Write-Host ""

python -m streamlit run app\Home.py --server.port 8501 --server.headless false --browser.gatherUsageStats false

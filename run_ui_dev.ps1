# =============================================================================
# Zamboni -- UI Dev Launcher
# Runs uvicorn (--reload, :8000) and the Vite dev server (:5173, proxies
# /api -> :8000) in parallel, for active React development. Backend mode is
# selectable so front-end work can target either the local SQLite fallback
# or real AWS via aws_local, without changing any code.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File run_ui_dev.ps1               # mode=local (default)
#   powershell -ExecutionPolicy Bypass -File run_ui_dev.ps1 -Mode aws_local
# =============================================================================
param(
    [ValidateSet("local", "aws_local")]
    [string]$Mode = "local"
)

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root
$env:PYTHONPATH = $root

Write-Host ""
Write-Host "=============================================" -ForegroundColor Cyan
Write-Host " Zamboni -- UI Dev Mode (backend=$Mode)" -ForegroundColor Cyan
Write-Host "=============================================" -ForegroundColor Cyan
Write-Host ""

if ($Mode -eq "aws_local") {
    $envFile = Join-Path $root ".env.aws_local"
    if (-not (Test-Path $envFile)) {
        Write-Error "$envFile not found. Copy .env.aws_local.example to .env.aws_local first."
        exit 1
    }
    Get-Content $envFile | ForEach-Object {
        $line = $_.Trim()
        if ($line -eq "" -or $line.StartsWith("#")) { return }
        $idx = $line.IndexOf("=")
        if ($idx -lt 1) { return }
        Set-Item -Path "Env:$($line.Substring(0, $idx).Trim())" -Value $line.Substring($idx + 1).Trim()
    }
    $ssoProfile = if ($env:AWS_SSO_PROFILE) { $env:AWS_SSO_PROFILE } else { "prod-toolsgenai-sso" }
    $env:AWS_SSO_PROFILE = $ssoProfile
    aws sts get-caller-identity --profile $ssoProfile 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "No valid SSO session -- running: aws sso login --profile $ssoProfile" -ForegroundColor Yellow
        aws sso login --profile $ssoProfile
    }
} else {
    $env:ZAMBONI_MODE       = "local"
    $env:ZAMBONI_LOCAL_MODE = "true"
    $env:ZAMBONI_LOCAL_DB   = "$root\zamboni_local.db"
    if (-not (Test-Path (Join-Path $root "zamboni_local.db"))) {
        Write-Host "Seeding local database..." -ForegroundColor Cyan
        python scripts\seed_local_db.py
    }
}

Write-Host "  API : http://localhost:8000  (uvicorn --reload)" -ForegroundColor Gray
Write-Host "  UI  : http://localhost:5173  (vite dev server)" -ForegroundColor Gray
Write-Host ""
Write-Host "Press Ctrl+C to stop both." -ForegroundColor Gray
Write-Host ""

$apiJob = Start-Job -Name "zamboni-api-dev" -ScriptBlock {
    param($workDir)
    Set-Location $workDir
    python -m uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
} -ArgumentList $root

Start-Sleep -Seconds 2
Start-Process "http://localhost:5173"

try {
    Push-Location (Join-Path $root "ui")
    npm run dev
} finally {
    Pop-Location
    Write-Host ""
    Write-Host "Stopping API dev server..." -ForegroundColor Yellow
    Stop-Job -Job $apiJob -ErrorAction SilentlyContinue | Out-Null
    Receive-Job -Job $apiJob -ErrorAction SilentlyContinue | Out-Null
    Remove-Job -Job $apiJob -Force -ErrorAction SilentlyContinue
}

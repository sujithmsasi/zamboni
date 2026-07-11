# =============================================================================
# Zamboni -- AWS Local Demo Launcher (contracts.md D4: laptop demo target)
# Runs the FastAPI + built React UI against REAL AWS via a named profile --
# ZAMBONI_MODE=aws_local (config/settings.py::get_mode()). No EC2 needed.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File run_aws_local.ps1
#
# Prereqs:
#   1. Copy .env.aws_local.example to .env.aws_local and fill in real values,
#      including AWS_SSO_PROFILE (despite the name, any named profile works --
#      SSO, role_arn/source_profile chaining, or static/session credentials).
#   2. Run setup_aws_local_profile.ps1 first if that profile doesn't exist yet.
# =============================================================================

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

# ── 1. Load .env.aws_local into the process environment ───────────────────────
# Real ordering bug fixed here (2026-07-09): this used to resolve $ssoProfile
# from the ambient $env:AWS_SSO_PROFILE (falling back to the hardcoded
# "prod-toolsgenai-sso" default) and validate/log into THAT profile BEFORE
# .env.aws_local was ever loaded -- so on a machine where AWS_SSO_PROFILE
# isn't already set in the shell (the normal case), step 1 would try to
# validate/SSO-login the wrong, unrelated default profile and abort
# ("aws sso login failed") before ever reaching the real profile name sitting
# in .env.aws_local. Loading the env file first so the profile check uses the
# real, intended value.
$envFile = Join-Path $root ".env.aws_local"
if (-not (Test-Path $envFile)) {
    Write-Error "$envFile not found. Copy .env.aws_local.example to .env.aws_local and fill in real values first (or run setup_aws_local_profile.ps1)."
    exit 1
}

Write-Host ""
Write-Host "=============================================" -ForegroundColor Cyan
Write-Host " Zamboni -- AWS Local Demo Mode" -ForegroundColor Cyan
Write-Host "=============================================" -ForegroundColor Cyan
Write-Host "[1/4] Loading $envFile ..." -ForegroundColor Yellow
Get-Content $envFile | ForEach-Object {
    $line = $_.Trim()
    if ($line -eq "" -or $line.StartsWith("#")) { return }
    $idx = $line.IndexOf("=")
    if ($idx -lt 1) { return }
    $name  = $line.Substring(0, $idx).Trim()
    $value = $line.Substring($idx + 1).Trim()
    Set-Item -Path "Env:$name" -Value $value
}
$env:PYTHONPATH = $root
Write-Host "      Loaded. ZAMBONI_MODE=$($env:ZAMBONI_MODE)" -ForegroundColor Green

$ssoProfile = if ($env:AWS_SSO_PROFILE) { $env:AWS_SSO_PROFILE } else { "prod-toolsgenai-sso" }
Write-Host " AWS profile : $ssoProfile" -ForegroundColor Gray
Write-Host ""

# ── 2. Ensure the profile's session is valid, login if it's SSO-based ────────
Write-Host "[2/4] Checking AWS session for profile '$ssoProfile'..." -ForegroundColor Yellow
aws sts get-caller-identity --profile $ssoProfile 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "      No valid session yet -- trying: aws sso login --profile $ssoProfile" -ForegroundColor Yellow
    Write-Host "      (If this profile uses static/session keys or role_arn chaining" -ForegroundColor Gray
    Write-Host "       instead of SSO, this step will fail -- re-run setup_aws_local_profile.ps1" -ForegroundColor Gray
    Write-Host "       to refresh its credentials instead.)" -ForegroundColor Gray
    aws sso login --profile $ssoProfile
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Could not establish a valid session for profile '$ssoProfile'. Aborting."
        exit 1
    }
} else {
    Write-Host "      Session OK." -ForegroundColor Green
}

# Real gap fixed here (2026-07-09): only lock_service.py/create_lock_table.py/
# control_plane_sync.py/aws_smoke_test.py call get_boto3_session(), which
# honors AWS_SSO_PROFILE explicitly. Everything else that actually talks to
# AWS -- athena_client.py, glue_client.py, s3_client.py, notifier.py, which
# together are the vast majority of real AWS traffic -- constructs a plain
# boto3.client(...) with no profile_name, relying entirely on boto3's
# default credential chain. Without AWS_PROFILE also set, none of those
# calls would pick up the profile's session at all.
$env:AWS_SSO_PROFILE = $ssoProfile
$env:AWS_PROFILE     = $ssoProfile

# ── 3. Build the React UI if it hasn't been built yet ─────────────────────────
$uiDistIndex = Join-Path $root "ui\dist\index.html"
if (-not (Test-Path $uiDistIndex)) {
    Write-Host "[3/4] ui\dist not found -- building React UI..." -ForegroundColor Yellow
    Push-Location (Join-Path $root "ui")
    npm ci
    if ($LASTEXITCODE -ne 0) { Pop-Location; Write-Error "npm ci failed."; exit 1 }
    npm run build
    if ($LASTEXITCODE -ne 0) { Pop-Location; Write-Error "npm run build failed."; exit 1 }
    Pop-Location
    Write-Host "      Build complete." -ForegroundColor Green
} else {
    Write-Host "[3/4] ui\dist already built -- skipping." -ForegroundColor Gray
}

# ── 4. Start uvicorn (serves API + built UI on :8000) ─────────────────────────
Write-Host "[4/4] Starting Zamboni API (mode=$($env:ZAMBONI_MODE)) on http://localhost:8000 ..." -ForegroundColor Green
Write-Host ""
Write-Host "Press Ctrl+C to stop." -ForegroundColor Gray
Write-Host ""

$browserJob = Start-Job -ScriptBlock { Start-Sleep -Seconds 2; Start-Process "http://localhost:8000" }
try {
    python -m uvicorn api.main:app --host 0.0.0.0 --port 8000
} finally {
    Remove-Job -Job $browserJob -Force -ErrorAction SilentlyContinue
    Write-Host ""
    Write-Host "Zamboni API stopped." -ForegroundColor Yellow
}

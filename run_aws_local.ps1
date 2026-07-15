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
function Import-DotEnvFile($path) {
    Get-Content $path | ForEach-Object {
        $line = $_.Trim()
        if ($line -eq "" -or $line.StartsWith("#")) { return }
        $idx = $line.IndexOf("=")
        if ($idx -lt 1) { return }
        $name  = $line.Substring(0, $idx).Trim()
        $value = $line.Substring($idx + 1).Trim()
        Set-Item -Path "Env:$name" -Value $value
    }
}

function Test-AwsProfileExists($profileName) {
    aws configure list --profile $profileName 2>$null | Out-Null
    return ($LASTEXITCODE -eq 0)
}

$envFile = Join-Path $root ".env.aws_local"
if (-not (Test-Path $envFile)) {
    Write-Error "$envFile not found. Copy .env.aws_local.example to .env.aws_local and fill in real values first (or run setup_aws_local_profile.ps1)."
    exit 1
}

Write-Host ""
Write-Host "=============================================" -ForegroundColor Cyan
Write-Host " Zamboni -- AWS Local Demo Mode" -ForegroundColor Cyan
Write-Host "=============================================" -ForegroundColor Cyan
Write-Host "[1/5] Loading $envFile ..." -ForegroundColor Yellow
Import-DotEnvFile $envFile
$env:PYTHONPATH = $root
Write-Host "      Loaded. ZAMBONI_MODE=$($env:ZAMBONI_MODE)" -ForegroundColor Green

# 2026-07-11 fix: no more hardcoded "prod-toolsgenai-sso" fallback here --
# that profile is cross-team/Bedrock-only in at least one org's account and
# has nothing to do with Zamboni's own AWS account. Silently falling back to
# it (rather than failing loudly) let a missing/placeholder AWS_SSO_PROFILE
# go unnoticed until AWS calls started failing or quietly authenticated as
# the wrong identity. Missing entirely is now a hard, immediate error.
$ssoProfile = $env:AWS_SSO_PROFILE
if ([string]::IsNullOrWhiteSpace($ssoProfile)) {
    Write-Error "AWS_SSO_PROFILE is not set in $envFile. Run setup_aws_local_profile.ps1 to create a profile and fill this in, then re-run."
    exit 1
}
Write-Host " AWS profile : $ssoProfile" -ForegroundColor Gray
Write-Host ""

# ── 2. Make sure the profile actually exists, then that its session is valid ─
# 2026-07-11 fix: previously this jumped straight to `aws sts
# get-caller-identity --profile $ssoProfile`, and if the profile didn't
# exist at all (the normal state on a fresh machine/org account -- Zamboni
# ships no working default), that call fails exactly the same way an
# expired-session call does, so the script would then try `aws sso login`
# against a profile with no sso_start_url configured, fail confusingly, and
# abort -- with no hint that the real fix is to create the profile, not log
# into it. Now checks existence first and offers to run
# setup_aws_local_profile.ps1 right here, interactively (paste keys from the
# AWS console, or chain to an existing profile), then reloads
# .env.aws_local afterward since that script may have written a different
# profile name into it.
Write-Host "[2/5] Checking AWS profile '$ssoProfile'..." -ForegroundColor Yellow
if (-not (Test-AwsProfileExists $ssoProfile)) {
    Write-Host "      Profile '$ssoProfile' does not exist on this machine yet." -ForegroundColor Red
    Write-Host "      This is expected on a fresh machine/org account -- Zamboni has no" -ForegroundColor Yellow
    Write-Host "      working default profile; it must be created once." -ForegroundColor Yellow
    $runSetup = Read-Host "      Set it up now via setup_aws_local_profile.ps1? [Y/n]"
    if ($runSetup -match '^[Nn]') {
        Write-Error "Cannot continue without a valid AWS profile. Run setup_aws_local_profile.ps1 manually, then re-run this script."
        exit 1
    }
    & (Join-Path $root "setup_aws_local_profile.ps1")
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Profile setup did not complete successfully. Re-run setup_aws_local_profile.ps1, then re-run this script."
        exit 1
    }
    Write-Host ""
    Write-Host "      Reloading $envFile after profile setup..." -ForegroundColor Yellow
    Import-DotEnvFile $envFile
    $ssoProfile = $env:AWS_SSO_PROFILE
    if ([string]::IsNullOrWhiteSpace($ssoProfile) -or -not (Test-AwsProfileExists $ssoProfile)) {
        Write-Error "Profile setup finished but '$ssoProfile' still isn't usable. Check .env.aws_local and re-run."
        exit 1
    }
    Write-Host "      Profile '$ssoProfile' created." -ForegroundColor Green
} else {
    Write-Host "      Profile exists." -ForegroundColor Green
}

aws sts get-caller-identity --profile $ssoProfile 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "      No valid session yet -- trying: aws sso login --profile $ssoProfile" -ForegroundColor Yellow
    Write-Host "      (If this profile uses static/session keys or role_arn chaining" -ForegroundColor Gray
    Write-Host "       instead of SSO, this step will fail -- re-run setup_aws_local_profile.ps1" -ForegroundColor Gray
    Write-Host "       to refresh its credentials instead.)" -ForegroundColor Gray
    aws sso login --profile $ssoProfile
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Could not establish a valid session for profile '$ssoProfile'. If it uses pasted/static credentials instead of SSO, run setup_aws_local_profile.ps1 to refresh them. Aborting."
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

# ── 3. Initialize/migrate the control-plane SQLite DB ──────────────────────────
# 2026-07-15 fix: this step was missing entirely -- the CodeDeploy path
# (deploy/scripts/after_install.sh) always runs scripts/init_control_plane_db.py
# before starting the app, but neither laptop launcher did, so a fresh (or
# schema-stale) zamboni_control.db was never created/migrated here. That's
# the real root cause behind two symptoms that look unrelated at first: an
# "empty control plane, no S3 backup available" refusal-to-start, and
# `stream_registry` missing a column (e.g. controlm_job_start_time) added by
# a migration that only ever runs inside this script. It's idempotent and
# safe to run on every launch -- CREATE TABLE IF NOT EXISTS + a guarded ALTER
# TABLE loop, both no-ops once already applied.
Write-Host "[3/5] Initializing control-plane DB ($($env:ZAMBONI_CONTROL_PLANE_DB))..." -ForegroundColor Yellow
python scripts\init_control_plane_db.py
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "      Control-plane DB init/migration failed." -ForegroundColor Red
    Write-Host "      If this is a genuinely first-ever run against an empty account (no S3" -ForegroundColor Yellow
    Write-Host "      backup exists yet), add ZAMBONI_CONTROL_PLANE_FIRST_INSTALL=true to .env" -ForegroundColor Yellow
    Write-Host "      (not .env.aws_local -- config/settings.py loads .env) and re-run." -ForegroundColor Yellow
    Write-Host "      Do NOT run scripts/seed_local_db.py against this -- that script seeds" -ForegroundColor Yellow
    Write-Host "      ZAMBONI_LOCAL_DB (the fabricated local/demo fixture), a different file" -ForegroundColor Yellow
    Write-Host "      entirely from ZAMBONI_CONTROL_PLANE_DB, and will not fix this." -ForegroundColor Yellow
    exit 1
}
Write-Host "      Control-plane DB ready." -ForegroundColor Green

# ── 4. Build the React UI if it hasn't been built yet ──────────────────────────
$uiDistIndex = Join-Path $root "ui\dist\index.html"
if (-not (Test-Path $uiDistIndex)) {
    Write-Host "[4/5] ui\dist not found -- building React UI..." -ForegroundColor Yellow
    Push-Location (Join-Path $root "ui")
    npm ci
    if ($LASTEXITCODE -ne 0) { Pop-Location; Write-Error "npm ci failed."; exit 1 }
    npm run build
    if ($LASTEXITCODE -ne 0) { Pop-Location; Write-Error "npm run build failed."; exit 1 }
    Pop-Location
    Write-Host "      Build complete." -ForegroundColor Green
} else {
    Write-Host "[4/5] ui\dist already built -- skipping." -ForegroundColor Gray
}

# ── 5. Start uvicorn (serves API + built UI on :8000) ──────────────────────────
Write-Host "[5/5] Starting Zamboni API (mode=$($env:ZAMBONI_MODE)) on http://localhost:8000 ..." -ForegroundColor Green
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

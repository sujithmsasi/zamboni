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
        Write-Error "$envFile not found. Copy .env.aws_local.example to .env.aws_local first (or run setup_aws_local_profile.ps1, which creates it for you)."
        exit 1
    }
    function Import-DotEnvFile($path) {
        Get-Content $path | ForEach-Object {
            $line = $_.Trim()
            if ($line -eq "" -or $line.StartsWith("#")) { return }
            $idx = $line.IndexOf("=")
            if ($idx -lt 1) { return }
            Set-Item -Path "Env:$($line.Substring(0, $idx).Trim())" -Value $line.Substring($idx + 1).Trim()
        }
    }
    function Test-AwsProfileExists($profileName) {
        aws configure list --profile $profileName 2>$null | Out-Null
        return ($LASTEXITCODE -eq 0)
    }

    Import-DotEnvFile $envFile

    # 2026-07-11 fix: no more hardcoded "prod-toolsgenai-sso" fallback --
    # same bug/fix as run_aws_local.ps1. That profile is cross-team/
    # Bedrock-only in at least one org's account and unrelated to Zamboni's
    # own AWS account; silently defaulting to it hid a missing
    # AWS_SSO_PROFILE until AWS calls failed or authenticated as the wrong
    # identity.
    $ssoProfile = $env:AWS_SSO_PROFILE
    if ([string]::IsNullOrWhiteSpace($ssoProfile)) {
        Write-Error "AWS_SSO_PROFILE is not set in $envFile. Run setup_aws_local_profile.ps1 to create a profile and fill this in, then re-run."
        exit 1
    }

    if (-not (Test-AwsProfileExists $ssoProfile)) {
        Write-Host "Profile '$ssoProfile' does not exist on this machine yet." -ForegroundColor Red
        $runSetup = Read-Host "Set it up now via setup_aws_local_profile.ps1? [Y/n]"
        if ($runSetup -match '^[Nn]') {
            Write-Error "Cannot continue without a valid AWS profile. Run setup_aws_local_profile.ps1 manually, then re-run this script."
            exit 1
        }
        & (Join-Path $root "setup_aws_local_profile.ps1")
        if ($LASTEXITCODE -ne 0) {
            Write-Error "Profile setup did not complete successfully."
            exit 1
        }
        Import-DotEnvFile $envFile
        $ssoProfile = $env:AWS_SSO_PROFILE
        if ([string]::IsNullOrWhiteSpace($ssoProfile) -or -not (Test-AwsProfileExists $ssoProfile)) {
            Write-Error "Profile setup finished but '$ssoProfile' still isn't usable. Check .env.aws_local and re-run."
            exit 1
        }
    }

    $env:AWS_SSO_PROFILE = $ssoProfile
    # Same fix as run_aws_local.ps1 (2026-07-09): most AWS calls (Athena/
    # Glue/S3/SNS) don't consume AWS_SSO_PROFILE at all -- they rely on
    # boto3's default chain, which needs AWS_PROFILE set instead.
    $env:AWS_PROFILE     = $ssoProfile
    aws sts get-caller-identity --profile $ssoProfile 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "No valid SSO session -- running: aws sso login --profile $ssoProfile" -ForegroundColor Yellow
        Write-Host "(If this profile uses static/session keys or role_arn chaining instead" -ForegroundColor Gray
        Write-Host " of SSO, this will fail -- run setup_aws_local_profile.ps1 to refresh it.)" -ForegroundColor Gray
        aws sso login --profile $ssoProfile
        if ($LASTEXITCODE -ne 0) {
            Write-Error "Could not establish a valid session for profile '$ssoProfile'. If it uses pasted/static credentials instead of SSO, run setup_aws_local_profile.ps1 to refresh them. Aborting."
            exit 1
        }
    }

    # 2026-07-15 fix: same gap as run_aws_local.ps1 -- this launcher never
    # initialized/migrated the control-plane SQLite DB either, so a fresh or
    # schema-stale zamboni_control.db (missing a column added by a later
    # migration, e.g. controlm_job_start_time) was never caught before the
    # app started against it. Idempotent, safe on every launch.
    Write-Host "Initializing control-plane DB ($($env:ZAMBONI_CONTROL_PLANE_DB))..." -ForegroundColor Yellow
    python scripts\init_control_plane_db.py
    if ($LASTEXITCODE -ne 0) {
        Write-Host ""
        Write-Host "Control-plane DB init/migration failed." -ForegroundColor Red
        Write-Host "If this is a genuinely first-ever run against an empty account, add" -ForegroundColor Yellow
        Write-Host "ZAMBONI_CONTROL_PLANE_FIRST_INSTALL=true to .env (not .env.aws_local) and" -ForegroundColor Yellow
        Write-Host "re-run. Do not use scripts/seed_local_db.py for this -- it seeds a" -ForegroundColor Yellow
        Write-Host "different file (ZAMBONI_LOCAL_DB, the fabricated demo fixture)." -ForegroundColor Yellow
        exit 1
    }
    Write-Host "Control-plane DB ready." -ForegroundColor Green
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

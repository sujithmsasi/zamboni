# =============================================================================
# Zamboni -- aws_local Profile Setup (interactive, one-time or re-run-to-refresh)
#
# Configures a named AWS CLI profile for aws_local mode and points
# .env.aws_local at it. Two supported inputs:
#
#   1. Static or temporary/session credentials (Access Key ID + Secret +
#      optional Session Token) -- simplest, but expires (often ~1h for an
#      assumed-role session token). Re-run this script to refresh.
#   2. Role-chaining (role_arn + source_profile) -- point at an existing,
#      long-lived profile (e.g. the one you already use for dev, like
#      "sub_dataengineer") that has sts:AssumeRole rights on the target
#      role's ARN. AWS CLI/boto3 then assumes the role and refreshes it
#      automatically on every call -- no re-running this script needed for
#      a multi-day window, which is what matters here since the demo-prep
#      plan runs the engine repeatedly over several days.
#
# Does NOT hardcode "prod-toolsgenai-sso" anywhere -- that default in
# run_aws_local.ps1/.env.aws_local.example is a cross-team, Bedrock-only
# profile and is never appropriate for this app's own AWS account.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File setup_aws_local_profile.ps1
# =============================================================================

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

function ConvertFrom-SecureStringPlain($secure) {
    if (-not $secure -or $secure.Length -eq 0) { return "" }
    $bstr = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        return [System.Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr)
    } finally {
        [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    }
}

Write-Host ""
Write-Host "=============================================" -ForegroundColor Cyan
Write-Host " Zamboni -- aws_local Profile Setup" -ForegroundColor Cyan
Write-Host "=============================================" -ForegroundColor Cyan
Write-Host ""

# ── 1. Profile name ────────────────────────────────────────────────────────────
# Naming convention: "zamboni-{env}" -- each AWS account is its own
# environment, so the profile name should say which one (zamboni-dev,
# zamboni-preprod, zamboni-prod, ...). This also drives the APP_ENV prompt
# below, so the header's environment tag and the account you're actually
# talking to stay in sync instead of relying on remembering to edit both.
$defaultProfile = "zamboni-dev"
$profileName = Read-Host "Profile name to create/update [$defaultProfile]"
if ([string]::IsNullOrWhiteSpace($profileName)) { $profileName = $defaultProfile }

$existing = aws configure list --profile $profileName 2>$null
if ($LASTEXITCODE -eq 0 -and $existing) {
    Write-Host ""
    Write-Host "Profile '$profileName' already exists. Continuing will overwrite its credentials/config." -ForegroundColor Yellow
    $confirm = Read-Host "Continue? [y/N]"
    if ($confirm -notmatch '^[Yy]') { Write-Host "Aborted."; exit 0 }
}

# ── 1b. Environment (drives APP_ENV, the header's "DEV"/"PREPROD"/"PROD" tag) ──
$envGuess = ""
if ($profileName -match '^zamboni-(dev|preprod|prod|test)$') { $envGuess = $Matches[1] }
$envPrompt = if ($envGuess) { "Environment this profile represents [$envGuess]" } else { "Environment this profile represents (dev/preprod/prod/test)" }
$appEnv = Read-Host $envPrompt
if ([string]::IsNullOrWhiteSpace($appEnv)) { $appEnv = $envGuess }
if ([string]::IsNullOrWhiteSpace($appEnv)) {
    Write-Error "An environment value is required (dev/preprod/prod/test) -- this drives the APP_ENV header tag."
    exit 1
}

# ── 2. Credential mode ──────────────────────────────────────────────────────────
Write-Host ""
Write-Host "How do you want to authenticate this profile?" -ForegroundColor Cyan
Write-Host "  1) Paste an access key / secret / session token I already have"
Write-Host "     (simplest, but expires -- re-run this script when it does)"
Write-Host "  2) Chain to an existing long-lived profile that can assume a role"
Write-Host "     (e.g. 'sub_dataengineer' -- recommended for a multi-day window," -ForegroundColor Gray
Write-Host "      since AWS CLI/boto3 auto-refreshes the assumed session)" -ForegroundColor Gray
$mode = Read-Host "Choice [1/2]"

$region = Read-Host "AWS region [us-west-2]"
if ([string]::IsNullOrWhiteSpace($region)) { $region = "us-west-2" }

if ($mode -eq "2") {
    # ── Role-chaining: role_arn + source_profile ──────────────────────────────
    $sourceProfile = Read-Host "Source profile that can assume the target role [sub_dataengineer]"
    if ([string]::IsNullOrWhiteSpace($sourceProfile)) { $sourceProfile = "sub_dataengineer" }

    $sourceCheck = aws sts get-caller-identity --profile $sourceProfile 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Source profile '$sourceProfile' isn't valid/logged in right now (aws sts get-caller-identity failed). Log it in first (e.g. 'aws sso login --profile $sourceProfile' if it's SSO-based), then re-run this script."
        exit 1
    }
    Write-Host "      Source profile '$sourceProfile' OK." -ForegroundColor Green

    $roleArn = Read-Host "Role ARN to assume (e.g. arn:aws:iam::123456789012:role/zamboni-preprod-role)"
    if ([string]::IsNullOrWhiteSpace($roleArn)) {
        Write-Error "Role ARN is required for role-chaining. Aborting."
        exit 1
    }
    $externalId = Read-Host "External ID (leave blank if none)"

    aws configure set role_arn $roleArn --profile $profileName
    aws configure set source_profile $sourceProfile --profile $profileName
    aws configure set region $region --profile $profileName
    if (-not [string]::IsNullOrWhiteSpace($externalId)) {
        aws configure set external_id $externalId --profile $profileName
    }
    Write-Host ""
    Write-Host "Configured '$profileName' to assume $roleArn via source profile '$sourceProfile'." -ForegroundColor Green

} else {
    # ── Static / temporary session credentials ────────────────────────────────
    $accessKeyId = Read-Host "AWS Access Key ID"
    if ([string]::IsNullOrWhiteSpace($accessKeyId)) {
        Write-Error "Access Key ID is required. Aborting."
        exit 1
    }
    $secretKeySecure = Read-Host "AWS Secret Access Key" -AsSecureString
    $secretKey = ConvertFrom-SecureStringPlain $secretKeySecure
    if ([string]::IsNullOrWhiteSpace($secretKey)) {
        Write-Error "Secret Access Key is required. Aborting."
        exit 1
    }
    $sessionTokenSecure = Read-Host "AWS Session Token (leave blank if this is a permanent IAM user, not an assumed role)" -AsSecureString
    $sessionToken = ConvertFrom-SecureStringPlain $sessionTokenSecure

    aws configure set aws_access_key_id $accessKeyId --profile $profileName
    aws configure set aws_secret_access_key $secretKey --profile $profileName
    if (-not [string]::IsNullOrWhiteSpace($sessionToken)) {
        aws configure set aws_session_token $sessionToken --profile $profileName
    } else {
        # Clear any stale session token from a previous run of this script.
        aws configure set aws_session_token "" --profile $profileName 2>$null | Out-Null
    }
    aws configure set region $region --profile $profileName

    # Best-effort cleanup of plaintext locals.
    $accessKeyId = $null; $secretKey = $null; $sessionToken = $null

    Write-Host ""
    Write-Host "Configured '$profileName' with the provided credentials." -ForegroundColor Green
    if (-not [string]::IsNullOrWhiteSpace($sessionTokenSecure)) {
        Write-Host "Note: session tokens expire (often ~1h) -- re-run this script with fresh values when it does." -ForegroundColor Yellow
    }
}

# ── 3. Validate ─────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "Validating profile '$profileName'..." -ForegroundColor Yellow
$identity = aws sts get-caller-identity --profile $profileName --output json 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Error "aws sts get-caller-identity failed for profile '$profileName'. Check the values entered above and re-run."
    exit 1
}
Write-Host "Success:" -ForegroundColor Green
Write-Host $identity

# ── 4. Point .env.aws_local at this profile ─────────────────────────────────────
$envFile = Join-Path $root ".env.aws_local"
$exampleFile = Join-Path $root ".env.aws_local.example"
if (-not (Test-Path $envFile)) {
    if (-not (Test-Path $exampleFile)) {
        Write-Error "$exampleFile not found -- can't create $envFile. Check your working directory."
        exit 1
    }
    Copy-Item $exampleFile $envFile
    Write-Host ""
    Write-Host "Created $envFile from .env.aws_local.example -- fill in the remaining" -ForegroundColor Yellow
    Write-Host "placeholder values (bucket names, Athena workgroups, SNS topic ARNs," -ForegroundColor Yellow
    Write-Host "account ID) before running run_aws_local.ps1." -ForegroundColor Yellow
}

$content = Get-Content $envFile -Raw
if ($content -match '(?m)^AWS_SSO_PROFILE=.*$') {
    $content = $content -replace '(?m)^AWS_SSO_PROFILE=.*$', "AWS_SSO_PROFILE=$profileName"
} else {
    $content += "`nAWS_SSO_PROFILE=$profileName`n"
}
# Keep APP_ENV (the header's environment tag) in sync with the profile's
# own environment -- these are two independent settings the app never
# cross-checks, so writing both here is what actually keeps them paired
# instead of relying on remembering to edit both by hand.
if ($content -match '(?m)^APP_ENV=.*$') {
    $content = $content -replace '(?m)^APP_ENV=.*$', "APP_ENV=$appEnv"
} else {
    $content += "`nAPP_ENV=$appEnv`n"
}
Set-Content -Path $envFile -Value $content -NoNewline

Write-Host ""
Write-Host "=============================================" -ForegroundColor Cyan
Write-Host " Done -- AWS_SSO_PROFILE=$profileName, APP_ENV=$appEnv in $envFile" -ForegroundColor Cyan
Write-Host "=============================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Gray
Write-Host "  1. Fill in any remaining placeholder values in $envFile (buckets," -ForegroundColor Gray
Write-Host "     workgroups, SNS topic ARNs, account ID) if you haven't already." -ForegroundColor Gray
Write-Host "  2. python scripts\aws_smoke_test.py --create-lock-table" -ForegroundColor Gray
Write-Host "  3. .\run_aws_local.ps1" -ForegroundColor Gray

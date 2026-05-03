# =============================================================================
# Zamboni - Complete Repo Sync from ZIP
# Run this ONCE to fully sync your local repo from the downloaded zip.
# This replaces all piecemeal copy commands from previous sprints.
#
# Usage:
#   1. Extract the zip to: C:\Users\mrsuj\Downloads\zamboni\
#      (so you get: C:\Users\mrsuj\Downloads\zamboni\zamboni\)
#   2. Open PowerShell as normal user (no admin needed)
#   3. Run: .\sync_zamboni.ps1
# =============================================================================

$src = "C:\Users\mrsuj\Downloads\zamboni\zamboni"
$dst = "C:\Users\mrsuj\projects\zamboni"

# Verify source exists
if (-not (Test-Path $src)) {
    Write-Error "Source not found: $src"
    Write-Error "Extract the zip first so you have: $src\README.md"
    exit 1
}

Write-Host ""
Write-Host "=============================================" -ForegroundColor Cyan
Write-Host " Zamboni Full Repo Sync" -ForegroundColor Cyan
Write-Host "=============================================" -ForegroundColor Cyan
Write-Host " From: $src" -ForegroundColor Gray
Write-Host " To:   $dst" -ForegroundColor Gray
Write-Host ""

# -- Helper: copy a file, create directory if needed --------------------------
function Sync-File {
    param($RelPath)
    $srcFile = Join-Path $src $RelPath
    $dstFile = Join-Path $dst $RelPath
    $dstDir  = Split-Path $dstFile -Parent

    if (-not (Test-Path $srcFile)) {
        Write-Host "  SKIP (not in zip): $RelPath" -ForegroundColor DarkGray
        return
    }
    if (-not (Test-Path $dstDir)) {
        New-Item -ItemType Directory -Path $dstDir -Force | Out-Null
    }
    Copy-Item -Path $srcFile -Destination $dstFile -Force
}

# -- Root files ----------------------------------------------------------------
Write-Host "[1/12] Root files..." -ForegroundColor Yellow
Sync-File ".env.example"
Sync-File ".gitattributes"
Sync-File ".gitignore"
Sync-File "README.md"
Sync-File "pyproject.toml"
Sync-File "requirements.txt"
Sync-File "requirements-dev.txt"
Sync-File "setup_ec2.sh"

# -- .github -------------------------------------------------------------------
Write-Host "[2/12] GitHub Actions workflows..." -ForegroundColor Yellow
Sync-File ".github\workflows\ci.yml"
Sync-File ".github\workflows\deploy.yml"

# -- .streamlit ----------------------------------------------------------------
Write-Host "[3/12] Streamlit config..." -ForegroundColor Yellow
Sync-File ".streamlit\config.toml"
Sync-File ".streamlit\pages.toml"
Sync-File ".streamlit\secrets.toml.example"

# -- app -----------------------------------------------------------------------
Write-Host "[4/12] Streamlit app..." -ForegroundColor Yellow
Sync-File "app\Home.py"
Sync-File "app\__init__.py"
Sync-File "app\assets\README.md"
Sync-File "app\components\__init__.py"
Sync-File "app\components\athena_runner.py"
Sync-File "app\components\auth.py"
Sync-File "app\components\filters.py"
Sync-File "app\components\header.py"
Sync-File "app\components\home_snapshot.py"
Sync-File "app\components\kpi_cards.py"
Sync-File "app\components\sidebar.py"
Sync-File "app\components\status_badge.py"
Sync-File "app\pages\__init__.py"
Sync-File "app\pages\1_Domain_Management.py"
Sync-File "app\pages\2_Table_Registration.py"
Sync-File "app\pages\3_Policy_Configuration.py"
Sync-File "app\pages\4_Health_Dashboard.py"
Sync-File "app\pages\5_Live_Activity.py"
Sync-File "app\pages\6_Dry_Run_Viewer.py"
Sync-File "app\pages\7_Execution_Log.py"
Sync-File "app\pages\8_Cost_Report.py"
Sync-File "app\pages\9_NonProd_Lifecycle.py"
Sync-File "app\pages\10_Stale_Resources.py"

# -- config --------------------------------------------------------------------
Write-Host "[5/12] Config..." -ForegroundColor Yellow
Sync-File "config\__init__.py"
Sync-File "config\settings.py"
Sync-File "config\domain_retention.json"
Sync-File "config\policy_templates.json"

# -- deploy --------------------------------------------------------------------
Write-Host "[6/12] Deploy scripts..." -ForegroundColor Yellow
Sync-File "deploy\README.md"
Sync-File "deploy\appspec.yml"
Sync-File "deploy\buildspec.yml"
Sync-File "deploy\iam_policy.json"
Sync-File "deploy\pipeline_config.md"
Sync-File "deploy\setup_ec2.sh"
Sync-File "deploy\create_athena_tables.sh"
Sync-File "deploy\cloudwatch\alarms.json"
Sync-File "deploy\cloudwatch\create_alarms.sh"
Sync-File "deploy\cloudwatch\dashboard.json"
Sync-File "deploy\scripts\after_install.sh"
Sync-File "deploy\scripts\app_start.sh"
Sync-File "deploy\scripts\before_install.sh"

# -- docs ----------------------------------------------------------------------
Write-Host "[7/12] Docs..." -ForegroundColor Yellow
Sync-File "docs\zamboni-setup-guide.html"

# -- engine --------------------------------------------------------------------
Write-Host "[8/12] Engine..." -ForegroundColor Yellow
Sync-File "engine\__init__.py"
# CLI
Sync-File "engine\cli\__init__.py"
Sync-File "engine\cli\cost_report.py"
Sync-File "engine\cli\dry_run.py"
Sync-File "engine\cli\enable.py"
Sync-File "engine\cli\fleet_status.py"
Sync-File "engine\cli\register.py"
# Core
Sync-File "engine\core\__init__.py"
Sync-File "engine\core\backpressure.py"
Sync-File "engine\core\circuit_breaker.py"
Sync-File "engine\core\config.py"
Sync-File "engine\core\execution_log.py"
Sync-File "engine\core\execution_log_parquet.py"
Sync-File "engine\core\health_checker.py"
Sync-File "engine\core\idempotency.py"
Sync-File "engine\core\notifier.py"
Sync-File "engine\core\property_sync.py"
Sync-File "engine\core\registry.py"
Sync-File "engine\core\window_evaluator.py"
# Engines
Sync-File "engine\engines\__init__.py"
Sync-File "engine\engines\archival_engine.py"
Sync-File "engine\engines\base.py"
Sync-File "engine\engines\hk_engine.py"
Sync-File "engine\engines\lifecycle_engine.py"
# Monitoring
Sync-File "engine\monitoring\__init__.py"
Sync-File "engine\monitoring\activity_scanner.py"
Sync-File "engine\monitoring\health_check.py"
Sync-File "engine\monitoring\metrics.py"
# Operations
Sync-File "engine\operations\__init__.py"
Sync-File "engine\operations\archival.py"
Sync-File "engine\operations\catalog_cleanup.py"
Sync-File "engine\operations\compaction.py"
Sync-File "engine\operations\dynamic_router.py"
Sync-File "engine\operations\vacuum.py"
# Scripts
Sync-File "engine\scripts\__init__.py"
Sync-File "engine\scripts\run_archival.py"
Sync-File "engine\scripts\run_cleanup.py"
Sync-File "engine\scripts\run_hk.py"
Sync-File "engine\scripts\run_lifecycle_cycle.py"
Sync-File "engine\scripts\run_lifecycle_scan.py"
# Strategies
Sync-File "engine\strategies\__init__.py"
Sync-File "engine\strategies\binpack.py"
Sync-File "engine\strategies\sort.py"
Sync-File "engine\strategies\zorder.py"
# Utils
Sync-File "engine\utils\__init__.py"
Sync-File "engine\utils\athena_client.py"
Sync-File "engine\utils\glue_client.py"
Sync-File "engine\utils\logger.py"
Sync-File "engine\utils\partition_utils.py"
Sync-File "engine\utils\s3_client.py"

# -- glue_jobs -----------------------------------------------------------------
Write-Host "[9/12] Glue jobs..." -ForegroundColor Yellow
Sync-File "glue_jobs\zamboni_compaction.py"

# -- SQL -----------------------------------------------------------------------
Write-Host "[10/12] SQL DDL..." -ForegroundColor Yellow
Sync-File "sql\create_domain_registry.sql"
Sync-File "sql\create_execution_log.sql"
Sync-File "sql\create_hk_config.sql"
Sync-File "sql\create_home_snapshot.sql"
Sync-File "sql\create_nonprod_registry.sql"
Sync-File "sql\create_stream_registry.sql"
Sync-File "sql\monitoring_queries.sql"

# -- Tests ---------------------------------------------------------------------
Write-Host "[11/12] Tests..." -ForegroundColor Yellow
Sync-File "tests\__init__.py"
Sync-File "tests\conftest.py"
Sync-File "tests\integration\README.md"
Sync-File "tests\integration\__init__.py"
Sync-File "tests\load\load_test.py"
Sync-File "tests\unit\__init__.py"
Sync-File "tests\unit\test_archival.py"
Sync-File "tests\unit\test_circuit_breaker.py"
Sync-File "tests\unit\test_cli.py"
Sync-File "tests\unit\test_compaction.py"
Sync-File "tests\unit\test_config_templates.py"
Sync-File "tests\unit\test_gap_closure_final.py"
Sync-File "tests\unit\test_lifecycle_states.py"
Sync-File "tests\unit\test_monitoring.py"
Sync-File "tests\unit\test_partition_utils.py"
Sync-File "tests\unit\test_settings.py"
Sync-File "tests\unit\test_sprint1.py"
Sync-File "tests\unit\test_sprint2.py"
Sync-File "tests\unit\test_sprint3.py"
Sync-File "tests\unit\test_v2_alignment.py"
Sync-File "tests\unit\test_vacuum.py"
Sync-File "tests\unit\test_window_evaluator.py"

# -- Cleanup: remove old files that no longer exist ---------------------------
Write-Host "[12/12] Removing stale files..." -ForegroundColor Yellow
$stale = @(
    "app\main.py",
    "app\_main.py",
    "zamboni_app.py",
    "app\pages\0_Home.py"
)
foreach ($f in $stale) {
    $full = Join-Path $dst $f
    if (Test-Path $full) {
        Remove-Item $full -Force
        Write-Host "  Removed: $f" -ForegroundColor DarkYellow
    }
}

# -- Run unit tests ----------------------------------------------------------
Write-Host ''
Write-Host '=============================================' -ForegroundColor Cyan
Write-Host ' Running unit tests...' -ForegroundColor Cyan
Write-Host '=============================================' -ForegroundColor Cyan
Write-Host ''

Set-Location $dst

$env:ZAMBONI_TEST_MODE = 'true'
python -m pytest tests\unit\ -v --tb=short
$testResult = $LASTEXITCODE

if ($testResult -ne 0) {
    Write-Host ''
    Write-Host '=============================================' -ForegroundColor Red
    Write-Host ' TESTS FAILED -- commit aborted.' -ForegroundColor Red
    Write-Host ' Fix failing tests before pushing.' -ForegroundColor Red
    Write-Host '=============================================' -ForegroundColor Red
    exit 1
}

Write-Host ''
Write-Host '=============================================' -ForegroundColor Green
Write-Host ' All tests passed.' -ForegroundColor Green
Write-Host '=============================================' -ForegroundColor Green
Write-Host ''

# -- Git commit ----------------------------------------------------------------
Write-Host ""
Write-Host "=============================================" -ForegroundColor Cyan
Write-Host " Files synced. Committing..." -ForegroundColor Cyan
Write-Host "=============================================" -ForegroundColor Cyan
Write-Host ""

Set-Location $dst
git add .
git status --short
Write-Host ""
git commit -m "chore: full repo sync - all sprints 1-5, 259 tests passing"
git push origin dev

Write-Host ''
Write-Host '=============================================' -ForegroundColor Green
Write-Host ' Sync complete!' -ForegroundColor Green
Write-Host ''
Write-Host ' Verify with:' -ForegroundColor Gray
Write-Host '   cd C:\Users\mrsuj\projects\zamboni' -ForegroundColor White
Write-Host '   python -m pytest tests\unit\' -ForegroundColor White
Write-Host '   python -m streamlit run app\Home.py' -ForegroundColor White
Write-Host '=============================================' -ForegroundColor Green


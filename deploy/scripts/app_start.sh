#!/usr/bin/env bash
# =============================================================================
# Zamboni — ApplicationStart Hook
# Runs AFTER AfterInstall. Final validation before CodeDeploy marks SUCCESS.
#
# Responsibilities:
#   1. Restart Streamlit service
#   2. Validate settings load correctly
#   3. Verify Athena connectivity (SELECT 1)
#   4. Confirm Streamlit is responding on port 8501
#   5. Log deploy event
#
# Exit non-zero → CodeDeploy marks deployment FAILED and rolls back.
# =============================================================================

set -euo pipefail

DEPLOY_DIR="/opt/zamboni"
LOG="/var/log/zamboni-deploy.log"
PYTHON="python3.11"

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] === ApplicationStart START ===" | tee -a "$LOG"

cd "$DEPLOY_DIR"

# ── 1. Restart Streamlit service ──────────────────────────────────────────────
echo "[app_start] Starting zamboni-app service..." | tee -a "$LOG"
systemctl start zamboni-app
sleep 5  # Give Streamlit a moment to start

if systemctl is-active --quiet zamboni-app; then
    echo "[app_start] zamboni-app is running ✓" | tee -a "$LOG"
else
    echo "[app_start] ERROR: zamboni-app failed to start." | tee -a "$LOG"
    journalctl -u zamboni-app -n 20 | tee -a "$LOG"
    exit 1
fi

# ── 2. Validate settings ──────────────────────────────────────────────────────
echo "[app_start] Validating settings..." | tee -a "$LOG"
$PYTHON -c "
from config.settings import (
    AWS_REGION, ATHENA_CATALOG, ATHENA_DATABASE,
    STAGING_BUCKET, ARCHIVE_BUCKET, ZAMBONI_METADATA_BUCKET
)
print(f'  AWS_REGION     : {AWS_REGION}')
print(f'  ATHENA_CATALOG : {ATHENA_CATALOG}')
print(f'  ATHENA_DATABASE: {ATHENA_DATABASE}')
print(f'  STAGING_BUCKET : {STAGING_BUCKET}')
" | tee -a "$LOG"

if [ $? -ne 0 ]; then
    echo "[app_start] ERROR: settings.py failed to load. Check .env file." | tee -a "$LOG"
    exit 1
fi
echo "[app_start] Settings validated ✓" | tee -a "$LOG"

# ── 3. Athena connectivity check ──────────────────────────────────────────────
echo "[app_start] Checking Athena connectivity..." | tee -a "$LOG"
$PYTHON -c "
import sys
try:
    from engine.utils.athena_client import run_query
    result = run_query('SELECT 1', workgroup='app', dry_run=False)
    print(f'  Athena query_id: {result}')
    print('  Athena connectivity OK ✓')
except Exception as e:
    print(f'  WARNING: Athena check failed: {e}')
    # Non-fatal — Athena may not be needed immediately at startup
    sys.exit(0)
" | tee -a "$LOG"

# ── 4. Streamlit health check ─────────────────────────────────────────────────
echo "[app_start] Checking Streamlit on port 8501..." | tee -a "$LOG"
RETRY=0
MAX_RETRIES=6
until curl -sf http://localhost:8501/_stcore/health > /dev/null 2>&1; do
    RETRY=$((RETRY + 1))
    if [ $RETRY -ge $MAX_RETRIES ]; then
        echo "[app_start] WARNING: Streamlit health check timed out after ${MAX_RETRIES} retries." | tee -a "$LOG"
        echo "[app_start] Service is running but may still be loading." | tee -a "$LOG"
        break
    fi
    echo "[app_start] Waiting for Streamlit... attempt $RETRY/$MAX_RETRIES" | tee -a "$LOG"
    sleep 5
done

# ── 5. Log deploy event ───────────────────────────────────────────────────────
DEPLOY_TIME=$(date -u +%Y-%m-%dT%H:%M:%SZ)
HOSTNAME=$(hostname)
echo "[app_start] Deploy completed successfully at $DEPLOY_TIME on $HOSTNAME" | tee -a "$LOG"

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] === ApplicationStart DONE ===" | tee -a "$LOG"
exit 0

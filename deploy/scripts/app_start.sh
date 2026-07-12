#!/usr/bin/env bash
# =============================================================================
# Zamboni — ApplicationStart Hook
# Runs AFTER AfterInstall. Final validation before CodeDeploy marks SUCCESS.
#
# Responsibilities:
#   1. Validate settings load correctly
#   2. Verify Athena connectivity (SELECT 1)
#   3. Log deploy event
#
# Exit non-zero → CodeDeploy marks deployment FAILED and rolls back.
# =============================================================================

set -euo pipefail

DEPLOY_DIR="/opt/zamboni"
LOG="/var/log/zamboni-deploy.log"
PYTHON="python3.11"

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] === ApplicationStart START ===" | tee -a "$LOG"

cd "$DEPLOY_DIR"

# ── 1. Validate settings ──────────────────────────────────────────────────────
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

# ── 1b. Control-plane DB liveness check ───────────────────────────────────────
# stream_registry/hk_config/domain_registry/nonprod_registry/controlm_jobs are
# SQLite-primary in production now (engine/core/control_plane.py) -- unlike
# the Athena check below, this one is FATAL: it's not a nice-to-have, it's
# the file the app actually reads/writes for these tables.
echo "[app_start] Checking control-plane DB liveness..." | tee -a "$LOG"
$PYTHON -c "
from engine.core.control_plane import read_sql
df = read_sql('SELECT 1 AS ok')
assert not df.empty and int(df.iloc[0]['ok']) == 1
print('  Control-plane DB liveness OK')
"
if [ $? -ne 0 ]; then
    echo "[app_start] ERROR: control-plane DB liveness check failed." | tee -a "$LOG"
    exit 1
fi

# ── 2. Athena connectivity check ──────────────────────────────────────────────
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

# ── 3. Start + health-check zamboni-api ──────────────────────────────────────
echo "[app_start] Starting zamboni-api service..." | tee -a "$LOG"
systemctl start zamboni-api
sleep 3

if systemctl is-active --quiet zamboni-api; then
    echo "[app_start] zamboni-api is running ✓" | tee -a "$LOG"
else
    echo "[app_start] ERROR: zamboni-api failed to start." | tee -a "$LOG"
    journalctl -u zamboni-api -n 20 | tee -a "$LOG"
    exit 1
fi

echo "[app_start] Checking zamboni-api on port 8000..." | tee -a "$LOG"
RETRY=0
MAX_RETRIES=6
until curl -sf http://localhost:8000/api/system/mode > /dev/null 2>&1; do
    RETRY=$((RETRY + 1))
    if [ $RETRY -ge $MAX_RETRIES ]; then
        echo "[app_start] WARNING: zamboni-api health check timed out after ${MAX_RETRIES} retries." | tee -a "$LOG"
        echo "[app_start] Service is running but may still be loading." | tee -a "$LOG"
        break
    fi
    echo "[app_start] Waiting for zamboni-api... attempt $RETRY/$MAX_RETRIES" | tee -a "$LOG"
    sleep 5
done

# ── 4. Start zamboni-control-plane-{sync,backup} ─────────────────────────────
# Non-fatal if either fails to start -- the control-plane DB liveness check
# above (step 2b) already confirmed the app itself can read/write it; these
# two are the Athena-sync and S3-backup loops layered on top, not something
# a request-serving path depends on synchronously.
for svc in zamboni-control-plane-sync zamboni-control-plane-backup; do
    echo "[app_start] Starting $svc service..." | tee -a "$LOG"
    systemctl start "$svc" || echo "[app_start] WARNING: $svc failed to start (non-fatal)." | tee -a "$LOG"
    sleep 2
    if systemctl is-active --quiet "$svc"; then
        echo "[app_start] $svc is running ✓" | tee -a "$LOG"
    else
        echo "[app_start] WARNING: $svc is not active." | tee -a "$LOG"
        journalctl -u "$svc" -n 20 | tee -a "$LOG"
    fi
done

# zamboni-control-plane-integrity is a .timer-triggered daily oneshot (see
# deploy/systemd/zamboni-control-plane-integrity.timer) -- nothing to start
# here, systemctl enable in after_install.sh is sufficient.

# ── 5. Log deploy event ───────────────────────────────────────────────────────
DEPLOY_TIME=$(date -u +%Y-%m-%dT%H:%M:%SZ)
HOSTNAME=$(hostname)
echo "[app_start] Deploy completed successfully at $DEPLOY_TIME on $HOSTNAME" | tee -a "$LOG"

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] === ApplicationStart DONE ===" | tee -a "$LOG"
exit 0

#!/usr/bin/env bash
# =============================================================================
# Zamboni — BeforeInstall Hook
# Runs BEFORE CodeDeploy copies files to /opt/zamboni/
#
# Responsibilities:
#   1. Stop the Streamlit service (so files can be overwritten)
#   2. Backup .env to /tmp — CodeDeploy will overwrite /opt/zamboni/
#   3. Backup .streamlit/secrets.toml
# =============================================================================

set -euo pipefail

DEPLOY_DIR="/opt/zamboni"
BACKUP_DIR="/tmp/zamboni_deploy_backup"
LOG="/var/log/zamboni-deploy.log"

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] === BeforeInstall START ===" | tee -a "$LOG"

# ── 1. Stop Streamlit service ──────────────────────────────────────────────────
if systemctl is-active --quiet zamboni-app 2>/dev/null; then
    echo "[before_install] Stopping zamboni-app service..." | tee -a "$LOG"
    systemctl stop zamboni-app
    echo "[before_install] zamboni-app stopped." | tee -a "$LOG"
else
    echo "[before_install] zamboni-app service not running — skipping stop." | tee -a "$LOG"
fi

# ── 2. Backup .env ────────────────────────────────────────────────────────────
mkdir -p "$BACKUP_DIR"

if [ -f "$DEPLOY_DIR/.env" ]; then
    cp "$DEPLOY_DIR/.env" "$BACKUP_DIR/.env"
    echo "[before_install] .env backed up to $BACKUP_DIR/.env" | tee -a "$LOG"
else
    echo "[before_install] WARNING: $DEPLOY_DIR/.env not found. First deploy?" | tee -a "$LOG"
fi

# ── 3. Backup Streamlit secrets ───────────────────────────────────────────────
if [ -f "$DEPLOY_DIR/.streamlit/secrets.toml" ]; then
    cp "$DEPLOY_DIR/.streamlit/secrets.toml" "$BACKUP_DIR/secrets.toml"
    echo "[before_install] secrets.toml backed up." | tee -a "$LOG"
fi

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] === BeforeInstall DONE ===" | tee -a "$LOG"

#!/usr/bin/env bash
# =============================================================================
# Zamboni — BeforeInstall Hook
# Runs BEFORE CodeDeploy copies files to /opt/zamboni/
#
# Responsibilities:
#   1. Stop the API + control-plane services (so files can be overwritten)
#   2. Backup .env to /tmp — CodeDeploy will overwrite /opt/zamboni/
# =============================================================================

set -euo pipefail

DEPLOY_DIR="/opt/zamboni"
BACKUP_DIR="/tmp/zamboni_deploy_backup"
LOG="/var/log/zamboni-deploy.log"

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] === BeforeInstall START ===" | tee -a "$LOG"

# ── 1. Stop API + control-plane services ──────────────────────────────────────
if systemctl is-active --quiet zamboni-api 2>/dev/null; then
    echo "[before_install] Stopping zamboni-api service..." | tee -a "$LOG"
    systemctl stop zamboni-api
    echo "[before_install] zamboni-api stopped." | tee -a "$LOG"
else
    echo "[before_install] zamboni-api service not running (first deploy?) — skipping stop." | tee -a "$LOG"
fi

for svc in zamboni-control-plane-sync zamboni-control-plane-backup; do
    if systemctl is-active --quiet "$svc" 2>/dev/null; then
        echo "[before_install] Stopping $svc service..." | tee -a "$LOG"
        systemctl stop "$svc"
        echo "[before_install] $svc stopped." | tee -a "$LOG"
    else
        echo "[before_install] $svc service not running (first deploy?) — skipping stop." | tee -a "$LOG"
    fi
done

# zamboni-control-plane-integrity is a .timer-triggered oneshot, not a
# long-running service -- nothing to stop, it just won't fire mid-deploy.

# ── The control-plane SQLite DB lives outside /opt/zamboni entirely
# (/data/zamboni, see after_install.sh) -- CodeDeploy's file copy below
# never touches it, so no backup/restore step is needed here the way
# .env/secrets.toml need one.

# ── 2. Backup .env ────────────────────────────────────────────────────────────
mkdir -p "$BACKUP_DIR"

if [ -f "$DEPLOY_DIR/.env" ]; then
    cp "$DEPLOY_DIR/.env" "$BACKUP_DIR/.env"
    echo "[before_install] .env backed up to $BACKUP_DIR/.env" | tee -a "$LOG"
else
    echo "[before_install] WARNING: $DEPLOY_DIR/.env not found. First deploy?" | tee -a "$LOG"
fi

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] === BeforeInstall DONE ===" | tee -a "$LOG"

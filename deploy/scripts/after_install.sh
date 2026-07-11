#!/usr/bin/env bash
# =============================================================================
# Zamboni — AfterInstall Hook
# Runs AFTER CodeDeploy copies files to /opt/zamboni/
#
# Responsibilities:
#   1. Restore .env from backup
#   2. Restore .streamlit/secrets.toml
#   3. Install Python dependencies
#   4. Set correct file permissions
#   5. Create .streamlit/ dir if not present
# =============================================================================

set -euo pipefail

DEPLOY_DIR="/opt/zamboni"
BACKUP_DIR="/tmp/zamboni_deploy_backup"
LOG="/var/log/zamboni-deploy.log"
PYTHON="python3.11"

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] === AfterInstall START ===" | tee -a "$LOG"

# ── 1. Restore .env ───────────────────────────────────────────────────────────
if [ -f "$BACKUP_DIR/.env" ]; then
    cp "$BACKUP_DIR/.env" "$DEPLOY_DIR/.env"
    echo "[after_install] .env restored from backup." | tee -a "$LOG"
else
    echo "[after_install] WARNING: No .env backup found." | tee -a "$LOG"
    echo "[after_install] Copying .env.example as template — YOU MUST UPDATE /opt/zamboni/.env" | tee -a "$LOG"
    cp "$DEPLOY_DIR/.env.example" "$DEPLOY_DIR/.env"
fi

# ── 2. Restore Streamlit secrets ──────────────────────────────────────────────
mkdir -p "$DEPLOY_DIR/.streamlit"

if [ -f "$BACKUP_DIR/secrets.toml" ]; then
    cp "$BACKUP_DIR/secrets.toml" "$DEPLOY_DIR/.streamlit/secrets.toml"
    echo "[after_install] secrets.toml restored." | tee -a "$LOG"
else
    echo "[after_install] No secrets.toml backup found." | tee -a "$LOG"
    if [ -f "$DEPLOY_DIR/.streamlit/secrets.toml.example" ]; then
        cp "$DEPLOY_DIR/.streamlit/secrets.toml.example" "$DEPLOY_DIR/.streamlit/secrets.toml"
        echo "[after_install] Created secrets.toml from example — update passwords immediately." | tee -a "$LOG"
    fi
fi

# ── 3. Install Python dependencies ────────────────────────────────────────────
echo "[after_install] Installing Python dependencies (production only)..." | tee -a "$LOG"
$PYTHON -m pip install --upgrade pip --quiet
$PYTHON -m pip install -r "$DEPLOY_DIR/requirements.txt" --quiet
echo "[after_install] Dependencies installed." | tee -a "$LOG"

# ── 3b. venv for zamboni-api.service (Phase 6) ────────────────────────────────
# The FastAPI/uvicorn service uses an isolated venv (deploy/systemd/
# zamboni-api.service's ExecStart points at .venv/bin/uvicorn) rather than
# the system-wide install above -- keeps the new API service's dependency
# set (fastapi, uvicorn, python-multipart, httpx) from being entangled with
# the legacy Streamlit service's system Python. Idempotent: skips creation
# if the venv already exists, always re-syncs requirements.
VENV_DIR="$DEPLOY_DIR/.venv"
if [ ! -d "$VENV_DIR" ]; then
    echo "[after_install] Creating venv at $VENV_DIR..." | tee -a "$LOG"
    $PYTHON -m venv "$VENV_DIR"
fi
"$VENV_DIR/bin/pip" install --upgrade pip --quiet
"$VENV_DIR/bin/pip" install -r "$DEPLOY_DIR/requirements.txt" --quiet
echo "[after_install] venv ready at $VENV_DIR." | tee -a "$LOG"

# ── 4. File permissions ───────────────────────────────────────────────────────
chmod +x "$DEPLOY_DIR/deploy/scripts/"*.sh
chmod 600 "$DEPLOY_DIR/.env"
chmod 600 "$DEPLOY_DIR/.streamlit/secrets.toml" 2>/dev/null || true
echo "[after_install] Permissions set." | tee -a "$LOG"

# ── 5. Ensure systemd service file is in place ───────────────────────────────
SERVICE_FILE="/etc/systemd/system/zamboni-app.service"
if [ ! -f "$SERVICE_FILE" ]; then
    echo "[after_install] Creating zamboni-app systemd service..." | tee -a "$LOG"
    cat > "$SERVICE_FILE" << 'EOF'
[Unit]
Description=Zamboni Streamlit App
After=network.target

[Service]
Type=simple
User=ec2-user
WorkingDirectory=/opt/zamboni
EnvironmentFile=/opt/zamboni/.env
ExecStart=/usr/local/bin/streamlit run app/Home.py \
    --server.port 8501 \
    --server.address 0.0.0.0 \
    --server.headless true \
    --browser.gatherUsageStats false
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload
    systemctl enable zamboni-app
    echo "[after_install] zamboni-app service registered." | tee -a "$LOG"
fi

# ── 6. zamboni-api systemd service (Phase 6 -- new, additive) ────────────────
# contracts.md §8: "New systemd unit zamboni-api.service ... Streamlit unit
# untouched until cutover sign-off" -- both services run side by side until
# a manual post-showcase cutover decision, not swapped here.
echo "[after_install] Installing zamboni-api systemd unit..." | tee -a "$LOG"
cp "$DEPLOY_DIR/deploy/systemd/zamboni-api.service" /etc/systemd/system/zamboni-api.service
systemctl daemon-reload
systemctl enable zamboni-api
echo "[after_install] zamboni-api service registered." | tee -a "$LOG"

# ── 7. Control-plane data directory ──────────────────────────────────────────
# /data/zamboni holds the control-plane SQLite DB (stream_registry,
# hk_config, domain_registry, nonprod_registry, controlm_jobs -- SQLite-
# primary in production, see engine/core/control_plane.py) -- deliberately
# OUTSIDE /opt/zamboni, which CodeDeploy replaces wholesale on every
# revision.
#
# 2026-07-11 audit fix: this now lives on a dedicated, retained EBS volume
# (deploy/zamboni-cfn.yaml's ZamboniControlPlaneVolume) that UserData
# mounts here at boot -- NOT the instance's root volume, which is
# DeleteOnTermination=true. Real gap this closes: the previous version of
# this step unconditionally ran `mkdir -p /data/zamboni` regardless of
# whether the dedicated volume actually mounted -- if the mount had
# silently failed (or the CFN template predated the dedicated volume),
# this mkdir would have happily succeeded creating a plain directory on
# ephemeral root storage, masking the failure until the NEXT instance
# replacement wiped it, having never actually been on retained storage at
# all. This step now REFUSES to proceed (exits non-zero, failing the
# whole deploy via this script's `set -euo pipefail`) unless /data/zamboni
# is a genuine mountpoint -- it must already exist by the time CodeDeploy
# reaches this hook, since UserData runs once at boot, before any
# CodeDeploy deployment ever fires.
if ! mountpoint -q /data/zamboni; then
    echo "[after_install] FATAL: /data/zamboni is not a mounted filesystem -- the dedicated control-plane EBS volume did not attach/mount at boot (check /var/log/zamboni/bootstrap.log). Refusing to proceed: writing the control-plane DB to ephemeral root storage would silently defeat its own durability guarantee." | tee -a "$LOG"
    exit 1
fi
chown ec2-user:ec2-user /data/zamboni
echo "[after_install] /data/zamboni confirmed mounted and ready." | tee -a "$LOG"

# ── 8. Control-plane DB schema init/migrate ──────────────────────────────────
# Idempotent -- CREATE TABLE IF NOT EXISTS + guarded ALTER TABLE, safe on
# every deploy. Never touches ZAMBONI_LOCAL_DB or seeds any data.
echo "[after_install] Initializing control-plane DB schema..." | tee -a "$LOG"
"$VENV_DIR/bin/python" "$DEPLOY_DIR/scripts/init_control_plane_db.py" | tee -a "$LOG"

# ── 9. zamboni-control-plane-{sync,backup,integrity} systemd services ───────
# Push the control-plane DB's current state to real Athena and back it up
# to S3 on their own intervals (tunable live via Settings -> Advanced, see
# scripts/control_plane_sync.py / control_plane_backup.py). All three are
# no-ops in ZAMBONI_LOCAL_MODE (never runs on this deployed instance) --
# safe to always install/enable.
for svc in zamboni-control-plane-sync zamboni-control-plane-backup; do
    echo "[after_install] Installing $svc systemd unit..." | tee -a "$LOG"
    cp "$DEPLOY_DIR/deploy/systemd/$svc.service" "/etc/systemd/system/$svc.service"
    systemctl daemon-reload
    systemctl enable "$svc"
    echo "[after_install] $svc service registered." | tee -a "$LOG"
done

echo "[after_install] Installing zamboni-control-plane-integrity timer..." | tee -a "$LOG"
cp "$DEPLOY_DIR/deploy/systemd/zamboni-control-plane-integrity.service" /etc/systemd/system/zamboni-control-plane-integrity.service
cp "$DEPLOY_DIR/deploy/systemd/zamboni-control-plane-integrity.timer" /etc/systemd/system/zamboni-control-plane-integrity.timer
systemctl daemon-reload
systemctl enable zamboni-control-plane-integrity.timer
echo "[after_install] zamboni-control-plane-integrity timer registered." | tee -a "$LOG"

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] === AfterInstall DONE ===" | tee -a "$LOG"

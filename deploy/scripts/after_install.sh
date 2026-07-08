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

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] === AfterInstall DONE ===" | tee -a "$LOG"

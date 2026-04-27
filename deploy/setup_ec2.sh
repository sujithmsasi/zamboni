#!/usr/bin/env bash
# =============================================================================
# Zamboni — EC2 Bootstrap Script
# Run once on a fresh Amazon Linux 2023 EC2 instance to set up Python,
# install dependencies, create directory structure, and register the
# Streamlit app as a systemd service.
#
# Usage:
#   sudo bash deploy/setup_ec2.sh
#
# Idempotent — safe to re-run.
# =============================================================================

set -euo pipefail

APP_DIR="/opt/zamboni"
APP_USER="ec2-user"
PYTHON_BIN="python3.11"
PIP_BIN="pip3.11"
SERVICE_NAME="zamboni-app"

echo "=============================================="
echo " Zamboni — EC2 Bootstrap"
echo "=============================================="
echo " App directory : $APP_DIR"
echo " App user      : $APP_USER"
echo " Python        : $PYTHON_BIN"
echo ""

# ── 1. System packages ─────────────────────────────────────────────────────
echo "[1/6] Installing system packages..."
dnf update -y --quiet
dnf install -y python3.11 python3.11-pip python3.11-devel git gcc --quiet
echo "      ✓ System packages installed"

# ── 2. App directory ───────────────────────────────────────────────────────
echo "[2/6] Creating app directory..."
mkdir -p "$APP_DIR"
chown "$APP_USER":"$APP_USER" "$APP_DIR"
echo "      ✓ Directory: $APP_DIR"

# ── 3. Python dependencies ─────────────────────────────────────────────────
echo "[3/6] Installing Python dependencies..."
if [ -f "$APP_DIR/requirements.txt" ]; then
  sudo -u "$APP_USER" $PIP_BIN install \
    --quiet \
    --no-warn-script-location \
    -r "$APP_DIR/requirements.txt"
  echo "      ✓ Python dependencies installed"
else
  echo "      WARN: requirements.txt not found at $APP_DIR/requirements.txt"
  echo "            Run this script after CodeDeploy has deployed the app."
fi

# ── 4. .env file ───────────────────────────────────────────────────────────
echo "[4/6] Checking .env file..."
if [ ! -f "$APP_DIR/.env" ]; then
  if [ -f "$APP_DIR/.env.example" ]; then
    cp "$APP_DIR/.env.example" "$APP_DIR/.env"
    chown "$APP_USER":"$APP_USER" "$APP_DIR/.env"
    chmod 600 "$APP_DIR/.env"
    echo "      WARN: .env created from .env.example — fill in real values!"
  else
    echo "      WARN: Neither .env nor .env.example found in $APP_DIR"
    echo "            Create $APP_DIR/.env before starting the app."
  fi
else
  echo "      ✓ .env already exists"
fi

# ── 5. Log directory ───────────────────────────────────────────────────────
echo "[5/6] Creating log directory..."
mkdir -p /var/log/zamboni
chown "$APP_USER":"$APP_USER" /var/log/zamboni
echo "      ✓ Log directory: /var/log/zamboni"

# ── 6. Systemd service ─────────────────────────────────────────────────────
echo "[6/6] Registering systemd service..."

cat > /etc/systemd/system/${SERVICE_NAME}.service << EOF_SERVICE
[Unit]
Description=Zamboni Streamlit App
After=network.target
StartLimitIntervalSec=0

[Service]
Type=simple
User=${APP_USER}
WorkingDirectory=${APP_DIR}
EnvironmentFile=${APP_DIR}/.env
ExecStart=/usr/local/bin/streamlit run app/Home.py \\
    --server.port 8501 \\
    --server.address 0.0.0.0 \\
    --server.headless true \\
    --browser.gatherUsageStats false
Restart=on-failure
RestartSec=10
StandardOutput=append:/var/log/zamboni/streamlit.log
StandardError=append:/var/log/zamboni/streamlit.log

[Install]
WantedBy=multi-user.target
EOF_SERVICE

systemctl daemon-reload
systemctl enable "$SERVICE_NAME" --quiet

echo "      ✓ Service registered: $SERVICE_NAME"
echo ""
echo "=============================================="
echo " Bootstrap complete."
echo ""
echo " Next steps:"
echo "   1. Edit $APP_DIR/.env with real values"
echo "   2. Run: bash deploy/create_athena_tables.sh"
echo "   3. Run: python -m engine.monitoring.health_check"
echo "   4. Run: sudo systemctl start $SERVICE_NAME"
echo "   5. Run: sudo systemctl status $SERVICE_NAME"
echo "=============================================="

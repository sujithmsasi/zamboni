#!/usr/bin/env bash
# Bootstrap an EC2 host for Zamboni before CodeDeploy takes over.

set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-python3.11}"
APP_DIR="${APP_DIR:-/opt/zamboni}"

sudo yum update -y
sudo yum install -y git ruby wget gcc openssl-devel bzip2-devel libffi-devel

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  sudo yum install -y python3.11 python3.11-pip || sudo yum install -y python3 python3-pip
fi

sudo mkdir -p "$APP_DIR"
sudo chown -R ec2-user:ec2-user "$APP_DIR"

echo "EC2 bootstrap complete. Deploy the application to $APP_DIR with CodeDeploy."

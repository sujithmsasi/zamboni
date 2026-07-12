#!/usr/bin/env bash
# =============================================================================
# Zamboni — CloudWatch Agent + log rotation setup
#
# Idempotent. Called from after_install.sh on EVERY CodeDeploy deployment
# (the first one on a fresh instance included), not from EC2 UserData --
# UserData only runs once, at first boot, so an ordinary CodeDeploy push to
# an ALREADY-RUNNING instance would never re-execute it. Nothing needs to
# log-ship before the first deployment anyway: the app/engines don't exist
# on the instance (and so produce no output) until CodeDeploy's own Install
# step places them.
#
# Mixed fatality, corrected 2026-07-12 after review: local log ROTATION
# (section 1) is FATAL -- exits 1, which after_install.sh (running under
# set -euo pipefail, no `||` swallow on this call) lets fail the whole
# deployment. zamboni-api/-control-plane-sync/-control-plane-backup now log
# ONLY to a file (StandardOutput=append:..., not journal), so a failure to
# install logrotate/enable its timer is no longer a cosmetic gap covered by
# journald's own bounded retention -- these files would grow completely
# unbounded, a real risk of filling the root volume and taking the instance
# down. The CloudWatch Agent (section 2) stays non-fatal -- shipping logs
# off-box is an observability nice-to-have, not something that threatens
# local disk, so a transient dnf/network issue or a missing
# ZAMBONI_LOG_GROUP only logs a warning and skips that section.
# =============================================================================

set -uo pipefail

DEPLOY_DIR="${DEPLOY_DIR:-/opt/zamboni}"
LOG="${LOG:-/var/log/zamboni-deploy.log}"

log() { echo "[configure_logging] $*" | tee -a "$LOG"; }

# Root-owned, not ec2-user-owned: ec2-user is the account the app services
# run as (zamboni-api etc. all set User=ec2-user), so ec2-user owning this
# directory would let a compromised app process unlink/replace ANY log
# file in it -- not just its own -- since directory write permission (not
# file ownership) is what governs delete/rename in Unix. 755 keeps it
# readable/traversable for debugging via SSH/SSM without granting write
# access. Also migrates ownership of any files already created under the
# OLD ec2-user-owned UserData bootstrap (EC2 UserData still creates this
# directory as ec2-user:ec2-user at first boot, deliberately left
# unchanged there -- see the NOTE above the UserData block in
# zamboni-cfn.yaml -- so this idempotent, every-deploy step is what
# actually corrects ownership on both fresh and already-running instances,
# not a one-time fix that only reaches new ones).
mkdir -p /var/log/zamboni
chown root:root /var/log/zamboni 2>/dev/null || log "WARNING: could not chown /var/log/zamboni to root -- directory ownership hardening skipped (non-fatal)."
chmod 755 /var/log/zamboni 2>/dev/null || true
find /var/log/zamboni -maxdepth 1 -type f -exec chown root:root {} + 2>/dev/null || true

# ── 1. Local weekly rotation ──────────────────────────────────────────────
# 2026-07-12, corrected same day: rotation is installed and enabled as its
# OWN fatal step, separate from the CloudWatch Agent below. Rationale: the
# 3 continuously-running systemd daemons now log ONLY to a file
# (StandardOutput=append:..., not journal -- see deploy/systemd/*.service),
# so a failure to install logrotate/enable its timer is no longer a
# cosmetic gap covered by journald's own bounded retention -- these files
# would grow completely unbounded, a real risk of filling the root volume
# and taking the instance down. This step therefore FAILS THE DEPLOYMENT
# (exit 1, no `|| true`/warn-and-continue) if it doesn't succeed; the
# CloudWatch Agent section further below stays non-fatal, since shipping
# logs off-box is an observability nice-to-have, not something that
# threatens local disk.
if ! dnf install -y logrotate --quiet; then
    log "FATAL: could not install logrotate -- local log files would grow unbounded with no rotation. Failing this deployment rather than silently skipping rotation."
    exit 1
fi
# Two stanzas: the 3 continuously-running systemd daemons (api,
# control-plane-sync, control-plane-backup) hold their log file open for
# the life of the process, so they need copytruncate (truncate in place,
# no restart) -- the default rename+recreate rotation would leave them
# writing to the old, now-renamed inode forever, since nothing tells them
# to reopen the file. zamboni-control-plane-integrity is a Type=oneshot
# unit fired by a daily .timer, and the 5 engine scripts run via EventBridge
# -> SSM Run Command -- both open their log file fresh on every invocation,
# so plain rotation (no copytruncate) is safe for those, same as
# bootstrap.log and zamboni-deploy.log.
#
# `maxsize` is a second trigger alongside `weekly`: without it, a crash
# loop writing continuously could fill the root volume well before the
# next scheduled rotation. logrotate.timer's own daily check (below) means
# maxsize is actually evaluated daily, not just weekly.
if ! cat > /etc/logrotate.d/zamboni <<'LOGROTATECFG'
# Zamboni log rotation -- local copy for on-instance debugging.
# CloudWatch Logs (the stack's LogRetentionDays parameter, default 30d) is
# the centralized, off-instance copy that survives even if this instance
# is replaced/lost -- that's the reason it matters, not necessarily a
# longer retention window (8 weekly rotations here can span longer than
# CloudWatch's 30-day default).
/var/log/zamboni/api.log
/var/log/zamboni/control-plane-sync.log
/var/log/zamboni/control-plane-backup.log {
    weekly
    maxsize 200M
    rotate 8
    compress
    delaycompress
    missingok
    notifempty
    copytruncate
}

/var/log/zamboni/bootstrap.log
/var/log/zamboni/hk.log
/var/log/zamboni/archival.log
/var/log/zamboni/lifecycle_scan.log
/var/log/zamboni/lifecycle.log
/var/log/zamboni/cleanup.log
/var/log/zamboni/control-plane-integrity.log
/var/log/zamboni-deploy.log {
    weekly
    maxsize 200M
    rotate 8
    compress
    delaycompress
    missingok
    notifempty
}
LOGROTATECFG
then
    log "FATAL: could not write /etc/logrotate.d/zamboni -- failing this deployment rather than leaving rotation unconfigured."
    exit 1
fi
log "Wrote /etc/logrotate.d/zamboni (weekly + 200M maxsize, 8 rotations kept locally)."

# AL2023 favors the logrotate package's own systemd timer over a cron.daily
# script -- enabling it directly (rather than installing cronie and relying
# on /etc/cron.daily/logrotate, which may not even be present) is the
# native, reliable trigger here.
if systemctl enable --now logrotate.timer 2>>"$LOG"; then
    log "logrotate.timer enabled -- daily check, rotates per each stanza's own frequency/maxsize."
else
    log "FATAL: could not enable logrotate.timer -- rotation would not run automatically. Failing this deployment rather than leaving log files to grow unbounded."
    exit 1
fi

# ── 2. CloudWatch Agent (needs the log group name) ────────────────────────
# Non-fatal from here on -- see the header comment above.
# Not a CFN stack Output auto-injected anywhere -- this stack takes bucket/
# topic/table names as parameters rather than discovering them, and
# ZAMBONI_LOG_GROUP (from .env, copied from the stack's LogGroupName
# output) follows that same manual-copy convention. Skips CW Agent
# configuration (not the rotation above, which doesn't need it) if unset.
ZAMBONI_LOG_GROUP="${ZAMBONI_LOG_GROUP:-}"
if [ -z "$ZAMBONI_LOG_GROUP" ] && [ -f "$DEPLOY_DIR/.env" ]; then
    ZAMBONI_LOG_GROUP="$(grep -E '^ZAMBONI_LOG_GROUP=' "$DEPLOY_DIR/.env" 2>/dev/null | tail -n1 | cut -d= -f2-)"
fi

# A dotenv value is sometimes quoted (ZAMBONI_LOG_GROUP="/zamboni/app") or
# carries a trailing \r if .env was ever touched on Windows -- interpolated
# raw into the JSON below, either would produce malformed JSON (an
# embedded literal " breaks the string; a raw CR is a control character
# JSON strings must not contain unescaped). Strip both plus surrounding
# whitespace before using it.
ZAMBONI_LOG_GROUP="$(printf '%s' "$ZAMBONI_LOG_GROUP" | tr -d '\r' | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'\$/\1/")"

if [ -z "$ZAMBONI_LOG_GROUP" ]; then
    log "WARNING: ZAMBONI_LOG_GROUP not set (not in the environment, not in $DEPLOY_DIR/.env) -- skipping CloudWatch Agent configuration. See the LogGroupName stack output and .env.example."
    exit 0
fi

# CloudWatch Logs group names only allow [A-Za-z0-9._/#-] -- reject
# anything else now rather than write JSON with an embedded value that
# could break the string (or just silently create the wrong log group).
if ! printf '%s' "$ZAMBONI_LOG_GROUP" | grep -qE '^[A-Za-z0-9._/#-]+$'; then
    log "WARNING: ZAMBONI_LOG_GROUP='$ZAMBONI_LOG_GROUP' contains characters CloudWatch Logs group names don't allow -- skipping CloudWatch Agent configuration rather than writing malformed config."
    exit 0
fi

if ! dnf install -y amazon-cloudwatch-agent --quiet; then
    log "WARNING: could not install amazon-cloudwatch-agent -- log shipping to CloudWatch will not be configured (non-fatal, rotation above already succeeded)."
    exit 0
fi

# Distinctive filename, not the default amazon-cloudwatch-agent.json:
# append-config copies whatever's at -c into
# amazon-cloudwatch-agent.d/<same basename> and merges it in -- if an org
# already has its own fragment under that default name, this would
# overwrite THEIRS on every deploy, the same clobbering append-config was
# chosen over fetch-config to avoid in the first place.
mkdir -p /opt/aws/amazon-cloudwatch-agent/etc
cat > /opt/aws/amazon-cloudwatch-agent/etc/zamboni-logs.json <<CWAGENTCFG
{
  "logs": {
    "logs_collected": {
      "files": {
        "collect_list": [
          {"file_path": "/var/log/zamboni/bootstrap.log", "log_group_name": "$ZAMBONI_LOG_GROUP", "log_stream_name": "{instance_id}/bootstrap"},
          {"file_path": "/var/log/zamboni/hk.log", "log_group_name": "$ZAMBONI_LOG_GROUP", "log_stream_name": "{instance_id}/hk"},
          {"file_path": "/var/log/zamboni/archival.log", "log_group_name": "$ZAMBONI_LOG_GROUP", "log_stream_name": "{instance_id}/archival"},
          {"file_path": "/var/log/zamboni/lifecycle_scan.log", "log_group_name": "$ZAMBONI_LOG_GROUP", "log_stream_name": "{instance_id}/lifecycle_scan"},
          {"file_path": "/var/log/zamboni/lifecycle.log", "log_group_name": "$ZAMBONI_LOG_GROUP", "log_stream_name": "{instance_id}/lifecycle"},
          {"file_path": "/var/log/zamboni/cleanup.log", "log_group_name": "$ZAMBONI_LOG_GROUP", "log_stream_name": "{instance_id}/cleanup"},
          {"file_path": "/var/log/zamboni/api.log", "log_group_name": "$ZAMBONI_LOG_GROUP", "log_stream_name": "{instance_id}/api"},
          {"file_path": "/var/log/zamboni/control-plane-sync.log", "log_group_name": "$ZAMBONI_LOG_GROUP", "log_stream_name": "{instance_id}/control-plane-sync"},
          {"file_path": "/var/log/zamboni/control-plane-backup.log", "log_group_name": "$ZAMBONI_LOG_GROUP", "log_stream_name": "{instance_id}/control-plane-backup"},
          {"file_path": "/var/log/zamboni/control-plane-integrity.log", "log_group_name": "$ZAMBONI_LOG_GROUP", "log_stream_name": "{instance_id}/control-plane-integrity"},
          {"file_path": "/var/log/zamboni-deploy.log", "log_group_name": "$ZAMBONI_LOG_GROUP", "log_stream_name": "{instance_id}/deploy"}
        ]
      }
    }
  }
}
CWAGENTCFG

# append-config (not fetch-config/set-config), on purpose: fetch-config
# REPLACES the agent's entire running configuration with exactly what's in
# -c, which would silently erase any org-managed metrics/log sources
# already configured on this instance (e.g. via SSM Parameter Store or a
# separately-managed config file) the first time this script runs against
# an instance that already has one. append-config merges this file's
# collect_list into whatever's already running instead.
if /opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl \
    -a append-config -m ec2 -s \
    -c file:/opt/aws/amazon-cloudwatch-agent/etc/zamboni-logs.json \
    >>"$LOG" 2>&1; then
    # This confirms the agent accepted the config and (re)started -- it
    # does NOT confirm log data has actually reached CloudWatch. Check
    # the log group in the console, or `aws logs tail`, to verify real
    # ingestion.
    log "CloudWatch Agent configured and started -- configured to ship /var/log/zamboni/*.log and /var/log/zamboni-deploy.log to log group $ZAMBONI_LOG_GROUP (not yet confirmed -- see the note above)."
else
    log "WARNING: CloudWatch Agent append-config/start failed -- log shipping to CloudWatch will not be active. Check 'systemctl status amazon-cloudwatch-agent'."
fi

exit 0

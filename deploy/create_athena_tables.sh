#!/usr/bin/env bash
# =============================================================================
# Zamboni — Create Athena Metadata Tables
# Run once on first deployment to create all 9 Iceberg metadata tables
# (sql/create_*.sql) and apply the two schema-catch-up ALTER files
# (sql/alter_*.sql). Safe to re-run at any time -- CREATE TABLE statements
# use IF NOT EXISTS, and ALTER statements individually report SKIP if the
# column is already there (Athena's ALTER TABLE ADD COLUMNS has no IF NOT
# EXISTS form, so a "column already exists" failure is expected and
# non-fatal on a re-run -- see sql/alter_*.sql's own idempotency notes).
#
# Usage:
#   bash deploy/create_athena_tables.sh
#
# Requirements:
#   - AWS CLI configured with zamboni-ec2-role permissions
#   - ZAMBONI_METADATA_BUCKET and ATHENA_RESULTS_BUCKET set in .env
#   - Athena workgroup zamboni-app must exist
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Load .env
if [ -f "$PROJECT_ROOT/.env" ]; then
  set -a; source "$PROJECT_ROOT/.env"; set +a
else
  echo "ERROR: .env not found at $PROJECT_ROOT/.env"
  echo "       Copy .env.example to .env and fill in values first."
  exit 1
fi

: "${ZAMBONI_METADATA_BUCKET:?ZAMBONI_METADATA_BUCKET not set in .env}"
: "${ATHENA_RESULTS_BUCKET:?ATHENA_RESULTS_BUCKET not set in .env}"
: "${AWS_REGION:=us-west-2}"

WORKGROUP="zamboni-app"
OUTPUT_LOCATION="${ATHENA_RESULTS_BUCKET}ddl/"
ALTER_WARNINGS=0

echo "=============================================="
echo " Zamboni — Create Athena Metadata Tables"
echo "=============================================="
echo " Region          : $AWS_REGION"
echo " Metadata Bucket : $ZAMBONI_METADATA_BUCKET"
echo " Results Bucket  : $ATHENA_RESULTS_BUCKET"
echo " Workgroup       : $WORKGROUP"
echo ""

# ── Helper: run a SQL file against Athena and wait ─────────────────────────
run_sql_file() {
  local file="$1"
  local description="$2"
  local sql

  echo -n "  [CREATE] $description ... "

  # Replace placeholder bucket paths with real values
  sql=$(cat "$file" \
    | sed "s|your-zamboni-metadata-bucket|${ZAMBONI_METADATA_BUCKET#s3://}|g" \
    | sed "s|s3://your-|s3://|g")

  local query_id
  query_id=$(aws athena start-query-execution \
    --query-string "$sql" \
    --work-group "$WORKGROUP" \
    --result-configuration "OutputLocation=$OUTPUT_LOCATION" \
    --region "$AWS_REGION" \
    --query "QueryExecutionId" \
    --output text)

  # Poll until complete
  local state="RUNNING"
  local attempts=0
  while [ "$state" = "RUNNING" ] || [ "$state" = "QUEUED" ]; do
    sleep 3
    state=$(aws athena get-query-execution \
      --query-execution-id "$query_id" \
      --region "$AWS_REGION" \
      --query "QueryExecution.Status.State" \
      --output text)
    attempts=$((attempts + 1))
    if [ $attempts -gt 40 ]; then
      echo "TIMEOUT"
      return 1
    fi
  done

  if [ "$state" = "SUCCEEDED" ]; then
    echo "OK"
  else
    local reason
    reason=$(aws athena get-query-execution \
      --query-execution-id "$query_id" \
      --region "$AWS_REGION" \
      --query "QueryExecution.Status.StateChangeReason" \
      --output text 2>/dev/null || echo "unknown")
    echo "FAILED: $reason"
    return 1
  fi
}

# ── Helper: run one raw SQL statement, tolerant of failure ─────────────────
# Used for the ALTER files only -- Athena's ALTER TABLE ... ADD COLUMNS has
# no IF NOT EXISTS form, so re-running this script against an environment
# that already has a column applied is an EXPECTED failure, not an error.
# Anything else non-'already exists' is reported as a warning (not fatal --
# these are additive schema catch-up, not foundational tables) and counted
# in ALTER_WARNINGS for the closing summary.
run_sql_string() {
  local sql="$1"
  local description="$2"

  echo -n "  [ALTER]  $description ... "

  local query_id
  query_id=$(aws athena start-query-execution \
    --query-string "$sql" \
    --work-group "$WORKGROUP" \
    --result-configuration "OutputLocation=$OUTPUT_LOCATION" \
    --region "$AWS_REGION" \
    --query "QueryExecutionId" \
    --output text)

  local state="RUNNING"
  local attempts=0
  while [ "$state" = "RUNNING" ] || [ "$state" = "QUEUED" ]; do
    sleep 3
    state=$(aws athena get-query-execution \
      --query-execution-id "$query_id" \
      --region "$AWS_REGION" \
      --query "QueryExecution.Status.State" \
      --output text)
    attempts=$((attempts + 1))
    if [ $attempts -gt 40 ]; then
      echo "TIMEOUT (re-run this script to retry)"
      ALTER_WARNINGS=$((ALTER_WARNINGS + 1))
      return 0
    fi
  done

  if [ "$state" = "SUCCEEDED" ]; then
    echo "OK"
    return 0
  fi

  local reason
  reason=$(aws athena get-query-execution \
    --query-execution-id "$query_id" \
    --region "$AWS_REGION" \
    --query "QueryExecution.Status.StateChangeReason" \
    --output text 2>/dev/null || echo "unknown")

  if echo "$reason" | grep -qi "already exist"; then
    echo "SKIP (already applied)"
  else
    echo "WARNING: $reason"
    ALTER_WARNINGS=$((ALTER_WARNINGS + 1))
  fi
  return 0
}

# ── Helper: split an ALTER file into individual statements and apply each ──
# One Athena query-string = one statement, so a file with several
# semicolon-separated ALTER TABLE ... ADD COLUMNS statements (as both
# sql/alter_*.sql files have) can't be sent in one call the way a
# single-statement CREATE TABLE file can. Assumes no semicolon appears
# inside a statement's own content (true for both alter_*.sql files today
# -- neither has a COMMENT string containing one).
apply_alter_file() {
  local file="$1"
  local base
  base=$(basename "$file")
  echo "  --- $base ---"

  local cleaned
  cleaned=$(grep -v '^[[:space:]]*--' "$file" \
    | sed "s|your-zamboni-metadata-bucket|${ZAMBONI_METADATA_BUCKET#s3://}|g" \
    | sed "s|s3://your-|s3://|g" \
    | tr '\n' ' ')

  local old_ifs="$IFS"
  IFS=';'
  local stmt
  for stmt in $cleaned; do
    IFS="$old_ifs"
    stmt="$(echo "$stmt" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
    if [ -z "$stmt" ]; then
      IFS=';'
      continue
    fi

    local desc
    desc=$(echo "$stmt" | grep -oE 'ALTER TABLE [A-Za-z0-9_.]+' | head -1)
    run_sql_string "$stmt" "${desc:-ALTER statement}"
    IFS=';'
  done
  IFS="$old_ifs"
}

# ── Run DDL files in dependency order ─────────────────────────────────────
SQL_DIR="$PROJECT_ROOT/sql"

run_sql_file "$SQL_DIR/create_domain_registry.sql"    "domain_registry"
run_sql_file "$SQL_DIR/create_stream_registry.sql"    "stream_registry"
run_sql_file "$SQL_DIR/create_hk_config.sql"          "hk_config"
run_sql_file "$SQL_DIR/create_execution_log.sql"      "execution_log"
run_sql_file "$SQL_DIR/create_nonprod_registry.sql"   "nonprod_registry"
run_sql_file "$SQL_DIR/create_home_snapshot.sql"      "home_snapshot"
run_sql_file "$SQL_DIR/create_audit_log.sql"          "audit_log"
run_sql_file "$SQL_DIR/create_controlm_jobs.sql"      "controlm_jobs"
run_sql_file "$SQL_DIR/create_vacuum_audit.sql"       "vacuum_audit"

echo ""
echo "── Applying schema catch-up ALTER statements (idempotent-safe) ──"
apply_alter_file "$SQL_DIR/alter_safety_core.sql"
apply_alter_file "$SQL_DIR/alter_control_plane_columns.sql"

echo ""
echo "=============================================="
echo " All 9 metadata tables created successfully."
if [ "$ALTER_WARNINGS" -gt 0 ]; then
  echo " $ALTER_WARNINGS ALTER statement(s) reported a non-'already exists'"
  echo " warning above -- review the output before assuming the schema is"
  echo " fully caught up."
fi
echo " Next: python scripts/aws_smoke_test.py"
echo "=============================================="

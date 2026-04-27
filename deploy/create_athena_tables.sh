#!/usr/bin/env bash
# =============================================================================
# Zamboni — Create Athena Metadata Tables
# Run once on first deployment to create all Iceberg metadata tables.
# Safe to re-run — uses CREATE TABLE IF NOT EXISTS.
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

# ── Run DDL files in dependency order ─────────────────────────────────────
SQL_DIR="$PROJECT_ROOT/sql"

run_sql_file "$SQL_DIR/create_domain_registry.sql"    "domain_registry"
run_sql_file "$SQL_DIR/create_stream_registry.sql"    "stream_registry"
run_sql_file "$SQL_DIR/create_hk_config.sql"          "hk_config"
run_sql_file "$SQL_DIR/create_execution_log.sql"      "execution_log"
run_sql_file "$SQL_DIR/create_nonprod_registry.sql"   "nonprod_registry"
run_sql_file "$SQL_DIR/create_home_snapshot.sql"      "home_snapshot"

echo ""
echo "=============================================="
echo " All metadata tables created successfully."
echo " Next: python -m engine.monitoring.health_check"
echo "=============================================="

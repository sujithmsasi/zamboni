#!/usr/bin/env bash
# Create Zamboni metadata tables in Athena in dependency order.

set -euo pipefail

WORKGROUP="${ATHENA_WG_APP:-zamboni-app}"
DATABASE="${ATHENA_DATABASE:-zamboni_catalog}"
CATALOG="${ATHENA_CATALOG:-glue_catalog}"
REGION="${AWS_REGION:-us-west-2}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

sql_files=(
  "sql/create_domain_registry.sql"
  "sql/create_stream_registry.sql"
  "sql/create_hk_config.sql"
  "sql/create_execution_log.sql"
  "sql/create_nonprod_registry.sql"
  "sql/create_home_snapshot.sql"
)

for rel_path in "${sql_files[@]}"; do
  file="$ROOT_DIR/$rel_path"
  if [[ ! -f "$file" ]]; then
    echo "Missing SQL file: $file" >&2
    exit 1
  fi

  echo "Submitting $rel_path"
  query_id="$(
    aws athena start-query-execution \
      --region "$REGION" \
      --work-group "$WORKGROUP" \
      --query-execution-context "Catalog=$CATALOG,Database=$DATABASE" \
      --query-string "file://$file" \
      --query 'QueryExecutionId' \
      --output text
  )"

  while true; do
    state="$(
      aws athena get-query-execution \
        --region "$REGION" \
        --query-execution-id "$query_id" \
        --query 'QueryExecution.Status.State' \
        --output text
    )"
    case "$state" in
      SUCCEEDED) break ;;
      FAILED|CANCELLED)
        aws athena get-query-execution --region "$REGION" --query-execution-id "$query_id"
        exit 1
        ;;
      *) sleep 3 ;;
    esac
  done
done

echo "Zamboni metadata table creation complete."

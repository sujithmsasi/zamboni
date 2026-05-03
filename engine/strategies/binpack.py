"""
Zamboni — Binpack Strategy
Builds OPTIMIZE SQL for Athena bin-pack compaction.
"""
from __future__ import annotations

from engine.utils.logger import get_logger
from engine.utils.partition_utils import parse_table_fqn

log = get_logger(__name__)


def build_optimize_sql(
    table_fqn: str,
    target_file_size_mb: int = 128,
    partition_filter: str | None = None,
) -> str:
    """
    Build OPTIMIZE ... REWRITE DATA SQL for Athena.

    Args:
        table_fqn:           Fully qualified table name
        target_file_size_mb: Target file size in MB
        partition_filter:    Optional WHERE clause fragment for partition pruning
                             e.g. "partition_date >= DATE '2026-03-01'"

    Returns:
        SQL string ready to execute via athena_client.run_query()
    """
    catalog, database, table = parse_table_fqn(table_fqn)

    where_clause = f"\nWHERE {partition_filter}" if partition_filter else ""

    sql = (
        f"OPTIMIZE TABLE {catalog}.{database}.{table} "
        f"REWRITE DATA "
        f"USING BIN_PACK "
        f"WITH (file_size_limit = '{target_file_size_mb}MB'){where_clause}"
    )

    log.info(
        "binpack.sql_built",
        table_fqn=table_fqn,
        target_mb=target_file_size_mb,
        has_partition_filter=partition_filter is not None,
    )
    return sql


def estimate_output_files(total_size_gb: float, target_file_size_mb: int) -> int:
    """Estimate how many files will result after bin-pack compaction."""
    if total_size_gb <= 0 or target_file_size_mb <= 0:
        return 0
    total_mb = total_size_gb * 1024
    return max(1, int(total_mb / target_file_size_mb))

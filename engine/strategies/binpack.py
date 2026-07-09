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

    Athena engine v3 OPTIMIZE syntax (verified against AWS docs, same class
    of "no catalog prefix" restriction vacuum.py already hard-codes for
    VACUUM): `OPTIMIZE [db_name.]table_name REWRITE DATA USING BIN_PACK
    [WHERE predicate]` — no `TABLE` keyword, no catalog-qualified 3-part
    name, no inline sizing clause. `target_file_size_mb` is NOT embedded in
    the SQL (Athena's OPTIMIZE takes no such option) — it's logged for
    visibility only. The real target size must already be set on the table
    via `write_target_data_file_size_bytes` in TBLPROPERTIES ahead of time
    (engine/core/property_sync.py::apply_vacuum_properties, which
    orchestrator.py runs before compaction/vacuum on every table's first
    HK run).

    Args:
        table_fqn:           Fully qualified table name (catalog.database.table)
        target_file_size_mb: Target file size in MB, for logging only
        partition_filter:    Optional WHERE clause fragment for partition pruning
                             e.g. "partition_date >= DATE '2026-03-01'"

    Returns:
        SQL string ready to execute via athena_client.run_query()
    """
    _, database, table = parse_table_fqn(table_fqn)

    where_clause = f"\nWHERE {partition_filter}" if partition_filter else ""

    sql = f"OPTIMIZE {database}.{table} REWRITE DATA USING BIN_PACK{where_clause}"

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

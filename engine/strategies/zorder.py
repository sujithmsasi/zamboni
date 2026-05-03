"""
Zamboni — Z-Order Strategy
Builds Glue job parameters for z-order compaction.
Z-order multi-dimensionally clusters data by multiple columns simultaneously —
best for tables queried by multiple independent filter columns (e.g. date + region + product).
"""
from __future__ import annotations

from engine.utils.logger import get_logger
from engine.utils.partition_utils import parse_table_fqn

log = get_logger(__name__)


def build_glue_params(
    table_fqn: str,
    zorder_columns: list[str],
    target_file_size_mb: int = 512,
    worker_type: str = "G.2X",
    num_workers: int = 5,
    execution_class: str = "FLEX",
    partition_filter: str | None = None,
) -> dict:
    """
    Build Glue job parameters for z-order compaction.
    These are passed as --job-args to the zamboni_compaction Glue job.

    Args:
        table_fqn:           Fully qualified table name
        zorder_columns:      Columns to z-order by (max recommended: 4)
        target_file_size_mb: Target output file size
        worker_type:         G.1X | G.2X | G.4X (dynamically selected)
        num_workers:         Number of Glue workers
        execution_class:     FLEX | STANDARD
        partition_filter:    Optional partition filter SQL fragment

    Returns:
        Dict of Glue job arguments
    """
    if len(zorder_columns) > 4:
        log.warning(
            "zorder.too_many_columns",
            table_fqn=table_fqn,
            count=len(zorder_columns),
            recommendation="Z-order effectiveness drops significantly beyond 4 columns",
        )

    catalog, database, table = parse_table_fqn(table_fqn)

    params = {
        "--strategy":            "zorder",
        "--catalog":             catalog,
        "--database":            database,
        "--table":               table,
        "--table_fqn":           table_fqn,
        "--zorder_columns":      ",".join(zorder_columns),
        "--target_file_size_mb": str(target_file_size_mb),
        "--worker_type":         worker_type,
        "--num_workers":         str(num_workers),
        "--execution_class":     execution_class,
    }

    if partition_filter:
        params["--partition_filter"] = partition_filter

    log.info(
        "zorder.params_built",
        table_fqn=table_fqn,
        zorder_columns=zorder_columns,
        worker_type=worker_type,
        execution_class=execution_class,
    )
    return params


def recommend_num_workers(total_size_gb: float, worker_type: str) -> int:
    """
    Z-order is more compute-intensive than sort.
    Recommend slightly more workers than sort for same table size.
    """
    capacity = {"G.1X": 3, "G.2X": 6, "G.4X": 12}
    gb_per_worker = capacity.get(worker_type, 6)
    workers = max(2, int(total_size_gb / gb_per_worker) + 1)
    return min(workers, 50)

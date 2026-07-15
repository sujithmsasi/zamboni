"""
Zamboni — Sort Strategy
Builds Glue job parameters for sort-based compaction.
Sort compaction rewrites files ordered by specified columns — improves
query performance on range predicates.
"""
from __future__ import annotations

from engine.utils.logger import get_logger
from engine.utils.partition_utils import parse_table_fqn

log = get_logger(__name__)


def build_glue_params(
    table_fqn: str,
    sort_columns: list[str],
    target_file_size_mb: int = 512,
    worker_type: str = "G.2X",
    num_workers: int = 5,
    execution_class: str = "FLEX",
    partition_filter: str | None = None,
) -> dict:
    """
    Build Glue job parameters for sort compaction.
    These are passed as --job-args to the zamboni_compaction Glue job.

    Args:
        table_fqn:           Fully qualified table name
        sort_columns:        Columns to sort by (order matters)
        target_file_size_mb: Target output file size
        worker_type:         G.1X | G.2X | G.4X (dynamically selected)
        num_workers:         Number of Glue workers
        execution_class:     FLEX | STANDARD (tier-based selection)
        partition_filter:    Optional partition filter SQL fragment

    Returns:
        Dict of Glue job arguments

    2026-07-15 note: "--catalog" here is deliberately still sourced from
    table_fqn's own catalog segment (always "glue_catalog" for a registered
    table) -- this is the ONE place that name should flow through. It's the
    Spark/Iceberg catalog name the zamboni_compaction Glue job's own Spark
    session is configured with (spark.sql.catalog.glue_catalog=...), not an
    Athena Data Catalog concept -- Athena SQL never needs it (see
    config/settings.py / engine/utils/athena_client.py).
    """
    catalog, database, table = parse_table_fqn(table_fqn)

    params = {
        "--strategy":            "sort",
        "--catalog":             catalog,
        "--database":            database,
        "--table":               table,
        "--table_fqn":           table_fqn,
        "--sort_columns":        ",".join(sort_columns),
        "--target_file_size_mb": str(target_file_size_mb),
        "--worker_type":         worker_type,
        "--num_workers":         str(num_workers),
        "--execution_class":     execution_class,
    }

    if partition_filter:
        params["--partition_filter"] = partition_filter

    log.info(
        "sort.params_built",
        table_fqn=table_fqn,
        sort_columns=sort_columns,
        worker_type=worker_type,
        execution_class=execution_class,
    )
    return params


def recommend_num_workers(total_size_gb: float, worker_type: str) -> int:
    """
    Recommend number of Glue workers for sort compaction
    based on table size and worker type.
    """
    # DPU capacity per worker type (approximate GB processable per worker)
    capacity = {"G.1X": 4, "G.2X": 8, "G.4X": 16}
    gb_per_worker = capacity.get(worker_type, 8)
    workers = max(2, int(total_size_gb / gb_per_worker) + 1)
    return min(workers, 50)  # cap at 50 workers

"""
Zamboni — Compaction Operation
Routes to the correct compaction strategy:
  - binpack  → Athena OPTIMIZE REWRITE DATA
  - sort     → Glue job (zamboni_compaction) with sort strategy
  - zorder   → Glue job (zamboni_compaction) with zorder strategy
"""
from __future__ import annotations

import os
import time

import boto3

from config.settings import AWS_REGION
from engine.core.health_checker import HealthResult
from engine.operations.dynamic_router import RoutingDecision, route
from engine.strategies import binpack, sort, zorder
from engine.utils.athena_client import get_query_stats, run_query
from engine.utils.logger import get_logger
from engine.utils.partition_utils import build_hot_partition_filter

log = get_logger(__name__)

# Glue job name — set via environment or .env
COMPACTION_GLUE_JOB = os.getenv("COMPACTION_GLUE_JOB_NAME", "zamboni-compaction")


def run_compaction(
    table_fqn: str,
    hk_config: dict,
    health: HealthResult,
    tier: str,
    dry_run: bool = False,
) -> dict:
    """
    Run compaction for a table using the strategy defined in hk_config.
    Dynamically selects worker type and execution class based on table metrics.

    Args:
        table_fqn:  Fully qualified table name
        hk_config:  Row from hk_config for this table
        health:     HealthResult from health_checker
        tier:       Table tier (critical | standard | low)
        dry_run:    If True, build and log SQL/params but do not execute

    Returns:
        Dict with operation results — files_compacted, bytes_rewritten,
        athena_query_id, glue_run_id, routing_decision
    """
    strategy    = hk_config.get("compaction_strategy", "binpack")
    target_mb   = hk_config.get("compaction_target_file_size_mb", 128)
    part_col    = hk_config.get("partition_column")
    part_days   = hk_config.get("partition_filter_days")
    # v2 B.7: prefer processing_cadence (drives lookback window) when set
    cadence     = hk_config.get("processing_cadence")

    # Build partition filter if configured. Cadence-driven window takes
    # priority; legacy partition_filter_days is the fallback.
    partition_filter = (
        build_hot_partition_filter(
            part_col, days=part_days, processing_cadence=cadence
        ) if part_col else None
    )

    # Dynamic routing — worker type + execution class from metrics
    routing = route(
        tier=tier,
        total_size_gb=health.total_size_gb,
        total_files=health.total_files,
    )

    log.info(
        "compaction.start",
        table_fqn=table_fqn,
        strategy=strategy,
        routing=routing.reason,
        dry_run=dry_run,
    )

    if strategy == "binpack":
        return _run_athena_binpack(
            table_fqn, target_mb, partition_filter, routing, dry_run
        )
    elif strategy == "sort":
        sort_cols = hk_config.get("sort_columns") or []
        return _run_glue_compaction(
            table_fqn, "sort", sort_cols, target_mb,
            partition_filter, routing, dry_run
        )
    elif strategy == "zorder":
        zorder_cols = hk_config.get("sort_columns") or []
        return _run_glue_compaction(
            table_fqn, "zorder", zorder_cols, target_mb,
            partition_filter, routing, dry_run
        )
    else:
        raise ValueError(f"Unknown compaction strategy '{strategy}' for {table_fqn}")


# ── Athena Binpack ─────────────────────────────────────────────────────────────

def _run_athena_binpack(
    table_fqn: str,
    target_mb: int,
    partition_filter: str | None,
    routing: RoutingDecision,
    dry_run: bool,
) -> dict:
    sql = binpack.build_optimize_sql(
        table_fqn=table_fqn,
        target_file_size_mb=target_mb,
        partition_filter=partition_filter,
    )

    # Athena workgroup — use critical workgroup for STANDARD tier
    wg = "critical" if routing.execution_class == "STANDARD" else "standard"

    query_id = run_query(sql, workgroup=wg, dry_run=dry_run)

    result = {
        "strategy":       "binpack",
        "engine":         "athena",
        "athena_query_id": query_id,
        "routing":        routing.reason,
        "dry_run":        dry_run,
    }

    if query_id and not dry_run:
        stats = get_query_stats(query_id)
        result["bytes_scanned"] = stats.get("bytes_scanned", 0)

    log.info("compaction.binpack_done", table_fqn=table_fqn, query_id=query_id)
    return result


# ── Glue Sort / Z-Order ───────────────────────────────────────────────────────

def _run_glue_compaction(
    table_fqn: str,
    strategy: str,
    columns: list[str],
    target_mb: int,
    partition_filter: str | None,
    routing: RoutingDecision,
    dry_run: bool,
) -> dict:
    if strategy == "sort":
        sort.recommend_num_workers(0, routing.worker_type)
        job_args = sort.build_glue_params(
            table_fqn=table_fqn,
            sort_columns=columns,
            target_file_size_mb=target_mb,
            worker_type=routing.worker_type,
            num_workers=routing.num_workers,
            execution_class=routing.execution_class,
            partition_filter=partition_filter,
        )
    else:
        job_args = zorder.build_glue_params(
            table_fqn=table_fqn,
            zorder_columns=columns,
            target_file_size_mb=target_mb,
            worker_type=routing.worker_type,
            num_workers=routing.num_workers,
            execution_class=routing.execution_class,
            partition_filter=partition_filter,
        )

    result = {
        "strategy":  strategy,
        "engine":    "glue",
        "job_name":  COMPACTION_GLUE_JOB,
        "job_args":  job_args,
        "routing":   routing.reason,
        "dry_run":   dry_run,
    }

    if dry_run:
        log.info(
            "compaction.glue_dry_run",
            table_fqn=table_fqn,
            strategy=strategy,
            job_args=job_args,
        )
        return result

    # Submit Glue job
    glue    = boto3.client("glue", region_name=AWS_REGION)
    response = glue.start_job_run(
        JobName=COMPACTION_GLUE_JOB,
        Arguments=job_args,
        WorkerType=routing.worker_type,
        NumberOfWorkers=routing.num_workers,
        ExecutionClass=routing.execution_class,
    )
    run_id = response["JobRunId"]
    result["glue_run_id"] = run_id

    log.info(
        "compaction.glue_submitted",
        table_fqn=table_fqn,
        strategy=strategy,
        run_id=run_id,
        worker_type=routing.worker_type,
        execution_class=routing.execution_class,
    )

    # Poll until complete
    _wait_for_glue_job(glue, run_id)
    result["status"] = "SUCCEEDED"
    return result


def _wait_for_glue_job(glue, run_id: str, poll_interval: int = 15) -> None:
    """Poll Glue job until terminal state."""
    while True:
        resp  = glue.get_job_run(JobName=COMPACTION_GLUE_JOB, RunId=run_id)
        state = resp["JobRun"]["JobRunState"]
        if state == "SUCCEEDED":
            return
        if state in ("FAILED", "ERROR", "TIMEOUT", "STOPPED"):
            error = resp["JobRun"].get("ErrorMessage", "unknown")
            raise RuntimeError(f"Glue job {run_id} {state}: {error}")
        log.debug("compaction.glue_polling", run_id=run_id, state=state)
        time.sleep(poll_interval)

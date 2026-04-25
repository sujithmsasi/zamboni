"""
Zamboni — Health Checker
Queries Iceberg metadata tables ($snapshots, $files, $manifests)
to assess table health and determine which operations are needed.
"""
from dataclasses import dataclass
from typing import Optional

from engine.utils.athena_client import read_sql
from engine.utils.logger import get_logger
from engine.utils.partition_utils import parse_table_fqn

log = get_logger(__name__)


@dataclass
class HealthResult:
    """Health assessment for a single Iceberg table."""
    table_fqn:              str

    # Snapshot health
    snapshot_count:         int   = 0
    oldest_snapshot_days:   int   = 0
    latest_snapshot_ts:     Optional[str] = None

    # File health
    total_files:            int   = 0
    avg_file_size_mb:       float = 0.0
    small_file_count:       int   = 0    # files < 64MB
    total_size_gb:          float = 0.0

    # Operations needed
    needs_compaction:       bool  = False
    needs_vacuum:           bool  = False
    needs_orphan_cleanup:   bool  = False

    # Reasons
    compaction_reason:      str   = ""
    vacuum_reason:          str   = ""

    # Raw check success
    check_success:          bool  = True
    check_error:            Optional[str] = None


def check(
    table_fqn: str,
    hk_config: dict,
    workgroup: str = "standard",
) -> HealthResult:
    """
    Full health check for a table.
    Returns a HealthResult — the HK Engine uses this to decide what to run.

    Args:
        table_fqn:  Fully qualified table name
        hk_config:  Row from hk_config table for this table
        workgroup:  Athena workgroup tier to use
    """
    result = HealthResult(table_fqn=table_fqn)

    try:
        _check_snapshots(table_fqn, hk_config, result, workgroup)
        _check_files(table_fqn, hk_config, result, workgroup)
    except Exception as e:
        log.error("health_checker.check_failed", table_fqn=table_fqn, error=str(e))
        result.check_success = False
        result.check_error   = str(e)

    log.info(
        "health_checker.result",
        table_fqn=table_fqn,
        snapshots=result.snapshot_count,
        small_files=result.small_file_count,
        needs_compaction=result.needs_compaction,
        needs_vacuum=result.needs_vacuum,
    )
    return result


def _check_snapshots(
    table_fqn: str,
    config: dict,
    result: HealthResult,
    workgroup: str,
) -> None:
    """Query $snapshots metadata table and assess vacuum need."""
    _, database, table = parse_table_fqn(table_fqn)

    sql = f"""
        SELECT
            COUNT(*)                                                AS snapshot_count,
            MAX(made_current_at)                                    AS latest_snapshot_ts,
            DATE_DIFF(
                'day',
                MIN(made_current_at),
                NOW()
            )                                                       AS oldest_snapshot_days
        FROM "glue_catalog"."{database}"."{table}$snapshots"
    """

    df = read_sql(sql, workgroup=workgroup, database=database)

    if df.empty or df.iloc[0]["snapshot_count"] is None:
        return

    row                         = df.iloc[0]
    result.snapshot_count       = int(row["snapshot_count"])
    result.oldest_snapshot_days = int(row.get("oldest_snapshot_days") or 0)
    result.latest_snapshot_ts   = str(row.get("latest_snapshot_ts") or "")

    # Assess vacuum need
    retention_days = config.get("snapshot_retention_days", 7)
    min_to_keep    = config.get("snapshot_min_to_keep", 30)

    if result.snapshot_count > min_to_keep and result.oldest_snapshot_days > retention_days:
        result.needs_vacuum   = True
        result.vacuum_reason  = (
            f"{result.snapshot_count} snapshots, "
            f"oldest is {result.oldest_snapshot_days} days "
            f"(retention: {retention_days} days, floor: {min_to_keep})"
        )


def _check_files(
    table_fqn: str,
    config: dict,
    result: HealthResult,
    workgroup: str,
) -> None:
    """Query $files metadata table and assess compaction need."""
    _, database, table = parse_table_fqn(table_fqn)

    sql = f"""
        SELECT
            COUNT(*)                                AS total_files,
            ROUND(AVG(file_size_in_bytes) / 1e6, 2) AS avg_file_size_mb,
            SUM(CASE WHEN file_size_in_bytes < 64 * 1024 * 1024
                     THEN 1 ELSE 0 END)             AS small_file_count,
            ROUND(SUM(file_size_in_bytes) / 1e9, 3) AS total_size_gb
        FROM "glue_catalog"."{database}"."{table}$files"
    """

    df = read_sql(sql, workgroup=workgroup, database=database)

    if df.empty or df.iloc[0]["total_files"] is None:
        return

    row                     = df.iloc[0]
    result.total_files      = int(row["total_files"])
    result.avg_file_size_mb = float(row.get("avg_file_size_mb") or 0)
    result.small_file_count = int(row.get("small_file_count") or 0)
    result.total_size_gb    = float(row.get("total_size_gb") or 0)

    # Assess compaction need
    target_mb = config.get("compaction_target_file_size_mb", 128)

    small_file_pct = (
        result.small_file_count / result.total_files * 100
        if result.total_files > 0 else 0
    )

    if small_file_pct > 20 or result.avg_file_size_mb < (target_mb * 0.5):
        result.needs_compaction  = True
        result.compaction_reason = (
            f"{result.small_file_count}/{result.total_files} files "
            f"below 64MB ({small_file_pct:.0f}%), "
            f"avg size {result.avg_file_size_mb:.1f}MB "
            f"(target: {target_mb}MB)"
        )


def get_snapshot_count(table_fqn: str, workgroup: str = "standard") -> int:
    """
    Lightweight snapshot count — used by circuit breaker and CLI tools
    without a full health check.
    """
    _, database, table = parse_table_fqn(table_fqn)
    sql = f"""
        SELECT COUNT(*) AS cnt
        FROM "glue_catalog"."{database}"."{table}$snapshots"
    """
    df = read_sql(sql, workgroup=workgroup, database=database)
    if df.empty:
        return 0
    return int(df.iloc[0]["cnt"])


def is_healthy(result: HealthResult) -> bool:
    """
    Return True if a table needs no housekeeping actions.
    A healthy table: no compaction, no vacuum, no orphan cleanup needed.
    """
    return (
        result.check_success
        and not result.needs_compaction
        and not result.needs_vacuum
        and not result.needs_orphan_cleanup
    )

"""
Zamboni -- Health Checker
Queries Iceberg metadata tables ($snapshots, $files, $manifests)
to assess table health and determine which operations are needed.

Hardening (Sprint 7 -- 6.1):
  - needs_orphan_cleanup now driven by hk_config.orphan_cleanup_cadence_days
    (default 7 days) checked against last_orphan_cleanup_at.
  - Orphan cleanup is safe to schedule: only flagged when cadence has elapsed
    AND the orphan_file_retention_days threshold has passed since last write.
  - Added orphan_reason to HealthResult for audit trail.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC

from engine.utils.athena_client import read_sql
from engine.utils.logger import get_logger
from engine.utils.partition_utils import parse_table_fqn

log = get_logger(__name__)

# ── Default thresholds (overridable via hk_config) ───────────────────────────
_DEFAULT_ORPHAN_RETENTION_DAYS  = 3   # orphan files older than N days
_DEFAULT_ORPHAN_CADENCE_DAYS    = 7   # run orphan cleanup at most every N days
_DEFAULT_SMALL_FILE_PCT         = 20  # % of files below 64MB triggers compaction
_DEFAULT_TARGET_FILE_MB         = 128


@dataclass
class HealthResult:
    """Health assessment for a single Iceberg table."""
    table_fqn:              str

    # Snapshot health
    snapshot_count:         int        = 0
    oldest_snapshot_days:   int        = 0
    latest_snapshot_ts:     str | None = None

    # File health
    total_files:            int        = 0
    avg_file_size_mb:       float      = 0.0
    small_file_count:       int        = 0      # files < 64 MB
    total_size_gb:          float      = 0.0

    # Operations needed
    needs_compaction:       bool       = False
    needs_vacuum:           bool       = False
    needs_orphan_cleanup:   bool       = False

    # Reasons (for execution_log and debugging)
    compaction_reason:      str        = ""
    vacuum_reason:          str        = ""
    orphan_reason:          str        = ""

    # Raw check success
    check_success:          bool       = True
    check_error:            str | None = None


def check(
    table_fqn: str,
    hk_config: dict,
    workgroup: str = "standard",
    last_orphan_cleanup_at: str | None = None,
) -> HealthResult:
    """
    Full health check for a table.
    Returns a HealthResult -- the HK Engine uses this to decide what to run.

    Args:
        table_fqn:               Fully qualified table name
        hk_config:               Row from hk_config for this table
        workgroup:               Athena workgroup tier to use
        last_orphan_cleanup_at:  ISO timestamp of last orphan cleanup run.
                                 Passed from execution_log to enforce cadence.
    """
    result = HealthResult(table_fqn=table_fqn)

    try:
        _check_snapshots(table_fqn, hk_config, result, workgroup)
        _check_files(table_fqn, hk_config, result, workgroup)
        _check_orphan_cleanup(hk_config, result, last_orphan_cleanup_at)
    except Exception as e:
        log.error("health_checker.check_failed",
                  table_fqn=table_fqn, error=str(e))
        result.check_success = False
        result.check_error   = str(e)

    log.info(
        "health_checker.result",
        table_fqn=table_fqn,
        snapshots=result.snapshot_count,
        small_files=result.small_file_count,
        needs_compaction=result.needs_compaction,
        needs_vacuum=result.needs_vacuum,
        needs_orphan_cleanup=result.needs_orphan_cleanup,
    )
    return result


# ── Private check functions ───────────────────────────────────────────────────

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

    retention_days = int(config.get("snapshot_retention_days") or 7)
    min_to_keep    = int(config.get("snapshot_min_to_keep")    or 30)

    if (
        result.snapshot_count > min_to_keep
        and result.oldest_snapshot_days > retention_days
    ):
        result.needs_vacuum  = True
        result.vacuum_reason = (
            f"{result.snapshot_count} snapshots, "
            f"oldest is {result.oldest_snapshot_days}d "
            f"(retention: {retention_days}d, floor: {min_to_keep})"
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
    result.small_file_count = int(row.get("small_file_count")   or 0)
    result.total_size_gb    = float(row.get("total_size_gb")    or 0)

    target_mb      = int(config.get("compaction_target_file_size_mb") or _DEFAULT_TARGET_FILE_MB)
    small_file_pct = (
        result.small_file_count / result.total_files * 100
        if result.total_files > 0 else 0
    )

    if small_file_pct > _DEFAULT_SMALL_FILE_PCT or result.avg_file_size_mb < (target_mb * 0.5):
        result.needs_compaction  = True
        result.compaction_reason = (
            f"{result.small_file_count}/{result.total_files} files "
            f"below 64MB ({small_file_pct:.0f}%), "
            f"avg size {result.avg_file_size_mb:.1f}MB "
            f"(target: {target_mb}MB)"
        )


def _check_orphan_cleanup(
    config: dict,
    result: HealthResult,
    last_orphan_cleanup_at: str | None,
) -> None:
    """
    Determine if orphan file cleanup is due.

    Safe cadence rules (6.1):
      1. orphan_cleanup_cadence_days (default 7) must have elapsed since
         last_orphan_cleanup_at. This prevents runaway cleanup on every
         HK trigger -- orphan cleanup touches S3 directly.
      2. orphan_file_retention_days (default 3) must be configured -- this
         is the safety margin so files written by concurrent writers are
         never deleted while still in use.
      3. If cadence config is 0 or negative, cleanup is disabled entirely.

    This function does NOT query Athena -- the scheduling decision is made
    purely from config + execution_log timestamp. The actual orphan file
    scan happens in vacuum.run_orphan_cleanup().
    """
    from datetime import datetime

    # Use explicit None check so 0 (disabled) is respected, not coerced to default
    _raw_cadence   = config.get("orphan_cleanup_cadence_days")
    _raw_retention = config.get("orphan_file_retention_days")
    cadence_days   = int(_raw_cadence)   if _raw_cadence   is not None else _DEFAULT_ORPHAN_CADENCE_DAYS
    retention_days = int(_raw_retention) if _raw_retention is not None else _DEFAULT_ORPHAN_RETENTION_DAYS

    # Cadence = 0 means explicitly disabled
    if cadence_days <= 0:
        result.orphan_reason = "orphan_cleanup disabled (cadence_days=0)"
        return

    # Retention must be at least 2 days to be safe
    if retention_days < 2:
        result.orphan_reason = (
            f"orphan_cleanup skipped: retention_days={retention_days} < 2 "
            "(unsafe, must be >= 2 to protect in-flight writers)"
        )
        log.warning(
            "health_checker.orphan_unsafe_config",
            retention_days=retention_days,
        )
        return

    # Check cadence: is it due?
    if last_orphan_cleanup_at:
        try:
            from dateutil import parser as dtparser
            last_dt = dtparser.parse(str(last_orphan_cleanup_at))
            if last_dt.tzinfo is None:
                from pytz import utc
                last_dt = utc.localize(last_dt)
            days_since = (datetime.now(UTC) - last_dt).days
            if days_since < cadence_days:
                result.orphan_reason = (
                    f"orphan_cleanup not due ({days_since}d since last run, "
                    f"cadence={cadence_days}d)"
                )
                return
        except Exception as e:
            log.warning("health_checker.orphan_cadence_parse_error",
                        last_ts=last_orphan_cleanup_at, error=str(e))
            # Fail safe -- skip if we can't parse the timestamp
            result.orphan_reason = f"orphan_cleanup skipped: could not parse last_ts ({e})"
            return

    # All checks passed -- schedule cleanup
    result.needs_orphan_cleanup = True
    result.orphan_reason = (
        f"orphan cleanup due (cadence={cadence_days}d, "
        f"retention={retention_days}d)"
        + (f", last run: {last_orphan_cleanup_at}" if last_orphan_cleanup_at else
           ", never run")
    )


# ── Public helpers ────────────────────────────────────────────────────────────

def get_snapshot_count(table_fqn: str, workgroup: str = "standard") -> int:
    """Lightweight snapshot count for circuit breaker and CLI tools."""
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
    A healthy table: check succeeded, nothing flagged as needed.
    """
    return (
        result.check_success
        and not result.needs_compaction
        and not result.needs_vacuum
        and not result.needs_orphan_cleanup
    )

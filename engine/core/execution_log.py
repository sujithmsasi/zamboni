"""
Zamboni — Execution Log
Write and query the unified execution log for all three engines.
Every operation — success, failure, skip, dry-run — gets a record here.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from config.settings import EXECUTION_LOG_TABLE
from engine.utils.athena_client import read_sql, run_query
from engine.utils.logger import get_logger

log = get_logger(__name__)


@dataclass
class LogEntry:
    """Represents a single engine operation to be logged."""

    # Run identity
    run_id:         str
    engine:         str          # hk | archival | lifecycle
    operation:      str          # compaction | vacuum | orphan_cleanup | archival | ...

    # Table context
    table_fqn:      str
    domain:         str
    layer:          str
    tier:           str
    environment:    str

    # Outcome
    status:         str          # SUCCESS | FAILURE | SKIPPED | DRY_RUN
    dry_run:        bool         = False
    skip_reason:    str | None = None
    error_message:  str | None = None

    # Timing
    started_at:     datetime | None = None
    completed_at:   datetime | None = None
    duration_seconds: int | None    = None

    # HK Engine metrics
    snapshots_before:     int | None   = None
    snapshots_after:      int | None   = None
    snapshots_expired:    int | None   = None
    orphan_files_deleted: int | None   = None
    files_compacted:      int | None   = None
    bytes_rewritten:      int | None   = None

    # Archival Engine metrics
    partition_date:   date | None  = None
    rows_archived:    int | None   = None
    bytes_archived:   int | None   = None
    archive_s3_path:  str | None   = None
    pre_validation:   str | None   = None
    post_validation:  str | None   = None

    # Cost tracking
    athena_query_id:  str | None   = None
    bytes_scanned:    int | None   = None

    # Safety Core (Workstream A / Phase 1a — contracts.md §3.2)
    lock_id:                  str | None = None
    metadata_location_before: str | None = None
    metadata_location_after:  str | None = None
    snapshot_id_before:       int | None = None
    snapshot_id_after:        int | None = None
    integrity_status:         str | None = None  # VERIFIED | FAILED | SKIPPED

    # Auto-generated
    execution_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    execution_date: date = field(default_factory=date.today)


def write(entry: LogEntry, dry_run: bool = False) -> bool:
    """
    Write a log entry to the execution_log Iceberg table.
    Returns True on success.
    """
    now = datetime.now(UTC)

    # Auto-fill timing if not provided
    if entry.started_at is None:
        entry.started_at = now
    if entry.completed_at is None:
        entry.completed_at = now
    if entry.duration_seconds is None and entry.started_at and entry.completed_at:
        entry.duration_seconds = int(
            (entry.completed_at - entry.started_at).total_seconds()
        )

    def _s(v) -> str:
        """SQL string literal or NULL."""
        return f"'{str(v).replace(chr(39), chr(39)*2)}'" if v is not None else "NULL"

    def _n(v) -> str:
        """SQL numeric literal or NULL."""
        return str(int(v)) if v is not None else "NULL"

    def _b(v) -> str:
        """SQL boolean."""
        return str(bool(v)).lower()

    def _ts(v) -> str:
        """SQL TIMESTAMP literal or NULL."""
        if v is None:
            return "NULL"
        if isinstance(v, datetime):
            return f"TIMESTAMP '{v.strftime('%Y-%m-%d %H:%M:%S')}'"
        return "NULL"

    def _dt(v) -> str:
        """SQL DATE literal or NULL."""
        if v is None:
            return "NULL"
        return f"DATE '{v.isoformat()}'"

    sql = f"""
        INSERT INTO {EXECUTION_LOG_TABLE} VALUES (
            {_s(entry.execution_id)},
            {_s(entry.run_id)},
            {_s(entry.engine)},
            {_s(entry.operation)},
            {_s(entry.table_fqn)},
            {_s(entry.domain)},
            {_s(entry.layer)},
            {_s(entry.tier)},
            {_s(entry.environment)},
            {_s(entry.status)},
            {_s(entry.skip_reason)},
            {_s(entry.error_message)},
            {_b(entry.dry_run)},
            {_ts(entry.started_at)},
            {_ts(entry.completed_at)},
            {_n(entry.duration_seconds)},
            {_n(entry.snapshots_before)},
            {_n(entry.snapshots_after)},
            {_n(entry.snapshots_expired)},
            {_n(entry.orphan_files_deleted)},
            {_n(entry.files_compacted)},
            {_n(entry.bytes_rewritten)},
            {_dt(entry.partition_date)},
            {_n(entry.rows_archived)},
            {_n(entry.bytes_archived)},
            {_s(entry.archive_s3_path)},
            {_s(entry.pre_validation)},
            {_s(entry.post_validation)},
            {_s(entry.athena_query_id)},
            {_n(entry.bytes_scanned)},
            {_dt(entry.execution_date)},
            {_s(entry.lock_id)},
            {_s(entry.metadata_location_before)},
            {_s(entry.metadata_location_after)},
            {_n(entry.snapshot_id_before)},
            {_n(entry.snapshot_id_after)},
            {_s(entry.integrity_status)}
        )
    """

    log.info(
        "execution_log.write",
        execution_id=entry.execution_id,
        engine=entry.engine,
        operation=entry.operation,
        table_fqn=entry.table_fqn,
        status=entry.status,
        dry_run=dry_run,
    )

    run_query(sql, workgroup="app", dry_run=dry_run)
    return True


def new_run_id() -> str:
    """Generate a new run_id to group all operations in one engine invocation."""
    return str(uuid.uuid4())


# ══════════════════════════════════════════════════════════════════════════════
#  QUERY
# ══════════════════════════════════════════════════════════════════════════════

def get_recent_runs(table_fqn: str, days: int = 7) -> list[dict]:
    """Return recent execution log entries for a table."""
    sql = f"""
        SELECT * FROM {EXECUTION_LOG_TABLE}
        WHERE table_fqn    = '{table_fqn}'
          AND execution_date >= CURRENT_DATE - INTERVAL '{days}' DAY
        ORDER BY started_at DESC
    """
    df = read_sql(sql, workgroup="app")
    return df.to_dict(orient="records")


def get_failure_count(table_fqn: str, days: int = 30) -> int:
    """
    Return the number of FAILURE records for a table in the last N days.
    Used by the circuit breaker to decide whether to disable a table.
    """
    sql = f"""
        SELECT COUNT(*) AS cnt
        FROM {EXECUTION_LOG_TABLE}
        WHERE table_fqn    = '{table_fqn}'
          AND status       = 'FAILURE'
          AND execution_date >= CURRENT_DATE - INTERVAL '{days}' DAY
    """
    df = read_sql(sql, workgroup="app")
    if df.empty:
        return 0
    return int(df.iloc[0]["cnt"])


def get_last_run(
    table_fqn: str,
    operation: str | None = None,
    only_success: bool = True,
) -> dict | None:
    """
    Return the most recent execution log entry for a table.

    v2 B.4 — by default returns only SUCCESS runs so that failed/incomplete
    prior runs do not block retries. Pass only_success=False to get the
    most recent run regardless of status.

    Args:
        table_fqn:    Fully qualified table name
        operation:    Optional operation filter (compaction|vacuum|hk_run|...)
        only_success: If True, only consider SUCCESS rows for dedupe purposes
    """
    conds = [f"table_fqn = '{table_fqn}'"]
    if operation:
        conds.append(f"operation = '{operation}'")
    if only_success:
        conds.append("status = 'SUCCESS'")
    where = " AND ".join(conds)

    sql = f"""
        SELECT * FROM {EXECUTION_LOG_TABLE}
        WHERE {where}
        ORDER BY started_at DESC
        LIMIT 1
    """
    df = read_sql(sql, workgroup="app")
    if df.empty:
        return None
    return df.iloc[0].to_dict()


def get_running(table_fqn: str) -> dict | None:
    """
    Return the most recent execution_log row with status='RUNNING' for a
    table, or None. Used by Gate 0's in-flight check (contracts.md §4 step 3).

    No engine writes status='RUNNING' yet as of Phase 1a -- the Phase 1b
    orchestrator will mark each step RUNNING at start and terminal at
    completion. This check is wired into Gate 0 now so it activates without
    further changes once that write path lands.
    """
    sql = f"""
        SELECT * FROM {EXECUTION_LOG_TABLE}
        WHERE table_fqn = '{table_fqn}'
          AND status    = 'RUNNING'
        ORDER BY started_at DESC
        LIMIT 1
    """
    df = read_sql(sql, workgroup="app")
    if df.empty:
        return None
    return df.iloc[0].to_dict()


def get_domain_summary(domain: str, days: int = 7) -> dict:
    """Return aggregated execution stats for a domain."""
    sql = f"""
        SELECT
            engine,
            operation,
            status,
            COUNT(*)                        AS runs,
            SUM(snapshots_expired)          AS snapshots_expired,
            SUM(orphan_files_deleted)       AS orphans_deleted,
            SUM(bytes_rewritten)            AS bytes_rewritten,
            AVG(duration_seconds)           AS avg_duration_sec
        FROM {EXECUTION_LOG_TABLE}
        WHERE domain         = '{domain}'
          AND execution_date >= CURRENT_DATE - INTERVAL '{days}' DAY
        GROUP BY engine, operation, status
        ORDER BY engine, operation
    """
    df = read_sql(sql, workgroup="app")
    return df.to_dict(orient="records")

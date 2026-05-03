"""
Zamboni — Activity Scanner
Queries CloudTrail (via Athena) to determine last_query_at and last_write_at
for non-prod Iceberg tables. Used by the Lifecycle Engine scan to populate
activity signals that drive stale detection.

If CloudTrail is not configured (CLOUDTRAIL_TABLE is empty), the scanner
returns graceful None values so the lifecycle engine falls back to
Glue catalog CreateTime as the activity baseline.

CloudTrail table schema expected:
  eventTime       TIMESTAMP
  eventName       STRING    (StartQueryExecution, CreateTable, etc.)
  requestParameters STRING  (JSON blob containing database/table references)
  userIdentity    STRING
  awsRegion       STRING

Two signals:
  last_query_at — most recent StartQueryExecution referencing the table
  last_write_at — most recent write event (CreateTable, BatchCreatePartition,
                   UpdateTable, PutObject pattern matching table prefix)
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from config.settings import AWS_REGION, CLOUDTRAIL_LOOKBACK_DAYS, CLOUDTRAIL_TABLE
from engine.utils.logger import get_logger

log = get_logger(__name__)


@dataclass
class ActivitySignals:
    """Activity signals for a single non-prod table."""
    table_fqn:          str
    last_query_at:      datetime | None = None
    last_write_at:      datetime | None = None
    days_since_activity: int | None     = None
    source:             str               = "none"  # cloudtrail | glue_create | none


def get_activity_signals(
    table_fqn: str,
    database: str,
    table_name: str,
    glue_create_time: datetime | None = None,
) -> ActivitySignals:
    """
    Retrieve activity signals for a non-prod table.

    Priority:
      1. CloudTrail (if CLOUDTRAIL_TABLE is configured)
      2. Glue CreateTime as baseline (last_write_at = created_at)
      3. None (no signal available)

    Args:
        table_fqn:         FQN for result attribution
        database:          Glue database name
        table_name:        Table name
        glue_create_time:  Table CreateTime from Glue catalog (fallback)

    Returns:
        ActivitySignals with populated timestamps and days_since_activity
    """
    signals = ActivitySignals(table_fqn=table_fqn)

    # ── Try CloudTrail first ──────────────────────────────────────────────────
    if CLOUDTRAIL_TABLE:
        try:
            signals = _query_cloudtrail(
                table_fqn, database, table_name,
                glue_create_time,
            )
            log.info(
                "activity_scanner.cloudtrail",
                table_fqn=table_fqn,
                last_query_at=str(signals.last_query_at),
                last_write_at=str(signals.last_write_at),
                days_since=signals.days_since_activity,
            )
            return signals
        except Exception as e:
            log.warning(
                "activity_scanner.cloudtrail_failed",
                table_fqn=table_fqn,
                error=str(e),
            )

    # ── Fallback: Glue CreateTime ─────────────────────────────────────────────
    if glue_create_time:
        if glue_create_time.tzinfo is None:
            from pytz import utc
            glue_create_time = utc.localize(glue_create_time)

        signals.last_write_at       = glue_create_time
        signals.days_since_activity = _days_since(glue_create_time)
        signals.source              = "glue_create"
        log.info(
            "activity_scanner.glue_fallback",
            table_fqn=table_fqn,
            days_since=signals.days_since_activity,
        )

    return signals


def _query_cloudtrail(
    table_fqn: str,
    database: str,
    table_name: str,
    glue_create_time: datetime | None,
) -> ActivitySignals:
    """Query Athena CloudTrail table for last_query_at and last_write_at."""
    from engine.utils.athena_client import read_sql

    signals = ActivitySignals(table_fqn=table_fqn, source="cloudtrail")
    lookback = CLOUDTRAIL_LOOKBACK_DAYS

    # ── last_query_at: StartQueryExecution events referencing this table ──────
    query_sql = f"""
        SELECT MAX(eventTime) AS last_query_at
        FROM {CLOUDTRAIL_TABLE}
        WHERE eventName = 'StartQueryExecution'
          AND awsRegion = '{AWS_REGION}'
          AND eventTime >= NOW() - INTERVAL '{lookback}' DAY
          AND (
              LOWER(requestParameters) LIKE '%{database.lower()}%'
              AND LOWER(requestParameters) LIKE '%{table_name.lower()}%'
          )
    """

    try:
        df = read_sql(query_sql, workgroup="nonprod")
        if not df.empty and df.iloc[0]["last_query_at"] is not None:
            signals.last_query_at = _parse_ts(df.iloc[0]["last_query_at"])
    except Exception as e:
        log.warning("activity_scanner.query_signal_failed",
                    table_fqn=table_fqn, error=str(e))

    # ── last_write_at: write events on this table ─────────────────────────────
    write_events = [
        "CreateTable", "UpdateTable", "BatchCreatePartition",
        "UpdatePartition", "BatchDeletePartition",
    ]
    events_str = "', '".join(write_events)

    write_sql = f"""
        SELECT MAX(eventTime) AS last_write_at
        FROM {CLOUDTRAIL_TABLE}
        WHERE eventName IN ('{events_str}')
          AND awsRegion = '{AWS_REGION}'
          AND eventTime >= NOW() - INTERVAL '{lookback}' DAY
          AND LOWER(requestParameters) LIKE '%{table_name.lower()}%'
          AND LOWER(requestParameters) LIKE '%{database.lower()}%'
    """

    try:
        df = read_sql(write_sql, workgroup="nonprod")
        if not df.empty and df.iloc[0]["last_write_at"] is not None:
            signals.last_write_at = _parse_ts(df.iloc[0]["last_write_at"])
    except Exception as e:
        log.warning("activity_scanner.write_signal_failed",
                    table_fqn=table_fqn, error=str(e))

    # ── Fallback last_write_at to Glue CreateTime if no CloudTrail write ──────
    if signals.last_write_at is None and glue_create_time:
        if glue_create_time.tzinfo is None:
            from pytz import utc
            glue_create_time = utc.localize(glue_create_time)
        signals.last_write_at = glue_create_time

    # ── days_since_activity: max of query and write ───────────────────────────
    candidates = [s for s in [signals.last_query_at, signals.last_write_at]
                  if s is not None]
    if candidates:
        most_recent             = max(candidates)
        signals.days_since_activity = _days_since(most_recent)

    return signals


def _days_since(ts: datetime) -> int:
    """Return integer days since a given datetime."""
    if ts.tzinfo is None:
        from pytz import utc
        ts = utc.localize(ts)
    delta = datetime.now(UTC) - ts
    return max(0, delta.days)


def _parse_ts(value) -> datetime | None:
    """Parse various timestamp formats to timezone-aware datetime."""
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            from pytz import utc
            return utc.localize(value)
        return value
    try:
        from dateutil import parser as dtparser
        dt = dtparser.parse(str(value))
        if dt.tzinfo is None:
            from pytz import utc
            dt = utc.localize(dt)
        return dt
    except Exception:
        return None


def bulk_get_activity_signals(
    tables: list[dict],
    batch_size: int = 50,
) -> dict[str, ActivitySignals]:
    """
    Get activity signals for a batch of tables.
    Returns dict mapping table_fqn → ActivitySignals.
    Used by lifecycle scan to avoid N+1 queries.
    """
    results = {}
    for table in tables:
        fqn         = table.get("table_fqn", "")
        database    = table.get("glue_database", "")
        table_name  = table.get("table_name", "")
        created_raw = table.get("created_at")

        created_at = None
        if created_raw:
            created_at = _parse_ts(created_raw)

        results[fqn] = get_activity_signals(
            table_fqn=fqn,
            database=database,
            table_name=table_name,
            glue_create_time=created_at,
        )
    return results

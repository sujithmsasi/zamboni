"""
Zamboni — Partition Utilities
Date-range filters for HK Engine (hot partition window)
and Archival Engine (cold partition discovery).
"""
from datetime import date, timedelta
from typing import Optional
from engine.utils.logger import get_logger

log = get_logger(__name__)


def build_hot_partition_filter(
    partition_column: str,
    days: Optional[int],
    reference_date: Optional[date] = None,
) -> Optional[str]:
    """
    Build a WHERE clause to restrict compaction to recently-written partitions.

    Args:
        partition_column:  e.g. 'partition_date'
        days:              Only process partitions from last N days.
                           None = no filter (process all partitions).
        reference_date:    Base date. Defaults to today.

    Returns:
        SQL fragment e.g. "partition_date >= DATE '2026-03-01'" or None.
    """
    if days is None:
        return None
    ref    = reference_date or date.today()
    cutoff = ref - timedelta(days=days)
    return f"{partition_column} >= DATE '{cutoff.isoformat()}'"


def build_cold_partition_filter(
    partition_column: str,
    retention_days: int,
    reference_date: Optional[date] = None,
) -> str:
    """
    Build a WHERE clause to find cold (archivable) partitions.
    Partitions older than retention_days are eligible for archival.

    Returns:
        SQL fragment e.g. "partition_date < DATE '2026-02-01'"
    """
    ref    = reference_date or date.today()
    cutoff = ref - timedelta(days=retention_days)
    return f"{partition_column} < DATE '{cutoff.isoformat()}'"


def parse_table_fqn(fqn: str) -> tuple[str, str, str]:
    """
    Parse 'catalog.database.table' into (catalog, database, table).
    Raises ValueError for unexpected formats.
    """
    parts = fqn.split(".")
    if len(parts) != 3:
        raise ValueError(
            f"Invalid table FQN '{fqn}'. Expected format: catalog.database.table"
        )
    return parts[0], parts[1], parts[2]


def build_archive_s3_prefix(
    domain: str,
    table_name: str,
    partition_date: date,
    archive_bucket: str,
) -> str:
    """
    Build the S3 prefix for an archived partition.
    Convention: s3://<bucket>/staging_archive/<domain>/<table>/partition_date=<date>/
    """
    return (
        f"{archive_bucket.rstrip('/')}/staging_archive"
        f"/{domain}/{table_name}"
        f"/partition_date={partition_date.isoformat()}/"
    )


def date_range(start: date, end: date) -> list[date]:
    """Return a list of dates from start (inclusive) to end (inclusive)."""
    days  = (end - start).days
    return [start + timedelta(days=i) for i in range(days + 1)]

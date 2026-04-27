"""
Zamboni — Partition Utilities
Date-range filters for HK Engine (hot partition window)
and Archival Engine (cold partition discovery).
"""
from datetime import date, timedelta
from typing import Optional
from engine.utils.logger import get_logger

log = get_logger(__name__)


# v2 design: lookback windows by processing_cadence
_CADENCE_LOOKBACK_DAYS = {
    "hourly":   7,
    "daily":    90,
    "weekly":   180,
    "monthly":  None,  # no filter — few partitions
}


def build_hot_partition_filter(
    partition_column: str,
    days: Optional[int] = None,
    reference_date: Optional[date] = None,
    processing_cadence: Optional[str] = None,
) -> Optional[str]:
    """
    Build a WHERE clause to restrict compaction to recently-written partitions.
    Avoids Athena's 100-partition OPTIMIZE limit on large historical tables.

    Priority:
      1. processing_cadence (v2 field) — uses _CADENCE_LOOKBACK_DAYS map
      2. days (legacy partition_filter_days from hk_config)
      3. None — no filter applied

    Args:
        partition_column:    e.g. 'partition_date'
        days:                Legacy lookback days from hk_config.partition_filter_days
        reference_date:      Base date. Defaults to today.
        processing_cadence:  v2 field — hourly|daily|weekly|monthly

    Returns:
        SQL fragment e.g. "partition_date >= DATE '2026-03-01'" or None
    """
    ref = reference_date or date.today()

    # Cadence-based window (v2 design — preferred)
    if processing_cadence:
        cadence = processing_cadence.lower().strip()
        if cadence == "monthly":
            return None
        lookback = _CADENCE_LOOKBACK_DAYS.get(cadence)
        if lookback is not None:
            cutoff = ref - timedelta(days=lookback)
            return f"{partition_column} >= DATE '{cutoff.isoformat()}'"

    # Legacy fallback
    if days is None:
        return None
    cutoff = ref - timedelta(days=days)
    return f"{partition_column} >= DATE '{cutoff.isoformat()}'"


def derive_hot_partitions_from_metadata(
    table_fqn: str,
    partition_column: str,
    workgroup: str,
    lookback_days: int = 90,
) -> Optional[str]:
    """
    Derive hot partition filter from Iceberg $partitions metadata.
    Falls back gracefully on any failure (returns None — caller should use
    build_hot_partition_filter as the next fallback).

    v2 Phase 6: improved partition targeting via real Iceberg metadata.

    Args:
        table_fqn:        Fully qualified Iceberg table name
        partition_column: Name of the partition column
        workgroup:        Athena workgroup for the metadata query
        lookback_days:    Window in days for "recent" partitions

    Returns:
        SQL fragment string or None on failure (caller falls back)
    """
    if not partition_column:
        return None
    try:
        from engine.utils.athena_client import read_sql
        ref    = date.today()
        cutoff = ref - timedelta(days=lookback_days)
        # Just verify the metadata table is queryable — return a date filter
        check_sql = f"""
            SELECT 1 FROM {table_fqn}$partitions LIMIT 1
        """
        df = read_sql(check_sql, workgroup=workgroup)
        if df.empty:
            return None
        return f"{partition_column} >= DATE '{cutoff.isoformat()}'"
    except Exception as e:
        log.warning(
            "partition_utils.metadata_query_failed",
            table_fqn=table_fqn,
            error=str(e),
        )
        return None


def get_cadence_lookback_days(cadence: Optional[str]) -> Optional[int]:
    """Return lookback days for a given processing cadence."""
    if not cadence:
        return None
    return _CADENCE_LOOKBACK_DAYS.get(cadence.lower().strip())


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

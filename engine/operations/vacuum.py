"""
Zamboni — Vacuum Operation
Two operations:
  1. expire_snapshots — removes old snapshots via Athena VACUUM
  2. orphan_cleanup   — removes orphan files via Athena VACUUM
"""
from engine.core.health_checker import HealthResult
from engine.utils.athena_client import run_query, get_query_stats
from engine.utils.partition_utils import parse_table_fqn
from engine.utils.logger import get_logger
from config.settings import SNAPSHOT_MIN_FLOOR, ORPHAN_MIN_RETENTION_HOURS

log = get_logger(__name__)


def run_expire_snapshots(
    table_fqn: str,
    hk_config: dict,
    health: HealthResult,
    tier: str,
    dry_run: bool = False,
) -> dict:
    """
    Expire old snapshots using Athena VACUUM.
    Respects hard floor — never removes below SNAPSHOT_MIN_FLOOR.

    Returns dict with snapshots_expired, athena_query_id.
    """
    retention_days = hk_config.get("snapshot_retention_days", 7)
    min_to_keep    = max(
        hk_config.get("snapshot_min_to_keep", SNAPSHOT_MIN_FLOOR),
        SNAPSHOT_MIN_FLOOR,
    )

    # Safety check — don't run if it would go below the floor
    if health.snapshot_count <= min_to_keep:
        log.info(
            "vacuum.skip_snapshot_floor",
            table_fqn=table_fqn,
            snapshot_count=health.snapshot_count,
            min_to_keep=min_to_keep,
        )
        return {
            "operation":        "expire_snapshots",
            "skipped":          True,
            "skip_reason":      f"snapshot_count ({health.snapshot_count}) <= min_to_keep ({min_to_keep})",
            "snapshots_expired": 0,
        }

    catalog, database, table = parse_table_fqn(table_fqn)

    sql = (
        f"VACUUM TABLE {catalog}.{database}.{table} "
        f"EXPIRE SNAPSHOTS "
        f"WITH (retention_threshold = '{retention_days}d', "
        f"min_snapshots_to_keep = {min_to_keep})"
    )

    wg = "critical" if tier == "critical" else "standard"

    log.info(
        "vacuum.expire_snapshots",
        table_fqn=table_fqn,
        retention_days=retention_days,
        min_to_keep=min_to_keep,
        current_snapshots=health.snapshot_count,
        dry_run=dry_run,
    )

    query_id = run_query(sql, workgroup=wg, dry_run=dry_run)

    result = {
        "operation":         "expire_snapshots",
        "athena_query_id":   query_id,
        "retention_days":    retention_days,
        "min_to_keep":       min_to_keep,
        "snapshots_before":  health.snapshot_count,
        "dry_run":           dry_run,
    }

    if query_id and not dry_run:
        stats = get_query_stats(query_id)
        result["bytes_scanned"] = stats.get("bytes_scanned", 0)

    return result


def run_orphan_cleanup(
    table_fqn: str,
    hk_config: dict,
    tier: str,
    dry_run: bool = False,
) -> dict:
    """
    Remove orphan files using Athena VACUUM.
    Hard minimum retention of ORPHAN_MIN_RETENTION_HOURS (48h).

    Returns dict with athena_query_id.
    """
    configured_days = hk_config.get("orphan_file_retention_days", 2)

    # Hard minimum — never delete files newer than 48h
    min_hours = ORPHAN_MIN_RETENTION_HOURS
    configured_hours = configured_days * 24
    retention_hours  = max(configured_hours, min_hours)

    catalog, database, table = parse_table_fqn(table_fqn)

    sql = (
        f"VACUUM TABLE {catalog}.{database}.{table} "
        f"REMOVE ORPHAN FILES "
        f"WITH (retention_threshold = '{retention_hours}h')"
    )

    wg = "critical" if tier == "critical" else "standard"

    log.info(
        "vacuum.orphan_cleanup",
        table_fqn=table_fqn,
        retention_hours=retention_hours,
        dry_run=dry_run,
    )

    query_id = run_query(sql, workgroup=wg, dry_run=dry_run)

    result = {
        "operation":       "orphan_cleanup",
        "athena_query_id": query_id,
        "retention_hours": retention_hours,
        "dry_run":         dry_run,
    }

    if query_id and not dry_run:
        stats = get_query_stats(query_id)
        result["bytes_scanned"] = stats.get("bytes_scanned", 0)

    return result

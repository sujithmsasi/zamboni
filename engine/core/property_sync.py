"""
Zamboni — Property Sync Workflow
v2 Phase 4 — set vacuum table properties once during registration.

If stream_registry.properties_synced is false, run an ALTER TABLE SET
TBLPROPERTIES to apply vacuum_max_snapshot_age_seconds and
vacuum_min_snapshots_to_keep, then mark properties_synced=true so we never
ALTER at runtime.

Backward compatible — gracefully handles missing properties_synced column
(treats as null/false) and missing last_execution_id field.
"""
from typing import Optional
from datetime import datetime, timezone

from engine.utils.logger import get_logger

log = get_logger(__name__)


def needs_property_sync(table_row: dict) -> bool:
    """
    Return True if this table needs vacuum properties applied.
    Treats missing column or null as 'needs sync'.
    """
    val = table_row.get("properties_synced")
    if val is None:
        return True
    # Athena returns booleans as Python bool
    return not bool(val)


def apply_vacuum_properties(
    table_fqn: str,
    hk_config: dict,
    workgroup: str,
    dry_run: bool = False,
) -> dict:
    """
    Run ALTER TABLE SET TBLPROPERTIES for vacuum control on this table.
    Pulls retention values from hk_config:
      snapshot_retention_days  → vacuum_max_snapshot_age_seconds
      snapshot_min_to_keep     → vacuum_min_snapshots_to_keep

    Returns a result dict with status + properties applied.
    Caller is responsible for marking properties_synced=true on success.
    """
    snap_days  = int(hk_config.get("snapshot_retention_days") or 7)
    snap_min   = int(hk_config.get("snapshot_min_to_keep")    or 30)
    snap_secs  = snap_days * 86400

    sql = f"""
        ALTER TABLE {table_fqn} SET TBLPROPERTIES (
            'vacuum_max_snapshot_age_seconds' = '{snap_secs}',
            'vacuum_min_snapshots_to_keep'    = '{snap_min}'
        )
    """

    result = {
        "table_fqn":       table_fqn,
        "vacuum_max_age":  snap_secs,
        "vacuum_min_keep": snap_min,
        "status":          "DRY_RUN" if dry_run else "PENDING",
    }

    if dry_run:
        log.info(
            "property_sync.dry_run",
            table_fqn=table_fqn,
            vacuum_max_age_seconds=snap_secs,
            vacuum_min_to_keep=snap_min,
        )
        return result

    try:
        from engine.utils.athena_client import run_query
        run_query(sql, workgroup=workgroup)
        result["status"] = "SUCCESS"
        log.info(
            "property_sync.applied",
            table_fqn=table_fqn,
            vacuum_max_age_seconds=snap_secs,
            vacuum_min_to_keep=snap_min,
        )
    except Exception as e:
        result["status"] = "FAILURE"
        result["error"]  = str(e)
        log.error(
            "property_sync.failed",
            table_fqn=table_fqn,
            error=str(e),
        )

    return result


def mark_properties_synced(
    table_fqn: str,
    workgroup: str,
    dry_run: bool = False,
) -> bool:
    """
    Set properties_synced=true on stream_registry for this table.
    Backward compatible — silently skips if column does not exist.
    """
    from config.settings import STREAM_REGISTRY_TABLE

    sql = f"""
        UPDATE {STREAM_REGISTRY_TABLE}
        SET properties_synced = true,
            updated_at        = TIMESTAMP '{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}'
        WHERE table_fqn = '{table_fqn}'
    """
    if dry_run:
        log.info("property_sync.mark_synced.dry_run", table_fqn=table_fqn)
        return True

    try:
        from engine.utils.athena_client import run_query
        run_query(sql, workgroup=workgroup)
        return True
    except Exception as e:
        # If column doesn't exist (older env), silently skip — backward compat
        msg = str(e).lower()
        if "column" in msg and "properties_synced" in msg:
            log.info(
                "property_sync.mark_synced.column_missing",
                table_fqn=table_fqn,
            )
            return False
        log.warning(
            "property_sync.mark_synced.failed",
            table_fqn=table_fqn, error=str(e),
        )
        return False

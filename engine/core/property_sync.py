"""
Zamboni -- Property Sync Workflow
Set vacuum table properties once at first HK run.

Gap 4: Now uses commit-frequency tier to pick retention values
       (HIGH/MEDIUM/LOW based on commits/day from $snapshots).
Gap 8: Adds vacuum_max_metadata_files_to_keep and
       write_target_data_file_size_bytes to the ALTER TABLE.

If stream_registry.properties_synced is false, run ALTER TABLE SET
TBLPROPERTIES, then mark properties_synced=true so we never ALTER at runtime.
"""
from __future__ import annotations

from datetime import UTC, datetime

from engine.core.commit_frequency import (
    TIER_PROPERTIES,
    classify_tier_from_config,
    get_commit_stats,
)
from engine.utils.athena_client import run_query
from engine.utils.logger import get_logger

log = get_logger(__name__)


def needs_property_sync(table_row: dict) -> bool:
    """Return True if vacuum properties have not been applied yet."""
    val = table_row.get("properties_synced")
    if val is None:
        return True
    return not bool(val)


def apply_vacuum_properties(
    table_fqn: str,
    hk_config:  dict,
    workgroup:  str,
    dry_run:    bool = False,
) -> dict:
    """
    Run ALTER TABLE SET TBLPROPERTIES for vacuum control.

    Gap 4: Determines correct retention values from commit frequency tier.
           Falls back to config-based tier when $snapshots is unavailable.
    Gap 8: Sets all four properties:
           - vacuum_max_snapshot_age_seconds
           - vacuum_min_snapshots_to_keep
           - vacuum_max_metadata_files_to_keep (NEW)
           - write_target_data_file_size_bytes  (NEW)

    Returns result dict with status + properties applied.
    """
    # ── Determine tier ────────────────────────────────────────────────────────
    # Try live commit frequency first (accurate).
    # Fall back to config-based classification (fast, no Athena call).
    try:
        from config.settings import ZAMBONI_LOCAL_MODE
        if not ZAMBONI_LOCAL_MODE:
            commit_stats = get_commit_stats(table_fqn, workgroup=workgroup)
            if commit_stats.error:
                # Commit stats query failed — fall back to config-based tier
                raise RuntimeError(commit_stats.error)
            tier_name   = commit_stats.commit_tier
            tier_source = "live_commit_frequency"
        else:
            raise RuntimeError("local mode — skip live commit query")
    except Exception:
        tier_name   = classify_tier_from_config(hk_config)
        tier_source = "config_snapshot_retention_days"

    props = TIER_PROPERTIES[tier_name]

    age_secs      = props["vacuum_max_snapshot_age_seconds"]
    min_snapshots = props["vacuum_min_snapshots_to_keep"]
    max_meta      = props["vacuum_max_metadata_files_to_keep"]
    file_size     = props["write_target_data_file_size_bytes"]

    # Zamboni floor: never go below SNAPSHOT_MIN_FLOOR regardless of tier
    from config.settings import SNAPSHOT_MIN_FLOOR
    min_snapshots = max(min_snapshots, SNAPSHOT_MIN_FLOOR)

    sql = f"""
        ALTER TABLE {table_fqn} SET TBLPROPERTIES (
            'vacuum_max_snapshot_age_seconds'   = '{age_secs}',
            'vacuum_min_snapshots_to_keep'      = '{min_snapshots}',
            'vacuum_max_metadata_files_to_keep' = '{max_meta}',
            'write_target_data_file_size_bytes' = '{file_size}'
        )
    """

    result = {
        "table_fqn":       table_fqn,
        "commit_tier":     tier_name,
        "tier_source":     tier_source,
        "vacuum_max_age":  age_secs,
        "vacuum_min_keep": min_snapshots,
        "max_meta_files":  max_meta,
        "file_size_bytes": file_size,
        "status":          "DRY_RUN" if dry_run else "PENDING",
    }

    if dry_run:
        log.info(
            "property_sync.dry_run",
            table_fqn=table_fqn,
            tier=tier_name,
            age_secs=age_secs,
            min_snapshots=min_snapshots,
            max_meta=max_meta,
        )
        return result

    try:
        run_query(sql, workgroup=workgroup)
        result["status"] = "SUCCESS"
        log.info(
            "property_sync.applied",
            table_fqn=table_fqn,
            tier=tier_name,
            tier_source=tier_source,
            age_secs=age_secs,
            min_snapshots=min_snapshots,
            max_meta=max_meta,
        )
    except Exception as e:
        result["status"] = "FAILURE"
        result["error"]  = str(e)
        log.error("property_sync.failed", table_fqn=table_fqn, error=str(e))

    return result


def mark_properties_synced(
    table_fqn: str,
    workgroup:  str,
    dry_run:    bool = False,
) -> bool:
    """Set properties_synced=true on stream_registry."""
    from config.settings import STREAM_REGISTRY_TABLE

    sql = f"""
        UPDATE {STREAM_REGISTRY_TABLE}
        SET properties_synced = true,
            updated_at        = TIMESTAMP '{datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S')}'
        WHERE table_fqn = '{table_fqn}'
    """
    if dry_run:
        log.info("property_sync.mark_synced.dry_run", table_fqn=table_fqn)
        return True

    try:
        run_query(sql, workgroup=workgroup)
        return True
    except Exception as e:
        msg = str(e).lower()
        if "column" in msg and "properties_synced" in msg:
            log.info("property_sync.mark_synced.column_missing", table_fqn=table_fqn)
            return False
        log.warning("property_sync.mark_synced.failed", table_fqn=table_fqn, error=str(e))
        return False

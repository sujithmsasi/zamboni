"""
Zamboni -- Vacuum Operation
Single VACUUM call per table handles BOTH:
  1. Snapshot expiry      (removes old snapshots)
  2. Orphan file removal  (removes unreferenced data files)

Athena engine v3 VACUUM syntax (HARD RULES):
  VACUUM database_name.table_name;
  - Bare VACUUM only. No clauses, no options, no catalog prefix.
  - Retention controlled ONLY via TBLPROPERTIES (set by property_sync).
  - Handles both snapshot expiry and orphan cleanup in one call.

Gap fixes implemented here:
  Gap 1:  Correct bare VACUUM SQL (no EXPIRE SNAPSHOTS clause)
  Gap 2:  Orphan cleanup merged into VACUUM (not a separate call)
  Gap 3:  Iterative VACUUM for heavily bloated tables
          ALTER TABLE to tighten retention, then loop up to 3x
  Gap 9:  Separate trivial-skip (< 5 snapshots) from safety-floor skip
  Gap 10: Compaction-before-vacuum ordering guard
"""
from __future__ import annotations

import time

from config.settings import (
    SNAPSHOT_MIN_FLOOR,
    SNAPSHOT_TRIVIAL_SKIP,
    VACUUM_BLOAT_THRESHOLD,
    VACUUM_ITERATION_SLEEP_SECS,
    VACUUM_MAX_ITERATIONS,
)
from engine.core.health_checker import HealthResult
from engine.utils.athena_client import get_query_stats, run_query
from engine.utils.logger import get_logger
from engine.utils.partition_utils import parse_table_fqn

log = get_logger(__name__)


class VacuumCancelledLeaseLost(RuntimeError):
    """Raised when a VACUUM iteration loop stops early because the
    caller's maintenance lock lease was lost (2026-07-11 audit fix) --
    raised between iterations/before submitting the next VACUUM call,
    never mid-query (a submitted Athena VACUUM statement itself isn't
    cancelled by this -- see maintenance_ops.run_safe_vacuum()'s own
    assert_held() call immediately before this function for the
    single-iteration case)."""
    def __init__(self, table_fqn: str):
        self.table_fqn = table_fqn
        super().__init__(f"VACUUM iterations for {table_fqn} stopped -- maintenance lock lease was lost")


def run_expire_snapshots(
    table_fqn: str,
    hk_config:  dict,
    health:     HealthResult,
    tier:       str,
    dry_run:    bool = False,
    cancel_check=None,
) -> dict:
    """
    Expire old snapshots and remove orphan files using Athena VACUUM.

    A single VACUUM call handles both snapshot expiry and orphan file
    removal in Athena engine v3. Retention is driven entirely by
    TBLPROPERTIES — property_sync must run before this.

    For heavily bloated tables (expired_snapshots > VACUUM_BLOAT_THRESHOLD):
      - Tightens retention via ALTER TABLE if not already at target
      - Runs VACUUM up to VACUUM_MAX_ITERATIONS times
      - Sleeps VACUUM_ITERATION_SLEEP_SECS between runs

    Returns dict with vacuum_iterations, snapshots_before, athena_query_ids.
    """
    min_to_keep = max(
        hk_config.get("snapshot_min_to_keep", SNAPSHOT_MIN_FLOOR),
        SNAPSHOT_MIN_FLOOR,
    )

    # ── Gap 9: G9 — trivially small table, skip ───────────────────────────────
    if health.snapshot_count < SNAPSHOT_TRIVIAL_SKIP:
        log.info(
            "vacuum.skip_trivial",
            table_fqn=table_fqn,
            snapshot_count=health.snapshot_count,
            threshold=SNAPSHOT_TRIVIAL_SKIP,
        )
        return {
            "operation":         "expire_snapshots",
            "skipped":           True,
            "skip_reason":       f"trivially_small: {health.snapshot_count} < {SNAPSHOT_TRIVIAL_SKIP}",
            "snapshots_expired": 0,
        }

    # ── Gap 9: safety floor — would not expire anything meaningful ────────────
    if health.snapshot_count <= min_to_keep:
        log.info(
            "vacuum.skip_safety_floor",
            table_fqn=table_fqn,
            snapshot_count=health.snapshot_count,
            min_to_keep=min_to_keep,
        )
        return {
            "operation":         "expire_snapshots",
            "skipped":           True,
            "skip_reason":       f"at_safety_floor: {health.snapshot_count} <= {min_to_keep}",
            "snapshots_expired": 0,
        }

    # ── Gap 10: G10 — pipeline anomaly, block VACUUM ─────────────────────────
    if getattr(health, "pipeline_anomaly", False):
        log.error(
            "vacuum.blocked_pipeline_anomaly",
            table_fqn=table_fqn,
            commits_per_day=getattr(health, "commits_per_day", 0),
        )
        return {
            "operation":   "expire_snapshots",
            "skipped":     True,
            "skip_reason": "FAILURE_SAFETY_BLOCKED: pipeline anomaly — fix commit rate at source",
        }

    _, database, table = parse_table_fqn(table_fqn)
    wg = "critical" if tier == "critical" else "standard"

    # ── Gap 3: determine if iterative VACUUM needed ───────────────────────────
    expired      = getattr(health, "expired_snapshots", 0)
    is_bloated   = expired > VACUUM_BLOAT_THRESHOLD
    max_iters    = VACUUM_MAX_ITERATIONS if is_bloated else 1

    if is_bloated:
        log.warning(
            "vacuum.bloated_table",
            table_fqn=table_fqn,
            expired_snapshots=expired,
            threshold=VACUUM_BLOAT_THRESHOLD,
            plan=f"iterative VACUUM x{max_iters} with {VACUUM_ITERATION_SLEEP_SECS}s sleep",
        )
        # Gap 3: tighten retention so more snapshots qualify per call
        # If property_sync already ran with the correct tier values,
        # this ALTER is effectively a no-op confirming current settings.
        _tighten_retention_for_bloated_table(
            database, table, wg, hk_config, dry_run
        )

    # ── Gap 1: correct bare VACUUM SQL — no catalog prefix, no clauses ────────
    # Athena engine v3 syntax:  VACUUM database.table;
    vacuum_sql = f"VACUUM {database}.{table}"

    log.info(
        "vacuum.start",
        table_fqn=table_fqn,
        snapshots_before=health.snapshot_count,
        expired_before=expired,
        is_bloated=is_bloated,
        max_iterations=max_iters,
        dry_run=dry_run,
    )

    query_ids    = []
    total_scanned = 0

    for iteration in range(1, max_iters + 1):
        if cancel_check is not None and cancel_check():
            log.error("vacuum.cancelled_lease_lost", table_fqn=table_fqn, iteration=iteration)
            raise VacuumCancelledLeaseLost(table_fqn)

        log.info(
            "vacuum.iteration",
            table_fqn=table_fqn,
            iteration=iteration,
            of=max_iters,
        )

        query_id = run_query(vacuum_sql, workgroup=wg, dry_run=dry_run, cancel_check=cancel_check)
        if query_id:
            query_ids.append(query_id)

        if query_id and not dry_run:
            stats = get_query_stats(query_id)
            total_scanned += stats.get("bytes_scanned", 0)

        # Sleep between iterations (except after last)
        if iteration < max_iters and not dry_run:
            log.debug(
                "vacuum.iteration_sleep",
                seconds=VACUUM_ITERATION_SLEEP_SECS,
                next_iteration=iteration + 1,
            )
            time.sleep(VACUUM_ITERATION_SLEEP_SECS)

    result = {
        "operation":         "expire_snapshots",
        "athena_query_ids":  query_ids,
        "athena_query_id":   query_ids[-1] if query_ids else None,
        "snapshots_before":  health.snapshot_count,
        "expired_before":    expired,
        "vacuum_iterations": len(query_ids),
        "is_bloated":        is_bloated,
        "bytes_scanned":     total_scanned,
        "min_to_keep":       min_to_keep,
        "dry_run":           dry_run,
    }

    log.info(
        "vacuum.complete",
        table_fqn=table_fqn,
        iterations=len(query_ids),
        bytes_scanned=total_scanned,
    )
    return result


def run_orphan_cleanup(
    table_fqn: str,
    hk_config:  dict,
    tier:       str,
    dry_run:    bool = False,
    cancel_check=None,
) -> dict:
    """
    Gap 2: Orphan file cleanup is handled by VACUUM in Athena engine v3.
    This function runs the same bare VACUUM — it is kept as a separate
    callable so the HK engine can schedule it on its own cadence
    (controlled by orphan_cleanup_cadence_days in hk_config).

    In Athena engine v3, a single VACUUM call:
      - Expires snapshots older than vacuum_max_snapshot_age_seconds
      - Removes orphan files older than the retention threshold
    Both happen in one call. There is no separate orphan-only VACUUM syntax.

    cancel_check (2026-07-11 audit fix): zero-arg callable returning True
    once the caller's maintenance lock lease is lost -- checked before
    submitting and threaded into the Athena poll so a lease lost mid-wait
    actively cancels the query.
    """
    if cancel_check is not None and cancel_check():
        log.error("vacuum.orphan_cleanup_cancelled_lease_lost", table_fqn=table_fqn)
        raise VacuumCancelledLeaseLost(table_fqn)

    _, database, table = parse_table_fqn(table_fqn)
    wg = "critical" if tier == "critical" else "standard"

    # Bare VACUUM — handles orphan cleanup as part of normal VACUUM
    vacuum_sql = f"VACUUM {database}.{table}"

    log.info(
        "vacuum.orphan_cleanup",
        table_fqn=table_fqn,
        note="Athena VACUUM handles both snapshots and orphans in one call",
        dry_run=dry_run,
    )

    query_id = run_query(vacuum_sql, workgroup=wg, dry_run=dry_run, cancel_check=cancel_check)

    result = {
        "operation":       "orphan_cleanup",
        "athena_query_id": query_id,
        "dry_run":         dry_run,
        "note":            "Athena VACUUM handles both snapshots and orphan files",
    }

    if query_id and not dry_run:
        stats = get_query_stats(query_id)
        result["bytes_scanned"] = stats.get("bytes_scanned", 0)

    return result


# ── Internal helpers ──────────────────────────────────────────────────────────

def _tighten_retention_for_bloated_table(
    database:  str,
    table:     str,
    workgroup: str,
    hk_config: dict,
    dry_run:   bool,
) -> None:
    """
    Gap 3: For heavily bloated tables, ensure TBLPROPERTIES are set to
    the target retention before iterating VACUUM.

    If property_sync already ran, this confirms the values are correct.
    If not yet run (e.g. emergency cleanup), this sets them now.

    Uses commit-frequency-based tier when available, falls back to
    hk_config snapshot_retention_days.
    """
    from engine.core.commit_frequency import (
        TIER_PROPERTIES,
        classify_tier_from_config,
    )

    tier_name = classify_tier_from_config(hk_config)
    props     = TIER_PROPERTIES[tier_name]

    age_secs  = props["vacuum_max_snapshot_age_seconds"]
    min_keep  = props["vacuum_min_snapshots_to_keep"]
    max_meta  = props["vacuum_max_metadata_files_to_keep"]
    file_size = props["write_target_data_file_size_bytes"]

    alter_sql = f"""
        ALTER TABLE {database}.{table} SET TBLPROPERTIES (
            'vacuum_max_snapshot_age_seconds'   = '{age_secs}',
            'vacuum_min_snapshots_to_keep'      = '{min_keep}',
            'vacuum_max_metadata_files_to_keep' = '{max_meta}',
            'write_target_data_file_size_bytes' = '{file_size}'
        )
    """

    log.info(
        "vacuum.tighten_retention",
        database=database, table=table,
        age_secs=age_secs, min_keep=min_keep,
        max_meta=max_meta, tier=tier_name,
        dry_run=dry_run,
    )

    try:
        run_query(alter_sql, workgroup=workgroup, dry_run=dry_run)
    except Exception as e:
        # Non-fatal — VACUUM will still run with existing properties
        log.warning(
            "vacuum.tighten_retention_failed",
            database=database, table=table, error=str(e),
        )

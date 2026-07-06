"""
Zamboni — Maintenance Ops (Workstream A / Phase 1b, contracts.md §5-A)

Clean ops module the orchestrator calls into. All safety logic (property
floors, pre-flight sanity, sanity-abort, vacuum_audit) lives here so a
future vacuum upgrade (e.g. the org-side 12-gap-type version — see
.claude/org_divergence.md) slots in beneath it without touching the safety
layer.

CRITICAL OVERRIDE (contracts.md §5-A) reconciliation note: contracts §5's
original three-step "REMOVE ORPHANS — two-phase ... delete with
older_than=..." design assumed a separable, parameterized orphan-only
delete call. This repo's Athena engine v3 has no such call — a single bare
`VACUUM db.table;` does snapshot expiry AND orphan removal together,
governed only by TBLPROPERTIES (engine/operations/vacuum.py:1-20). §5-A
adapts this into one SAFE-VACUUM step:
    a. property clamp   (the floors live here)
    b. pre-flight sanity ($snapshots / $files)
    c. run VACUUM        (delegates to engine.operations.vacuum — gaps
                           1,2,3,9,10 extended, never restructured)
There is therefore no separate run_expire()/run_orphan_delete() — see
.claude/decisions.md for why those two names from the phase brief collapse
into run_safe_vacuum() here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from config.settings import (
    MAX_ORPHAN_DELETE_PCT,
    ORPHAN_MIN_AGE_HOURS_FLOOR,
    SNAPSHOT_MIN_AGE_HOURS,
    VACUUM_AUDIT_TABLE,
)
from engine.core.health_checker import HealthResult
from engine.operations import compaction, vacuum
from engine.utils.athena_client import read_sql, run_query
from engine.utils.logger import get_logger
from engine.utils.partition_utils import parse_table_fqn

log = get_logger(__name__)


# ── OPTIMIZE ──────────────────────────────────────────────────────────────────

def run_optimize(
    table_fqn: str,
    hk_config: dict,
    health:    HealthResult,
    tier:      str,
    dry_run:   bool = False,
    table_row: dict | None = None,
) -> dict:
    """Thin wrapper over engine.operations.compaction — single import surface
    for the orchestrator alongside run_safe_vacuum()."""
    return compaction.run_compaction(
        table_fqn=table_fqn, hk_config=hk_config, health=health,
        tier=tier, dry_run=dry_run, table_row=table_row,
    )


# ── SAFE-VACUUM (contracts.md §5-A) ──────────────────────────────────────────

@dataclass
class SafeVacuumResult:
    aborted:               bool       = False
    aborted_reason:        str | None = None
    sanity_pct:            float | None = None
    files_estimated:       int | None = None
    files_deleted:         int | None = None
    bytes_reclaimed:       int | None = None
    older_than_hours_used: int | None = None
    vacuum_result:         dict = field(default_factory=dict)
    dry_run:               bool = False


def run_safe_vacuum(
    table_fqn: str,
    hk_config: dict,
    health:    HealthResult,
    tier:      str,
    workgroup: str,
    dry_run:   bool = False,
) -> SafeVacuumResult:
    """
    contracts.md §5-A SAFE-VACUUM sequence:
      a. PROPERTY CLAMP  — floors live here, persisted on the table.
      b. PRE-FLIGHT SANITY — estimate scope via "$snapshots"/"$files";
         abort (no delete) if would_expire_pct > MAX_ORPHAN_DELETE_PCT.
      c. RUN VACUUM — delegates to engine.operations.vacuum (unmodified).
      d. POST-AUDIT — re-query "$files" for files/bytes delta.

    Integrity verification (before/after TableState + verify_advanced) and
    vacuum_audit persistence are the orchestrator's job — it already
    captures state around OPTIMIZE too, so that's the single place both
    steps' before/after snapshots live.
    """
    result = SafeVacuumResult(dry_run=dry_run)

    clamp = _clamp_vacuum_properties(table_fqn, hk_config, workgroup, dry_run)
    floor_hours = clamp["floor_hours"]
    result.older_than_hours_used = int(floor_hours)

    sanity = _preflight_sanity(table_fqn, floor_hours)
    result.sanity_pct      = sanity["would_expire_pct"]
    result.files_estimated = sanity["total_files"]

    if sanity["would_expire_pct"] > MAX_ORPHAN_DELETE_PCT:
        result.aborted        = True
        result.aborted_reason = "ORPHAN_SANITY_ABORT"
        log.error(
            "maintenance_ops.orphan_sanity_abort",
            table_fqn=table_fqn, sanity_pct=sanity["would_expire_pct"],
            threshold=MAX_ORPHAN_DELETE_PCT,
        )
        return result

    result.vacuum_result = vacuum.run_expire_snapshots(
        table_fqn=table_fqn, hk_config=hk_config, health=health,
        tier=tier, dry_run=dry_run,
    )

    if not dry_run:
        post = _files_metrics(table_fqn)
        if post["total_files"] is not None and sanity["total_files"] is not None:
            result.files_deleted = max(sanity["total_files"] - post["total_files"], 0)
        if post["total_bytes"] is not None and sanity["total_bytes"] is not None:
            result.bytes_reclaimed = max(sanity["total_bytes"] - post["total_bytes"], 0)

    return result


def _clamp_vacuum_properties(
    table_fqn: str,
    hk_config: dict,
    workgroup: str,
    dry_run:   bool,
) -> dict:
    """
    contracts.md §5-A step a: before VACUUM, clamp
    vacuum_max_snapshot_age_seconds >= max(policy, ORPHAN_MIN_AGE_HOURS_FLOOR,
    SNAPSHOT_MIN_AGE_HOURS) and vacuum_min_snapshots_to_keep >= max(policy, 1).
    Clamped values PERSIST on the table — they ARE the safety floors.
    """
    policy_days    = int(hk_config.get("snapshot_retention_days") or 7)
    policy_seconds = policy_days * 86400
    floor_seconds  = max(
        policy_seconds,
        ORPHAN_MIN_AGE_HOURS_FLOOR * 3600,
        SNAPSHOT_MIN_AGE_HOURS * 3600,
    )
    min_keep = max(int(hk_config.get("snapshot_min_to_keep") or 1), 1)

    sql = f"""
        ALTER TABLE {table_fqn} SET TBLPROPERTIES (
            'vacuum_max_snapshot_age_seconds' = '{floor_seconds}',
            'vacuum_min_snapshots_to_keep'    = '{min_keep}'
        )
    """
    log.info(
        "maintenance_ops.property_clamp",
        table_fqn=table_fqn, floor_seconds=floor_seconds,
        min_keep=min_keep, dry_run=dry_run,
    )
    try:
        run_query(sql, workgroup=workgroup, dry_run=dry_run)
    except Exception as e:
        # Non-fatal — VACUUM still runs against whatever properties are
        # already set (matches vacuum.py's existing tighten-retention
        # failure handling convention).
        log.warning("maintenance_ops.property_clamp_failed", table_fqn=table_fqn, error=str(e))

    return {
        "floor_seconds": floor_seconds,
        "floor_hours":   floor_seconds / 3600,
        "min_keep":      min_keep,
    }


def _preflight_sanity(table_fqn: str, floor_hours: float) -> dict:
    """
    contracts.md §5-A step b: would_expire_pct = snapshots older than the
    clamped floor / total snapshots, plus files_count + total_bytes before.

    Local-mode approximation: "$snapshots"/"$files" are Iceberg metadata
    tables with no SQLite equivalent (engine/utils/local_db.py translates
    Athena SQL but has no metadata-table concept). read_sql_local() returns
    an empty DataFrame for these queries, so would_expire_pct is reported
    as 0.0 (nothing to abort on) rather than raising — the same "empty
    result = no signal" behavior health_checker.py already relies on for
    "$snapshots"/"$files" in local mode. Documented in .claude/decisions.md.
    """
    _, database, table = parse_table_fqn(table_fqn)

    would_expire_pct = 0.0
    try:
        sql = f"""
            SELECT
                COUNT(*) AS total_snapshots,
                SUM(CASE WHEN committed_at < NOW() - INTERVAL '{int(floor_hours)}' HOUR
                         THEN 1 ELSE 0 END) AS would_expire
            FROM "glue_catalog"."{database}"."{table}$snapshots"
        """
        df = read_sql(sql, workgroup="app", database=database)
        if not df.empty and df.iloc[0]["total_snapshots"]:
            total    = int(df.iloc[0]["total_snapshots"])
            expiring = int(df.iloc[0]["would_expire"] or 0)
            would_expire_pct = round(expiring / total * 100, 2) if total else 0.0
    except Exception as e:
        log.warning("maintenance_ops.preflight_snapshots_failed", table_fqn=table_fqn, error=str(e))

    files = _files_metrics(table_fqn)
    return {
        "would_expire_pct": would_expire_pct,
        "total_files":      files["total_files"],
        "total_bytes":      files["total_bytes"],
    }


def _files_metrics(table_fqn: str) -> dict:
    """Shared by pre-flight sanity (before) and post-audit (after)."""
    _, database, table = parse_table_fqn(table_fqn)
    total_files = None
    total_bytes = None
    try:
        sql = f"""
            SELECT COUNT(*) AS total_files, SUM(file_size_in_bytes) AS total_bytes
            FROM "glue_catalog"."{database}"."{table}$files"
        """
        df = read_sql(sql, workgroup="app", database=database)
        if not df.empty and df.iloc[0]["total_files"] is not None:
            total_files = int(df.iloc[0]["total_files"])
            total_bytes = int(df.iloc[0]["total_bytes"] or 0)
    except Exception as e:
        log.warning("maintenance_ops.files_metrics_failed", table_fqn=table_fqn, error=str(e))
    return {"total_files": total_files, "total_bytes": total_bytes}


# ── vacuum_audit (contracts.md §3.3) ──────────────────────────────────────────

def write_vacuum_audit(
    run_id:                str,
    table_fqn:              str,
    operation:              str,
    lock_id:                str | None,
    dry_run:                bool,
    snapshots_before:       int | None = None,
    snapshots_after:        int | None = None,
    files_estimated:        int | None = None,
    files_deleted:          int | None = None,
    bytes_reclaimed:        int | None = None,
    older_than_hours_used:  int | None = None,
    sanity_pct:             float | None = None,
    aborted:                bool = False,
    aborted_reason:         str | None = None,
    started_at:             datetime | None = None,
    completed_at:           datetime | None = None,
) -> None:
    """
    Every SAFE-VACUUM run (including sanity-aborts) writes one row here —
    this is the VP-reportable audit trail (contracts.md §3.3). Always
    persisted, even for dry runs (dry_run column records that fact) so the
    acceptance dry-run demo has a real row to show.
    """
    now = datetime.now(UTC)
    started_at   = started_at or now
    completed_at = completed_at or now

    def _s(v) -> str:
        return f"'{str(v).replace(chr(39), chr(39) * 2)}'" if v is not None else "NULL"

    def _n(v) -> str:
        return str(int(v)) if v is not None else "NULL"

    def _f(v) -> str:
        return str(float(v)) if v is not None else "NULL"

    def _b(v) -> str:
        return str(bool(v)).lower()

    def _ts(v: datetime) -> str:
        return f"TIMESTAMP '{v.strftime('%Y-%m-%d %H:%M:%S')}'"

    sql = f"""
        INSERT INTO {VACUUM_AUDIT_TABLE} VALUES (
            {_s(run_id)},
            {_s(table_fqn)},
            {_s(operation)},
            {_n(snapshots_before)},
            {_n(snapshots_after)},
            {_n(files_estimated)},
            {_n(files_deleted)},
            {_n(bytes_reclaimed)},
            {_n(older_than_hours_used)},
            {_f(sanity_pct)},
            {_b(aborted)},
            {_s(aborted_reason)},
            {_s(lock_id)},
            {_b(dry_run)},
            {_ts(started_at)},
            {_ts(completed_at)}
        )
    """
    log.info(
        "maintenance_ops.vacuum_audit_write",
        run_id=run_id, table_fqn=table_fqn, aborted=aborted, dry_run=dry_run,
    )
    # Same dry_run convention as execution_log.write(): in ZAMBONI_LOCAL_MODE,
    # run_query() short-circuits to SQLite before the dry_run check, so local
    # dry runs still produce a real, queryable row (contracts.md acceptance
    # criteria). Against real Athena, dry_run=True logs the SQL only.
    run_query(sql, workgroup="app", dry_run=dry_run)

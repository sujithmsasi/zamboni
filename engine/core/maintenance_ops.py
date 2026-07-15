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
    ZAMBONI_LOCAL_MODE,
)
from engine.core.execution_log_parquet import AuditBuffer
from engine.core.health_checker import HealthResult
from engine.operations import compaction, vacuum
from engine.utils.athena_client import read_sql, run_query
from engine.utils.logger import get_logger
from engine.utils.partition_utils import parse_table_fqn

log = get_logger(__name__)


class SafetyCheckError(RuntimeError):
    """
    Raised when a Safe-VACUUM safety prerequisite -- property clamp,
    property clamp verification (readback), or pre-flight sanity
    (snapshots/files query) -- could not be confirmed.

    2026-07-11 audit fix: every one of these previously caught its own
    exception, logged a warning, and either continued regardless (property
    clamp) or fell back to a falsely-reassuring default (pre-flight sanity
    defaulted to would_expire_pct=0.0 on a real query failure, which is
    exactly the "nothing will expire" reading that lets the abort-above-
    threshold check pass right through an AWS outage or permissions
    issue). run_safe_vacuum() now catches this specific exception and
    converts it into the existing SafeVacuumResult.aborted path (same
    audit/alert/circuit-breaker handling the pre-existing sanity-threshold
    abort already has in orchestrator.py) -- VACUUM is never called when
    any prerequisite is unconfirmed, not just when it's known-bad.
    """


# ── OPTIMIZE ──────────────────────────────────────────────────────────────────

def run_optimize(
    table_fqn: str,
    hk_config: dict,
    health:    HealthResult,
    tier:      str,
    dry_run:   bool = False,
    table_row: dict | None = None,
    cancel_check=None,
) -> dict:
    """Thin wrapper over engine.operations.compaction — single import surface
    for the orchestrator alongside run_safe_vacuum().

    cancel_check (2026-07-11 audit fix): zero-arg callable returning True
    once the caller's maintenance lock lease is lost -- threaded through
    to compaction.py's Athena/Glue polling loops so a lease lost mid-wait
    actively cancels the in-flight query/job.
    """
    return compaction.run_compaction(
        table_fqn=table_fqn, hk_config=hk_config, health=health,
        tier=tier, dry_run=dry_run, table_row=table_row, cancel_check=cancel_check,
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
    cancel_check=None,
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

    cancel_check (2026-07-11 audit fix): zero-arg callable returning True
    once the caller's maintenance lock lease is lost. Checked explicitly
    immediately before the VACUUM call itself (the one genuinely
    destructive step here) and threaded through to
    engine.operations.vacuum's iteration loop so a lease lost mid-wait
    stops further iterations rather than completing them unprotected.
    """
    result = SafeVacuumResult(dry_run=dry_run)

    try:
        clamp = _clamp_vacuum_properties(table_fqn, hk_config, workgroup, dry_run)
    except SafetyCheckError as e:
        result.aborted = True
        result.aborted_reason = f"PROPERTY_CLAMP_FAILED: {e}"
        log.error("maintenance_ops.safety_check_aborted", table_fqn=table_fqn, reason=result.aborted_reason)
        return result

    floor_hours = clamp["floor_hours"]
    result.older_than_hours_used = int(floor_hours)

    try:
        sanity = _preflight_sanity(table_fqn, floor_hours)
    except SafetyCheckError as e:
        result.aborted = True
        result.aborted_reason = f"PREFLIGHT_SANITY_FAILED: {e}"
        log.error("maintenance_ops.safety_check_aborted", table_fqn=table_fqn, reason=result.aborted_reason)
        return result

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

    if cancel_check is not None and cancel_check():
        result.aborted        = True
        result.aborted_reason = "LEASE_LOST"
        log.error("maintenance_ops.lease_lost_aborted", table_fqn=table_fqn)
        return result

    result.vacuum_result = vacuum.run_expire_snapshots(
        table_fqn=table_fqn, hk_config=hk_config, health=health,
        tier=tier, dry_run=dry_run, cancel_check=cancel_check,
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

    2026-07-11 audit fix: a failed ALTER TABLE used to be logged as a
    non-fatal warning and VACUUM would proceed against whatever properties
    happened to already be set -- meaning a floor that was never actually
    applied could silently be weaker than intended. Now raises
    SafetyCheckError on write failure (caught by run_safe_vacuum(), which
    aborts without calling VACUUM), and -- since a "successful" ALTER call
    doesn't guarantee Athena actually applied the exact values requested --
    reads the properties back from Glue afterward and raises if they don't
    match what was just clamped. Skipped for dry_run (nothing was written
    to verify) and ZAMBONI_LOCAL_MODE (SQLite has no TBLPROPERTIES/Glue
    equivalent to read back, same documented approximation
    _preflight_sanity's "$snapshots"/"$files" already uses).
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
        log.error("maintenance_ops.property_clamp_failed", table_fqn=table_fqn, error=str(e))
        raise SafetyCheckError(f"property clamp write failed for {table_fqn}: {e}") from e

    if not dry_run and not ZAMBONI_LOCAL_MODE:
        _verify_effective_vacuum_properties(table_fqn, floor_seconds, min_keep)

    return {
        "floor_seconds": floor_seconds,
        "floor_hours":   floor_seconds / 3600,
        "min_keep":      min_keep,
    }


def _verify_effective_vacuum_properties(table_fqn: str, expected_floor_seconds: int, expected_min_keep: int) -> None:
    """
    Read back the table's actual TBLPROPERTIES from Glue (the ALTER TABLE
    SET TBLPROPERTIES call surfaces there) and confirm the clamp really
    took effect -- a "successful" ALTER call doesn't by itself guarantee
    Athena applied the exact values requested. Raises SafetyCheckError on
    any mismatch, missing table, or read failure.
    """
    from engine.utils.glue_client import get_table

    _, database, table = parse_table_fqn(table_fqn)
    try:
        glue_table = get_table(database, table)
    except Exception as e:
        raise SafetyCheckError(f"could not read back table properties for {table_fqn}: {e}") from e

    if not glue_table:
        raise SafetyCheckError(f"could not read back table properties for {table_fqn}: table not found in Glue")

    params = glue_table.get("Parameters", {}) or {}
    try:
        effective_age    = int(params.get("vacuum_max_snapshot_age_seconds", -1))
        effective_keep   = int(params.get("vacuum_min_snapshots_to_keep", -1))
    except (TypeError, ValueError) as e:
        raise SafetyCheckError(
            f"could not parse effective table properties for {table_fqn}: {params} ({e})"
        ) from e

    if effective_age != expected_floor_seconds or effective_keep != expected_min_keep:
        raise SafetyCheckError(
            f"property clamp did not take effect for {table_fqn}: expected "
            f"age={expected_floor_seconds}s min_keep={expected_min_keep}, "
            f"read back age={effective_age}s min_keep={effective_keep}"
        )


def _preflight_sanity(table_fqn: str, floor_hours: float) -> dict:
    """
    contracts.md §5-A step b: would_expire_pct = snapshots older than the
    clamped floor / total snapshots, plus files_count + total_bytes before.

    Local-mode approximation (unchanged, still explicit and documented):
    "$snapshots"/"$files" are Iceberg metadata tables with no SQLite
    equivalent (engine/utils/local_db.py translates Athena SQL but has no
    metadata-table concept), so would_expire_pct is reported as 0.0
    (nothing to abort on) -- the same "empty result = no signal" behavior
    health_checker.py already relies on for "$snapshots"/"$files" in local
    mode.

    2026-07-11 audit fix, outside local mode: a genuine query failure or
    an incomplete result (no rows, or a null total_snapshots) now raises
    SafetyCheckError instead of silently defaulting would_expire_pct to
    0.0 -- that default reads as "nothing will expire," which is exactly
    the falsely-reassuring value that let this abort-above-threshold check
    pass right through a real AWS outage or permissions issue. Caught by
    run_safe_vacuum(), which aborts without calling VACUUM.
    """
    _, database, table = parse_table_fqn(table_fqn)

    if ZAMBONI_LOCAL_MODE:
        files = _files_metrics(table_fqn)
        return {"would_expire_pct": 0.0, "total_files": files["total_files"], "total_bytes": files["total_bytes"]}

    sql = f"""
        SELECT
            COUNT(*) AS total_snapshots,
            SUM(CASE WHEN committed_at < NOW() - INTERVAL '{int(floor_hours)}' HOUR
                     THEN 1 ELSE 0 END) AS would_expire
        FROM "{database}"."{table}$snapshots"
    """
    try:
        df = read_sql(sql, workgroup="app", database=database)
    except Exception as e:
        log.error("maintenance_ops.preflight_snapshots_failed", table_fqn=table_fqn, error=str(e))
        raise SafetyCheckError(f"pre-flight snapshot sanity check failed for {table_fqn}: {e}") from e

    if df.empty or df.iloc[0]["total_snapshots"] is None:
        raise SafetyCheckError(f"pre-flight snapshot sanity check returned an incomplete result for {table_fqn}")

    total    = int(df.iloc[0]["total_snapshots"])
    expiring = int(df.iloc[0]["would_expire"] or 0)
    would_expire_pct = round(expiring / total * 100, 2) if total else 0.0

    files = _files_metrics(table_fqn, required=True)
    return {
        "would_expire_pct": would_expire_pct,
        "total_files":      files["total_files"],
        "total_bytes":      files["total_bytes"],
    }


def _files_metrics(table_fqn: str, required: bool = False) -> dict:
    """
    Shared by pre-flight sanity (before, required=True outside local mode
    -- a failed/incomplete files query must block VACUUM the same as a
    failed snapshots query) and post-audit (after, required=False -- a
    post-audit metrics failure shouldn't retroactively fail a VACUUM that
    already ran).
    """
    _, database, table = parse_table_fqn(table_fqn)
    total_files = None
    total_bytes = None
    try:
        sql = f"""
            SELECT COUNT(*) AS total_files, SUM(file_size_in_bytes) AS total_bytes
            FROM "{database}"."{table}$files"
        """
        df = read_sql(sql, workgroup="app", database=database)
        if not df.empty and df.iloc[0]["total_files"] is not None:
            total_files = int(df.iloc[0]["total_files"])
            total_bytes = int(df.iloc[0]["total_bytes"] or 0)
        elif required:
            raise SafetyCheckError(f"files pre-flight check returned an incomplete result for {table_fqn}")
    except SafetyCheckError:
        raise
    except Exception as e:
        log.warning("maintenance_ops.files_metrics_failed", table_fqn=table_fqn, error=str(e))
        if required:
            raise SafetyCheckError(f"files pre-flight check failed for {table_fqn}: {e}") from e
    return {"total_files": total_files, "total_bytes": total_bytes}


# ── vacuum_audit (contracts.md §3.3) ──────────────────────────────────────────

def _vacuum_audit_values_tuple(row: dict) -> str:
    """Render one vacuum_audit row (dict, same keys as write_vacuum_audit()'s
    kwargs) as a positional SQL VALUES tuple -- shared by write_vacuum_audit()
    (single-row immediate write) and write_vacuum_audit_many() (multi-row
    batched write) so both stay in sync with the vacuum_audit column order.
    Same pattern as execution_log.py's _values_tuple()."""

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

    return f"""(
            {_s(row.get("run_id"))},
            {_s(row.get("table_fqn"))},
            {_s(row.get("operation"))},
            {_n(row.get("snapshots_before"))},
            {_n(row.get("snapshots_after"))},
            {_n(row.get("files_estimated"))},
            {_n(row.get("files_deleted"))},
            {_n(row.get("bytes_reclaimed"))},
            {_n(row.get("older_than_hours_used"))},
            {_f(row.get("sanity_pct"))},
            {_b(row.get("aborted"))},
            {_s(row.get("aborted_reason"))},
            {_s(row.get("lock_id"))},
            {_b(row.get("dry_run"))},
            {_ts(row.get("started_at"))},
            {_ts(row.get("completed_at"))}
        )"""


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
    buffer:                 AuditBuffer | None = None,
) -> None:
    """
    Every SAFE-VACUUM run (including sanity-aborts) writes one row here —
    this is the VP-reportable audit trail (contracts.md §3.3). Always
    persisted, even for dry runs (dry_run column records that fact) so the
    acceptance dry-run demo has a real row to show.

    buffer (2026-07-16 perf fix): when given, the row is appended to the
    caller's fleet-wide AuditBuffer instead of writing immediately — both
    the aborted and successful vacuum paths in
    orchestrator.py::_run_safe_vacuum_step() pass the same buffer, so a
    whole HK fleet run produces exactly one Athena write for vacuum_audit
    regardless of how many tables ran vacuum, instead of one write per
    table. Omit buffer (the default) to preserve the original immediate-
    write behavior for standalone/single-table callers (tests, ad-hoc
    scripts).
    """
    now = datetime.now(UTC)
    row = {
        "run_id": run_id, "table_fqn": table_fqn, "operation": operation,
        "lock_id": lock_id, "dry_run": dry_run,
        "snapshots_before": snapshots_before, "snapshots_after": snapshots_after,
        "files_estimated": files_estimated, "files_deleted": files_deleted,
        "bytes_reclaimed": bytes_reclaimed, "older_than_hours_used": older_than_hours_used,
        "sanity_pct": sanity_pct, "aborted": aborted, "aborted_reason": aborted_reason,
        "started_at": started_at or now, "completed_at": completed_at or now,
    }
    log.info(
        "maintenance_ops.vacuum_audit_write",
        run_id=run_id, table_fqn=table_fqn, aborted=aborted, dry_run=dry_run,
        buffered=buffer is not None,
    )

    if buffer is not None:
        buffer.append(row)
        return

    sql = f"INSERT INTO {VACUUM_AUDIT_TABLE} VALUES {_vacuum_audit_values_tuple(row)}"
    # Same dry_run convention as execution_log.write(): in ZAMBONI_LOCAL_MODE,
    # run_query() short-circuits to SQLite before the dry_run check, so local
    # dry runs still produce a real, queryable row (contracts.md acceptance
    # criteria). Against real Athena, dry_run=True logs the SQL only.
    run_query(sql, workgroup="app", dry_run=dry_run)


def write_vacuum_audit_many(rows: list[dict]) -> int:
    """
    Batched counterpart to write_vacuum_audit() -- one multi-row INSERT for
    every buffered row instead of one INSERT per row. Used as an
    AuditBuffer's write_many_fn when a fleet-wide vacuum-audit buffer is in
    play (see hk_engine.py::HKEngine.run()).

    dry_run must stay per-row correct even inside one batch: a table can be
    individually mid dry-run-ramp-up (is_in_dry_run_ramp()) while the rest
    of the fleet run is not, so rows collected across a whole HK run can
    have mixed dry_run values -- and run_query(dry_run=True) skips the
    write entirely against real Athena (only ZAMBONI_LOCAL_MODE ignores the
    flag). Splitting into at most two batched INSERTs (one per dry_run
    value) preserves that per-row semantic exactly, instead of forcing one
    flag onto every row in the buffer. The common case (a whole run is or
    isn't dry_run) still produces exactly one real Athena write.

    Falls back to a per-row immediate write only if a group's batched
    INSERT itself fails, so one malformed row can't silently drop every
    other table's audit row for the run -- same fallback shape as
    execution_log.write_many()'s ParquetLogBuffer counterpart.
    """
    if not rows:
        return 0

    groups: dict[bool, list[dict]] = {False: [], True: []}
    for row in rows:
        groups[bool(row.get("dry_run", False))].append(row)

    written = 0
    for dry_run_flag, group in groups.items():
        if not group:
            continue
        try:
            values_sql = ",\n        ".join(_vacuum_audit_values_tuple(r) for r in group)
            sql = f"INSERT INTO {VACUUM_AUDIT_TABLE} VALUES {values_sql}"
            run_query(sql, workgroup="app", dry_run=dry_run_flag)
            written += len(group)
            log.info("maintenance_ops.vacuum_audit_write_many", count=len(group), dry_run=dry_run_flag)
            continue
        except Exception as e:
            log.warning(
                "maintenance_ops.vacuum_audit_write_many_failed_falling_back_per_row",
                count=len(group), dry_run=dry_run_flag, error=str(e),
            )

        for row in group:
            try:
                row_sql = f"INSERT INTO {VACUUM_AUDIT_TABLE} VALUES {_vacuum_audit_values_tuple(row)}"
                run_query(row_sql, workgroup="app", dry_run=dry_run_flag)
                written += 1
            except Exception as row_e:
                log.error(
                    "maintenance_ops.vacuum_audit_row_write_failed",
                    table_fqn=row.get("table_fqn"), error=str(row_e),
                )
    return written

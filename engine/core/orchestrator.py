"""
Zamboni — Maintenance Orchestrator (Workstream A / Phase 1b)
contracts.md §5 (LOCKED sequence) + §5-A (Athena VACUUM adaptation ruling)

Completion-serialized, commit-verified table maintenance — the direct fix
for the metadata-loss incident (clock-spaced dual maintenance). Replaces
"OPTIMIZE, wait N minutes, VACUUM" with:

    Gate 0 (contracts §4) -> lock held
    -> Gate 1 (Control-M) -> Gate 2 (window) -> idempotency -> Gate 3
       (frequency/dedupe) -> Gate 4 (circuit breaker) -> property sync
       -> health check
    -> capture_state -> OPTIMIZE -> verify_advanced("optimize")
    -> capture_state -> SAFE-VACUUM (contracts §5-A: property clamp ->
       pre-flight sanity -> bare VACUUM -> post-audit) -> verify_advanced("vacuum")
    -> finally: release lock

Any verify FAILED or step exception -> integrity_status=FAILED, trip the
circuit breaker immediately (not threshold-gated like the legacy op-failure
path — an integrity failure IS the incident this exists to prevent), SNS
alert with before/after metadata locations, halt remaining steps, release
lock regardless.

Every step writes execution_log (both writer modes, via the existing
ParquetLogBuffer/EXECUTION_LOG_MODE routing) with lock_id + metadata
before/after + snapshot ids + integrity_status.

ORCHESTRATED_MAINTENANCE (config.settings, default true) is hk_engine.py's
opt-in switch into this path — see HKEngine._process_table. Rollback lever:
set false to fall back to the pre-orchestrator per-op flow untouched.

Gates 1-4 + idempotency + property_sync + health-check below intentionally
duplicate engine/engines/hk_engine.py::_run_gates_and_operations' preamble
(minus the Operations section, which this module replaces). That function
is tightly coupled to HKEngine instance state (self.run_id, self._write_log,
self._log_buffer) and Phase 1a already established the norm of not
re-indenting/restructuring large existing blocks (see its Gate 0 split into
_run_gates_and_operations). Extracting just is_due() was safe (see
hk_engine.py); extracting the rest would have meant restructuring ~130
tested lines for one caller. Duplication is bounded to straightforward
conditional checks, not the safety-critical operations logic. See
.claude/decisions.md.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from config.settings import EXECUTION_LOG_MODE
from engine.core import circuit_breaker, execution_log, health_checker, maintenance_ops, notifier, registry
from engine.core.backpressure import wait_for_capacity
from engine.core.config import get_hk_config
from engine.core.conflict_detector import check_with_cache
from engine.core.execution_log import LogEntry
from engine.core.execution_log_parquet import ParquetLogBuffer
from engine.core.health_checker import HealthResult, is_healthy
from engine.core.idempotency import build_execution_id, check_already_executed, get_window_id, mark_executed
from engine.core.integrity_checker import IntegrityResult, capture_state, verify_advanced
from engine.core.lock_service import LockService
from engine.core.property_sync import apply_vacuum_properties, mark_properties_synced, needs_property_sync
from engine.core.window_evaluator import EXECUTE, evaluate
from engine.engines.hk_engine import _TIER_TO_WORKGROUP, _parse_gate0_override, is_due
from engine.utils.athena_client import AthenaQueryTimeout
from engine.utils.glue_client import is_upstream_job_complete
from engine.utils.logger import get_logger

log = get_logger(__name__)


@dataclass
class StepResult:
    step:             str
    status:           str            # SUCCESS | FAILURE | SKIPPED | DRY_RUN
    integrity_status: str | None = None
    detail:           str | None = None


@dataclass
class RunResult:
    table_fqn:   str
    dry_run:     bool
    status:      str                 # SUCCESS | FAILURE | SKIPPED
    skip_reason: str | None = None
    lock_id:     str | None = None
    steps:       list[StepResult] = field(default_factory=list)


def run_table_maintenance(fqn: str, dry_run: bool = True, run_id: str | None = None) -> RunResult:
    """contracts.md §5 -- the sequence below is LOCKED. See module docstring."""
    run_id = run_id or execution_log.new_run_id()

    table_row = registry.get_table(fqn)
    if not table_row:
        return RunResult(table_fqn=fqn, dry_run=dry_run, status="SKIPPED", skip_reason="SKIP_NO_TABLE")

    tier      = table_row.get("tier", "standard")
    hk_config = get_hk_config(fqn)
    if not hk_config:
        return RunResult(table_fqn=fqn, dry_run=dry_run, status="SKIPPED", skip_reason="SKIP_NO_CONFIG")

    log_buffer = ParquetLogBuffer(run_id=run_id, engine="hk")

    def _write(operation: str, status: str, **kw) -> None:
        entry = LogEntry(
            run_id=run_id, engine="hk", operation=operation, table_fqn=fqn,
            domain=table_row.get("domain", ""),
            layer=table_row.get("layer", ""), tier=tier,
            environment=table_row.get("environment", "prod"),
            status=status, dry_run=dry_run, **kw,
        )
        mode = (EXECUTION_LOG_MODE or "auto").lower()
        if mode == "insert":
            execution_log.write(entry, dry_run=dry_run)
        else:
            log_buffer.append(entry)

    # ── Gate 0 — Maintenance Conflict Gate (contracts.md §4) ──────────────────
    override_until   = _parse_gate0_override(hk_config.get("gate0_override_until"))
    gate0_overridden = bool(override_until and override_until > datetime.now(UTC))

    if gate0_overridden:
        log.warning(
            "orchestrator.gate0_overridden", table_fqn=fqn,
            reason=hk_config.get("gate0_override_reason"), actor=hk_config.get("gate0_override_by"),
        )
        _write(
            "gate0", "OVERRIDDEN",
            skip_reason=(
                f"GATE0_OVERRIDDEN reason={hk_config.get('gate0_override_reason')} "
                f"actor={hk_config.get('gate0_override_by')}"
            ),
        )
    else:
        conflict = check_with_cache(fqn)
        if conflict.get("conflict"):
            _write("hk_run", "SKIPPED", skip_reason=f"SKIP_AWS_OPTIMIZER_CONFLICT ({conflict})")
            log_buffer.flush(dry_run=dry_run)
            return RunResult(fqn, dry_run, "SKIPPED", "SKIP_AWS_OPTIMIZER_CONFLICT")

    if execution_log.get_running(fqn):
        _write("hk_run", "SKIPPED", skip_reason="SKIP_ALREADY_RUNNING")
        log_buffer.flush(dry_run=dry_run)
        return RunResult(fqn, dry_run, "SKIPPED", "SKIP_ALREADY_RUNNING")

    lock_service = LockService()
    lock = lock_service.acquire(fqn, "orchestrated_maintenance")
    if lock is None:
        _write("hk_run", "SKIPPED", skip_reason="SKIP_LOCK_HELD")
        log_buffer.flush(dry_run=dry_run)
        return RunResult(fqn, dry_run, "SKIPPED", "SKIP_LOCK_HELD")

    lock_id = f"{lock.table_fqn}:{lock.lock_owner}"
    result  = RunResult(table_fqn=fqn, dry_run=dry_run, status="SUCCESS", lock_id=lock_id)

    try:
        # Mark RUNNING via an immediate (unbuffered) write so Gate 0's
        # in-flight check is visible to any concurrent trigger for the
        # duration of this run, regardless of EXECUTION_LOG_MODE.
        execution_log.write(
            LogEntry(
                run_id=run_id, engine="hk", operation="hk_run", table_fqn=fqn,
                domain=table_row.get("domain", ""),
                layer=table_row.get("layer", ""), tier=tier,
                environment=table_row.get("environment", "prod"),
                status="RUNNING", dry_run=dry_run, lock_id=lock_id,
                started_at=datetime.now(UTC),
            ),
            dry_run=dry_run,
        )

        # ── Gate 1 — Upstream batch completion (Control-M) ────────────────────
        if hk_config.get("gate1_enabled", 0):
            upstream_job = (
                table_row.get("dependent_on_controlm_job")
                or table_row.get("controlm_pipeline_job")
                or table_row.get("dependent_job_name")
            )
            if upstream_job and not is_upstream_job_complete(upstream_job):
                _write("hk_run", "SKIPPED", skip_reason="SKIP_UPSTREAM_PENDING")
                result.status, result.skip_reason = "SKIPPED", "SKIP_UPSTREAM_PENDING"
                return result

        # ── Gate 2 — Safe window ───────────────────────────────────────────────
        if hk_config.get("gate2_enabled", 1):
            decision = evaluate(hk_config.get("window_config", ""), force=table_row.get("force_run", False))
            if decision != EXECUTE:
                _write("hk_run", "SKIPPED", skip_reason=decision)
                result.status, result.skip_reason = "SKIPPED", decision
                return result

        # ── Idempotency ─────────────────────────────────────────────────────────
        window_id    = get_window_id()
        execution_id = build_execution_id(run_id, fqn, "hk_run", window_id)
        if check_already_executed(execution_id, fqn):
            _write("hk_run", "SKIPPED", skip_reason=f"SKIP_DUPLICATE (execution_id={execution_id})")
            result.status, result.skip_reason = "SKIPPED", "SKIP_DUPLICATE"
            return result

        # ── Gate 3 — run_frequency + dedupe ──────────────────────────────────────
        due, skip_reason = is_due(fqn, hk_config)
        if not due:
            _write("hk_run", "SKIPPED", skip_reason=skip_reason)
            result.status, result.skip_reason = "SKIPPED", skip_reason
            return result

        # ── Gate 4 — Circuit breaker ──────────────────────────────────────────────
        if hk_config.get("gate3_enabled", 1) and circuit_breaker.check(fqn) == circuit_breaker.OPEN:
            _write("hk_run", "SKIPPED", skip_reason="SKIP_CIRCUIT_OPEN")
            result.status, result.skip_reason = "SKIPPED", "SKIP_CIRCUIT_OPEN"
            return result

        # ── Property sync (first-run ALTER) ────────────────────────────────────
        workgroup = _TIER_TO_WORKGROUP.get(tier, "zamboni-standard")
        if needs_property_sync(table_row):
            sync_result = apply_vacuum_properties(fqn, hk_config, workgroup, dry_run=dry_run)
            if sync_result.get("status") == "SUCCESS":
                mark_properties_synced(fqn, workgroup, dry_run=dry_run)
            _write("property_sync", sync_result.get("status", "FAILURE"), error_message=sync_result.get("error"))

        # ── Health check ──────────────────────────────────────────────────────────
        last_orphan_ts = None
        try:
            last_orphan = execution_log.get_last_run(fqn, operation="orphan_cleanup", only_success=True)
            last_orphan_ts = last_orphan.get("completed_at") if last_orphan else None
        except Exception:
            last_orphan_ts = None

        health = health_checker.check(fqn, hk_config, workgroup=workgroup, last_orphan_cleanup_at=last_orphan_ts)
        if not health.check_success:
            _write("hk_run", "FAILURE", error_message=f"Health check failed: {health.check_error}")
            result.status = "FAILURE"
            return result

        if is_healthy(health):
            _write("hk_run", "SKIPPED", skip_reason="SKIP_HEALTHY")
            result.status, result.skip_reason = "SKIPPED", "SKIP_HEALTHY"
            return result

        # ── OPTIMIZE → verify_advanced ──────────────────────────────────────────
        if health.needs_compaction:
            step, log_status, log_kwargs = _run_optimize_step(
                fqn, hk_config, health, tier, workgroup, dry_run, lock_id, table_row,
            )
            _write("compaction", log_status, **log_kwargs)
            result.steps.append(step)
            if step.status == "FAILURE":
                _trip_and_alert(fqn, "OPTIMIZE", step.detail, dry_run)
                result.status = "FAILURE"
                return result

        # ── SAFE-VACUUM → verify_advanced (contracts.md §5-A) ────────────────────
        if health.needs_vacuum or health.needs_orphan_cleanup:
            step, log_status, log_kwargs = _run_safe_vacuum_step(
                fqn, hk_config, health, tier, workgroup, dry_run, lock_id, run_id,
            )
            _write("vacuum", log_status, **log_kwargs)
            result.steps.append(step)
            if step.status == "FAILURE":
                _trip_and_alert(fqn, "SAFE-VACUUM", step.detail, dry_run)
                result.status = "FAILURE"
                return result

        if not dry_run:
            mark_executed(execution_id, fqn, dry_run=False)

        _write("hk_run", "DRY_RUN" if dry_run else "SUCCESS")
        return result
    finally:
        lock_service.release(lock)
        log_buffer.flush(dry_run=dry_run)


def _trip_and_alert(fqn: str, step_name: str, detail: str | None, dry_run: bool) -> None:
    """
    contracts.md §5: any verify FAILED or step exception trips the circuit
    breaker immediately (not threshold-gated like the legacy op-failure
    path) and sends an SNS alert — this failure mode IS the incident.
    """
    failure_count = execution_log.get_failure_count(fqn) + 1
    circuit_breaker.trip(fqn, failure_count, dry_run=dry_run)
    notifier.send_alert(
        subject=f"Integrity check failed after {step_name}",
        message=detail or "no detail",
        table_fqn=fqn,
        dry_run=dry_run,
    )


def _run_optimize_step(
    fqn: str, hk_config: dict, health: HealthResult, tier: str,
    workgroup: str, dry_run: bool, lock_id: str, table_row: dict,
) -> tuple[StepResult, str, dict]:
    """Returns (StepResult, execution_log status, execution_log kwargs)."""
    started = datetime.now(UTC)

    if not wait_for_capacity(workgroup, max_wait_seconds=30):
        reason = f"SKIP_BACKPRESSURE_TIMEOUT (workgroup={workgroup})"
        return (
            StepResult("optimize", "SKIPPED"), "SKIPPED",
            {"skip_reason": reason, "started_at": started, "completed_at": datetime.now(UTC)},
        )

    before = capture_state(fqn)
    try:
        op_result = maintenance_ops.run_optimize(fqn, hk_config, health, tier, dry_run, table_row=table_row)
    except AthenaQueryTimeout as te:
        detail = f"compaction: {te}"
        return (
            StepResult("optimize", "FAILURE", detail=detail), "FAILURE",
            {"error_message": detail, "started_at": started, "completed_at": datetime.now(UTC)},
        )
    except Exception as e:
        detail = f"compaction: {e}"
        return (
            StepResult("optimize", "FAILURE", detail=detail), "FAILURE",
            {"error_message": detail, "started_at": started, "completed_at": datetime.now(UTC)},
        )

    if dry_run:
        integrity = IntegrityResult("SKIPPED", "optimize", "dry_run")
        after     = before
    else:
        after     = capture_state(fqn)
        integrity = verify_advanced(before, after, "optimize")

    log_kwargs = {
        "lock_id":                  lock_id,
        "metadata_location_before": before.metadata_location,
        "metadata_location_after":  after.metadata_location if not dry_run else None,
        "snapshot_id_before":       before.current_snapshot_id,
        "snapshot_id_after":        after.current_snapshot_id if not dry_run else None,
        "integrity_status":         integrity.status,
        "started_at":               started,
        "completed_at":              datetime.now(UTC),
        "files_compacted":          op_result.get("files_compacted"),
        "bytes_rewritten":          op_result.get("bytes_rewritten"),
        "athena_query_id":         op_result.get("athena_query_id"),
        "bytes_scanned":            op_result.get("bytes_scanned", 0),
    }

    if dry_run:
        return StepResult("optimize", "DRY_RUN", integrity.status, integrity.detail), "DRY_RUN", log_kwargs
    if integrity.status == "FAILED":
        return StepResult("optimize", "FAILURE", integrity.status, integrity.detail), "FAILURE", log_kwargs
    return StepResult("optimize", "SUCCESS", integrity.status, integrity.detail), "SUCCESS", log_kwargs


def _run_safe_vacuum_step(
    fqn: str, hk_config: dict, health: HealthResult, tier: str,
    workgroup: str, dry_run: bool, lock_id: str, run_id: str,
) -> tuple[StepResult, str, dict]:
    """Returns (StepResult, execution_log status, execution_log kwargs)."""
    started = datetime.now(UTC)

    if not wait_for_capacity(workgroup, max_wait_seconds=30):
        reason = f"SKIP_BACKPRESSURE_TIMEOUT (workgroup={workgroup})"
        return (
            StepResult("vacuum", "SKIPPED"), "SKIPPED",
            {"skip_reason": reason, "started_at": started, "completed_at": datetime.now(UTC)},
        )

    before = capture_state(fqn)
    try:
        vac_result = maintenance_ops.run_safe_vacuum(fqn, hk_config, health, tier, workgroup, dry_run)
    except Exception as e:
        detail   = f"vacuum: {e}"
        completed = datetime.now(UTC)
        return (
            StepResult("vacuum", "FAILURE", detail=detail), "FAILURE",
            {"error_message": detail, "started_at": started, "completed_at": completed},
        )

    completed = datetime.now(UTC)

    if vac_result.aborted:
        maintenance_ops.write_vacuum_audit(
            run_id=run_id, table_fqn=fqn, operation="vacuum", lock_id=lock_id, dry_run=dry_run,
            snapshots_before=before.snapshot_count,
            files_estimated=vac_result.files_estimated,
            older_than_hours_used=vac_result.older_than_hours_used,
            sanity_pct=vac_result.sanity_pct,
            aborted=True, aborted_reason=vac_result.aborted_reason,
            started_at=started, completed_at=completed,
        )
        return (
            StepResult("vacuum", "FAILURE", "FAILED", vac_result.aborted_reason), "FAILURE",
            {
                "error_message":    vac_result.aborted_reason,
                "integrity_status": "FAILED",
                "started_at":        started,
                "completed_at":       completed,
            },
        )

    if dry_run:
        integrity = IntegrityResult("SKIPPED", "vacuum", "dry_run")
        after     = before
    else:
        after     = capture_state(fqn)
        integrity = verify_advanced(before, after, "vacuum", min_snapshot_age_hours=vac_result.older_than_hours_used)

    maintenance_ops.write_vacuum_audit(
        run_id=run_id, table_fqn=fqn, operation="vacuum", lock_id=lock_id, dry_run=dry_run,
        snapshots_before=before.snapshot_count,
        snapshots_after=(after.snapshot_count if not dry_run else None),
        files_estimated=vac_result.files_estimated, files_deleted=vac_result.files_deleted,
        bytes_reclaimed=vac_result.bytes_reclaimed, older_than_hours_used=vac_result.older_than_hours_used,
        sanity_pct=vac_result.sanity_pct, aborted=False,
        started_at=started, completed_at=completed,
    )

    log_kwargs = {
        "lock_id":                  lock_id,
        "metadata_location_before": before.metadata_location,
        "metadata_location_after":  after.metadata_location if not dry_run else None,
        "snapshot_id_before":       before.current_snapshot_id,
        "snapshot_id_after":        after.current_snapshot_id if not dry_run else None,
        "integrity_status":         integrity.status,
        "snapshots_before":         before.snapshot_count,
        "snapshots_after":          after.snapshot_count if not dry_run else None,
        "orphan_files_deleted":     vac_result.files_deleted,
        "athena_query_id":         vac_result.vacuum_result.get("athena_query_id"),
        "bytes_scanned":            vac_result.vacuum_result.get("bytes_scanned", 0),
        "started_at":                started,
        "completed_at":               completed,
    }

    if dry_run:
        return StepResult("vacuum", "DRY_RUN", integrity.status, integrity.detail), "DRY_RUN", log_kwargs
    if integrity.status == "FAILED":
        return StepResult("vacuum", "FAILURE", integrity.status, integrity.detail), "FAILURE", log_kwargs
    return StepResult("vacuum", "SUCCESS", integrity.status, integrity.detail), "SUCCESS", log_kwargs

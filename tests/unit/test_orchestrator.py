"""
Unit tests for engine/core/orchestrator.py (Phase 1b, contracts.md §5 + §5-A).
"""
from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import engine.core.orchestrator as orch
from engine.core.health_checker import HealthResult
from engine.core.integrity_checker import TableState
from engine.core.maintenance_ops import SafeVacuumResult

TABLE_ROW = {
    "table_fqn":   "glue_catalog.finance_db.orch_t1",
    "tier":        "standard",
    "domain":      "finance",
    "layer":       "staging",
    "environment": "prod",
}

# Gates 1-3 disabled + every_trigger frequency -- isolates the tests to the
# OPTIMIZE/SAFE-VACUUM sequence under test, matching contracts.md §5's LOCKED
# sequence rather than re-testing gates 1-4 (already covered by test_safety_core.py).
HK_CONFIG = {
    "run_frequency":          "every_trigger",
    "window_config":          "",
    "gate1_enabled":          0,
    "gate2_enabled":          0,
    "gate3_enabled":          0,
    "snapshot_retention_days": 7,
    "snapshot_min_to_keep":    30,
}


def _state(loc, count, ts=None):
    return TableState(
        table_fqn=TABLE_ROW["table_fqn"], metadata_location=loc,
        current_snapshot_id=1, snapshot_count=count,
        current_snapshot_ts=ts, captured_at=datetime.now(UTC),
    )


def _health(**kw) -> HealthResult:
    h = HealthResult(table_fqn=TABLE_ROW["table_fqn"])
    h.check_success = True
    for k, v in kw.items():
        setattr(h, k, v)
    return h


def _base_patches(monkeypatch, health):
    """Common wiring shared by every orchestrator test below."""
    monkeypatch.setattr(orch.registry, "get_table", lambda fqn: dict(TABLE_ROW))
    monkeypatch.setattr(orch, "get_hk_config", lambda fqn: dict(HK_CONFIG))
    monkeypatch.setattr(orch, "check_with_cache", lambda fqn: {"conflict": False})
    monkeypatch.setattr(orch.execution_log, "get_running", lambda fqn: None)
    monkeypatch.setattr(orch.execution_log, "get_last_run", lambda *a, **k: None)
    monkeypatch.setattr(orch.execution_log, "get_failure_count", lambda fqn: 0)
    monkeypatch.setattr(orch.execution_log, "write", lambda entry, dry_run=False: True)
    monkeypatch.setattr(orch, "check_already_executed", lambda *a, **k: False)
    monkeypatch.setattr(orch, "needs_property_sync", lambda table_row: False)
    monkeypatch.setattr(orch.health_checker, "check", lambda *a, **k: health)
    monkeypatch.setattr(orch, "EXECUTION_LOG_MODE", "insert")  # avoid real S3/Parquet flush

    lock = MagicMock(table_fqn=TABLE_ROW["table_fqn"], lock_owner="me")
    lock_service = MagicMock()
    lock_service.acquire.return_value = lock
    monkeypatch.setattr(orch, "LockService", lambda: lock_service)
    return lock_service


# ══════════════════════════════════════════════════════════════════════════════
#  Happy path
# ══════════════════════════════════════════════════════════════════════════════

def test_happy_path_calls_optimize_then_vacuum_in_order_under_one_lock(monkeypatch):
    health = _health(needs_compaction=True, needs_vacuum=True, needs_orphan_cleanup=False, snapshot_count=100)
    lock_service = _base_patches(monkeypatch, health)

    mark_calls = []
    monkeypatch.setattr(orch, "mark_executed", lambda *a, **k: mark_calls.append(a))

    calls = []
    monkeypatch.setattr(orch.maintenance_ops, "run_optimize", lambda *a, **k: calls.append("optimize") or {"files_compacted": 5})
    vac_result = SafeVacuumResult(vacuum_result={"athena_query_id": "q1"})
    monkeypatch.setattr(orch.maintenance_ops, "run_safe_vacuum", lambda *a, **k: calls.append("vacuum") or vac_result)
    audit_calls = []
    monkeypatch.setattr(orch.maintenance_ops, "write_vacuum_audit", lambda **k: audit_calls.append(k))

    states = iter([
        _state("s3://v1", 100),                       # before optimize
        _state("s3://v2", 101),                        # after optimize
        _state("s3://v2", 101),                        # before vacuum
        _state("s3://v3", 40, datetime.now(UTC)),       # after vacuum
    ])
    monkeypatch.setattr(orch, "capture_state", lambda fqn: next(states))

    result = orch.run_table_maintenance(TABLE_ROW["table_fqn"], dry_run=False, run_id="run-happy")

    assert calls == ["optimize", "vacuum"]
    assert result.status == "SUCCESS"
    assert [s.step for s in result.steps] == ["optimize", "vacuum"]
    assert all(s.integrity_status == "VERIFIED" for s in result.steps)
    assert len(audit_calls) == 1
    assert len(mark_calls) == 1
    lock_service.acquire.assert_called_once_with(TABLE_ROW["table_fqn"], "orchestrated_maintenance")
    lock_service.release.assert_called_once()


# ══════════════════════════════════════════════════════════════════════════════
#  Mid-step integrity failure halts remaining steps
# ══════════════════════════════════════════════════════════════════════════════

def test_optimize_integrity_failure_halts_before_vacuum_and_trips_breaker(monkeypatch):
    health = _health(needs_compaction=True, needs_vacuum=True, needs_orphan_cleanup=False, snapshot_count=100)
    lock_service = _base_patches(monkeypatch, health)

    calls = []
    monkeypatch.setattr(orch.maintenance_ops, "run_optimize", lambda *a, **k: calls.append("optimize") or {})
    monkeypatch.setattr(orch.maintenance_ops, "run_safe_vacuum", lambda *a, **k: calls.append("vacuum") or SafeVacuumResult())
    monkeypatch.setattr(orch.maintenance_ops, "write_vacuum_audit", lambda **k: None)

    # Pointer does NOT advance after OPTIMIZE -> verify_advanced FAILS
    states = iter([_state("s3://v1", 100), _state("s3://v1", 100)])
    monkeypatch.setattr(orch, "capture_state", lambda fqn: next(states))

    tripped = []
    monkeypatch.setattr(orch.circuit_breaker, "trip", lambda fqn, count, dry_run=False: tripped.append((fqn, count)))
    alerts = []
    monkeypatch.setattr(orch.notifier, "send_alert", lambda **k: alerts.append(k))

    result = orch.run_table_maintenance(TABLE_ROW["table_fqn"], dry_run=False, run_id="run-fail")

    assert calls == ["optimize"]  # vacuum never called -- halted
    assert result.status == "FAILURE"
    assert len(result.steps) == 1
    assert result.steps[0].integrity_status == "FAILED"
    assert len(tripped) == 1
    assert len(alerts) == 1
    lock_service.release.assert_called_once()


# ══════════════════════════════════════════════════════════════════════════════
#  Dry run
# ══════════════════════════════════════════════════════════════════════════════

def test_dry_run_skips_verify_and_mark_executed(monkeypatch):
    health = _health(needs_compaction=True, needs_vacuum=True, needs_orphan_cleanup=False, snapshot_count=100)
    lock_service = _base_patches(monkeypatch, health)

    monkeypatch.setattr(orch.maintenance_ops, "run_optimize", lambda *a, **k: {"files_compacted": 1})
    monkeypatch.setattr(orch.maintenance_ops, "run_safe_vacuum", lambda *a, **k: SafeVacuumResult(dry_run=True))
    monkeypatch.setattr(orch.maintenance_ops, "write_vacuum_audit", lambda **k: None)

    capture_calls = []
    monkeypatch.setattr(orch, "capture_state", lambda fqn: capture_calls.append(fqn) or _state("s3://v1", 100))

    mark_calls = []
    monkeypatch.setattr(orch, "mark_executed", lambda *a, **k: mark_calls.append(a))

    result = orch.run_table_maintenance(TABLE_ROW["table_fqn"], dry_run=True, run_id="run-dry")

    assert result.status == "SUCCESS"
    assert result.dry_run is True
    assert all(s.status == "DRY_RUN" for s in result.steps)
    assert all(s.integrity_status == "SKIPPED" for s in result.steps)
    # Only the "before" state is captured per step in dry_run -- no "after".
    assert len(capture_calls) == 2
    assert mark_calls == []
    lock_service.release.assert_called_once()


# ══════════════════════════════════════════════════════════════════════════════
#  Backpressure / workgroup mapping regression
# ══════════════════════════════════════════════════════════════════════════════

def test_backpressure_routes_through_tier_to_workgroup_mapping(monkeypatch):
    health = _health(needs_compaction=True, needs_vacuum=False, needs_orphan_cleanup=False, snapshot_count=10)
    lock_service = _base_patches(monkeypatch, health)

    monkeypatch.setattr(orch.maintenance_ops, "run_optimize", lambda *a, **k: {})
    states = iter([_state("s3://v1", 10), _state("s3://v2", 11)])
    monkeypatch.setattr(orch, "capture_state", lambda fqn: next(states))

    seen_workgroups = []

    def fake_wait(workgroup, **kw):
        seen_workgroups.append(workgroup)
        return True

    monkeypatch.setattr(orch, "wait_for_capacity", fake_wait)

    orch.run_table_maintenance(TABLE_ROW["table_fqn"], dry_run=False, run_id="run-bp")

    # Must receive the real zamboni-* workgroup name, not the raw tier alias.
    assert seen_workgroups == ["zamboni-standard"]
    lock_service.release.assert_called_once()


# ══════════════════════════════════════════════════════════════════════════════
#  Gate 0 (standalone callability -- contracts.md §4)
# ══════════════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════════════
#  Lease lost -- no subsequent operation starts (2026-07-11 audit fix)
# ══════════════════════════════════════════════════════════════════════════════

class _FakeLostHeartbeat:
    """A LockHeartbeat stand-in whose lease is already lost -- used to
    verify the orchestrator genuinely refuses to start OPTIMIZE once
    ownership may have moved to another process, without needing to wait
    on a real background thread's timing."""
    lost = True

    def __init__(self, *a, **k):
        pass

    def start(self):
        pass

    def stop(self):
        pass

    def assert_held(self):
        from engine.core.lock_service import LockLostError
        raise LockLostError("lease lost (test)")


def test_lease_already_lost_before_optimize_prevents_optimize_and_vacuum(monkeypatch):
    """No subsequent operation starts after ownership is lost: OPTIMIZE
    must never be called, and since OPTIMIZE fails the run halts before
    SAFE-VACUUM is even attempted."""
    health = HealthResult(table_fqn=TABLE_ROW["table_fqn"], needs_compaction=True, needs_vacuum=True, snapshot_count=100)
    _base_patches(monkeypatch, health)
    monkeypatch.setattr(orch, "LockHeartbeat", _FakeLostHeartbeat)

    optimize_calls = []
    vacuum_calls = []
    monkeypatch.setattr(orch.maintenance_ops, "run_optimize", lambda *a, **k: optimize_calls.append(1) or {})
    monkeypatch.setattr(orch.maintenance_ops, "run_safe_vacuum", lambda *a, **k: vacuum_calls.append(1) or SafeVacuumResult())

    tripped = []
    monkeypatch.setattr(orch.circuit_breaker, "trip", lambda fqn, count, dry_run=False: tripped.append((fqn, count)))
    alerts = []
    monkeypatch.setattr(orch.notifier, "send_alert", lambda **k: alerts.append(k))

    result = orch.run_table_maintenance(TABLE_ROW["table_fqn"], dry_run=False, run_id="run-lease-lost")

    assert optimize_calls == [], "OPTIMIZE must never be called once the lease is already lost"
    assert vacuum_calls == [], "SAFE-VACUUM must never start either -- the run halts on OPTIMIZE's failure"
    assert result.status == "FAILURE"
    assert len(tripped) == 1
    assert len(alerts) == 1


def test_lease_lost_between_optimize_and_vacuum_prevents_vacuum(monkeypatch):
    """A lease still held for OPTIMIZE but lost by the time SAFE-VACUUM
    would start must let OPTIMIZE finish (already in flight) but refuse
    to start VACUUM."""
    health = HealthResult(table_fqn=TABLE_ROW["table_fqn"], needs_compaction=True, needs_vacuum=True, snapshot_count=100)
    _base_patches(monkeypatch, health)

    class _FlippingHeartbeat:
        lost = False

        def __init__(self, *a, **k):
            pass

        def start(self):
            pass

        def stop(self):
            pass

        def assert_held(self):
            if self.lost:
                from engine.core.lock_service import LockLostError
                raise LockLostError("lease lost mid-run (test)")

    fake_heartbeat = _FlippingHeartbeat()
    monkeypatch.setattr(orch, "LockHeartbeat", lambda *a, **k: fake_heartbeat)

    def fake_optimize(*a, **k):
        fake_heartbeat.lost = True  # simulate the lease being lost right after OPTIMIZE completes
        return {"files_compacted": 1}

    monkeypatch.setattr(orch.maintenance_ops, "run_optimize", fake_optimize)
    vacuum_calls = []
    monkeypatch.setattr(orch.maintenance_ops, "run_safe_vacuum", lambda *a, **k: vacuum_calls.append(1) or SafeVacuumResult())

    # optimize's before + after, then vacuum's "before" capture (which
    # happens ahead of the try/assert_held() block regardless of whether
    # the step goes on to actually run).
    states = iter([_state("s3://v1", 100), _state("s3://v2", 101), _state("s3://v2", 101)])
    monkeypatch.setattr(orch, "capture_state", lambda fqn: next(states))

    monkeypatch.setattr(orch.circuit_breaker, "trip", lambda fqn, count, dry_run=False: None)
    monkeypatch.setattr(orch.notifier, "send_alert", lambda **k: None)

    result = orch.run_table_maintenance(TABLE_ROW["table_fqn"], dry_run=False, run_id="run-lease-lost-mid")

    assert vacuum_calls == [], "SAFE-VACUUM must never start once the lease was lost after OPTIMIZE"
    assert result.status == "FAILURE"


def test_gate0_lock_held_skips_without_touching_operations(monkeypatch):
    health = _health(needs_compaction=True, needs_vacuum=True, snapshot_count=100)
    monkeypatch.setattr(orch.registry, "get_table", lambda fqn: dict(TABLE_ROW))
    monkeypatch.setattr(orch, "get_hk_config", lambda fqn: dict(HK_CONFIG))
    monkeypatch.setattr(orch, "check_with_cache", lambda fqn: {"conflict": False})
    monkeypatch.setattr(orch.execution_log, "get_running", lambda fqn: None)
    monkeypatch.setattr(orch.execution_log, "write", lambda *a, **k: True)
    monkeypatch.setattr(orch, "EXECUTION_LOG_MODE", "insert")
    monkeypatch.setattr(orch.health_checker, "check", lambda *a, **k: health)

    optimize_calls = []
    monkeypatch.setattr(orch.maintenance_ops, "run_optimize", lambda *a, **k: optimize_calls.append(1) or {})

    lock_service = MagicMock()
    lock_service.acquire.return_value = None  # contended
    monkeypatch.setattr(orch, "LockService", lambda: lock_service)

    result = orch.run_table_maintenance(TABLE_ROW["table_fqn"], dry_run=True, run_id="run-lock")

    assert result.status == "SKIPPED"
    assert result.skip_reason == "SKIP_LOCK_HELD"
    assert optimize_calls == []
    lock_service.release.assert_not_called()


# ══════════════════════════════════════════════════════════════════════════════
#  vacuum_audit_buffer threading (2026-07-16 audit-batching perf fix)
# ══════════════════════════════════════════════════════════════════════════════

def test_vacuum_audit_buffer_receives_row_instead_of_immediate_write(monkeypatch):
    """When a fleet-wide vacuum_audit_buffer is passed in, the successful
    vacuum path must append to it (via maintenance_ops.write_vacuum_audit's
    real buffer= handling) instead of writing to Athena immediately."""
    health = _health(needs_compaction=False, needs_vacuum=True, needs_orphan_cleanup=False, snapshot_count=100)
    _base_patches(monkeypatch, health)

    vac_result = SafeVacuumResult(vacuum_result={"athena_query_id": "q1"})
    monkeypatch.setattr(orch.maintenance_ops, "run_safe_vacuum", lambda *a, **k: vac_result)

    queries = []
    monkeypatch.setattr(orch.maintenance_ops, "run_query", lambda sql, **k: queries.append(sql))

    states = iter([_state("s3://v2", 101), _state("s3://v3", 40, datetime.now(UTC))])
    monkeypatch.setattr(orch, "capture_state", lambda fqn: next(states))

    from engine.core.execution_log_parquet import AuditBuffer
    buffer = AuditBuffer(write_many_fn=orch.maintenance_ops.write_vacuum_audit_many)

    result = orch.run_table_maintenance(
        TABLE_ROW["table_fqn"], dry_run=False, run_id="run-buffered",
        vacuum_audit_buffer=buffer,
    )

    assert result.status == "SUCCESS"
    assert queries == [], "vacuum_audit must not write to Athena immediately when a buffer is given"
    assert len(buffer._rows) == 1
    assert buffer._rows[0]["table_fqn"] == TABLE_ROW["table_fqn"]

    n = buffer.flush()
    assert n == 1
    assert len(queries) == 1, "the buffered row must land in exactly one Athena write once flushed"


def test_vacuum_audit_buffer_receives_aborted_row_too(monkeypatch):
    """Both the aborted and successful vacuum paths append to the same
    buffer -- the aborted branch must not bypass it and write immediately."""
    health = _health(needs_compaction=False, needs_vacuum=True, needs_orphan_cleanup=False, snapshot_count=100)
    _base_patches(monkeypatch, health)

    aborted_result = SafeVacuumResult(aborted=True, aborted_reason="ORPHAN_SANITY_ABORT")
    monkeypatch.setattr(orch.maintenance_ops, "run_safe_vacuum", lambda *a, **k: aborted_result)
    monkeypatch.setattr(orch.circuit_breaker, "trip", lambda fqn, count, dry_run=False: None)
    monkeypatch.setattr(orch.notifier, "send_alert", lambda **k: None)

    queries = []
    monkeypatch.setattr(orch.maintenance_ops, "run_query", lambda sql, **k: queries.append(sql))
    monkeypatch.setattr(orch, "capture_state", lambda fqn: _state("s3://v1", 100))

    from engine.core.execution_log_parquet import AuditBuffer
    buffer = AuditBuffer(write_many_fn=orch.maintenance_ops.write_vacuum_audit_many)

    result = orch.run_table_maintenance(
        TABLE_ROW["table_fqn"], dry_run=False, run_id="run-aborted-buffered",
        vacuum_audit_buffer=buffer,
    )

    assert result.status == "FAILURE"
    assert queries == [], "an aborted vacuum row must also be buffered, not written immediately"
    assert len(buffer._rows) == 1
    assert buffer._rows[0]["aborted"] is True


def test_no_vacuum_audit_buffer_preserves_immediate_write(monkeypatch):
    """Omitting vacuum_audit_buffer (the default, e.g. a single ad-hoc
    table run) must preserve the original immediate-write behavior --
    exact regression guard for the pre-existing single-table callers."""
    health = _health(needs_compaction=False, needs_vacuum=True, needs_orphan_cleanup=False, snapshot_count=100)
    _base_patches(monkeypatch, health)

    vac_result = SafeVacuumResult(vacuum_result={"athena_query_id": "q1"})
    monkeypatch.setattr(orch.maintenance_ops, "run_safe_vacuum", lambda *a, **k: vac_result)

    queries = []
    monkeypatch.setattr(orch.maintenance_ops, "run_query", lambda sql, **k: queries.append(sql))
    states = iter([_state("s3://v2", 101), _state("s3://v3", 40, datetime.now(UTC))])
    monkeypatch.setattr(orch, "capture_state", lambda fqn: next(states))

    result = orch.run_table_maintenance(TABLE_ROW["table_fqn"], dry_run=False, run_id="run-unbuffered")

    assert result.status == "SUCCESS"
    assert len(queries) == 1, "no buffer given -- vacuum_audit must write immediately, exactly as before"

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
    "stream_id":   "s1",
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

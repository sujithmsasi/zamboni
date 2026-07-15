"""
Engine-level wiring tests for the audit-pipeline batching perf fix
(2026-07-16): HKEngine's fleet-wide vacuum_audit AuditBuffer, and
LifecycleEngine's governance-run AuditBuffer for execution_log rows
(run() buffers; run_cleanup()'s destructive catalog_cleanup rows
deliberately stay immediate -- see lifecycle_engine.py::_write_log()'s
own docstring).
"""
from __future__ import annotations

import engine.engines.hk_engine as hk_engine_mod
import engine.engines.lifecycle_engine as lifecycle_engine_mod
from engine.core.execution_log_parquet import AuditBuffer
from engine.engines.hk_engine import HKEngine
from engine.engines.lifecycle_engine import LifecycleEngine

# ══════════════════════════════════════════════════════════════════════════════
#  HKEngine -- fleet-wide vacuum_audit buffer
# ══════════════════════════════════════════════════════════════════════════════

def test_hk_engine_run_wires_vacuum_audit_buffer_and_flushes_it():
    """Source-level check (same convention as
    test_gap6_parquet_buffer_wired_in_hk_engine): AuditBuffer must be
    created for vacuum_audit and flushed at the end of run()."""
    with open("engine/engines/hk_engine.py", encoding="utf-8") as f:
        content = f.read()

    assert "self._vacuum_audit_buffer = AuditBuffer(" in content
    assert "write_vacuum_audit_many" in content
    assert "self._vacuum_audit_buffer.flush_async()" in content


def test_process_table_passes_instance_vacuum_audit_buffer_to_orchestrator(monkeypatch):
    """_process_table() must forward self._vacuum_audit_buffer (the one
    fleet-wide buffer created in run()) into
    orchestrator.run_table_maintenance(), not create a new one per table
    or drop it."""
    engine = HKEngine(dry_run=True)
    engine._vacuum_audit_buffer = AuditBuffer(write_many_fn=lambda rows: len(rows))

    captured = {}

    class _FakeResult:
        status = "SUCCESS"

    def _fake_run_table_maintenance(fqn, dry_run=True, run_id=None, vacuum_audit_buffer=None):
        captured["vacuum_audit_buffer"] = vacuum_audit_buffer
        captured["fqn"] = fqn
        return _FakeResult()

    monkeypatch.setattr(
        "engine.core.orchestrator.run_table_maintenance",
        _fake_run_table_maintenance,
    )

    table_row = {"table_fqn": "glue_catalog.finance_db.t1", "tier": "standard"}
    outcome = engine._process_table(table_row)

    assert outcome == "succeeded"
    assert captured["vacuum_audit_buffer"] is engine._vacuum_audit_buffer
    assert captured["fqn"] == "glue_catalog.finance_db.t1"


def test_vacuum_audit_buffer_flush_async_is_a_no_op_when_nothing_ran(monkeypatch):
    """A fleet run where no table needed vacuum must still flush cleanly
    (empty buffer) -- no Athena call, no exception."""
    calls = []
    monkeypatch.setattr(
        "engine.core.maintenance_ops.write_vacuum_audit_many",
        lambda rows: calls.append(rows) or len(rows),
    )
    buffer = AuditBuffer(write_many_fn=hk_engine_mod.maintenance_ops.write_vacuum_audit_many)
    fut = buffer.flush_async()

    assert fut.result(timeout=2) == 0
    assert calls == []


# ══════════════════════════════════════════════════════════════════════════════
#  LifecycleEngine -- governance run (run()) buffers; run_cleanup() doesn't
# ══════════════════════════════════════════════════════════════════════════════

def test_lifecycle_run_batches_write_log_calls_into_one_write_many(monkeypatch):
    """run()'s per-table _write_log() calls (via _evaluate_table()) must
    all land in one execution_log.write_many() call at the end of the run,
    not one execution_log.write() per table."""
    rows = [
        {"table_fqn": "glue_catalog.finance_preprod_db.t1", "lifecycle_state": "ACTIVE"},
        {"table_fqn": "glue_catalog.finance_preprod_db.t2", "lifecycle_state": "ACTIVE"},
        {"table_fqn": "glue_catalog.finance_preprod_db.t3", "lifecycle_state": "ACTIVE"},
    ]
    engine = LifecycleEngine(dry_run=False)
    monkeypatch.setattr(engine, "_get_active_registry_tables", lambda env: rows)

    def _fake_evaluate(table_row):
        # Simulate what a real transition does: write a log entry via the
        # engine's own _write_log(), which should route through the buffer.
        engine._write_log(table_row, "lifecycle_transition", "SUCCESS")
        return "transitioned"

    monkeypatch.setattr(engine, "_evaluate_table", _fake_evaluate)

    write_calls = []
    write_many_calls = []
    monkeypatch.setattr(lifecycle_engine_mod.execution_log, "write", lambda *a, **k: write_calls.append(1) or True)
    monkeypatch.setattr(
        lifecycle_engine_mod.execution_log, "write_many",
        lambda entries, dry_run=False: write_many_calls.append(list(entries)) or len(entries),
    )

    result = engine.run(environment="preprod")

    assert result["transitioned"] == 3
    assert write_calls == [], "no per-table execution_log.write() call should happen when buffered"
    assert len(write_many_calls) == 1, "all 3 tables' log rows must land in exactly one write_many() call"
    assert len(write_many_calls[0]) == 3


def test_lifecycle_cleanup_writes_immediately_not_buffered(monkeypatch):
    """run_cleanup() intentionally does NOT set self._audit_buffer --
    catalog_cleanup rows (documenting an irreversible Glue DROP) stay
    synchronous, the 'critical event' carve-out AuditBuffer's docstring
    describes."""
    engine = LifecycleEngine(dry_run=False)

    write_calls = []
    write_many_calls = []
    monkeypatch.setattr(lifecycle_engine_mod.execution_log, "write", lambda *a, **k: write_calls.append(1) or True)
    monkeypatch.setattr(
        lifecycle_engine_mod.execution_log, "write_many",
        lambda entries, dry_run=False: write_many_calls.append(list(entries)) or len(entries),
    )

    table_row = {"table_fqn": "glue_catalog.finance_preprod_db.t1", "domain": "finance", "environment": "preprod"}
    engine._write_log(table_row, "catalog_cleanup", "SUCCESS", bytes_reclaimed=1024)

    assert len(write_calls) == 1, "run_cleanup()-style calls (no buffer set) must write immediately"
    assert write_many_calls == []


def test_lifecycle_write_log_uses_buffer_when_one_is_set(monkeypatch):
    """Direct unit check of _write_log()'s buffer-routing branch, isolated
    from run()'s own control flow."""
    engine = LifecycleEngine(dry_run=False)
    appended = []
    engine._audit_buffer = AuditBuffer(write_many_fn=lambda entries: appended.extend(entries) or len(entries))

    write_calls = []
    monkeypatch.setattr(lifecycle_engine_mod.execution_log, "write", lambda *a, **k: write_calls.append(1) or True)

    table_row = {"table_fqn": "t1", "domain": "finance", "environment": "preprod"}
    engine._write_log(table_row, "lifecycle_transition", "SUCCESS")

    assert write_calls == [], "must not write immediately once a buffer is set"
    assert len(engine._audit_buffer._rows) == 1, "the entry must be sitting in the buffer, unflushed"

    engine._audit_buffer.flush()
    assert len(appended) == 1
    assert appended[0].table_fqn == "t1"

"""
Unit tests for the audit-pipeline batching perf fix (2026-07-16):
  - engine.core.execution_log_parquet.AuditBuffer (generic buffer + flush,
    the ParquetLogBuffer-alike for tables with no Parquet/add_files path)
  - engine.core.audit.persist_many() (audit_log's batched write)
  - engine.core.maintenance_ops.write_vacuum_audit()/write_vacuum_audit_many()
    (vacuum_audit's buffer-aware write + batched write)
"""
from __future__ import annotations

import threading
import time
from datetime import UTC, datetime

import pytest

import engine.core.audit as audit_mod
import engine.core.execution_log_parquet as elp
import engine.core.maintenance_ops as mo
from engine.core.audit import AuditAction, AuditEvent
from engine.core.execution_log_parquet import AuditBuffer


def _event(target_id="t1") -> AuditEvent:
    return AuditEvent(
        actor="tester", action_type=AuditAction.HK_ENABLE,
        page_source="test", target_type="table", target_id=target_id,
    )


# ══════════════════════════════════════════════════════════════════════════════
#  AuditBuffer -- append / flush basics
# ══════════════════════════════════════════════════════════════════════════════

def test_append_does_not_flush_immediately():
    calls = []
    buffer = AuditBuffer(write_many_fn=lambda rows: calls.append(rows) or len(rows), max_size=50)

    buffer.append(_event("a"))
    buffer.append(_event("b"))

    assert calls == [], "append() must not write until flush() or max_size"


def test_flush_performs_exactly_one_write_many_call_for_all_buffered_rows():
    calls = []
    buffer = AuditBuffer(write_many_fn=lambda rows: calls.append(list(rows)) or len(rows))

    for i in range(5):
        buffer.append(_event(f"t{i}"))
    n = buffer.flush()

    assert n == 5
    assert len(calls) == 1, "flush() must be exactly one write_many_fn call, not one per row"
    assert len(calls[0]) == 5


def test_flush_clears_the_buffer():
    calls = []
    buffer = AuditBuffer(write_many_fn=lambda rows: calls.append(rows) or len(rows))
    buffer.append(_event())
    buffer.flush()
    buffer.flush()  # second flush -- buffer should already be empty

    assert len(calls) == 1, "a second flush() on an empty buffer must not call write_many_fn again"


def test_flush_on_empty_buffer_is_a_noop():
    calls = []
    buffer = AuditBuffer(write_many_fn=lambda rows: calls.append(rows) or len(rows))
    n = buffer.flush()

    assert n == 0
    assert calls == []


def test_append_auto_flushes_once_max_size_reached():
    calls = []
    buffer = AuditBuffer(write_many_fn=lambda rows: calls.append(list(rows)) or len(rows), max_size=3)

    buffer.append(_event("a"))
    buffer.append(_event("b"))
    assert calls == [], "must not flush before max_size"
    buffer.append(_event("c"))

    assert len(calls) == 1, "must auto-flush synchronously once max_size is reached"
    assert len(calls[0]) == 3


def test_flush_never_raises_even_if_write_many_fn_raises():
    def _boom(rows):
        raise RuntimeError("Athena unavailable")

    buffer = AuditBuffer(write_many_fn=_boom)
    buffer.append(_event())

    n = buffer.flush()  # must not raise

    assert n == 0


# ══════════════════════════════════════════════════════════════════════════════
#  AuditBuffer -- flush_async() / non-blocking background flush
# ══════════════════════════════════════════════════════════════════════════════

def test_flush_async_runs_off_the_calling_thread():
    seen_threads = []

    def _write_many(rows):
        seen_threads.append(threading.current_thread())
        return len(rows)

    buffer = AuditBuffer(write_many_fn=_write_many)
    buffer.append(_event())

    fut = buffer.flush_async()
    result = fut.result(timeout=5)

    assert result == 1
    assert len(seen_threads) == 1
    assert seen_threads[0] is not threading.current_thread()
    assert seen_threads[0].name.startswith("zamboni-audit-flush")


def test_flush_async_on_empty_buffer_does_not_touch_the_executor(monkeypatch):
    calls = []
    monkeypatch.setattr(elp._BACKGROUND_FLUSH_EXECUTOR, "submit", lambda *a, **k: calls.append(1))

    buffer = AuditBuffer(write_many_fn=lambda rows: len(rows))
    fut = buffer.flush_async()

    assert calls == [], "an empty buffer must not submit anything to the background executor"
    assert fut.result(timeout=1) == 0


def test_flush_async_failure_logs_warning_not_exception():
    def _boom(rows):
        raise RuntimeError("Athena unavailable")

    buffer = AuditBuffer(write_many_fn=_boom, label="test-boom")
    buffer.append(_event())

    fut = buffer.flush_async()
    result = fut.result(timeout=5)  # must not raise -- background failure is swallowed + logged

    assert result == 0


def test_submit_background_flush_falls_back_to_sync_on_submit_failure(monkeypatch):
    monkeypatch.setattr(
        elp._BACKGROUND_FLUSH_EXECUTOR, "submit",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("executor shut down")),
    )
    calls = []
    fut = elp.submit_background_flush(lambda: calls.append(1) or 1, "test-label")

    assert calls == [1], "submission failure must fall back to a synchronous call, not lose the flush"
    assert fut.result(timeout=1) == 1


def test_append_and_flush_are_thread_safe_under_concurrent_append():
    calls = []
    lock = threading.Lock()

    def _write_many(rows):
        with lock:
            calls.append(list(rows))
        return len(rows)

    buffer = AuditBuffer(write_many_fn=_write_many, max_size=1_000_000)  # avoid auto-flush mid-test

    def _worker(n):
        for i in range(20):
            buffer.append(_event(f"{n}-{i}"))

    threads = [threading.Thread(target=_worker, args=(n,)) for n in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    n = buffer.flush()
    assert n == 200, "every concurrently-appended row must survive into the flush"


# ══════════════════════════════════════════════════════════════════════════════
#  engine.core.audit.persist_many()
# ══════════════════════════════════════════════════════════════════════════════

def test_persist_many_is_one_batched_insert_not_per_event(monkeypatch):
    queries = []
    # persist_many() imports run_query lazily from engine.utils.athena_client --
    # patch it there, matching how _persist()/persist_many() actually resolve it.
    import engine.utils.athena_client as athena_client_mod
    monkeypatch.setattr(athena_client_mod, "run_query", lambda sql, **k: queries.append(sql))

    events = [_event(f"t{i}") for i in range(4)]
    n = audit_mod.persist_many(events)

    assert n == 4
    assert len(queries) == 1, "4 events should produce exactly 1 INSERT, not 4"
    assert all(f"'t{i}'" in queries[0] for i in range(4))


def test_persist_many_empty_list_is_noop(monkeypatch):
    calls = []
    import engine.utils.athena_client as athena_client_mod
    monkeypatch.setattr(athena_client_mod, "run_query", lambda sql, **k: calls.append(sql))

    assert audit_mod.persist_many([]) == 0
    assert calls == []


def test_persist_many_falls_back_to_per_event_audit_on_batch_failure(monkeypatch):
    import engine.utils.athena_client as athena_client_mod

    def _boom(sql, **k):
        raise RuntimeError("Athena timeout")
    monkeypatch.setattr(athena_client_mod, "run_query", _boom)

    persisted = []
    monkeypatch.setattr(audit_mod, "audit", lambda event: persisted.append(event.target_id) or True)

    events = [_event("a"), _event("b")]
    n = audit_mod.persist_many(events)

    assert n == 2
    assert persisted == ["a", "b"], "fallback must still persist every event individually"


# ══════════════════════════════════════════════════════════════════════════════
#  maintenance_ops.write_vacuum_audit(buffer=...) / write_vacuum_audit_many()
# ══════════════════════════════════════════════════════════════════════════════

def test_write_vacuum_audit_with_buffer_appends_instead_of_writing(monkeypatch):
    queries = []
    monkeypatch.setattr(mo, "run_query", lambda sql, **k: queries.append(sql))

    buffer = AuditBuffer(write_many_fn=mo.write_vacuum_audit_many)
    mo.write_vacuum_audit(
        run_id="run-1", table_fqn="t1", operation="vacuum", lock_id="lock-1",
        dry_run=False, aborted=False, buffer=buffer,
    )

    assert queries == [], "with a buffer, write_vacuum_audit() must not write immediately"
    assert len(buffer._rows) == 1


def test_write_vacuum_audit_without_buffer_writes_immediately(monkeypatch):
    """Preserves original behavior for standalone/single-table callers (no
    regression to the pre-existing test_write_vacuum_audit_builds_insert_with_expected_columns)."""
    queries = []
    monkeypatch.setattr(mo, "run_query", lambda sql, **k: queries.append(sql))

    mo.write_vacuum_audit(
        run_id="run-1", table_fqn="t1", operation="vacuum", lock_id="lock-1",
        dry_run=False, aborted=False,
    )

    assert len(queries) == 1


def test_write_vacuum_audit_many_is_one_batched_insert_for_uniform_dry_run(monkeypatch):
    queries = []
    monkeypatch.setattr(mo, "run_query", lambda sql, **k: queries.append((sql, k)))

    rows = [
        {
            "run_id": "run-1", "table_fqn": f"t{i}", "operation": "vacuum",
            "lock_id": "lock-1", "dry_run": False, "aborted": False,
            "started_at": datetime.now(UTC), "completed_at": datetime.now(UTC),
        }
        for i in range(5)
    ]

    n = mo.write_vacuum_audit_many(rows)

    assert n == 5
    assert len(queries) == 1, "5 rows with the same dry_run value should be exactly 1 INSERT"
    sql, kwargs = queries[0]
    assert all(f"'t{i}'" in sql for i in range(5))
    assert kwargs.get("dry_run") is False


def test_write_vacuum_audit_many_splits_by_dry_run_flag(monkeypatch):
    """A table individually mid dry-run ramp-up can produce a mixed-dry_run
    buffer within one fleet run -- must not force one flag onto every row,
    since run_query(dry_run=True) skips the real Athena write entirely."""
    calls = []
    monkeypatch.setattr(mo, "run_query", lambda sql, **k: calls.append(k.get("dry_run")))

    rows = [
        {"run_id": "r", "table_fqn": "real1", "operation": "vacuum", "lock_id": None,
         "dry_run": False, "aborted": False, "started_at": datetime.now(UTC), "completed_at": datetime.now(UTC)},
        {"run_id": "r", "table_fqn": "real2", "operation": "vacuum", "lock_id": None,
         "dry_run": False, "aborted": False, "started_at": datetime.now(UTC), "completed_at": datetime.now(UTC)},
        {"run_id": "r", "table_fqn": "dry1", "operation": "vacuum", "lock_id": None,
         "dry_run": True, "aborted": False, "started_at": datetime.now(UTC), "completed_at": datetime.now(UTC)},
    ]

    n = mo.write_vacuum_audit_many(rows)

    assert n == 3
    assert sorted(calls) == [False, True], "exactly 2 batched INSERTs -- one per dry_run group"


def test_write_vacuum_audit_many_empty_list_is_noop(monkeypatch):
    calls = []
    monkeypatch.setattr(mo, "run_query", lambda sql, **k: calls.append(sql))

    assert mo.write_vacuum_audit_many([]) == 0
    assert calls == []


def test_write_vacuum_audit_many_falls_back_to_per_row_on_batch_failure(monkeypatch):
    call_count = {"n": 0}

    def _run_query(sql, **k):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("batched insert failed")
        return None

    monkeypatch.setattr(mo, "run_query", _run_query)

    rows = [
        {"run_id": "r", "table_fqn": "a", "operation": "vacuum", "lock_id": None,
         "dry_run": False, "aborted": False, "started_at": datetime.now(UTC), "completed_at": datetime.now(UTC)},
        {"run_id": "r", "table_fqn": "b", "operation": "vacuum", "lock_id": None,
         "dry_run": False, "aborted": False, "started_at": datetime.now(UTC), "completed_at": datetime.now(UTC)},
    ]
    n = mo.write_vacuum_audit_many(rows)

    # 1 failed batch attempt + 2 per-row fallback attempts = 3 calls total
    assert call_count["n"] == 3
    assert n == 2, "both rows must still land via the per-row fallback"


def test_write_vacuum_audit_many_one_bad_row_does_not_drop_the_others(monkeypatch):
    """The batch INSERT fails once (forcing the per-row fallback), then one
    specific row's own individual write also fails -- the other two rows
    must still land."""
    call_log = []

    def _run_query(sql, **k):
        call_log.append(sql)
        if len(call_log) == 1:
            raise RuntimeError("batch failed")
        if "'bad'" in sql:
            raise RuntimeError("row failed")
        return None

    monkeypatch.setattr(mo, "run_query", _run_query)
    monkeypatch.setattr(mo, "_vacuum_audit_values_tuple", lambda row: f"('{row['table_fqn']}')")

    rows = [
        {"run_id": "r", "table_fqn": "good1", "operation": "vacuum", "lock_id": None, "dry_run": False, "aborted": False},
        {"run_id": "r", "table_fqn": "bad", "operation": "vacuum", "lock_id": None, "dry_run": False, "aborted": False},
        {"run_id": "r", "table_fqn": "good2", "operation": "vacuum", "lock_id": None, "dry_run": False, "aborted": False},
    ]
    n = mo.write_vacuum_audit_many(rows)

    assert n == 2, "the one bad row must not prevent the other two from being written"

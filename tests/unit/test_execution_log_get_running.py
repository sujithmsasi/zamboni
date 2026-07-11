"""
Regression test for engine/core/execution_log.py::get_running().

Real bug found in a 2026-07-09 audit: execution_log is append-only -- a
run's RUNNING row is never updated in place, only superseded by a second,
terminal row (see orchestrator.py's unbuffered RUNNING write followed by a
separate terminal _write() call, same run_id + operation). The original
query only filtered on status='RUNNING' with no correlation to that later
terminal row, so ANY table that completed even one orchestrated run would
show as permanently "already running" on every subsequent Gate 0 check --
it never cleared. These tests exercise the real SQL against a real SQLite
table, not a mocked read_sql, since the bug was in the query itself.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

import config.settings as settings
import engine.utils.athena_client as athena_client
import engine.utils.local_db as local_db
from engine.core import execution_log

_DDL = """
CREATE TABLE IF NOT EXISTS execution_log (
    execution_id TEXT,
    run_id       TEXT,
    engine       TEXT,
    operation    TEXT,
    table_fqn    TEXT,
    status       TEXT,
    started_at   TEXT
)
"""


@pytest.fixture
def log_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test_execution_log.db"
    monkeypatch.setattr(athena_client, "ZAMBONI_LOCAL_MODE", True)
    monkeypatch.setattr(settings, "ZAMBONI_LOCAL_DB", str(db_path))
    monkeypatch.setattr(local_db, "_conns", {})

    local_db.create_tables({"execution_log": _DDL}, db_path=str(db_path))
    conn = local_db.get_connection(str(db_path))
    yield conn
    monkeypatch.setattr(local_db, "_conns", {})


def _insert(conn, *, run_id, operation, status, started_at, fqn="glue_catalog.db.t1"):
    conn.execute(
        "INSERT INTO execution_log (execution_id, run_id, engine, operation, "
        "table_fqn, status, started_at) VALUES (?, ?, 'hk', ?, ?, ?, ?)",
        (f"{run_id}-{status}", run_id, operation, fqn, status, started_at),
    )
    conn.commit()


def test_genuinely_running_row_is_returned(log_db):
    _insert(
        log_db, run_id="run-1", operation="hk_run", status="RUNNING",
        started_at=datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S"),
    )
    result = execution_log.get_running("glue_catalog.db.t1")
    assert result is not None
    assert result["run_id"] == "run-1"


def test_completed_run_no_longer_blocks_the_table(log_db):
    """The exact bug: a table that finished one run must not be
    permanently gated out of every future run."""
    started = (datetime.now(UTC) - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
    _insert(log_db, run_id="run-1", operation="hk_run", status="RUNNING", started_at=started)
    _insert(log_db, run_id="run-1", operation="hk_run", status="SUCCESS", started_at=started)

    assert execution_log.get_running("glue_catalog.db.t1") is None


def test_completed_run_does_not_mask_a_new_genuinely_running_run(log_db):
    """A second, later run for the same table must still be detected as
    in-flight even though an earlier run_id already completed."""
    old_started = (datetime.now(UTC) - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    _insert(log_db, run_id="run-1", operation="hk_run", status="RUNNING", started_at=old_started)
    _insert(log_db, run_id="run-1", operation="hk_run", status="SUCCESS", started_at=old_started)

    new_started = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
    _insert(log_db, run_id="run-2", operation="hk_run", status="RUNNING", started_at=new_started)

    result = execution_log.get_running("glue_catalog.db.t1")
    assert result is not None
    assert result["run_id"] == "run-2"


def test_stale_running_row_past_lock_ttl_is_ignored(log_db):
    """A crashed run (RUNNING written, process killed before any terminal
    write) must not lock the table out forever -- bounded by the same TTL
    LockService already uses to treat a lock as abandoned."""
    stale_started = (
        datetime.now(UTC) - timedelta(minutes=settings.LOCK_TTL_MINUTES + 30)
    ).strftime("%Y-%m-%d %H:%M:%S")
    _insert(log_db, run_id="run-1", operation="hk_run", status="RUNNING", started_at=stale_started)

    assert execution_log.get_running("glue_catalog.db.t1") is None


def test_recent_running_row_within_lock_ttl_still_blocks(log_db):
    recent_started = (
        datetime.now(UTC) - timedelta(minutes=settings.LOCK_TTL_MINUTES - 10)
    ).strftime("%Y-%m-%d %H:%M:%S")
    _insert(log_db, run_id="run-1", operation="hk_run", status="RUNNING", started_at=recent_started)

    assert execution_log.get_running("glue_catalog.db.t1") is not None


def test_unparseable_started_at_fails_closed_not_open(log_db):
    """Real bug fix (2026-07-10): _coerce_datetime() returns None for an
    unparseable started_at (e.g. pandas' NaT sentinel, which stringifies
    to the literal text 'NaT'), and the old `if started_dt and ...` check
    silently fell through to `return row` in that case -- treating a
    crashed/malformed row as genuinely in-flight forever, with no TTL
    bound at all. Must now return None (not blocking) instead, the same
    direction the TTL-based staleness check already fails."""
    _insert(log_db, run_id="run-1", operation="hk_run", status="RUNNING", started_at="NaT")

    assert execution_log.get_running("glue_catalog.db.t1") is None

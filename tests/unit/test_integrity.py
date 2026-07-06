"""
Unit tests for engine/core/integrity_checker.py (Phase 1b, contracts.md §5-A).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

import engine.core.integrity_checker as ic
from engine.core.integrity_checker import TableState, capture_state, verify_advanced


def _state(metadata_location, snapshot_count, current_snapshot_ts=None, current_snapshot_id=1):
    return TableState(
        table_fqn="glue_catalog.db.t", metadata_location=metadata_location,
        current_snapshot_id=current_snapshot_id, snapshot_count=snapshot_count,
        current_snapshot_ts=current_snapshot_ts, captured_at=datetime.now(UTC),
    )


# ══════════════════════════════════════════════════════════════════════════════
#  capture_state
# ══════════════════════════════════════════════════════════════════════════════

def test_capture_state_local_mode_stub(monkeypatch):
    monkeypatch.setattr(ic, "ZAMBONI_LOCAL_MODE", True)
    state = capture_state("glue_catalog.db.t")
    assert state.metadata_location == ic._LOCAL_STUB_LOCATION
    assert state.snapshot_count is None
    assert state.current_snapshot_id is None


def test_capture_state_reads_glue_metadata_and_snapshots(monkeypatch):
    monkeypatch.setattr(ic, "ZAMBONI_LOCAL_MODE", False)
    monkeypatch.setattr(ic, "get_table", lambda db, t: {"Parameters": {"metadata_location": "s3://meta/v5.json"}})

    ts = datetime.now(UTC)
    calls = []

    def fake_read_sql(sql, **kw):
        calls.append(sql)
        if "ORDER BY committed_at DESC" in sql:
            return pd.DataFrame([{"snapshot_id": 42, "committed_at": ts.isoformat()}])
        return pd.DataFrame([{"cnt": 7}])

    monkeypatch.setattr(ic, "read_sql", fake_read_sql)

    state = capture_state("glue_catalog.finance_db.t")

    assert state.metadata_location == "s3://meta/v5.json"
    assert state.current_snapshot_id == 42
    assert state.snapshot_count == 7
    assert len(calls) == 2


def test_capture_state_survives_glue_and_athena_failures(monkeypatch):
    """Fail-safe: a bad Glue/Athena lookup should not raise, just leave fields None."""
    monkeypatch.setattr(ic, "ZAMBONI_LOCAL_MODE", False)
    monkeypatch.setattr(ic, "get_table", lambda db, t: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(ic, "read_sql", lambda sql, **kw: (_ for _ in ()).throw(RuntimeError("boom")))

    state = capture_state("glue_catalog.finance_db.t")

    assert state.metadata_location is None
    assert state.snapshot_count is None


# ══════════════════════════════════════════════════════════════════════════════
#  verify_advanced — optimize
# ══════════════════════════════════════════════════════════════════════════════

def test_verify_optimize_advanced_ok():
    before = _state("s3://v1.json", 10)
    after  = _state("s3://v2.json", 11)
    result = verify_advanced(before, after, "optimize")
    assert result.status == "VERIFIED"


def test_verify_optimize_pointer_unchanged_fails():
    before = _state("s3://v1.json", 10)
    after  = _state("s3://v1.json", 11)
    result = verify_advanced(before, after, "optimize")
    assert result.status == "FAILED"
    assert "did not change" in result.detail


def test_verify_optimize_snapshot_count_decrease_fails():
    before = _state("s3://v1.json", 10)
    after  = _state("s3://v2.json", 9)
    result = verify_advanced(before, after, "optimize")
    assert result.status == "FAILED"
    assert "decreased" in result.detail


# ══════════════════════════════════════════════════════════════════════════════
#  verify_advanced — vacuum (contracts.md §5-A)
# ══════════════════════════════════════════════════════════════════════════════

def test_verify_vacuum_advanced_ok():
    now    = datetime.now(UTC)
    before = _state("s3://v1.json", 100)
    after  = _state("s3://v2.json", 40, current_snapshot_ts=now - timedelta(hours=100))
    result = verify_advanced(before, after, "vacuum", min_snapshot_age_hours=72)
    assert result.status == "VERIFIED"


def test_verify_vacuum_pointer_unchanged_fails():
    """§5-A voids §5's old 'pointer unchanged for orphan' rule -- combined
    VACUUM always commits, so an unchanged pointer is now a FAILURE."""
    before = _state("s3://v1.json", 100)
    after  = _state("s3://v1.json", 100)
    result = verify_advanced(before, after, "vacuum")
    assert result.status == "FAILED"
    assert "combined expire+orphan" in result.detail


def test_verify_vacuum_snapshot_count_increase_fails():
    before = _state("s3://v1.json", 50)
    after  = _state("s3://v2.json", 51)
    result = verify_advanced(before, after, "vacuum")
    assert result.status == "FAILED"
    assert "increased" in result.detail


def test_verify_vacuum_below_age_floor_fails():
    now    = datetime.now(UTC)
    before = _state("s3://v1.json", 100)
    after  = _state("s3://v2.json", 40, current_snapshot_ts=now - timedelta(hours=10))
    result = verify_advanced(before, after, "vacuum", min_snapshot_age_hours=72)
    assert result.status == "FAILED"
    assert "floor" in result.detail


def test_verify_vacuum_no_floor_given_skips_age_check():
    before = _state("s3://v1.json", 100)
    after  = _state("s3://v2.json", 40, current_snapshot_ts=datetime.now(UTC))
    result = verify_advanced(before, after, "vacuum")
    assert result.status == "VERIFIED"


# ══════════════════════════════════════════════════════════════════════════════
#  verify_advanced — local mode / misuse
# ══════════════════════════════════════════════════════════════════════════════

def test_verify_local_mode_stub_is_skipped():
    before = _state(ic._LOCAL_STUB_LOCATION, None)
    after  = _state(ic._LOCAL_STUB_LOCATION, None)
    result = verify_advanced(before, after, "vacuum")
    assert result.status == "SKIPPED"


def test_verify_unknown_operation_raises():
    before = _state("s3://v1.json", 10)
    after  = _state("s3://v2.json", 10)
    with pytest.raises(ValueError):
        verify_advanced(before, after, "expire")

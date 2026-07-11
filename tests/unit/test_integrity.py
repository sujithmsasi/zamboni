"""
Unit tests for engine/core/integrity_checker.py (Phase 1b, contracts.md §5-A).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

import engine.core.integrity_checker as ic
from engine.core.integrity_checker import TableState, capture_state, verify_advanced


def _state(
    metadata_location, snapshot_count, current_snapshot_ts=None, current_snapshot_id=1,
    metadata_capture_ok=True, snapshot_capture_ok=True,
):
    return TableState(
        table_fqn="glue_catalog.db.t", metadata_location=metadata_location,
        current_snapshot_id=current_snapshot_id, snapshot_count=snapshot_count,
        current_snapshot_ts=current_snapshot_ts, captured_at=datetime.now(UTC),
        metadata_capture_ok=metadata_capture_ok, snapshot_capture_ok=snapshot_capture_ok,
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
    assert state.metadata_capture_ok is True
    assert state.snapshot_capture_ok is True


def test_capture_state_survives_glue_and_athena_failures(monkeypatch):
    """Fail-safe: a bad Glue/Athena lookup should not raise, just leave fields None."""
    monkeypatch.setattr(ic, "ZAMBONI_LOCAL_MODE", False)
    monkeypatch.setattr(ic, "get_table", lambda db, t: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(ic, "read_sql", lambda sql, **kw: (_ for _ in ()).throw(RuntimeError("boom")))

    state = capture_state("glue_catalog.finance_db.t")

    assert state.metadata_location is None
    assert state.snapshot_count is None
    assert state.metadata_capture_ok is False
    assert state.snapshot_capture_ok is False


def test_capture_state_table_not_found_marks_metadata_capture_failed(monkeypatch):
    """get_table() returning falsy (table not found) is also a capture
    failure, not a legitimately-empty metadata_location."""
    monkeypatch.setattr(ic, "ZAMBONI_LOCAL_MODE", False)
    monkeypatch.setattr(ic, "get_table", lambda db, t: None)
    monkeypatch.setattr(ic, "read_sql", lambda sql, **kw: pd.DataFrame([{"cnt": 0}]))

    state = capture_state("glue_catalog.finance_db.t")

    assert state.metadata_capture_ok is False


def test_capture_state_glue_ok_but_snapshot_count_query_fails_marks_snapshot_capture_failed(monkeypatch):
    """2026-07-11 audit fix: Glue and Athena capture status must be tracked
    independently -- a Glue success alongside an Athena snapshot-count
    failure must not look like a fully successful capture."""
    monkeypatch.setattr(ic, "ZAMBONI_LOCAL_MODE", False)
    monkeypatch.setattr(ic, "get_table", lambda db, t: {"Parameters": {"metadata_location": "s3://meta/v5.json"}})
    monkeypatch.setattr(ic, "read_sql", lambda sql, **kw: (_ for _ in ()).throw(RuntimeError("athena boom")))

    state = capture_state("glue_catalog.finance_db.t")

    assert state.metadata_capture_ok is True
    assert state.metadata_location == "s3://meta/v5.json"
    assert state.snapshot_capture_ok is False


def test_capture_state_empty_count_result_marks_snapshot_capture_failed(monkeypatch):
    """A genuine COUNT(*) query always returns exactly one row -- an empty
    result means something went wrong with the query itself, not a
    legitimate zero-snapshots table (which would show cnt=0, one row)."""
    monkeypatch.setattr(ic, "ZAMBONI_LOCAL_MODE", False)
    monkeypatch.setattr(ic, "get_table", lambda db, t: {"Parameters": {"metadata_location": "s3://meta/v5.json"}})

    def fake_read_sql(sql, **kw):
        if "ORDER BY committed_at DESC" in sql:
            return pd.DataFrame()
        return pd.DataFrame()  # empty count result

    monkeypatch.setattr(ic, "read_sql", fake_read_sql)

    state = capture_state("glue_catalog.finance_db.t")

    assert state.snapshot_capture_ok is False


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


# ══════════════════════════════════════════════════════════════════════════════
#  verify_advanced — metadata_capture_ok false-positive fix (2026-07-10 audit)
# ══════════════════════════════════════════════════════════════════════════════

def test_verify_before_metadata_capture_failed_fails_closed_not_false_verified():
    """Real bug this closes: before.metadata_location=None (capture failed)
    vs after=a real value used to compare as 'changed' -> false VERIFIED.
    Must now report FAILED, since the before-state was never confirmed."""
    before = _state(None, None, metadata_capture_ok=False)
    after  = _state("s3://v2.json", 11)
    result = verify_advanced(before, after, "optimize")
    assert result.status == "FAILED"
    assert "metadata capture failed" in result.detail


def test_verify_after_metadata_capture_failed_fails_closed_not_false_verified():
    before = _state("s3://v1.json", 10)
    after  = _state(None, None, metadata_capture_ok=False)
    result = verify_advanced(before, after, "vacuum")
    assert result.status == "FAILED"
    assert "metadata capture failed" in result.detail


def test_verify_both_metadata_capture_ok_true_unaffected():
    """Sanity: the normal (both captures succeeded) path is untouched."""
    before = _state("s3://v1.json", 10, metadata_capture_ok=True)
    after  = _state("s3://v2.json", 11, metadata_capture_ok=True)
    result = verify_advanced(before, after, "optimize")
    assert result.status == "VERIFIED"


# ══════════════════════════════════════════════════════════════════════════════
#  verify_advanced — snapshot_capture_ok independent-failure fix (2026-07-11)
# ══════════════════════════════════════════════════════════════════════════════

def test_verify_before_snapshot_capture_failed_fails_closed():
    """Real gap this closes: a snapshot-capture failure used to leave
    snapshot_count=None on that side, which the count/age checks silently
    skip via `is not None` guards -- masking a real regression the Athena
    side simply failed to report on. Must FAIL, not silently VERIFY."""
    before = _state("s3://v1.json", None, snapshot_capture_ok=False)
    after  = _state("s3://v2.json", 40, current_snapshot_ts=datetime.now(UTC))
    result = verify_advanced(before, after, "vacuum")
    assert result.status == "FAILED"
    assert "snapshot capture failed" in result.detail


def test_verify_after_snapshot_capture_failed_fails_closed():
    before = _state("s3://v1.json", 100)
    after  = _state("s3://v2.json", None, snapshot_capture_ok=False)
    result = verify_advanced(before, after, "optimize")
    assert result.status == "FAILED"
    assert "snapshot capture failed" in result.detail


def test_verify_metadata_ok_but_snapshot_capture_failed_still_fails():
    """Even when the metadata pointer comparison alone would have looked
    fine (a real advance), a failed snapshot capture on either side must
    still fail the whole verification -- it's a separate, independent
    safety check, not a fallback covered by the pointer check."""
    before = _state("s3://v1.json", 100, snapshot_capture_ok=False)
    after  = _state("s3://v2.json", 40)
    result = verify_advanced(before, after, "vacuum")
    assert result.status == "FAILED"
    assert "snapshot capture failed" in result.detail

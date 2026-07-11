"""
Unit tests for engine/core/maintenance_ops.py (Phase 1b, contracts.md §5-A
SAFE-VACUUM: property clamp -> readback verify -> pre-flight sanity ->
bare VACUUM -> post-audit).

2026-07-11 audit fix: every safety prerequisite here now either succeeds
or raises SafetyCheckError -- there is no more "log a warning and proceed
anyway" path. run_safe_vacuum() converts a SafetyCheckError into the same
SafeVacuumResult.aborted path the pre-existing sanity-threshold abort
already used, so VACUUM is provably never called when any prerequisite
(property write, property verification, snapshot query, or files query)
fails or returns an incomplete result.
"""
from __future__ import annotations

import pandas as pd
import pytest

import engine.core.maintenance_ops as mo
from engine.core.health_checker import HealthResult

TABLE_FQN = "glue_catalog.finance_db.orders"

# snapshot_retention_days=1 -> 24h policy, well below the 72h floor
HK_CONFIG = {"snapshot_retention_days": 1, "snapshot_min_to_keep": 30}


def _files_df():
    return pd.DataFrame([{"total_files": 500, "total_bytes": 1_000_000}])


def _matching_glue_table(floor_seconds: int, min_keep: int):
    """A get_table() stub whose Parameters match whatever the clamp just
    wrote -- the readback-verification happy path."""
    return {
        "Parameters": {
            "vacuum_max_snapshot_age_seconds": str(floor_seconds),
            "vacuum_min_snapshots_to_keep": str(min_keep),
        }
    }


def _patch_matching_readback(monkeypatch, floor_seconds: int, min_keep: int):
    monkeypatch.setattr(
        "engine.utils.glue_client.get_table",
        lambda db, t: _matching_glue_table(floor_seconds, min_keep),
    )


# ══════════════════════════════════════════════════════════════════════════════
#  Property clamp — happy path (write + readback verify)
# ══════════════════════════════════════════════════════════════════════════════

def test_clamp_enforces_floor_regardless_of_tiny_policy(monkeypatch):
    from config.settings import ORPHAN_MIN_AGE_HOURS_FLOOR, SNAPSHOT_MIN_AGE_HOURS

    captured = []
    monkeypatch.setattr(mo, "run_query", lambda sql, **k: captured.append(sql))
    monkeypatch.setattr(mo, "ZAMBONI_LOCAL_MODE", False)
    # floor_hours = max(24h policy, 72h, 24h) = 72h -> 259200s, min_keep=30
    _patch_matching_readback(monkeypatch, 72 * 3600, 30)

    clamp = mo._clamp_vacuum_properties(TABLE_FQN, HK_CONFIG, "zamboni-standard", dry_run=False)

    assert clamp["floor_hours"] >= ORPHAN_MIN_AGE_HOURS_FLOOR
    assert clamp["floor_hours"] >= SNAPSHOT_MIN_AGE_HOURS
    assert len(captured) == 1
    assert "SET TBLPROPERTIES" in captured[0]


def test_clamp_respects_policy_when_above_floor(monkeypatch):
    monkeypatch.setattr(mo, "run_query", lambda sql, **k: None)
    monkeypatch.setattr(mo, "ZAMBONI_LOCAL_MODE", False)
    hk_config = {"snapshot_retention_days": 30, "snapshot_min_to_keep": 5}  # 720h -- above floor
    _patch_matching_readback(monkeypatch, 30 * 24 * 3600, 5)

    clamp = mo._clamp_vacuum_properties(TABLE_FQN, hk_config, "zamboni-standard", dry_run=False)

    assert clamp["floor_hours"] == 30 * 24
    assert clamp["min_keep"] == 5


def test_clamp_dry_run_skips_readback_verification(monkeypatch):
    """Nothing was actually written in dry_run -- verifying a readback of
    a no-op write would be meaningless (and would see stale properties)."""
    monkeypatch.setattr(mo, "run_query", lambda sql, **k: None)
    monkeypatch.setattr(mo, "ZAMBONI_LOCAL_MODE", False)

    calls = []
    monkeypatch.setattr("engine.utils.glue_client.get_table", lambda db, t: calls.append(1) or None)

    clamp = mo._clamp_vacuum_properties(TABLE_FQN, HK_CONFIG, "zamboni-standard", dry_run=True)

    assert calls == [], "dry_run must never attempt a readback verification"
    assert clamp["min_keep"] == 30


def test_clamp_local_mode_skips_readback_verification(monkeypatch):
    """SQLite has no TBLPROPERTIES/Glue equivalent to read back -- same
    documented local-mode approximation as _preflight_sanity's
    "$snapshots"/"$files"."""
    monkeypatch.setattr(mo, "run_query", lambda sql, **k: None)
    monkeypatch.setattr(mo, "ZAMBONI_LOCAL_MODE", True)

    calls = []
    monkeypatch.setattr("engine.utils.glue_client.get_table", lambda db, t: calls.append(1) or None)

    clamp = mo._clamp_vacuum_properties(TABLE_FQN, HK_CONFIG, "zamboni-standard", dry_run=False)

    assert calls == []
    assert clamp["min_keep"] == 30


# ══════════════════════════════════════════════════════════════════════════════
#  Fault injection — every prerequisite must block VACUUM on failure
# ══════════════════════════════════════════════════════════════════════════════

def test_property_clamp_write_failure_aborts_before_vacuum(monkeypatch):
    def _raise(sql, **k):
        raise RuntimeError("Athena unavailable")

    monkeypatch.setattr(mo, "run_query", _raise)
    monkeypatch.setattr(mo, "ZAMBONI_LOCAL_MODE", False)

    vacuum_calls = []
    monkeypatch.setattr(mo.vacuum, "run_expire_snapshots", lambda **k: vacuum_calls.append(k) or {})

    health = HealthResult(table_fqn=TABLE_FQN, snapshot_count=100)
    result = mo.run_safe_vacuum(TABLE_FQN, HK_CONFIG, health, "standard", "zamboni-standard", dry_run=False)

    assert result.aborted is True
    assert "PROPERTY_CLAMP_FAILED" in result.aborted_reason
    assert vacuum_calls == [], "VACUUM must never be called when the property write fails"


def test_property_clamp_readback_mismatch_aborts_before_vacuum(monkeypatch):
    """A "successful" ALTER call that didn't actually take effect (readback
    shows different values than requested) must abort just as hard as an
    outright write failure."""
    monkeypatch.setattr(mo, "run_query", lambda sql, **k: None)
    monkeypatch.setattr(mo, "ZAMBONI_LOCAL_MODE", False)
    # Mismatched on purpose -- readback shows a much weaker floor than requested.
    monkeypatch.setattr("engine.utils.glue_client.get_table", lambda db, t: _matching_glue_table(3600, 1))

    vacuum_calls = []
    monkeypatch.setattr(mo.vacuum, "run_expire_snapshots", lambda **k: vacuum_calls.append(k) or {})

    health = HealthResult(table_fqn=TABLE_FQN, snapshot_count=100)
    result = mo.run_safe_vacuum(TABLE_FQN, HK_CONFIG, health, "standard", "zamboni-standard", dry_run=False)

    assert result.aborted is True
    assert "PROPERTY_CLAMP_FAILED" in result.aborted_reason
    assert "did not take effect" in result.aborted_reason
    assert vacuum_calls == []


def test_property_clamp_readback_table_not_found_aborts(monkeypatch):
    monkeypatch.setattr(mo, "run_query", lambda sql, **k: None)
    monkeypatch.setattr(mo, "ZAMBONI_LOCAL_MODE", False)
    monkeypatch.setattr("engine.utils.glue_client.get_table", lambda db, t: None)

    vacuum_calls = []
    monkeypatch.setattr(mo.vacuum, "run_expire_snapshots", lambda **k: vacuum_calls.append(k) or {})

    health = HealthResult(table_fqn=TABLE_FQN, snapshot_count=100)
    result = mo.run_safe_vacuum(TABLE_FQN, HK_CONFIG, health, "standard", "zamboni-standard", dry_run=False)

    assert result.aborted is True
    assert vacuum_calls == []


def test_preflight_snapshot_query_failure_aborts_before_vacuum(monkeypatch):
    monkeypatch.setattr(mo, "run_query", lambda sql, **k: None)
    monkeypatch.setattr(mo, "ZAMBONI_LOCAL_MODE", False)
    _patch_matching_readback(monkeypatch, 72 * 3600, 30)

    def _raise(sql, **k):
        raise RuntimeError("Athena query timed out")

    monkeypatch.setattr(mo, "read_sql", _raise)

    vacuum_calls = []
    monkeypatch.setattr(mo.vacuum, "run_expire_snapshots", lambda **k: vacuum_calls.append(k) or {})

    health = HealthResult(table_fqn=TABLE_FQN, snapshot_count=100)
    result = mo.run_safe_vacuum(TABLE_FQN, HK_CONFIG, health, "standard", "zamboni-standard", dry_run=False)

    assert result.aborted is True
    assert "PREFLIGHT_SANITY_FAILED" in result.aborted_reason
    assert vacuum_calls == []


def test_preflight_snapshot_query_incomplete_result_aborts(monkeypatch):
    """An empty/null result (not an exception) must be treated the same
    as a real failure -- a falsely-reassuring 0.0 default used to let this
    slip through the abort-above-threshold check."""
    monkeypatch.setattr(mo, "run_query", lambda sql, **k: None)
    monkeypatch.setattr(mo, "ZAMBONI_LOCAL_MODE", False)
    _patch_matching_readback(monkeypatch, 72 * 3600, 30)
    monkeypatch.setattr(mo, "read_sql", lambda sql, **k: pd.DataFrame())

    vacuum_calls = []
    monkeypatch.setattr(mo.vacuum, "run_expire_snapshots", lambda **k: vacuum_calls.append(k) or {})

    health = HealthResult(table_fqn=TABLE_FQN, snapshot_count=100)
    result = mo.run_safe_vacuum(TABLE_FQN, HK_CONFIG, health, "standard", "zamboni-standard", dry_run=False)

    assert result.aborted is True
    assert "PREFLIGHT_SANITY_FAILED" in result.aborted_reason
    assert vacuum_calls == []


def test_preflight_files_query_failure_aborts_before_vacuum(monkeypatch):
    monkeypatch.setattr(mo, "run_query", lambda sql, **k: None)
    monkeypatch.setattr(mo, "ZAMBONI_LOCAL_MODE", False)
    _patch_matching_readback(monkeypatch, 72 * 3600, 30)

    def fake_read_sql(sql, **k):
        if "$snapshots" in sql:
            return pd.DataFrame([{"total_snapshots": 100, "would_expire": 5}])
        raise RuntimeError("files query failed")

    monkeypatch.setattr(mo, "read_sql", fake_read_sql)

    vacuum_calls = []
    monkeypatch.setattr(mo.vacuum, "run_expire_snapshots", lambda **k: vacuum_calls.append(k) or {})

    health = HealthResult(table_fqn=TABLE_FQN, snapshot_count=100)
    result = mo.run_safe_vacuum(TABLE_FQN, HK_CONFIG, health, "standard", "zamboni-standard", dry_run=False)

    assert result.aborted is True
    assert "PREFLIGHT_SANITY_FAILED" in result.aborted_reason
    assert vacuum_calls == []


# ══════════════════════════════════════════════════════════════════════════════
#  Pre-flight sanity + abort (contracts.md §5-A step b) — threshold path
# ══════════════════════════════════════════════════════════════════════════════

def test_orphan_sanity_abort_above_threshold_deletes_nothing(monkeypatch):
    monkeypatch.setattr(mo, "run_query", lambda sql, **k: None)
    monkeypatch.setattr(mo, "ZAMBONI_LOCAL_MODE", False)
    _patch_matching_readback(monkeypatch, 72 * 3600, 30)

    def fake_read_sql(sql, **k):
        if "$snapshots" in sql:
            return pd.DataFrame([{"total_snapshots": 100, "would_expire": 25}])
        return _files_df()

    monkeypatch.setattr(mo, "read_sql", fake_read_sql)

    vacuum_calls = []
    monkeypatch.setattr(mo.vacuum, "run_expire_snapshots", lambda **k: vacuum_calls.append(k) or {})

    health = HealthResult(table_fqn=TABLE_FQN, snapshot_count=100)
    result = mo.run_safe_vacuum(TABLE_FQN, HK_CONFIG, health, "standard", "zamboni-standard", dry_run=False)

    assert result.aborted is True
    assert result.aborted_reason == "ORPHAN_SANITY_ABORT"
    assert result.sanity_pct == 25.0
    assert vacuum_calls == []  # nothing deleted


def test_orphan_sanity_proceeds_under_threshold_with_clamped_floor(monkeypatch):
    from config.settings import ORPHAN_MIN_AGE_HOURS_FLOOR

    monkeypatch.setattr(mo, "run_query", lambda sql, **k: None)
    monkeypatch.setattr(mo, "ZAMBONI_LOCAL_MODE", False)
    _patch_matching_readback(monkeypatch, 72 * 3600, 30)

    def fake_read_sql(sql, **k):
        if "$snapshots" in sql:
            return pd.DataFrame([{"total_snapshots": 100, "would_expire": 5}])
        return _files_df()

    monkeypatch.setattr(mo, "read_sql", fake_read_sql)

    vacuum_calls = []
    monkeypatch.setattr(
        mo.vacuum, "run_expire_snapshots",
        lambda **k: vacuum_calls.append(k) or {"athena_query_id": "q1"},
    )

    health = HealthResult(table_fqn=TABLE_FQN, snapshot_count=100)
    # HK_CONFIG's policy (24h) is below the 72h floor -- must still clamp up.
    result = mo.run_safe_vacuum(TABLE_FQN, HK_CONFIG, health, "standard", "zamboni-standard", dry_run=False)

    assert result.aborted is False
    assert result.older_than_hours_used >= ORPHAN_MIN_AGE_HOURS_FLOOR
    assert len(vacuum_calls) == 1
    assert result.vacuum_result.get("athena_query_id") == "q1"


def test_dry_run_still_runs_sanity_but_flags_vacuum_call_as_dry_run(monkeypatch):
    monkeypatch.setattr(mo, "run_query", lambda sql, dry_run=False, **k: None)
    monkeypatch.setattr(mo, "ZAMBONI_LOCAL_MODE", False)
    # dry_run=True already skips readback verification -- no get_table() mock needed.

    def fake_read_sql(sql, **k):
        if "$snapshots" in sql:
            return pd.DataFrame([{"total_snapshots": 100, "would_expire": 5}])
        return _files_df()

    monkeypatch.setattr(mo, "read_sql", fake_read_sql)

    captured_dry_run = []
    monkeypatch.setattr(
        mo.vacuum, "run_expire_snapshots",
        lambda **k: captured_dry_run.append(k.get("dry_run")) or {},
    )

    health = HealthResult(table_fqn=TABLE_FQN, snapshot_count=100)
    result = mo.run_safe_vacuum(TABLE_FQN, HK_CONFIG, health, "standard", "zamboni-standard", dry_run=True)

    assert result.aborted is False
    assert captured_dry_run == [True]
    # post-audit is skipped for dry runs -- nothing to diff against
    assert result.files_deleted is None
    assert result.bytes_reclaimed is None


def test_local_mode_still_runs_end_to_end_without_real_glue_calls(monkeypatch):
    """The full documented local-mode approximation path -- no Glue
    readback, would_expire_pct=0.0 -- must still let a real (non-dry-run)
    VACUUM proceed in local/demo mode."""
    monkeypatch.setattr(mo, "run_query", lambda sql, **k: None)
    monkeypatch.setattr(mo, "ZAMBONI_LOCAL_MODE", True)
    monkeypatch.setattr(mo, "read_sql", lambda sql, **k: pd.DataFrame())

    vacuum_calls = []
    monkeypatch.setattr(
        mo.vacuum, "run_expire_snapshots",
        lambda **k: vacuum_calls.append(k) or {"athena_query_id": "local-1"},
    )

    health = HealthResult(table_fqn=TABLE_FQN, snapshot_count=100)
    result = mo.run_safe_vacuum(TABLE_FQN, HK_CONFIG, health, "standard", "zamboni-standard", dry_run=False)

    assert result.aborted is False
    assert result.sanity_pct == 0.0
    assert len(vacuum_calls) == 1


# ══════════════════════════════════════════════════════════════════════════════
#  cancel_check / lease-lost (2026-07-11 audit fix)
# ══════════════════════════════════════════════════════════════════════════════

def test_run_safe_vacuum_lease_lost_aborts_before_vacuum_call(monkeypatch):
    """A lease lost after pre-flight sanity passes but before the actual
    VACUUM call must still abort -- no subsequent destructive operation
    starts once ownership may have moved to another process."""
    monkeypatch.setattr(mo, "run_query", lambda sql, **k: None)
    monkeypatch.setattr(mo, "ZAMBONI_LOCAL_MODE", False)
    _patch_matching_readback(monkeypatch, 72 * 3600, 30)

    def fake_read_sql(sql, **k):
        if "$snapshots" in sql:
            return pd.DataFrame([{"total_snapshots": 100, "would_expire": 5}])
        return _files_df()

    monkeypatch.setattr(mo, "read_sql", fake_read_sql)

    vacuum_calls = []
    monkeypatch.setattr(mo.vacuum, "run_expire_snapshots", lambda **k: vacuum_calls.append(k) or {})

    health = HealthResult(table_fqn=TABLE_FQN, snapshot_count=100)
    result = mo.run_safe_vacuum(
        TABLE_FQN, HK_CONFIG, health, "standard", "zamboni-standard", dry_run=False,
        cancel_check=lambda: True,
    )

    assert result.aborted is True
    assert result.aborted_reason == "LEASE_LOST"
    assert vacuum_calls == []


def test_run_safe_vacuum_cancel_check_threaded_into_vacuum_call(monkeypatch):
    """cancel_check must reach engine.operations.vacuum's iteration loop
    too, not just the top-level check in run_safe_vacuum() -- verifies
    the plumbing, not just the shortcut path."""
    monkeypatch.setattr(mo, "run_query", lambda sql, **k: None)
    monkeypatch.setattr(mo, "ZAMBONI_LOCAL_MODE", False)
    _patch_matching_readback(monkeypatch, 72 * 3600, 30)

    def fake_read_sql(sql, **k):
        if "$snapshots" in sql:
            return pd.DataFrame([{"total_snapshots": 100, "would_expire": 5}])
        return _files_df()

    monkeypatch.setattr(mo, "read_sql", fake_read_sql)

    received_cancel_check = []
    monkeypatch.setattr(
        mo.vacuum, "run_expire_snapshots",
        lambda **k: received_cancel_check.append(k.get("cancel_check")) or {},
    )

    cancel_fn = lambda: False  # noqa: E731
    health = HealthResult(table_fqn=TABLE_FQN, snapshot_count=100)
    mo.run_safe_vacuum(
        TABLE_FQN, HK_CONFIG, health, "standard", "zamboni-standard", dry_run=False,
        cancel_check=cancel_fn,
    )

    assert received_cancel_check == [cancel_fn]


# ══════════════════════════════════════════════════════════════════════════════
#  write_vacuum_audit
# ══════════════════════════════════════════════════════════════════════════════

def test_write_vacuum_audit_builds_insert_with_expected_columns(monkeypatch):
    captured = []
    monkeypatch.setattr(mo, "run_query", lambda sql, **k: captured.append((sql, k)))

    mo.write_vacuum_audit(
        run_id="run-1", table_fqn=TABLE_FQN, operation="vacuum", lock_id="lock-1",
        dry_run=False, snapshots_before=100, snapshots_after=40,
        files_estimated=500, files_deleted=10, bytes_reclaimed=2048,
        older_than_hours_used=96, sanity_pct=5.0, aborted=False,
    )

    assert len(captured) == 1
    sql, kwargs = captured[0]
    assert f"INSERT INTO {mo.VACUUM_AUDIT_TABLE}" in sql
    assert "'run-1'" in sql
    assert kwargs.get("dry_run") is False

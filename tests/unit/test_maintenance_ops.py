"""
Unit tests for engine/core/maintenance_ops.py (Phase 1b, contracts.md §5-A
SAFE-VACUUM: property clamp -> pre-flight sanity -> bare VACUUM -> post-audit).
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


# ══════════════════════════════════════════════════════════════════════════════
#  Property clamp
# ══════════════════════════════════════════════════════════════════════════════

def test_clamp_enforces_floor_regardless_of_tiny_policy(monkeypatch):
    from config.settings import ORPHAN_MIN_AGE_HOURS_FLOOR, SNAPSHOT_MIN_AGE_HOURS

    captured = []
    monkeypatch.setattr(mo, "run_query", lambda sql, **k: captured.append(sql))

    clamp = mo._clamp_vacuum_properties(TABLE_FQN, HK_CONFIG, "zamboni-standard", dry_run=False)

    assert clamp["floor_hours"] >= ORPHAN_MIN_AGE_HOURS_FLOOR
    assert clamp["floor_hours"] >= SNAPSHOT_MIN_AGE_HOURS
    assert len(captured) == 1
    assert "SET TBLPROPERTIES" in captured[0]


def test_clamp_respects_policy_when_above_floor(monkeypatch):
    monkeypatch.setattr(mo, "run_query", lambda sql, **k: None)
    hk_config = {"snapshot_retention_days": 30, "snapshot_min_to_keep": 5}  # 720h -- above floor

    clamp = mo._clamp_vacuum_properties(TABLE_FQN, hk_config, "zamboni-standard", dry_run=False)

    assert clamp["floor_hours"] == 30 * 24
    assert clamp["min_keep"] == 5


# ══════════════════════════════════════════════════════════════════════════════
#  Pre-flight sanity + abort (contracts.md §5-A step b)
# ══════════════════════════════════════════════════════════════════════════════

def test_orphan_sanity_abort_above_threshold_deletes_nothing(monkeypatch):
    monkeypatch.setattr(mo, "run_query", lambda sql, **k: None)

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

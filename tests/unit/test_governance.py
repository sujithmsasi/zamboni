"""
Unit tests for engine/core/governance.py (Workstream A / Phase 1c).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd

import engine.core.governance as gov
from engine.core.governance import dual_optimizer_report, fleet_conflict_summary

# ══════════════════════════════════════════════════════════════════════════════
#  dual_optimizer_report
# ══════════════════════════════════════════════════════════════════════════════

def test_dual_optimizer_report_filters_and_counts(monkeypatch):
    calls = []

    def fake_read_sql(sql, **kw):
        calls.append(sql)
        if "COUNT(*)" in sql:
            return pd.DataFrame([{"cnt": 2}])
        return pd.DataFrame([
            {"table_fqn": "glue_catalog.finance_master_db.fin_payment_master",
             "domain": "finance", "layer": "master", "tier": "standard",
             "aws_opt_compaction": True, "aws_opt_retention": False, "aws_opt_orphan": False,
             "aws_opt_checked_at": "2026-07-05 00:00:00",
             "gate0_override_until": None, "gate0_override_reason": None, "gate0_override_by": None},
        ])

    monkeypatch.setattr(gov, "read_sql", fake_read_sql)

    report = dual_optimizer_report(page=1, size=50, domain="finance")

    assert report["total"] == 2
    assert len(report["data"]) == 1
    assert report["data"][0]["table_fqn"] == "glue_catalog.finance_master_db.fin_payment_master"
    assert any("r.domain = 'finance'" in c for c in calls)


def test_dual_optimizer_report_export_all_skips_paging(monkeypatch):
    captured = {}

    def fake_read_sql(sql, **kw):
        if "COUNT(*)" in sql:
            return pd.DataFrame([{"cnt": 0}])
        captured["sql"] = sql
        return pd.DataFrame()

    monkeypatch.setattr(gov, "read_sql", fake_read_sql)
    dual_optimizer_report(export_all=True)
    assert "LIMIT" not in captured["sql"]


def test_dual_optimizer_report_column_missing_returns_empty(monkeypatch):
    def _raise(sql, **kw):
        raise RuntimeError('no such column: "aws_opt_compaction"')
    monkeypatch.setattr(gov, "read_sql", _raise)

    report = dual_optimizer_report()
    assert report == {"data": [], "total": 0, "page": 1, "size": 50}


# ══════════════════════════════════════════════════════════════════════════════
#  fleet_conflict_summary
# ══════════════════════════════════════════════════════════════════════════════

def test_fleet_conflict_summary_counts(monkeypatch):
    now = datetime.now(UTC)
    stale_ts   = (now - timedelta(hours=100)).strftime("%Y-%m-%d %H:%M:%S")
    fresh_ts   = (now - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    future_ts  = (now + timedelta(hours=5)).strftime("%Y-%m-%d %H:%M:%S")

    df = pd.DataFrame([
        # conflicted, scanned, stale
        {"aws_opt_compaction": True, "aws_opt_retention": False, "aws_opt_orphan": False,
         "aws_opt_checked_at": stale_ts, "gate0_override_until": None},
        # conflicted, scanned, fresh, overridden
        {"aws_opt_compaction": False, "aws_opt_retention": True, "aws_opt_orphan": False,
         "aws_opt_checked_at": fresh_ts, "gate0_override_until": future_ts},
        # not conflicted, never scanned
        {"aws_opt_compaction": False, "aws_opt_retention": False, "aws_opt_orphan": False,
         "aws_opt_checked_at": None, "gate0_override_until": None},
    ])
    monkeypatch.setattr(gov, "read_sql", lambda sql, **kw: df)

    summary = fleet_conflict_summary()

    assert summary["total"] == 3
    assert summary["scanned"] == 2
    assert summary["conflicted"] == 2
    assert summary["stale_cache"] == 1
    assert summary["overridden"] == 1


def test_fleet_conflict_summary_empty_df(monkeypatch):
    monkeypatch.setattr(gov, "read_sql", lambda sql, **kw: pd.DataFrame())
    assert fleet_conflict_summary() == {"total": 0, "scanned": 0, "conflicted": 0, "stale_cache": 0, "overridden": 0}


def test_fleet_conflict_summary_column_missing_fails_safe(monkeypatch):
    def _raise(sql, **kw):
        raise RuntimeError('no such column: "gate0_override_until"')
    monkeypatch.setattr(gov, "read_sql", _raise)

    summary = fleet_conflict_summary()
    assert summary == {"total": 0, "scanned": 0, "conflicted": 0, "stale_cache": 0, "overridden": 0}


def test_truthy_handles_nan_without_raising():
    import numpy as np
    assert gov._truthy(float("nan")) is False
    assert gov._truthy(np.nan) is False
    assert gov._truthy(None) is False
    assert gov._truthy(True) is True
    assert gov._truthy(1) is True

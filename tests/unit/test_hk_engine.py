from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

from engine.core import registry
from engine.engines.hk_engine import HKEngine


def test_registry_includes_dry_run_until_tables_by_default():
    captured = {}

    def fake_read_sql(sql, workgroup):
        captured["sql"] = sql
        return pd.DataFrame()

    with patch("engine.core.registry.read_sql", side_effect=fake_read_sql):
        registry.get_enabled_tables(environment="prod")

    assert "dry_run_until IS NULL" not in captured["sql"]


def test_registry_can_exclude_dry_run_until_tables():
    captured = {}

    def fake_read_sql(sql, workgroup):
        captured["sql"] = sql
        return pd.DataFrame()

    with patch("engine.core.registry.read_sql", side_effect=fake_read_sql):
        registry.get_enabled_tables(environment="prod", include_dry_run=False)

    assert "dry_run_until IS NULL" in captured["sql"]


def test_daily_frequency_skips_success_from_today():
    engine = HKEngine(dry_run=False)
    last_run = {
        "status": "SUCCESS",
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }

    with patch("engine.engines.hk_engine.execution_log.get_last_run", return_value=last_run):
        reason = engine._frequency_skip_reason(
            "glue_catalog.db.table",
            {"run_frequency": "daily"},
        )

    assert reason == "SKIP_NOT_DUE"


def test_weekly_frequency_runs_after_seven_days():
    engine = HKEngine(dry_run=False)
    last_run = {
        "status": "SUCCESS",
        "completed_at": (datetime.now(timezone.utc) - timedelta(days=8)).isoformat(),
    }

    with patch("engine.engines.hk_engine.execution_log.get_last_run", return_value=last_run):
        reason = engine._frequency_skip_reason(
            "glue_catalog.db.table",
            {"run_frequency": "weekly"},
        )

    assert reason is None


def test_dry_run_until_makes_table_effectively_dry_run():
    engine = HKEngine(dry_run=False)
    future = (datetime.now(timezone.utc) + timedelta(days=1)).date().isoformat()
    assert engine._effective_dry_run({"dry_run_until": future}) is True
    assert engine._effective_dry_run({"dry_run_until": "2000-01-01"}) is False


def test_process_table_logs_operation_and_summary_for_ramp_up_dry_run():
    engine = HKEngine(dry_run=False)
    table_row = {
        "table_fqn": "glue_catalog.db.table",
        "domain": "finance",
        "layer": "staging",
        "tier": "standard",
        "environment": "prod",
        "dry_run_until": (datetime.now(timezone.utc) + timedelta(days=1)).date().isoformat(),
    }
    health = SimpleNamespace(
        check_success=True,
        check_error=None,
        needs_compaction=True,
        needs_vacuum=False,
        needs_orphan_cleanup=False,
        snapshot_count=42,
    )
    write_calls = []

    with patch("engine.engines.hk_engine.get_hk_config", return_value={
        "window_config": "",
        "run_frequency": "every_trigger",
        "compaction_strategy": "binpack",
    }), patch("engine.engines.hk_engine.circuit_breaker.check", return_value="CLOSED"), \
        patch("engine.engines.hk_engine.health_checker.check", return_value=health), \
        patch("engine.engines.hk_engine.is_healthy", return_value=False), \
        patch("engine.engines.hk_engine.compaction.run_compaction", return_value={
            "athena_query_id": None,
            "files_compacted": 10,
        }) as compact, \
        patch.object(engine, "_write_log", side_effect=lambda **kwargs: write_calls.append(kwargs)):

        outcome = engine._process_table(table_row)

    assert outcome == "succeeded"
    assert compact.call_args.kwargs["dry_run"] is True
    assert [call["operation"] for call in write_calls] == ["compaction", "hk_run"]
    assert [call["status"] for call in write_calls] == ["DRY_RUN", "DRY_RUN"]

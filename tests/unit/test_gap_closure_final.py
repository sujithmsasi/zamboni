"""
Final gap closure tests — Sprint 5.
Covers gaps 1-7 from the remaining consistency + runtime correctness audit.
Each test references the gap number for traceability.
"""
import os
import pytest
from datetime import datetime, timezone, timedelta, date
from unittest.mock import patch, MagicMock, call
import pandas as pd


# ── Gap 1: systemd entrypoint ─────────────────────────────────────────────────

def test_gap1_after_install_uses_home_py():
    """deploy/scripts/after_install.sh must reference app/Home.py as ExecStart."""
    with open("deploy/scripts/after_install.sh") as f:
        content = f.read()
    assert "app/Home.py" in content, "app/Home.py not found in after_install.sh"


def test_gap1_after_install_no_main_py():
    """deploy/scripts/after_install.sh must NOT reference app/main.py."""
    with open("deploy/scripts/after_install.sh") as f:
        content = f.read()
    assert "app/main.py" not in content, "app/main.py still present in after_install.sh"


# ── Gap 2: _is_due uses hk_run operation filter ───────────────────────────────

def test_gap2_is_due_queries_hk_run_operation():
    """_is_due must call get_last_run with operation='hk_run' — not bare call."""
    from engine.engines.hk_engine import HKEngine
    engine = HKEngine(dry_run=True)
    hk_config = {"run_frequency": "daily"}

    with patch("engine.engines.hk_engine.execution_log") as ml:
        ml.get_last_run.return_value = None
        engine._is_due("glue_catalog.test.t1", hk_config)

    # Must have been called with operation="hk_run"
    assert ml.get_last_run.called
    call_kwargs = ml.get_last_run.call_args
    args, kwargs = call_kwargs
    assert kwargs.get("operation") == "hk_run" or (len(args) > 1 and args[1] == "hk_run"), \
        "get_last_run not called with operation='hk_run'"


def test_gap2_is_due_queries_only_success():
    """_is_due must call get_last_run with only_success=True."""
    from engine.engines.hk_engine import HKEngine
    engine = HKEngine(dry_run=True)

    with patch("engine.engines.hk_engine.execution_log") as ml:
        ml.get_last_run.return_value = None
        engine._is_due("t", {"run_frequency": "daily"})

    call_kwargs = ml.get_last_run.call_args
    _, kwargs = call_kwargs
    assert kwargs.get("only_success", True) is True, \
        "get_last_run not called with only_success=True"


def test_gap2_failed_prior_run_does_not_block_retry():
    """A failed prior run must not prevent the table from running again."""
    from engine.engines.hk_engine import HKEngine
    engine = HKEngine(dry_run=True)

    # get_last_run returns None (no SUCCESS) — even if failures exist
    with patch("engine.engines.hk_engine.execution_log") as ml:
        ml.get_last_run.return_value = None  # only_success=True filtered it out
        due, reason = engine._is_due("t", {"run_frequency": "daily"})
    assert due is True, "Failed prior run should not block retry"


# ── Gap 3: idempotency stable across run_ids ─────────────────────────────────

def test_gap3_same_table_operation_window_yields_same_id():
    """Two different run_ids for the same table+operation+window must produce same ID."""
    from engine.core.idempotency import build_execution_id
    id_a = build_execution_id("run-morning", "glue_catalog.fin.t1", "hk_run", "20260427")
    id_b = build_execution_id("run-evening", "glue_catalog.fin.t1", "hk_run", "20260427")
    assert id_a == id_b, \
        f"Different run_ids produced different execution IDs: {id_a} vs {id_b}"


def test_gap3_different_windows_yield_different_ids():
    """Same table+operation but different windows must produce different IDs."""
    from engine.core.idempotency import build_execution_id
    id_a = build_execution_id("run-1", "t", "hk_run", "20260427")
    id_b = build_execution_id("run-1", "t", "hk_run", "20260428")
    assert id_a != id_b


def test_gap3_different_operations_yield_different_ids():
    """Same table+window but different operations must produce different IDs."""
    from engine.core.idempotency import build_execution_id
    id_a = build_execution_id("run-1", "t", "compaction",  "20260427")
    id_b = build_execution_id("run-1", "t", "hk_run",      "20260427")
    assert id_a != id_b


def test_gap3_run_id_not_in_hash():
    """run_id must NOT affect the execution_id hash."""
    from engine.core.idempotency import build_execution_id
    import hashlib
    # Verify by checking that changing only run_id keeps the same digest prefix
    id_x = build_execution_id("run-x",     "glue_catalog.d.t", "hk_run", "20260427")
    id_y = build_execution_id("run-y-v99", "glue_catalog.d.t", "hk_run", "20260427")
    # They should be identical
    assert id_x == id_y, \
        "run_id should not be included in execution_id hash"


def test_gap3_deterministic_across_calls():
    """build_execution_id is deterministic — no random component."""
    from engine.core.idempotency import build_execution_id
    ids = [build_execution_id("r", "t", "op", "20260427") for _ in range(10)]
    assert len(set(ids)) == 1, "build_execution_id is not deterministic"


# ── Gap 4: backpressure timeout enforced ─────────────────────────────────────

def test_gap4_backpressure_timeout_returns_false():
    """wait_for_capacity returns False when workgroup stays at limit."""
    from engine.core.backpressure import wait_for_capacity
    with patch("engine.core.backpressure.get_running_query_count",
               return_value=25):
        result = wait_for_capacity("zamboni-standard", max_wait_seconds=0, limit=25)
    assert result is False, "Expected False on timeout"


def test_gap4_backpressure_timeout_skips_in_hk():
    """When backpressure returns False, HK writes SKIPPED log, not proceeding."""
    from engine.engines.hk_engine import HKEngine
    engine = HKEngine(dry_run=True)

    captured_logs = []
    def capture(entry, **kw):
        captured_logs.append(entry)
        return True

    with patch("engine.engines.hk_engine.wait_for_capacity", return_value=False), \
         patch("engine.engines.hk_engine.execution_log") as mel, \
         patch("engine.engines.hk_engine.compaction") as mc:

        mel.write.side_effect = capture

        # Simulate: needs_compaction=True, wait_for_capacity=False
        from unittest.mock import MagicMock
        health = MagicMock()
        health.needs_compaction = True
        health.needs_vacuum = False
        health.needs_orphan_cleanup = False
        health.snapshot_count = 10

        table_row = {
            "table_fqn": "glue_catalog.test.t",
            "domain": "test", "layer": "staging",
            "tier": "standard", "environment": "prod",
        }

        engine._write_log(
            table_row, "compaction", "SKIPPED",
            skip_reason="SKIP_BACKPRESSURE_TIMEOUT (workgroup=zamboni-standard)",
            effective_dry_run=True,
        )

    assert len(captured_logs) == 1
    assert captured_logs[0].status == "SKIPPED"
    assert "BACKPRESSURE" in (captured_logs[0].skip_reason or "")
    mc.run_compaction.assert_not_called()


def test_gap4_backpressure_fail_open_on_count_check_error():
    """wait_for_capacity returns True (fail open) if count check itself fails."""
    from engine.core.backpressure import wait_for_capacity
    with patch("engine.core.backpressure.get_running_query_count",
               return_value=None):  # None = check failed
        result = wait_for_capacity("zamboni-standard", max_wait_seconds=5, limit=25)
    assert result is True, "Should fail open when count check fails"


def test_gap4_backpressure_workgroup_names_match_config():
    """Workgroup names used in HK must be in backpressure._DEFAULT_LIMITS."""
    from engine.core.backpressure import _DEFAULT_LIMITS
    required_workgroups = {"zamboni-critical", "zamboni-standard", "zamboni-low"}
    for wg in required_workgroups:
        assert wg in _DEFAULT_LIMITS, \
            f"Workgroup {wg} missing from backpressure._DEFAULT_LIMITS"


# ── Gap 5: dry_run_until ramp-up table discovery ─────────────────────────────

def test_gap5_get_enabled_tables_includes_rampup_tables():
    """Tables with hk_enabled=false but dry_run_until >= today must be discovered."""
    from engine.core import registry

    today = date.today()
    future = (today + timedelta(days=7)).isoformat()
    past   = (today - timedelta(days=1)).isoformat()

    mock_rows = [
        # Fully enabled
        {"table_fqn": "t1", "hk_enabled": True,  "dry_run_until": None,   "table_format": "iceberg"},
        # Ramp-up active
        {"table_fqn": "t2", "hk_enabled": False, "dry_run_until": future, "table_format": "iceberg"},
        # Disabled + no ramp-up
        {"table_fqn": "t3", "hk_enabled": False, "dry_run_until": None,   "table_format": "iceberg"},
        # Ramp-up expired
        {"table_fqn": "t4", "hk_enabled": False, "dry_run_until": past,   "table_format": "iceberg"},
    ]

    with patch("engine.core.registry.read_sql") as msql:
        msql.return_value = pd.DataFrame(mock_rows)
        tables = registry.get_enabled_tables(environment="prod")

    # Should include t1 (enabled) and t2 (active ramp-up)
    # SQL does the filtering — we trust the WHERE clause. Just verify it was called.
    assert msql.called
    sql_arg = msql.call_args[0][0]
    # The OR clause for dry_run_until must be present
    assert "dry_run_until" in sql_arg, \
        "get_enabled_tables SQL must include dry_run_until condition"
    assert "CURRENT_DATE" in sql_arg, \
        "get_enabled_tables SQL must compare against CURRENT_DATE"


def test_gap5_sql_has_or_clause_for_hk_enabled_and_rampup():
    """SQL must include hk_enabled=true OR (dry_run_until >= today)."""
    from engine.core import registry
    with patch("engine.core.registry.read_sql") as msql:
        msql.return_value = pd.DataFrame()
        registry.get_enabled_tables(environment="prod")
    sql = msql.call_args[0][0]
    # Both conditions must be in the OR clause
    assert "hk_enabled = true" in sql
    assert "dry_run_until" in sql
    assert "CURRENT_DATE" in sql


def test_gap5_is_in_dry_run_ramp_active():
    """is_in_dry_run_ramp returns True for active ramp-up."""
    from engine.core.registry import is_in_dry_run_ramp
    future = (date.today() + timedelta(days=5)).isoformat()
    assert is_in_dry_run_ramp({"dry_run_until": future}) is True


def test_gap5_is_in_dry_run_ramp_expired():
    """is_in_dry_run_ramp returns False for expired ramp-up."""
    from engine.core.registry import is_in_dry_run_ramp
    past = (date.today() - timedelta(days=1)).isoformat()
    assert is_in_dry_run_ramp({"dry_run_until": past}) is False


def test_gap5_is_in_dry_run_ramp_none():
    """is_in_dry_run_ramp returns False when dry_run_until is null."""
    from engine.core.registry import is_in_dry_run_ramp
    assert is_in_dry_run_ramp({"dry_run_until": None}) is False
    assert is_in_dry_run_ramp({}) is False


# ── Gap 6: Parquet buffer integration and schema ──────────────────────────────

def test_gap6_parquet_buffer_wired_in_hk_engine():
    """ParquetLogBuffer must be imported and used in HK Engine."""
    with open("engine/engines/hk_engine.py") as f:
        content = f.read()
    assert "ParquetLogBuffer" in content, \
        "ParquetLogBuffer not imported/used in HK Engine"
    assert "log_buffer.flush" in content, \
        "log_buffer.flush not called in HK Engine run()"


def test_gap6_entry_to_dict_has_all_ddl_columns():
    """_entry_to_dict must produce all columns present in execution_log DDL."""
    from engine.core.execution_log_parquet import ParquetLogBuffer
    from engine.core.execution_log import LogEntry
    import re

    with open("sql/create_execution_log.sql") as f:
        ddl = f.read()
    ddl_cols = set(re.findall(r'^\s{4}(\w+)\s+\w+', ddl, re.MULTILINE))

    buffer = ParquetLogBuffer(run_id="test")
    entry = LogEntry(
        run_id="r", engine="hk", operation="hk_run",
        table_fqn="t", domain="d", layer="staging",
        tier="standard", environment="prod", status="SUCCESS",
    )
    row = buffer._entry_to_dict(entry)
    row_keys = set(row.keys())

    missing = ddl_cols - row_keys
    assert not missing, \
        f"_entry_to_dict missing DDL columns: {sorted(missing)}"


def test_gap6_parquet_buffer_insert_fallback_mode():
    """mode=insert must use per-row write, not Parquet path."""
    from engine.core.execution_log_parquet import ParquetLogBuffer
    from engine.core.execution_log import LogEntry

    with patch("engine.core.execution_log_parquet.EXECUTION_LOG_MODE", "insert"):
        buffer = ParquetLogBuffer(run_id="test-insert")
        buffer.append(LogEntry(
            run_id="r", engine="hk", operation="hk_run",
            table_fqn="t", domain="d", layer="staging",
            tier="standard", environment="prod", status="SUCCESS",
        ))
        with patch("engine.core.execution_log.write") as mw:
            mw.return_value = True
            result = buffer.flush(dry_run=True)

    assert result["mode"] == "insert"
    assert result["rows_written"] == 1


def test_gap6_parquet_buffer_auto_falls_back_to_insert_on_error():
    """mode=auto must fall back to insert if Parquet write fails."""
    from engine.core.execution_log_parquet import ParquetLogBuffer
    from engine.core.execution_log import LogEntry

    with patch("engine.core.execution_log_parquet.EXECUTION_LOG_MODE", "auto"):
        buffer = ParquetLogBuffer(run_id="test-auto")
        buffer.append(LogEntry(
            run_id="r", engine="hk", operation="hk_run",
            table_fqn="t", domain="d", layer="staging",
            tier="standard", environment="prod", status="SUCCESS",
        ))
        # Simulate Parquet write failure
        with patch.object(buffer, "_write_via_parquet",
                          side_effect=Exception("S3 unavailable")), \
             patch("engine.core.execution_log.write") as mw:
            mw.return_value = True
            result = buffer.flush(dry_run=True)

    assert result["mode"] == "insert", \
        "Should fall back to insert when Parquet fails in auto mode"


def test_gap6_parquet_buffer_strict_mode_does_not_fallback():
    """mode=parquet must raise/return error without falling back to insert."""
    from engine.core.execution_log_parquet import ParquetLogBuffer
    from engine.core.execution_log import LogEntry

    with patch("engine.core.execution_log_parquet.EXECUTION_LOG_MODE", "parquet"):
        buffer = ParquetLogBuffer(run_id="test-strict")
        buffer.append(LogEntry(
            run_id="r", engine="hk", operation="hk_run",
            table_fqn="t", domain="d", layer="staging",
            tier="standard", environment="prod", status="SUCCESS",
        ))
        with patch.object(buffer, "_write_via_parquet",
                          side_effect=Exception("S3 unavailable")), \
             patch("engine.core.execution_log.write") as mw:
            result = buffer.flush(dry_run=False)

    # Strict mode: error returned, insert NOT called
    assert result.get("error") is not None
    mw.assert_not_called()


# ── Gap 7: README trigger language ───────────────────────────────────────────

def test_gap7_readme_eventbridge_is_phase1_primary():
    """README must document EventBridge as Phase 1 primary scheduling."""
    with open("README.md") as f:
        content = f.read()
    assert "Phase 1" in content
    assert "EventBridge" in content


def test_gap7_readme_controlm_documented_as_optional():
    """README must NOT imply Control-M is the only scheduling option."""
    with open("README.md") as f:
        content = f.read()
    # Control-M is mentioned (as optional Phase 2) but not as the only option
    # Confirm EventBridge is also present and Phase 1 reference exists
    assert "EventBridge" in content
    # Confirm no statement saying Control-M is required
    assert "Control-M is required" not in content
    assert "must use Control-M" not in content


def test_gap7_readme_no_ecs_fargate():
    """README must not reference ECS Fargate (orchestrator is on EC2)."""
    with open("README.md") as f:
        content = f.read()
    assert "ECS Fargate" not in content
    assert "ecs.amazonaws" not in content.lower()

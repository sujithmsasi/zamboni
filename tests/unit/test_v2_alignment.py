"""
v2 design alignment tests — Sprint 4 gap closure.
Covers: A.2 effective dry_run, B.3 monthly, B.4 retry preserve,
B.5 idempotency, B.6 property sync, B.7 hot partition cadence,
C.9 backpressure, D.10-12 Parquet log writer, E.13 DDL fields.
"""
from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import patch

# ── A.2: effective dry_run propagation ───────────────────────────────────────

def test_write_log_accepts_effective_dry_run():
    """_write_log must accept effective_dry_run param."""
    import inspect

    from engine.engines.hk_engine import HKEngine
    sig = inspect.signature(HKEngine._write_log)
    assert "effective_dry_run" in sig.parameters


def test_write_log_uses_effective_dry_run_in_log_entry():
    """LogEntry.dry_run should reflect effective_dry_run, not engine dry_run."""
    from engine.engines.hk_engine import HKEngine
    engine = HKEngine(dry_run=False)  # engine NOT in dry_run

    captured = []
    def capture(entry, **kw):
        captured.append(entry)
        return True

    table_row = {
        "table_fqn": "glue_catalog.test.t1",
        "domain": "test", "layer": "staging",
        "tier": "standard", "environment": "prod",
    }

    with patch("engine.engines.hk_engine.execution_log") as ml:
        ml.write.side_effect = capture
        # Pass effective_dry_run=True (ramp-up)
        engine._write_log(table_row, "compaction", "DRY_RUN",
                          effective_dry_run=True)

    assert captured[0].dry_run is True  # not engine.dry_run=False


def test_write_log_falls_back_to_engine_dry_run_when_unset():
    """If effective_dry_run is None, use engine dry_run."""
    from engine.engines.hk_engine import HKEngine
    engine = HKEngine(dry_run=True)

    captured = []
    def capture(entry, **kw):
        captured.append(entry)
        return True

    with patch("engine.engines.hk_engine.execution_log") as ml:
        ml.write.side_effect = capture
        engine._write_log(
            {"table_fqn": "t", "domain": "d", "layer": "s",
             "tier": "standard", "environment": "prod"},
            "compaction", "DRY_RUN",
        )
    assert captured[0].dry_run is True


# ── B.3: monthly run_frequency ───────────────────────────────────────────────

def test_monthly_in_frequency_hours():
    from engine.engines.hk_engine import _FREQUENCY_HOURS
    assert "monthly" in _FREQUENCY_HOURS
    assert _FREQUENCY_HOURS["monthly"] > _FREQUENCY_HOURS["weekly"]


def test_monthly_due_when_run_30_days_ago():
    from engine.engines.hk_engine import HKEngine
    engine = HKEngine(dry_run=True)
    last_run_ts = (datetime.now(UTC) - timedelta(hours=720)).isoformat()
    with patch("engine.engines.hk_engine.execution_log") as ml:
        ml.get_last_run.return_value = {"completed_at": last_run_ts}
        due, reason = engine._is_due("t", {"run_frequency": "monthly"})
    assert due is True


def test_monthly_not_due_when_run_5_days_ago():
    from engine.engines.hk_engine import HKEngine
    engine = HKEngine(dry_run=True)
    last_run_ts = (datetime.now(UTC) - timedelta(hours=120)).isoformat()
    with patch("engine.engines.hk_engine.execution_log") as ml:
        ml.get_last_run.return_value = {"completed_at": last_run_ts}
        due, reason = engine._is_due("t", {"run_frequency": "monthly"})
    assert due is False
    assert "monthly" in reason
    assert "SKIP_NOT_DUE" in reason


# ── B.4: retry preserved on failed prior runs ────────────────────────────────

def test_get_last_run_default_filters_success_only():
    """get_last_run should default to only_success=True so failed runs allow retry."""
    import inspect

    from engine.core.execution_log import get_last_run
    sig = inspect.signature(get_last_run)
    assert "only_success" in sig.parameters
    assert sig.parameters["only_success"].default is True


# ── B.5: idempotency ─────────────────────────────────────────────────────────

def test_build_execution_id_deterministic():
    from engine.core.idempotency import build_execution_id
    a = build_execution_id("run-1", "glue_catalog.fin.t1", "compaction", "20260427")
    b = build_execution_id("run-1", "glue_catalog.fin.t1", "compaction", "20260427")
    assert a == b


def test_build_execution_id_differs_by_window():
    from engine.core.idempotency import build_execution_id
    a = build_execution_id("run-1", "t", "compaction", "20260427")
    b = build_execution_id("run-1", "t", "compaction", "20260428")
    assert a != b


def test_build_execution_id_differs_by_operation():
    from engine.core.idempotency import build_execution_id
    a = build_execution_id("run-1", "t", "compaction", "20260427")
    b = build_execution_id("run-1", "t", "vacuum", "20260427")
    assert a != b


def test_check_already_executed_returns_false_on_missing_column():
    """Backward compat — if last_execution_id doesn't exist, return False."""
    from engine.core.idempotency import check_already_executed
    with patch("engine.utils.athena_client.read_sql") as ms:
        ms.side_effect = Exception("Column 'last_execution_id' does not exist")
        result = check_already_executed("exec_1", "t")
    assert result is False


# ── B.6: property sync ───────────────────────────────────────────────────────

def test_needs_property_sync_true_when_null():
    from engine.core.property_sync import needs_property_sync
    assert needs_property_sync({"properties_synced": None}) is True
    assert needs_property_sync({}) is True


def test_needs_property_sync_false_when_true():
    from engine.core.property_sync import needs_property_sync
    assert needs_property_sync({"properties_synced": True}) is False


def test_apply_vacuum_properties_dry_run():
    from engine.core.property_sync import apply_vacuum_properties
    result = apply_vacuum_properties(
        "glue_catalog.test.t1",
        {"snapshot_retention_days": 7, "snapshot_min_to_keep": 30},
        workgroup="standard",
        dry_run=True,
    )
    assert result["status"] == "DRY_RUN"
    # vacuum_max_age depends on commit tier (HIGH=604800, MEDIUM=1209600, LOW=2592000)
    # snapshot_retention_days=7 → HIGH tier → 7 days = 604800 seconds
    assert result["vacuum_max_age"] == 7 * 86400  # HIGH tier retention
    assert result["vacuum_min_keep"] == 30         # Zamboni floor: max(tier_min=2, floor=30)


def test_mark_properties_synced_silent_on_missing_column():
    """Backward compat — returns False silently if column missing."""
    from engine.core.property_sync import mark_properties_synced
    with patch("engine.utils.athena_client.run_query") as ms:
        ms.side_effect = Exception("Column 'properties_synced' does not exist")
        result = mark_properties_synced("t", workgroup="app", dry_run=False)
    assert result is False


# ── B.7: hot partition filter from cadence ───────────────────────────────────

def test_build_hot_partition_filter_cadence_daily():
    from engine.utils.partition_utils import build_hot_partition_filter
    f = build_hot_partition_filter("partition_date", processing_cadence="daily")
    assert f is not None
    assert "partition_date >=" in f


def test_build_hot_partition_filter_cadence_monthly_no_filter():
    """monthly cadence → no filter (few partitions)."""
    from engine.utils.partition_utils import build_hot_partition_filter
    assert build_hot_partition_filter("p", processing_cadence="monthly") is None


def test_build_hot_partition_filter_cadence_overrides_days():
    """If both cadence and days set, cadence wins."""
    from datetime import date

    from engine.utils.partition_utils import build_hot_partition_filter
    f = build_hot_partition_filter(
        "partition_date",
        days=1,
        processing_cadence="daily",
        reference_date=date(2026, 4, 27),
    )
    # cadence=daily → 90 day lookback → 2026-01-27 (not days=1 → 2026-04-26)
    assert "2026-01-27" in f


def test_build_hot_partition_filter_legacy_days_still_works():
    from datetime import date

    from engine.utils.partition_utils import build_hot_partition_filter
    f = build_hot_partition_filter(
        "partition_date", days=7,
        reference_date=date(2026, 4, 27),
    )
    assert "2026-04-20" in f


def test_get_cadence_lookback_days():
    from engine.utils.partition_utils import get_cadence_lookback_days
    assert get_cadence_lookback_days("hourly") == 7
    assert get_cadence_lookback_days("daily")  == 90
    assert get_cadence_lookback_days("weekly") == 180
    assert get_cadence_lookback_days("monthly") is None
    assert get_cadence_lookback_days("unknown") is None


# ── C.9: backpressure ────────────────────────────────────────────────────────

def test_backpressure_falls_open_on_check_failure():
    """If get_running_query_count fails, allow dispatch (fail open)."""
    from engine.core.backpressure import wait_for_capacity
    with patch("engine.core.backpressure.get_running_query_count",
               return_value=None):
        result = wait_for_capacity("zamboni-standard", max_wait_seconds=2)
    assert result is True


def test_backpressure_returns_true_when_capacity_available():
    from engine.core.backpressure import wait_for_capacity
    with patch("engine.core.backpressure.get_running_query_count",
               return_value=5):
        result = wait_for_capacity("zamboni-standard", max_wait_seconds=2,
                                   limit=25)
    assert result is True


def test_can_dispatch_returns_false_at_limit():
    from engine.core.backpressure import can_dispatch
    with patch("engine.core.backpressure.get_running_query_count",
               return_value=25):
        assert can_dispatch("zamboni-standard", limit=25) is False


# ── D.10-12: Parquet execution log writer ───────────────────────────────────

def test_parquet_log_buffer_basic():
    from engine.core.execution_log_parquet import ParquetLogBuffer
    buffer = ParquetLogBuffer(run_id="test-run-1")
    assert buffer.entries == []
    assert buffer.flushed is False


def test_parquet_log_buffer_flush_empty():
    from engine.core.execution_log_parquet import ParquetLogBuffer
    buffer = ParquetLogBuffer(run_id="test-empty")
    result = buffer.flush()
    assert result["rows_written"] == 0


def test_parquet_log_buffer_dry_run():
    from engine.core.execution_log import LogEntry
    from engine.core.execution_log_parquet import ParquetLogBuffer
    buffer = ParquetLogBuffer(run_id="dry-run-test")
    buffer.append(LogEntry(
        run_id="r", engine="hk", operation="hk_run",
        table_fqn="t", domain="d", layer="staging",
        tier="standard", environment="prod", status="DRY_RUN",
    ))
    result = buffer.flush(dry_run=True)
    assert result["rows_written"] == 1
    assert "dry_run" in result.get("mode", "") or result["mode"] == "parquet_dry_run"


def test_execution_log_mode_setting_exists():
    from config import settings
    assert hasattr(settings, "EXECUTION_LOG_MODE")
    # default should be 'auto'
    assert settings.EXECUTION_LOG_MODE in ("auto", "parquet", "insert", "both")


def test_parquet_writer_falls_back_to_insert_mode():
    """If mode=insert, never tries Parquet."""
    from engine.core.execution_log import LogEntry
    from engine.core.execution_log_parquet import ParquetLogBuffer
    with patch("engine.core.execution_log_parquet.EXECUTION_LOG_MODE", "insert"):
        buffer = ParquetLogBuffer(run_id="insert-only")
        buffer.append(LogEntry(
            run_id="r", engine="hk", operation="hk_run",
            table_fqn="t", domain="d", layer="staging",
            tier="standard", environment="prod", status="SUCCESS",
        ))
        with patch("engine.core.execution_log.write") as mw:
            mw.return_value = True
            result = buffer.flush(dry_run=True)
        assert result["mode"] == "insert"


# ── E.13: DDL fields ─────────────────────────────────────────────────────────

def test_stream_registry_ddl_has_processing_cadence():
    with open("sql/create_stream_registry.sql", encoding='utf-8') as f:
        ddl = f.read()
    assert "processing_cadence" in ddl


def test_stream_registry_ddl_has_properties_synced():
    with open("sql/create_stream_registry.sql", encoding='utf-8') as f:
        ddl = f.read()
    assert "properties_synced" in ddl


def test_stream_registry_ddl_has_last_execution_id():
    with open("sql/create_stream_registry.sql", encoding='utf-8') as f:
        ddl = f.read()
    assert "last_execution_id" in ddl


def test_stream_registry_ddl_has_dry_run_until():
    with open("sql/create_stream_registry.sql", encoding='utf-8') as f:
        ddl = f.read()
    assert "dry_run_until" in ddl


# ── E.15: Docs alignment ─────────────────────────────────────────────────────

def test_readme_no_ecs_fargate_references():
    """README must not reference Fargate/ECS — orchestrator is on EC2."""
    with open("README.md", encoding='utf-8') as f:
        readme = f.read()
    assert "Fargate" not in readme
    # ECS in 'EC2' is fine; but standalone ECS reference would not be
    assert "ECS Fargate" not in readme
    assert "ecs.amazonaws" not in readme.lower()

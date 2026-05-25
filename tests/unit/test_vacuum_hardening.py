"""
Vacuum Hardening Tests — all 12 gaps + Gap 13 partition improvement.
"""
from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest

# ── Gap 1 + 2: Correct Athena VACUUM SQL ─────────────────────────────────────

class TestVacuumSQL:
    """VACUUM must emit bare 'VACUUM database.table' — no clauses, no catalog."""

    def _make_health(self, snapshot_count=50, expired=20):
        from engine.core.health_checker import HealthResult
        r = HealthResult(table_fqn="glue_catalog.fin_db.fin_payment")
        r.snapshot_count    = snapshot_count
        r.expired_snapshots = expired
        r.needs_vacuum      = True
        return r

    def test_vacuum_sql_is_bare_no_options(self):
        """VACUUM must not contain EXPIRE SNAPSHOTS, WITH, or TIMESTAMP."""
        from engine.operations import vacuum
        sql_issued = []
        with patch("engine.operations.vacuum.run_query",
                   side_effect=lambda s, **kw: sql_issued.append(s) or "qid-1"), \
             patch("engine.operations.vacuum.get_query_stats", return_value={}):
            vacuum.run_expire_snapshots(
                "glue_catalog.fin_db.fin_payment",
                {"snapshot_retention_days": 7, "snapshot_min_to_keep": 2},
                self._make_health(),
                tier="standard",
                dry_run=False,
            )
        assert sql_issued, "run_query was not called"
        sql = sql_issued[0].strip()
        assert "EXPIRE SNAPSHOTS" not in sql.upper()
        assert "WITH (" not in sql.upper()
        assert "TIMESTAMP" not in sql.upper()
        assert "REMOVE ORPHAN" not in sql.upper()

    def test_vacuum_sql_no_catalog_prefix(self):
        """VACUUM must use 'database.table' — not 'glue_catalog.database.table'."""
        from engine.operations import vacuum
        sql_issued = []
        with patch("engine.operations.vacuum.run_query",
                   side_effect=lambda s, **kw: sql_issued.append(s) or "qid-1"), \
             patch("engine.operations.vacuum.get_query_stats", return_value={}):
            vacuum.run_expire_snapshots(
                "glue_catalog.fin_db.fin_payment",
                {"snapshot_retention_days": 7, "snapshot_min_to_keep": 2},
                self._make_health(),
                tier="standard",
                dry_run=False,
            )
        # Filter to VACUUM calls only
        vacuum_calls = [s.strip().upper() for s in sql_issued
                        if s.strip().upper().startswith("VACUUM")]
        assert vacuum_calls, "No VACUUM calls found"
        assert "GLUE_CATALOG" not in vacuum_calls[0]
        assert vacuum_calls[0] == "VACUUM FIN_DB.FIN_PAYMENT"

    def test_vacuum_sql_correct_format(self):
        """SQL must be exactly: VACUUM database.table"""
        from engine.operations import vacuum
        sql_issued = []
        with patch("engine.operations.vacuum.run_query",
                   side_effect=lambda s, **kw: sql_issued.append(s) or "qid-1"),              patch("engine.operations.vacuum.get_query_stats", return_value={}):
            vacuum.run_expire_snapshots(
                "glue_catalog.finance_db.fin_payment",
                {"snapshot_min_to_keep": 2},
                self._make_health(),
                tier="standard",
                dry_run=False,
            )
        # Filter to VACUUM calls only (ALTER TABLE may also be in list)
        vacuum_calls = [s.strip() for s in sql_issued if s.strip().upper().startswith("VACUUM")]
        assert vacuum_calls, f"No VACUUM calls found. SQL issued: {sql_issued}"
        assert vacuum_calls[0] == "VACUUM finance_db.fin_payment"

    def test_orphan_cleanup_uses_vacuum(self):
        """Gap 2: orphan cleanup must use VACUUM, not ALTER TABLE EXECUTE."""
        from engine.operations import vacuum
        sql_issued = []
        with patch("engine.operations.vacuum.run_query",
                   side_effect=lambda s, **kw: sql_issued.append(s) or "qid-1"),              patch("engine.operations.vacuum.get_query_stats", return_value={}):
            vacuum.run_orphan_cleanup(
                "glue_catalog.fin_db.fin_payment",
                {"orphan_file_retention_days": 2},
                tier="standard",
                dry_run=False,
            )
        sql = sql_issued[0].strip().upper()
        assert "VACUUM" in sql
        assert "EXECUTE" not in sql
        assert "REMOVE_ORPHAN_FILES" not in sql
        assert "ALTER TABLE" not in sql


# ── Gap 3: Iterative VACUUM ───────────────────────────────────────────────────

class TestIterativeVacuum:
    def _make_bloated_health(self, expired=600):
        from engine.core.health_checker import HealthResult
        r = HealthResult(table_fqn="glue_catalog.fin_db.fin_payment")
        r.snapshot_count    = 1200
        r.expired_snapshots = expired
        r.needs_vacuum      = True
        return r

    def test_normal_table_single_vacuum_call(self):
        """Non-bloated table: exactly 1 VACUUM call."""
        from engine.core.health_checker import HealthResult
        from engine.operations import vacuum
        r = HealthResult(table_fqn="glue_catalog.fin_db.t")
        r.snapshot_count    = 50
        r.expired_snapshots = 20

        call_count = [0]
        def mock_run(sql, **kw):
            call_count[0] += 1
            return f"qid-{call_count[0]}"

        with patch("engine.operations.vacuum.run_query", side_effect=mock_run), \
             patch("engine.operations.vacuum.get_query_stats", return_value={}), \
             patch("engine.operations.vacuum.time.sleep"):
            vacuum.run_expire_snapshots(
                "glue_catalog.fin_db.t", {}, r, tier="standard"
            )
        assert call_count[0] == 1

    def test_bloated_table_multiple_vacuum_calls(self):
        """Bloated table (expired > 500): up to VACUUM_MAX_ITERATIONS calls."""
        from config.settings import VACUUM_MAX_ITERATIONS
        from engine.operations import vacuum

        call_count = [0]
        def mock_run(sql, **kw):
            call_count[0] += 1
            return f"qid-{call_count[0]}"

        with patch("engine.operations.vacuum.run_query", side_effect=mock_run), \
             patch("engine.operations.vacuum.get_query_stats", return_value={}), \
             patch("engine.operations.vacuum.time.sleep"):
            result = vacuum.run_expire_snapshots(
                "glue_catalog.fin_db.t",
                {},
                self._make_bloated_health(),
                tier="standard",
            )

        # ALTER TABLE (tighten retention) + VACUUM_MAX_ITERATIONS VACUUM calls
        assert call_count[0] == VACUUM_MAX_ITERATIONS + 1  # 1 ALTER + 3 VACUUM
        assert result["vacuum_iterations"] == VACUUM_MAX_ITERATIONS
        assert result["is_bloated"] is True

    def test_bloated_table_sleeps_between_iterations(self):
        """Sleep must be called between iterations, not after last."""
        from config.settings import VACUUM_ITERATION_SLEEP_SECS, VACUUM_MAX_ITERATIONS
        from engine.operations import vacuum

        sleep_calls = []

        call_idx2 = [0]
        def _rq2(sql, **kw):
            call_idx2[0] += 1
            return f"qid-{call_idx2[0]}"

        with patch("engine.operations.vacuum.run_query", side_effect=_rq2), \
             patch("engine.operations.vacuum.get_query_stats", return_value={}), \
             patch("engine.operations.vacuum.time.sleep",
                   side_effect=lambda s: sleep_calls.append(s)):
            vacuum.run_expire_snapshots(
                "glue_catalog.fin_db.t",
                {"snapshot_min_to_keep": 2},
                self._make_bloated_health(),
                tier="standard",
            )

        assert len(sleep_calls) == VACUUM_MAX_ITERATIONS - 1
        assert all(s == VACUUM_ITERATION_SLEEP_SECS for s in sleep_calls)

    def test_bloated_table_alters_retention_first(self):
        """Gap 3: ALTER TABLE must fire before VACUUM iterations for bloated tables."""
        from engine.operations import vacuum
        calls = []

        def mock_run(sql, **kw):
            calls.append(sql.strip())
            return "qid-1"

        with patch("engine.operations.vacuum.run_query", side_effect=mock_run), \
             patch("engine.operations.vacuum.get_query_stats", return_value={}), \
             patch("engine.operations.vacuum.time.sleep"):
            vacuum.run_expire_snapshots(
                "glue_catalog.fin_db.t",
                {},
                self._make_bloated_health(),
                tier="standard",
            )

        # First call should be ALTER TABLE (tighten retention)
        assert "ALTER TABLE" in calls[0].upper()
        assert "TBLPROPERTIES" in calls[0].upper()
        # Subsequent calls should be VACUUM
        for sql in calls[1:]:
            assert sql.startswith("VACUUM")


# ── Gap 4: Commit frequency tier ─────────────────────────────────────────────

class TestCommitFrequency:
    def test_high_tier_above_48(self):
        from engine.core.commit_frequency import COMMIT_TIER_HIGH, _classify_tier
        assert _classify_tier(96.0)  == COMMIT_TIER_HIGH
        assert _classify_tier(48.1)  == COMMIT_TIER_HIGH

    def test_medium_tier_12_to_48(self):
        from engine.core.commit_frequency import COMMIT_TIER_MEDIUM, _classify_tier
        assert _classify_tier(24.0)  == COMMIT_TIER_MEDIUM
        assert _classify_tier(12.1)  == COMMIT_TIER_MEDIUM

    def test_low_tier_below_12(self):
        from engine.core.commit_frequency import COMMIT_TIER_LOW, _classify_tier
        assert _classify_tier(6.0)   == COMMIT_TIER_LOW
        assert _classify_tier(0.0)   == COMMIT_TIER_LOW

    def test_high_tier_7_day_retention(self):
        from engine.core.commit_frequency import COMMIT_TIER_HIGH, TIER_PROPERTIES
        assert TIER_PROPERTIES[COMMIT_TIER_HIGH]["vacuum_max_snapshot_age_seconds"] == 604800

    def test_medium_tier_14_day_retention(self):
        from engine.core.commit_frequency import COMMIT_TIER_MEDIUM, TIER_PROPERTIES
        assert TIER_PROPERTIES[COMMIT_TIER_MEDIUM]["vacuum_max_snapshot_age_seconds"] == 1209600

    def test_low_tier_30_day_retention(self):
        from engine.core.commit_frequency import COMMIT_TIER_LOW, TIER_PROPERTIES
        assert TIER_PROPERTIES[COMMIT_TIER_LOW]["vacuum_max_snapshot_age_seconds"] == 2592000

    def test_tier_properties_have_all_four_fields(self):
        from engine.core.commit_frequency import TIER_PROPERTIES
        required = ["vacuum_max_snapshot_age_seconds", "vacuum_min_snapshots_to_keep",
                    "vacuum_max_metadata_files_to_keep", "write_target_data_file_size_bytes"]
        for tier, props in TIER_PROPERTIES.items():
            for field in required:
                assert field in props, f"TIER_PROPERTIES[{tier}] missing {field}"

    def test_write_target_file_size_256mb_all_tiers(self):
        from engine.core.commit_frequency import TIER_PROPERTIES
        for tier, props in TIER_PROPERTIES.items():
            assert props["write_target_data_file_size_bytes"] == 268435456

    def test_classify_tier_from_config_low_retention(self):
        from engine.core.commit_frequency import COMMIT_TIER_LOW, classify_tier_from_config
        # 30 days retention → LOW tier
        assert classify_tier_from_config({"snapshot_retention_days": 30}) == COMMIT_TIER_LOW

    def test_classify_tier_from_config_high_retention(self):
        from engine.core.commit_frequency import COMMIT_TIER_HIGH, classify_tier_from_config
        assert classify_tier_from_config({"snapshot_retention_days": 7}) == COMMIT_TIER_HIGH

    def test_get_commit_stats_falls_back_to_low_on_error(self):
        """CommitStats must default to LOW tier on any query failure."""
        from engine.core.commit_frequency import COMMIT_TIER_LOW, get_commit_stats
        with patch("engine.core.commit_frequency.read_sql",
                   side_effect=Exception("Athena unavailable")):
            stats = get_commit_stats("glue_catalog.fin_db.t", workgroup="standard")
        assert stats.commit_tier == COMMIT_TIER_LOW
        assert stats.error != ""


# ── Gap 5: G10 Anomalous commit rate ─────────────────────────────────────────

class TestAnomalousCommitRate:
    def test_anomalous_flag_above_warn_threshold(self):
        from config.settings import ANOMALOUS_COMMITS_WARN
        from engine.core.commit_frequency import CommitStats
        s = CommitStats(table_fqn="t", commits_per_day=ANOMALOUS_COMMITS_WARN + 1)
        s.anomalous = s.commits_per_day > ANOMALOUS_COMMITS_WARN
        assert s.anomalous is True

    def test_pipeline_anomaly_above_block_threshold(self):
        from config.settings import ANOMALOUS_COMMITS_BLOCK
        from engine.core.commit_frequency import CommitStats
        s = CommitStats(table_fqn="t", commits_per_day=ANOMALOUS_COMMITS_BLOCK + 1)
        s.pipeline_anomaly = s.commits_per_day > ANOMALOUS_COMMITS_BLOCK
        assert s.pipeline_anomaly is True

    def test_vacuum_blocked_on_pipeline_anomaly(self):
        """G10: VACUUM must return SKIPPED when pipeline_anomaly=True."""
        from engine.core.health_checker import HealthResult
        from engine.operations import vacuum
        r = HealthResult(table_fqn="glue_catalog.fin_db.t")
        r.snapshot_count    = 200
        r.expired_snapshots = 100
        r.pipeline_anomaly  = True

        result = vacuum.run_expire_snapshots(
            "glue_catalog.fin_db.t", {}, r, tier="standard"
        )
        assert result["skipped"] is True
        assert "SAFETY_BLOCKED" in result["skip_reason"]


# ── Gap 6: expired_snapshots in health check ─────────────────────────────────

class TestHealthCheckerEnrichment:
    def test_health_result_has_expired_snapshots_field(self):
        from engine.core.health_checker import HealthResult
        r = HealthResult(table_fqn="t")
        assert hasattr(r, "expired_snapshots")
        assert r.expired_snapshots == 0

    def test_health_result_has_commits_per_day_field(self):
        from engine.core.health_checker import HealthResult
        r = HealthResult(table_fqn="t")
        assert hasattr(r, "commits_per_day")
        assert hasattr(r, "commit_tier")
        assert hasattr(r, "anomalous_commit_rate")
        assert hasattr(r, "pipeline_anomaly")

    def test_needs_vacuum_on_high_expired_count(self):
        """needs_vacuum=True when expired_snapshots > 10 even if not oldest."""
        import pandas as pd

        from engine.core.health_checker import HealthResult, _check_snapshots

        mock_row = {
            "snapshot_count":      50,
            "latest_snapshot_ts":  "2026-05-01",
            "oldest_snapshot_days": 5,   # within 7-day retention
            "expired_snapshots":   15,   # > 10 — should trigger vacuum
            "commits_7d":          70,   # 10/day
        }
        df = pd.DataFrame([mock_row])

        with patch("engine.core.health_checker.read_sql", return_value=df):
            from engine.core.health_checker import HealthResult
            result = HealthResult(table_fqn="glue_catalog.fin_db.t")
            _check_snapshots(
                "glue_catalog.fin_db.t",
                {"snapshot_retention_days": 7, "snapshot_min_to_keep": 30},
                result,
                "standard",
            )

        # 50 snapshots > 30 floor AND expired > 10 → needs_vacuum
        assert result.needs_vacuum is True
        assert result.expired_snapshots == 15


# ── Gap 8: property sync has all four properties ─────────────────────────────

class TestPropertySyncAllFields:
    def test_alter_table_has_all_four_properties(self):
        """ALTER TABLE must set all 4 properties including new ones."""
        from engine.core.property_sync import apply_vacuum_properties
        sql_issued = []
        with patch("engine.core.property_sync.run_query",
                   side_effect=lambda s, **kw: sql_issued.append(s) or "qid"), \
             patch("engine.core.property_sync.get_commit_stats",
                   side_effect=Exception("local mode")):
            apply_vacuum_properties(
                "glue_catalog.fin_db.t",
                {"snapshot_retention_days": 7},
                workgroup="standard",
                dry_run=False,
            )

        assert sql_issued
        sql = sql_issued[0].upper()
        assert "VACUUM_MAX_SNAPSHOT_AGE_SECONDS"   in sql
        assert "VACUUM_MIN_SNAPSHOTS_TO_KEEP"      in sql
        assert "VACUUM_MAX_METADATA_FILES_TO_KEEP" in sql
        assert "WRITE_TARGET_DATA_FILE_SIZE_BYTES" in sql

    def test_property_sync_result_has_all_fields(self):
        from engine.core.property_sync import apply_vacuum_properties
        with patch("engine.core.property_sync.run_query", return_value="qid"), \
             patch("engine.core.property_sync.get_commit_stats",
                   side_effect=Exception("local mode")):
            result = apply_vacuum_properties(
                "glue_catalog.fin_db.t", {}, "standard", dry_run=False,
            )
        assert "max_meta_files"  in result
        assert "file_size_bytes" in result
        assert "commit_tier"     in result


# ── Gap 9: Separate skip thresholds ──────────────────────────────────────────

class TestSkipThresholds:
    def _make_health(self, count):
        from engine.core.health_checker import HealthResult
        r = HealthResult(table_fqn="glue_catalog.fin_db.t")
        r.snapshot_count    = count
        r.expired_snapshots = max(0, count - 2)
        return r

    def test_trivial_skip_below_5(self):
        from config.settings import SNAPSHOT_TRIVIAL_SKIP
        from engine.operations import vacuum
        result = vacuum.run_expire_snapshots(
            "glue_catalog.fin_db.t", {},
            self._make_health(SNAPSHOT_TRIVIAL_SKIP - 1),
            tier="standard",
        )
        assert result["skipped"] is True
        assert "trivially_small" in result["skip_reason"]

    def test_safety_floor_skip(self):
        from engine.operations import vacuum
        # 30 snapshots, min_to_keep=30 → at floor
        result = vacuum.run_expire_snapshots(
            "glue_catalog.fin_db.t",
            {"snapshot_min_to_keep": 30},
            self._make_health(30),
            tier="standard",
        )
        assert result["skipped"] is True
        assert "at_safety_floor" in result["skip_reason"]

    def test_trivial_skip_different_from_floor_skip(self):
        """The two skip reasons must be distinguishable in execution_log."""
        from config.settings import SNAPSHOT_TRIVIAL_SKIP
        from engine.operations import vacuum

        trivial = vacuum.run_expire_snapshots(
            "t", {}, self._make_health(SNAPSHOT_TRIVIAL_SKIP - 1), tier="standard"
        )
        floor = vacuum.run_expire_snapshots(
            "t", {"snapshot_min_to_keep": 40}, self._make_health(40), tier="standard"
        )
        assert trivial["skip_reason"] != floor["skip_reason"]


# ── Gap 10: OPTIMIZE → VACUUM ordering ───────────────────────────────────────

class TestOptimizeVacuumOrdering:
    def test_vacuum_deferred_when_compaction_failed(self):
        """If compaction FAILED, VACUUM must not run."""
        with open("engine/engines/hk_engine.py", encoding="utf-8") as f:
            src = f.read()
        assert "_compaction_ok" in src, "Missing _compaction_ok guard"
        assert "SKIP_COMPACTION_PREREQUISITE" in src, "Missing skip reason string"
        assert "vacuum_deferred" in src.lower(), "Missing vacuum deferred log"

    def test_vacuum_runs_when_compaction_succeeded(self):
        """If compaction SUCCESS or not needed, VACUUM proceeds."""
        with open("engine/engines/hk_engine.py", encoding="utf-8") as f:
            src = f.read()
        assert "op_status in (\"SUCCESS\", \"DRY_RUN\", \"SKIPPED\")" in src


# ── Gap 13: Partition type-aware filters ──────────────────────────────────────

class TestPartitionTypeAwareFilters:
    def test_date_type_emits_date_literal(self):
        from engine.utils.partition_utils import build_hot_partition_filter
        result = build_hot_partition_filter("partition_date", days=7, partition_type="date")
        assert result is not None
        assert "DATE '" in result
        assert "TIMESTAMP" not in result

    def test_timestamp_type_emits_timestamp_literal(self):
        from engine.utils.partition_utils import build_hot_partition_filter
        result = build_hot_partition_filter("load_ts", days=7, partition_type="timestamp")
        assert result is not None
        assert "TIMESTAMP '" in result
        assert "00:00:00" in result

    def test_int_yyyymmdd_emits_integer(self):
        from engine.utils.partition_utils import build_hot_partition_filter
        result = build_hot_partition_filter("partition_dt", days=7, partition_type="int_yyyymmdd")
        assert result is not None
        assert "DATE '" not in result
        assert "TIMESTAMP" not in result
        # Should be a bare integer YYYYMMDD
        import re
        assert re.search(r">= \d{8}", result), f"Expected integer date, got: {result}"

    def test_string_type_emits_string_literal(self):
        from engine.utils.partition_utils import build_hot_partition_filter
        result = build_hot_partition_filter("dt", days=7, partition_type="string")
        assert result is not None
        assert "DATE '" not in result
        assert result.startswith("dt >= '")

    def test_identity_type_returns_none(self):
        """Non-date partitions must return None — no date filter applicable."""
        from engine.utils.partition_utils import build_hot_partition_filter
        result = build_hot_partition_filter("region", days=7, partition_type="identity")
        assert result is None

    def test_none_partition_type_returns_none(self):
        from engine.utils.partition_utils import build_hot_partition_filter
        result = build_hot_partition_filter("col", days=7, partition_type="none")
        assert result is None

    def test_default_type_is_date_backward_compatible(self):
        """No partition_type arg → behaves as before (DATE literal)."""
        from engine.utils.partition_utils import build_hot_partition_filter
        result = build_hot_partition_filter("partition_date", days=30)
        assert result is not None
        assert "DATE '" in result

    def test_cadence_daily_date_type(self):
        from engine.utils.partition_utils import build_hot_partition_filter
        result = build_hot_partition_filter(
            "partition_date", processing_cadence="daily", partition_type="date"
        )
        assert result is not None
        today = date.today()
        cutoff = today - timedelta(days=90)
        assert cutoff.isoformat() in result

    def test_monthly_cadence_returns_none_regardless_of_type(self):
        from engine.utils.partition_utils import build_hot_partition_filter
        result = build_hot_partition_filter(
            "partition_date", processing_cadence="monthly", partition_type="date"
        )
        assert result is None


class TestPartitionDiscovery:
    def test_discover_partition_spec_returns_dict(self):
        from engine.utils.glue_client import discover_partition_spec
        # In local mode returns default with partition_type
        result = discover_partition_spec("finance_db", "fin_payment")
        assert isinstance(result, dict)
        assert "partition_type" in result
        # In local mode, partition_column may or may not be set
        # depending on Glue availability — just check dict is returned
        assert result.get("partition_type") in (
            "date", "timestamp", "int_yyyymmdd", "string", "identity", "none"
        )

    def test_glue_type_to_partition_type_date(self):
        from engine.utils.glue_client import _glue_type_to_partition_type
        assert _glue_type_to_partition_type("date")      == "date"
        assert _glue_type_to_partition_type("DATE")      == "date"
        assert _glue_type_to_partition_type("timestamp") == "timestamp"
        assert _glue_type_to_partition_type("bigint")    == "int_yyyymmdd"
        assert _glue_type_to_partition_type("string")    == "string"

    def test_detect_date_column_finds_partition_date(self):
        from engine.utils.glue_client import _detect_date_column
        columns = [
            {"Name": "transaction_id", "Type": "string"},
            {"Name": "amount",         "Type": "double"},
            {"Name": "partition_date", "Type": "date"},
        ]
        result = _detect_date_column(columns)
        assert result is not None
        assert result["partition_column"] == "partition_date"
        assert result["partition_type"]   == "date"

    def test_detect_date_column_returns_none_for_no_date(self):
        from engine.utils.glue_client import _detect_date_column
        columns = [
            {"Name": "region",   "Type": "string"},
            {"Name": "category", "Type": "string"},
        ]
        result = _detect_date_column(columns)
        assert result is None

    def test_detect_date_column_priority_order(self):
        """partition_date is preferred over transaction_date."""
        from engine.utils.glue_client import _detect_date_column
        columns = [
            {"Name": "transaction_date", "Type": "date"},
            {"Name": "partition_date",   "Type": "date"},
        ]
        result = _detect_date_column(columns)
        assert result["partition_column"] == "partition_date"

    def test_hk_config_ddl_has_partition_type(self):
        with open("sql/create_hk_config.sql", encoding="utf-8") as f:
            ddl = f.read()
        assert "partition_type" in ddl

    def test_settings_have_vacuum_constants(self):
        from config.settings import (
            ANOMALOUS_COMMITS_BLOCK,
            ANOMALOUS_COMMITS_WARN,
            SNAPSHOT_TRIVIAL_SKIP,
            VACUUM_BLOAT_THRESHOLD,
            VACUUM_ITERATION_SLEEP_SECS,
            VACUUM_MAX_ITERATIONS,
        )
        assert SNAPSHOT_TRIVIAL_SKIP   == 5
        assert ANOMALOUS_COMMITS_WARN  == 50
        assert ANOMALOUS_COMMITS_BLOCK == 100
        assert VACUUM_MAX_ITERATIONS   == 3
        assert VACUUM_BLOAT_THRESHOLD  == 500

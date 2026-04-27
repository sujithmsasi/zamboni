"""
Sprint 3 gap closure tests.
Covers: activity signals (H5), deploy script existence (M3).
"""
import os
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock
import pandas as pd


# ── H5: Activity signals ──────────────────────────────────────────────────────

class TestActivitySignals:

    def test_signals_with_glue_fallback(self):
        """When CloudTrail is not configured, use Glue CreateTime as fallback."""
        from engine.monitoring.activity_scanner import get_activity_signals
        created = datetime.now(timezone.utc) - timedelta(days=45)

        with patch("engine.monitoring.activity_scanner.CLOUDTRAIL_TABLE", ""):
            signals = get_activity_signals(
                table_fqn="glue_catalog.test_db.test_table",
                database="test_db",
                table_name="test_table",
                glue_create_time=created,
            )

        assert signals.last_write_at is not None
        assert signals.days_since_activity == 45
        assert signals.source == "glue_create"

    def test_signals_no_cloudtrail_no_create_time(self):
        """No CloudTrail and no CreateTime → all signals are None."""
        from engine.monitoring.activity_scanner import get_activity_signals

        with patch("engine.monitoring.activity_scanner.CLOUDTRAIL_TABLE", ""):
            signals = get_activity_signals(
                table_fqn="glue_catalog.test_db.test_table",
                database="test_db",
                table_name="test_table",
                glue_create_time=None,
            )

        assert signals.last_query_at is None
        assert signals.last_write_at is None
        assert signals.days_since_activity is None
        assert signals.source == "none"

    def test_days_since_activity_calculation(self):
        """days_since_activity should be accurate to the day."""
        from engine.monitoring.activity_scanner import _days_since
        ten_days_ago = datetime.now(timezone.utc) - timedelta(days=10)
        assert _days_since(ten_days_ago) == 10

    def test_days_since_zero_for_now(self):
        from engine.monitoring.activity_scanner import _days_since
        just_now = datetime.now(timezone.utc) - timedelta(minutes=30)
        assert _days_since(just_now) == 0

    def test_parse_ts_string_iso(self):
        from engine.monitoring.activity_scanner import _parse_ts
        ts_str = "2026-01-15T10:30:00"
        result = _parse_ts(ts_str)
        assert result is not None
        assert result.year == 2026
        assert result.month == 1
        assert result.tzinfo is not None

    def test_parse_ts_datetime_naive_gets_utc(self):
        from engine.monitoring.activity_scanner import _parse_ts
        naive = datetime(2026, 3, 1, 12, 0, 0)
        result = _parse_ts(naive)
        assert result.tzinfo is not None

    def test_parse_ts_none_returns_none(self):
        from engine.monitoring.activity_scanner import _parse_ts
        assert _parse_ts(None) is None

    def test_cloudtrail_signals_with_mock_athena(self):
        """With CloudTrail configured, query Athena for both signals."""
        from engine.monitoring.activity_scanner import get_activity_signals

        last_query = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
        last_write = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()

        query_df = pd.DataFrame([{"last_query_at": last_query}])
        write_df = pd.DataFrame([{"last_write_at": last_write}])

        call_count = [0]
        def mock_read_sql(sql, **kwargs):
            call_count[0] += 1
            if "StartQueryExecution" in sql:
                return query_df
            return write_df

        with patch("engine.monitoring.activity_scanner.CLOUDTRAIL_TABLE",
                   "glue_catalog.logs_db.cloudtrail"), \
             patch("engine.utils.athena_client.read_sql",
                   side_effect=mock_read_sql):

            signals = get_activity_signals(
                table_fqn="glue_catalog.finance_preprod.finance_staging",
                database="finance_preprod",
                table_name="finance_staging",
                glue_create_time=None,
            )

        assert signals.source == "cloudtrail"
        # days_since_activity should be 3 (last_write is more recent than last_query)
        assert signals.days_since_activity is not None
        assert signals.days_since_activity <= 5

    def test_cloudtrail_falls_back_on_athena_error(self):
        """If CloudTrail Athena query fails, falls back to Glue CreateTime."""
        from engine.monitoring.activity_scanner import get_activity_signals
        created = datetime.now(timezone.utc) - timedelta(days=20)

        with patch("engine.monitoring.activity_scanner.CLOUDTRAIL_TABLE",
                   "glue_catalog.logs_db.cloudtrail"), \
             patch("engine.utils.athena_client.read_sql",
                   side_effect=Exception("Athena timeout")):

            signals = get_activity_signals(
                table_fqn="glue_catalog.finance_preprod.finance_staging",
                database="finance_preprod",
                table_name="finance_staging",
                glue_create_time=created,
            )

        # Individual CloudTrail queries failed but function completed
        # source is "cloudtrail" but last_write_at falls back to glue_create_time
        assert signals.days_since_activity == 20
        assert signals.last_write_at is not None  # glue fallback populated it

    def test_activity_signals_dataclass_fields(self):
        """ActivitySignals dataclass has all required fields."""
        from engine.monitoring.activity_scanner import ActivitySignals
        s = ActivitySignals(table_fqn="test")
        assert hasattr(s, "table_fqn")
        assert hasattr(s, "last_query_at")
        assert hasattr(s, "last_write_at")
        assert hasattr(s, "days_since_activity")
        assert hasattr(s, "source")

    def test_most_recent_signal_wins_for_days_since(self):
        """days_since_activity uses the MOST RECENT of query/write."""
        from engine.monitoring.activity_scanner import get_activity_signals

        # last_query_at = 10 days ago, last_write_at = 3 days ago
        # days_since_activity should be 3 (write is more recent)
        query_ts = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        write_ts = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()

        query_df = pd.DataFrame([{"last_query_at": query_ts}])
        write_df = pd.DataFrame([{"last_write_at": write_ts}])

        def mock_read_sql(sql, **kwargs):
            if "StartQueryExecution" in sql:
                return query_df
            return write_df

        with patch("engine.monitoring.activity_scanner.CLOUDTRAIL_TABLE",
                   "glue_catalog.logs_db.cloudtrail"), \
             patch("engine.utils.athena_client.read_sql",
                   side_effect=mock_read_sql):

            signals = get_activity_signals(
                table_fqn="t", database="db", table_name="t"
            )

        assert signals.days_since_activity == 3


# ── M3: Deploy scripts exist ──────────────────────────────────────────────────

def test_setup_ec2_script_exists():
    """deploy/setup_ec2.sh must exist (referenced in setup guide)."""
    assert os.path.exists("deploy/setup_ec2.sh"), \
        "deploy/setup_ec2.sh missing — referenced in setup guide"


def test_create_athena_tables_script_exists():
    """deploy/create_athena_tables.sh must exist (referenced in setup guide)."""
    assert os.path.exists("deploy/create_athena_tables.sh"), \
        "deploy/create_athena_tables.sh missing — referenced in setup guide"


def test_setup_ec2_references_correct_entrypoint():
    """setup_ec2.sh should reference app/Home.py not app/main.py."""
    with open("deploy/setup_ec2.sh") as f:
        content = f.read()
    assert "app/Home.py" in content
    assert "app/main.py" not in content


def test_setup_ec2_is_executable_script():
    """setup_ec2.sh should have bash shebang."""
    with open("deploy/setup_ec2.sh") as f:
        first_line = f.readline()
    assert "bash" in first_line or "sh" in first_line


def test_create_athena_tables_references_sql_files():
    """create_athena_tables.sh should reference all 6 SQL DDL files."""
    with open("deploy/create_athena_tables.sh") as f:
        content = f.read()
    required_tables = [
        "create_domain_registry.sql",
        "create_stream_registry.sql",
        "create_hk_config.sql",
        "create_execution_log.sql",
        "create_nonprod_registry.sql",
        "create_home_snapshot.sql",
    ]
    for sql_file in required_tables:
        assert sql_file in content, \
            f"{sql_file} not referenced in create_athena_tables.sh"


# ── H5: Lifecycle engine wiring ───────────────────────────────────────────────

def test_lifecycle_engine_imports_activity_scanner():
    """Lifecycle engine should import activity scanner."""
    with open("engine/engines/lifecycle_engine.py") as f:
        content = f.read()
    assert "activity_scanner" in content or "get_activity_signals" in content, \
        "lifecycle_engine.py does not import activity_scanner"


def test_upsert_updates_activity_signals():
    """_upsert_nonprod_registry should update last_query_at and last_write_at."""
    with open("engine/engines/lifecycle_engine.py") as f:
        content = f.read()
    assert "last_query_at" in content
    assert "last_write_at" in content
    assert "days_since_activity" in content


def test_cloudtrail_settings_exist():
    """CLOUDTRAIL_TABLE and CLOUDTRAIL_LOOKBACK_DAYS should be in settings."""
    from config import settings
    assert hasattr(settings, "CLOUDTRAIL_TABLE")
    assert hasattr(settings, "CLOUDTRAIL_LOOKBACK_DAYS")
    assert isinstance(settings.CLOUDTRAIL_LOOKBACK_DAYS, int)

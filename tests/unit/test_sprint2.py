"""
Sprint 2 gap closure tests.
Covers: run_frequency (H1), dedupe/SKIP_NOT_DUE (H2), tier parallelism
config (H3), operation-level logging structure (H4).
"""
from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import patch

# ── H1 + H2: run_frequency + dedupe ──────────────────────────────────────────

class TestIsDue:
    """Tests for HKEngine._is_due() frequency and dedupe logic."""

    def _engine(self):
        from engine.engines.hk_engine import HKEngine
        return HKEngine(dry_run=True)

    def _config(self, freq):
        return {"run_frequency": freq}

    def test_every_trigger_always_due(self):
        engine = self._engine()
        with patch("engine.engines.hk_engine.execution_log"):
            due, reason = engine._is_due("t", self._config("every_trigger"))
        assert due is True
        assert reason == ""

    def test_daily_due_when_never_run(self):
        engine = self._engine()
        with patch("engine.engines.hk_engine.execution_log") as mock_log:
            mock_log.get_last_run.return_value = None
            due, reason = engine._is_due("t", self._config("daily"))
        assert due is True

    def test_daily_due_when_run_22h_ago(self):
        engine = self._engine()
        last_run_ts = (datetime.now(UTC) - timedelta(hours=22)).isoformat()
        with patch("engine.engines.hk_engine.execution_log") as mock_log:
            mock_log.get_last_run.return_value = {"completed_at": last_run_ts}
            due, reason = engine._is_due("t", self._config("daily"))
        assert due is True

    def test_daily_not_due_when_run_2h_ago(self):
        engine = self._engine()
        last_run_ts = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
        with patch("engine.engines.hk_engine.execution_log") as mock_log:
            mock_log.get_last_run.return_value = {"completed_at": last_run_ts}
            due, reason = engine._is_due("t", self._config("daily"))
        assert due is False
        assert "SKIP_NOT_DUE" in reason
        assert "daily" in reason

    def test_weekly_not_due_when_run_3_days_ago(self):
        engine = self._engine()
        last_run_ts = (datetime.now(UTC) - timedelta(hours=72)).isoformat()
        with patch("engine.engines.hk_engine.execution_log") as mock_log:
            mock_log.get_last_run.return_value = {"completed_at": last_run_ts}
            due, reason = engine._is_due("t", self._config("weekly"))
        assert due is False
        assert "SKIP_NOT_DUE" in reason

    def test_weekly_due_when_run_8_days_ago(self):
        engine = self._engine()
        last_run_ts = (datetime.now(UTC) - timedelta(hours=192)).isoformat()
        with patch("engine.engines.hk_engine.execution_log") as mock_log:
            mock_log.get_last_run.return_value = {"completed_at": last_run_ts}
            due, reason = engine._is_due("t", self._config("weekly"))
        assert due is True

    def test_defaults_to_daily_threshold_for_unknown_freq(self):
        engine = self._engine()
        last_run_ts = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
        with patch("engine.engines.hk_engine.execution_log") as mock_log:
            mock_log.get_last_run.return_value = {"completed_at": last_run_ts}
            due, reason = engine._is_due("t", {"run_frequency": "unknown_freq"})
        assert due is False  # uses daily threshold of 20h

    def test_fails_gracefully_on_log_error(self):
        """If execution_log query fails, allow the run."""
        engine = self._engine()
        with patch("engine.engines.hk_engine.execution_log") as mock_log:
            mock_log.get_last_run.side_effect = Exception("Athena unavailable")
            due, reason = engine._is_due("t", self._config("daily"))
        assert due is True  # fail open

    def test_skip_reason_includes_hours_since(self):
        engine = self._engine()
        last_run_ts = (datetime.now(UTC) - timedelta(hours=5)).isoformat()
        with patch("engine.engines.hk_engine.execution_log") as mock_log:
            mock_log.get_last_run.return_value = {"completed_at": last_run_ts}
            due, reason = engine._is_due("t", self._config("daily"))
        assert "5." in reason or "4." in reason  # ~5h shown in reason


# ── H3: Tier parallelism configuration ───────────────────────────────────────

def test_tier_worker_counts_defined():
    from engine.engines.hk_engine import _TIER_WORKERS
    assert "critical" in _TIER_WORKERS
    assert "standard" in _TIER_WORKERS
    assert "low"      in _TIER_WORKERS


def test_critical_gets_fewer_workers_than_standard():
    """critical should not have more workers than standard (avoids starvation)."""
    from engine.engines.hk_engine import _TIER_WORKERS
    # critical is smaller fleet so fewer workers is fine
    assert _TIER_WORKERS["critical"] <= _TIER_WORKERS["standard"]


def test_frequency_thresholds_defined():
    from engine.engines.hk_engine import _FREQUENCY_HOURS
    assert "every_trigger" in _FREQUENCY_HOURS
    assert "daily"         in _FREQUENCY_HOURS
    assert "weekly"        in _FREQUENCY_HOURS
    assert _FREQUENCY_HOURS["every_trigger"] == 0
    assert _FREQUENCY_HOURS["daily"]   > 0
    assert _FREQUENCY_HOURS["weekly"]  > _FREQUENCY_HOURS["daily"]


# ── H4: Operation-level logging ───────────────────────────────────────────────

def test_hk_engine_has_write_log_method():
    from engine.engines.hk_engine import HKEngine
    engine = HKEngine(dry_run=True)
    assert hasattr(engine, "_write_log")
    assert callable(engine._write_log)


def test_write_log_accepts_operation_param():
    """_write_log must accept different operation names — compaction, vacuum, etc."""
    from engine.engines.hk_engine import HKEngine
    engine = HKEngine(dry_run=True)

    table_row = {
        "table_fqn": "glue_catalog.test_db.test_table",
        "domain": "test", "layer": "staging",
        "tier": "standard", "environment": "dev",
    }

    # Should not raise for any operation name
    with patch("engine.engines.hk_engine.execution_log") as mock_log:
        mock_log.write.return_value = True
        for op in ["compaction", "vacuum", "orphan_cleanup", "hk_run"]:
            engine._write_log(table_row, operation=op, status="DRY_RUN")
        assert mock_log.write.call_count == 4


def test_write_log_passes_metrics_to_log_entry():
    """Per-operation log records should include operation-specific metrics."""
    from engine.engines.hk_engine import HKEngine
    engine = HKEngine(dry_run=True)

    table_row = {
        "table_fqn": "glue_catalog.test_db.test_table",
        "domain": "test", "layer": "staging",
        "tier": "standard", "environment": "dev",
    }

    captured = []
    def capture_write(entry, **kwargs):
        captured.append(entry)
        return True

    with patch("engine.engines.hk_engine.execution_log") as mock_log:
        mock_log.write.side_effect = capture_write
        engine._write_log(
            table_row, "compaction", "DRY_RUN",
            files_compacted=42,
            bytes_rewritten=1024 * 1024 * 500,
            bytes_scanned=1024 * 1024 * 100,
        )

    assert len(captured) == 1
    entry = captured[0]
    assert entry.operation == "compaction"
    assert entry.files_compacted == 42
    assert entry.bytes_rewritten == 1024 * 1024 * 500


# ── Integration: SKIP_NOT_DUE is a valid skip_reason constant ────────────────

def test_skip_not_due_reason_format():
    """SKIP_NOT_DUE reason must be parseable for Streamlit execution log."""
    from engine.engines.hk_engine import HKEngine
    engine = HKEngine(dry_run=True)
    last_run_ts = (datetime.now(UTC) - timedelta(hours=1)).isoformat()

    with patch("engine.engines.hk_engine.execution_log") as mock_log:
        mock_log.get_last_run.return_value = {"completed_at": last_run_ts}
        due, reason = engine._is_due("t", {"run_frequency": "daily"})

    assert due is False
    assert reason.startswith("SKIP_NOT_DUE")
    # Reason should be a non-empty string (stored in execution_log.skip_reason)
    assert len(reason) > 10

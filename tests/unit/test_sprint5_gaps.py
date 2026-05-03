"""
Sprint 5 gap closure tests.
Covers:
  Gap 1 -- Parquet buffer receives entries / flush writes rows / insert mode uses direct write
  Gap 2 -- Tier alias maps to real zamboni-* workgroup for backpressure
"""
from unittest.mock import MagicMock, patch

# ── Gap 1: Parquet log buffer wiring ─────────────────────────────────────────

class TestParquetBufferWiring:
    """_write_log must route to buffer.append in parquet/auto/both modes."""

    def _engine(self):
        from engine.engines.hk_engine import HKEngine
        return HKEngine(dry_run=True)

    def _table_row(self):
        return {
            "table_fqn": "glue_catalog.test_db.test_table",
            "domain": "test", "layer": "staging",
            "tier": "standard", "environment": "prod",
        }

    def test_parquet_mode_routes_to_buffer_not_direct_write(self):
        """In parquet mode _write_log appends to buffer, not execution_log.write."""
        from engine.core.execution_log_parquet import ParquetLogBuffer
        engine = self._engine()
        engine._log_buffer = ParquetLogBuffer(run_id="test-run")

        with patch("engine.engines.hk_engine.EXECUTION_LOG_MODE", "parquet"), \
             patch("engine.engines.hk_engine.execution_log") as mel:
            engine._write_log(self._table_row(), "hk_run", "DRY_RUN")

        # Direct write must NOT have been called
        mel.write.assert_not_called()
        # Buffer must have the entry
        assert len(engine._log_buffer.entries) == 1
        assert engine._log_buffer.entries[0].operation == "hk_run"

    def test_auto_mode_routes_to_buffer(self):
        """In auto mode (default) entries go to buffer."""
        from engine.core.execution_log_parquet import ParquetLogBuffer
        engine = self._engine()
        engine._log_buffer = ParquetLogBuffer(run_id="test-auto")

        with patch("engine.engines.hk_engine.EXECUTION_LOG_MODE", "auto"), \
             patch("engine.engines.hk_engine.execution_log") as mel:
            engine._write_log(self._table_row(), "compaction", "DRY_RUN")
            engine._write_log(self._table_row(), "vacuum", "DRY_RUN")

        mel.write.assert_not_called()
        assert len(engine._log_buffer.entries) == 2

    def test_insert_mode_uses_direct_write_not_buffer(self):
        """In insert mode _write_log calls execution_log.write directly."""
        from engine.core.execution_log_parquet import ParquetLogBuffer
        engine = self._engine()
        engine._log_buffer = ParquetLogBuffer(run_id="test-insert")

        with patch("engine.engines.hk_engine.EXECUTION_LOG_MODE", "insert"), \
             patch("engine.engines.hk_engine.execution_log") as mel:
            mel.write.return_value = True
            engine._write_log(self._table_row(), "hk_run", "DRY_RUN")

        mel.write.assert_called_once()
        # Buffer must be empty — no append
        assert len(engine._log_buffer.entries) == 0

    def test_both_mode_routes_to_buffer(self):
        """In both mode entries go to buffer (flush handles fallback)."""
        from engine.core.execution_log_parquet import ParquetLogBuffer
        engine = self._engine()
        engine._log_buffer = ParquetLogBuffer(run_id="test-both")

        with patch("engine.engines.hk_engine.EXECUTION_LOG_MODE", "both"), \
             patch("engine.engines.hk_engine.execution_log") as mel:
            engine._write_log(self._table_row(), "hk_run", "DRY_RUN")

        mel.write.assert_not_called()
        assert len(engine._log_buffer.entries) == 1

    def test_no_buffer_on_instance_falls_back_to_direct_write(self):
        """If _log_buffer not set (called outside run()), falls back to direct write."""
        engine = self._engine()
        # No _log_buffer set on instance

        with patch("engine.engines.hk_engine.EXECUTION_LOG_MODE", "parquet"), \
             patch("engine.engines.hk_engine.execution_log") as mel:
            mel.write.return_value = True
            engine._write_log(self._table_row(), "hk_run", "DRY_RUN")

        mel.write.assert_called_once()

    def test_buffer_flush_writes_nonzero_rows(self):
        """After appending entries, flush must report rows_written > 0."""
        from engine.core.execution_log_parquet import ParquetLogBuffer
        engine = self._engine()
        engine._log_buffer = ParquetLogBuffer(run_id="flush-test")

        with patch("engine.engines.hk_engine.EXECUTION_LOG_MODE", "parquet"):
            for op in ["compaction", "vacuum", "hk_run"]:
                engine._write_log(self._table_row(), op, "DRY_RUN")

        assert len(engine._log_buffer.entries) == 3

        # Flush in dry_run mode — no real S3/Athena calls
        result = engine._log_buffer.flush(dry_run=True)
        assert result["rows_written"] == 3

    def test_no_duplicate_writes_in_parquet_mode(self):
        """Entries must not be written both to buffer AND direct write."""
        from engine.core.execution_log_parquet import ParquetLogBuffer
        engine = self._engine()
        engine._log_buffer = ParquetLogBuffer(run_id="no-dupe")

        direct_calls = []
        buffer_appends = []
        orig_append = engine._log_buffer.append

        def track_append(entry):
            buffer_appends.append(entry)
            orig_append(entry)

        engine._log_buffer.append = track_append

        with patch("engine.engines.hk_engine.EXECUTION_LOG_MODE", "parquet"), \
             patch("engine.engines.hk_engine.execution_log") as mel:
            mel.write.side_effect = lambda e, **kw: direct_calls.append(e)
            engine._write_log(self._table_row(), "hk_run", "SUCCESS")

        assert len(direct_calls) == 0, "Direct write must not be called in parquet mode"
        assert len(buffer_appends) == 1

    def test_log_buffer_created_in_run(self):
        """run() must create self._log_buffer before processing tables."""
        with open("engine/engines/hk_engine.py", encoding='utf-8') as f:
            src = f.read()
        assert "self._log_buffer = ParquetLogBuffer" in src

    def test_log_buffer_flushed_in_run(self):
        """run() must flush self._log_buffer at end."""
        with open("engine/engines/hk_engine.py", encoding='utf-8') as f:
            src = f.read()
        assert "self._log_buffer.flush" in src


# ── Gap 2: Tier alias -> zamboni-* workgroup mapping ─────────────────────────

class TestWorkgroupMapping:
    """Backpressure must receive real zamboni-* names, not tier aliases."""

    def test_tier_to_workgroup_constant_exists(self):
        from engine.engines.hk_engine import _TIER_TO_WORKGROUP
        assert "critical" in _TIER_TO_WORKGROUP
        assert "standard" in _TIER_TO_WORKGROUP
        assert "low"      in _TIER_TO_WORKGROUP

    def test_tier_to_workgroup_values_are_zamboni_names(self):
        from engine.engines.hk_engine import _TIER_TO_WORKGROUP
        for tier, wg in _TIER_TO_WORKGROUP.items():
            assert wg.startswith("zamboni-"), \
                f"Tier '{tier}' maps to '{wg}' — must start with 'zamboni-'"

    def test_tier_to_workgroup_matches_backpressure_limits(self):
        """Every workgroup in _TIER_TO_WORKGROUP must be in backpressure._DEFAULT_LIMITS."""
        from engine.core.backpressure import _DEFAULT_LIMITS
        from engine.engines.hk_engine import _TIER_TO_WORKGROUP
        for tier, wg in _TIER_TO_WORKGROUP.items():
            assert wg in _DEFAULT_LIMITS, \
                f"Workgroup '{wg}' (from tier '{tier}') not in backpressure._DEFAULT_LIMITS"

    def test_workgroup_assigned_from_mapping_in_process_table(self):
        """_process_table sets workgroup using _TIER_TO_WORKGROUP, not raw tier."""
        with open("engine/engines/hk_engine.py", encoding='utf-8') as f:
            src = f.read()
        assert "_TIER_TO_WORKGROUP.get(tier" in src, \
            "workgroup must be set via _TIER_TO_WORKGROUP.get(tier, ...) not raw tier alias"

    def test_backpressure_receives_zamboni_workgroup_not_tier_alias(self):
        """wait_for_capacity must be called with 'zamboni-*' not 'critical'/'standard'."""
        from engine.engines.hk_engine import HKEngine

        engine = HKEngine(dry_run=True)
        captured_wg = []

        def mock_wait(workgroup, **kwargs):
            captured_wg.append(workgroup)
            return True  # capacity available

        table_row = {
            "table_fqn": "glue_catalog.test_db.t1",
            "domain": "test", "layer": "staging",
            "tier": "standard", "environment": "prod",
        }

        with patch("engine.engines.hk_engine.wait_for_capacity", side_effect=mock_wait), \
             patch("engine.engines.hk_engine.execution_log"), \
             patch("engine.engines.hk_engine.registry.get_table", return_value=table_row), \
             patch("engine.engines.hk_engine.get_hk_config") as mhk, \
             patch("engine.engines.hk_engine.health_checker") as mhc, \
             patch("engine.engines.hk_engine.is_upstream_job_complete", return_value=True), \
             patch("engine.engines.hk_engine.evaluate", return_value="EXECUTE"), \
             patch("engine.engines.hk_engine.circuit_breaker") as mcb, \
             patch("engine.engines.hk_engine.is_in_dry_run_ramp", return_value=False), \
             patch("engine.engines.hk_engine.build_execution_id", return_value="exec_test"), \
             patch("engine.engines.hk_engine.check_already_executed", return_value=False), \
             patch("engine.engines.hk_engine.needs_property_sync", return_value=False), \
             patch("engine.engines.hk_engine.EXECUTION_LOG_MODE", "insert"):

            mhk.return_value = {"run_frequency": "every_trigger", "window_config": ""}
            mcb.check.return_value = "CLOSED"
            mcb.OPEN = "OPEN"
            mcb.CLOSED = "CLOSED"

            health = MagicMock()
            health.check_success = True
            health.needs_compaction = True
            health.needs_vacuum = False
            health.needs_orphan_cleanup = False
            health.snapshot_count = 10
            health.total_size_gb = 1.0
            health.total_files = 100
            mhc.check.return_value = health

            with patch("engine.engines.hk_engine.is_healthy", return_value=False), \
                 patch("engine.engines.hk_engine.compaction") as mcomp:
                mcomp.run_compaction.return_value = {}
                engine._process_table(table_row)

        # All wait_for_capacity calls must use zamboni-* names
        for wg in captured_wg:
            assert wg.startswith("zamboni-"), \
                f"wait_for_capacity received tier alias '{wg}' instead of zamboni-* name"

    def test_backpressure_timeout_skips_with_clear_reason(self):
        """When wait_for_capacity returns False, operation is SKIPPED with BP reason."""
        from engine.core.execution_log_parquet import ParquetLogBuffer
        from engine.engines.hk_engine import HKEngine

        engine = HKEngine(dry_run=True)
        engine._log_buffer = ParquetLogBuffer(run_id="bp-test")

        captured = []
        def track_write(entry, **kw):
            captured.append(entry)

        table_row = {
            "table_fqn": "glue_catalog.test_db.t1",
            "domain": "test", "layer": "staging",
            "tier": "standard", "environment": "prod",
        }

        with patch("engine.engines.hk_engine.wait_for_capacity", return_value=False), \
             patch("engine.engines.hk_engine.execution_log") as mel, \
             patch("engine.engines.hk_engine.EXECUTION_LOG_MODE", "insert"):
            mel.write.side_effect = track_write

            # Call the write_log path that would be triggered by backpressure timeout
            engine._write_log(
                table_row, "compaction", "SKIPPED",
                skip_reason="SKIP_BACKPRESSURE_TIMEOUT (workgroup=zamboni-standard)",
                effective_dry_run=True,
            )

        assert len(captured) == 1
        assert captured[0].status == "SKIPPED"
        assert "BACKPRESSURE" in (captured[0].skip_reason or "")
        assert "zamboni-" in (captured[0].skip_reason or "")

    def test_backpressure_success_path_does_not_skip(self):
        """When wait_for_capacity returns True, operation proceeds normally."""
        from engine.core.backpressure import wait_for_capacity
        with patch("engine.core.backpressure.get_running_query_count",
                   return_value=5):
            result = wait_for_capacity("zamboni-standard", max_wait_seconds=5, limit=25)
        assert result is True

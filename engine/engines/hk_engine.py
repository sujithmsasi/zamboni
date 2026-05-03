"""
Zamboni — HK Engine
The core housekeeping engine. Processes all enabled Iceberg tables:
  1. Gate 1     — upstream batch completion check
  2. Window     — safe window evaluation
  3. Frequency  — run_frequency + dedupe (SKIP_NOT_DUE)  [Sprint 2: H1+H2]
  4. Circuit    — skip if circuit breaker is open
  5. Health     — assess what operations are needed
  6. Compact    — bin-pack (Athena) or sort/zorder (Glue)
  7. Vacuum     — expire snapshots + orphan file cleanup
  8. Log        — per-operation records + hk_run summary [Sprint 2: H4]
  9. Metrics    — CloudWatch publish

Sprint 2 additions:
  - run_frequency + dedupe: tables skip if run recently (SKIP_NOT_DUE)
  - Tier-ordered parallelism: ThreadPoolExecutor per tier group [H3]
  - Operation-level log records: separate rows for compaction/vacuum/orphan [H4]
  - dry_run_until ramp-up: per-table dry_run flag from is_in_dry_run_ramp [C2]
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime

from config.settings import EXECUTION_LOG_MODE
from engine.core import (
    circuit_breaker,
    execution_log,
    health_checker,
    notifier,
    registry,
)
from engine.core.backpressure import wait_for_capacity
from engine.core.config import get_hk_config
from engine.core.execution_log import LogEntry
from engine.core.execution_log_parquet import ParquetLogBuffer
from engine.core.health_checker import is_healthy
from engine.core.idempotency import (
    build_execution_id,
    check_already_executed,
    get_window_id,
    mark_executed,
)
from engine.core.property_sync import (
    apply_vacuum_properties,
    mark_properties_synced,
    needs_property_sync,
)
from engine.core.registry import is_in_dry_run_ramp
from engine.core.window_evaluator import EXECUTE, evaluate
from engine.engines.base import BaseEngine
from engine.monitoring.metrics import publish_engine_run
from engine.operations import compaction, vacuum
from engine.utils.glue_client import is_upstream_job_complete
from engine.utils.logger import get_logger

log = get_logger(__name__)

# ── run_frequency thresholds ──────────────────────────────────────────────────
# Hours of buffer added so a 24h "daily" window isn't broken by a 25-min drift
_FREQUENCY_HOURS = {
    "every_trigger": 0,    # always run
    "daily":         20,   # run if last run > 20h ago
    "weekly":        160,  # run if last run > 160h ago  (6.7 days)
    "monthly":       700,  # run if last run > 700h ago  (29.2 days)
}

# ── Parallelism by tier ───────────────────────────────────────────────────────
_TIER_WORKERS = {
    "critical": 5,
    "standard": 10,
    "low":       3,
}

# ── Tier alias to actual Athena workgroup name (Gap 2) ────────────────────────
# Backpressure checks must use real Athena workgroup names, not tier aliases.
_TIER_TO_WORKGROUP = {
    "critical": "zamboni-critical",
    "standard": "zamboni-standard",
    "low":      "zamboni-low",
}


class HKEngine(BaseEngine):
    """
    HK Engine — automated compaction, snapshot expiry, orphan file cleanup.
    Triggered hourly by EventBridge (or post-batch via Control-M).
    Self-regulating: tables that are not due, outside their window, or
    already healthy are skipped automatically.
    """

    def run(
        self,
        domain: str | None = None,
        layer:  str | None = None,
        tier:   str | None = None,
        table_fqn: str | None = None,
        environment: str = "prod",
    ) -> dict:
        """
        Run HK Engine.

        Scope (use one):
          table_fqn            — single table
          domain + layer/tier  — filtered subset
          (none)               — all enabled tables

        Returns run summary dict.
        """
        self._log_start(
            scope=table_fqn or domain or "all",
            environment=environment,
            domain=domain, layer=layer, tier=tier,
        )

        # ── Fetch tables ──────────────────────────────────────────────────────
        if table_fqn:
            row    = registry.get_table(table_fqn)
            tables = [row] if row else []
        else:
            tables = registry.get_enabled_tables(
                environment=environment,
                domain=domain, tier=tier, layer=layer,
            )

        log.info("hk_engine.tables_fetched", count=len(tables), run_id=self.run_id)

        # D.10-12 — batch Parquet log buffer; flushed once at end of run.
        # Store on instance so _write_log can route to it without threading
        # log_buffer through every call site (Gap 1).
        self._log_buffer = ParquetLogBuffer(run_id=self.run_id, engine="hk")
        log_buffer = self._log_buffer  # local alias for flush call below

        succeeded     = 0
        failed        = 0
        skipped       = 0
        failed_tables = []

        # ── Tier-ordered parallel processing (H3) ─────────────────────────────
        # Process critical first, then standard, then low.
        # Each tier runs with its own thread pool so critical never waits on low.
        for tier_name in ("critical", "standard", "low", None):
            if tier_name is None:
                tier_tables = [t for t in tables if t.get("tier") not in ("critical","standard","low")]
            else:
                tier_tables = [t for t in tables if t.get("tier") == tier_name]

            if not tier_tables:
                continue

            max_workers = _TIER_WORKERS.get(tier_name or "standard", 5)
            log.info(
                "hk_engine.tier_batch",
                tier=tier_name, count=len(tier_tables),
                max_workers=max_workers, run_id=self.run_id,
            )

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {
                    executor.submit(self._process_table, table_row, log_buffer): table_row
                    for table_row in tier_tables
                }
                for future in as_completed(futures):
                    table_row = futures[future]
                    fqn       = table_row["table_fqn"]
                    try:
                        outcome = future.result()
                        if outcome == "succeeded":
                            succeeded += 1
                        elif outcome == "skipped":
                            skipped += 1
                        elif outcome == "failed":
                            failed += 1
                            failed_tables.append(fqn)
                    except Exception as e:
                        failed += 1
                        failed_tables.append(fqn)
                        self._log_table_error(fqn, e)
                        self._write_log(
                            table_row=table_row,
                            operation="hk_run",
                            status="FAILURE",
                            error_message=str(e),
                        )

        # ── Summary alert on failures ─────────────────────────────────────────
        if failed_tables and not self.dry_run:
            notifier.send_engine_failure_summary(
                engine="hk",
                run_id=self.run_id,
                failed_tables=failed_tables,
            )

        result = self.summary_dict(
            tables_processed=len(tables),
            succeeded=succeeded,
            failed=failed,
            skipped=skipped,
        )
        self._log_complete(result)

        # Flush buffered log entries (Parquet/insert depending on mode)
        flush_result = self._log_buffer.flush(dry_run=self.dry_run)
        log.info(
            "hk_engine.log_buffer_flushed",
            mode=flush_result.get("mode"),
            rows=flush_result.get("rows_written"),
            run_id=self.run_id,
        )

        publish_engine_run(
            engine="hk", run_id=self.run_id,
            succeeded=succeeded, failed=failed, skipped=skipped,
            duration_s=result.get("elapsed_seconds", 0),
            dry_run=self.dry_run,
        )
        return result

    # ── Single table processing ───────────────────────────────────────────────

    def _process_table(self, table_row: dict, log_buffer: ParquetLogBuffer | None = None) -> str:
        """
        Process one table through all gates and operations.
        Returns: 'succeeded' | 'skipped' | 'failed'
        """
        fqn  = table_row["table_fqn"]
        tier = table_row.get("tier", "standard")

        log.info("hk_engine.processing", table_fqn=fqn, tier=tier, run_id=self.run_id)

        # ── Ramp-up check: per-table dry_run ─────────────────────────────────
        table_dry_run = self.dry_run or is_in_dry_run_ramp(table_row)

        # ── Get HK config ─────────────────────────────────────────────────────
        hk_config = get_hk_config(fqn)
        if not hk_config:
            self._log_table_skip(fqn, "SKIP_NO_CONFIG")
            self._write_log(table_row, "hk_run", "SKIPPED", skip_reason="SKIP_NO_CONFIG")
            return "skipped"

        # ── Gate 1 — Upstream batch completion ────────────────────────────────
        upstream_job = table_row.get("dependent_job_name")
        if upstream_job and table_row.get("dependent_job_type") == "glue":
            if not is_upstream_job_complete(upstream_job):
                self._log_table_skip(fqn, "SKIP_UPSTREAM_PENDING")
                self._write_log(table_row, "hk_run", "SKIPPED",
                                skip_reason="SKIP_UPSTREAM_PENDING")
                return "skipped"

        # ── Gate 2 — Safe window ──────────────────────────────────────────────
        decision = evaluate(
            hk_config.get("window_config", ""),
            force=table_row.get("force_run", False),
        )
        if decision != EXECUTE:
            self._log_table_skip(fqn, decision)
            self._write_log(table_row, "hk_run", "SKIPPED", skip_reason=decision)
            return "skipped"

        # ── B.5 — Idempotency check (v2) ─────────────────────────────────────
        # Build execution_id from run_id+table_fqn+operation+window_id.
        # If a SUCCESS already exists for this exact key, skip as duplicate.
        window_id    = get_window_id()
        execution_id = build_execution_id(self.run_id, fqn, "hk_run", window_id)
        if check_already_executed(execution_id, fqn):
            self._log_table_skip(fqn, "SKIP_DUPLICATE")
            self._write_log(
                table_row, "hk_run", "SKIPPED",
                skip_reason=f"SKIP_DUPLICATE (execution_id={execution_id})",
                effective_dry_run=table_dry_run,
            )
            return "skipped"

        # ── Gate 3 — run_frequency + dedupe (H1 + H2) ────────────────────────
        due, skip_reason = self._is_due(fqn, hk_config)
        if not due:
            self._log_table_skip(fqn, skip_reason)
            self._write_log(table_row, "hk_run", "SKIPPED", skip_reason=skip_reason)
            return "skipped"

        # ── Gate 4 — Circuit breaker ──────────────────────────────────────────
        if circuit_breaker.check(fqn) == circuit_breaker.OPEN:
            self._log_table_skip(fqn, "SKIP_CIRCUIT_OPEN")
            self._write_log(table_row, "hk_run", "SKIPPED",
                            skip_reason="SKIP_CIRCUIT_OPEN")
            return "skipped"

        # ── B.6 — Property sync (v2) ─────────────────────────────────────────
        # If table hasn't had vacuum properties applied, ALTER TABLE once.
        if needs_property_sync(table_row):
            workgroup_for_alter = _TIER_TO_WORKGROUP.get(tier, "zamboni-standard")
            sync_result = apply_vacuum_properties(
                fqn, hk_config, workgroup_for_alter,
                dry_run=table_dry_run,
            )
            if sync_result.get("status") == "SUCCESS":
                mark_properties_synced(fqn, workgroup_for_alter, dry_run=table_dry_run)
            self._write_log(
                table_row, "property_sync",
                sync_result.get("status", "FAILURE"),
                error_message=sync_result.get("error"),
                effective_dry_run=table_dry_run,
            )

        # ── Health check ──────────────────────────────────────────────────────
        # Real Athena workgroup name for backpressure + health checks (Gap 2)
        workgroup = _TIER_TO_WORKGROUP.get(tier, "zamboni-standard")
        health    = health_checker.check(fqn, hk_config, workgroup=workgroup)

        if not health.check_success:
            self._write_log(
                table_row, "hk_run", "FAILURE",
                error_message=f"Health check failed: {health.check_error}",
            )
            return "failed"

        if is_healthy(health):
            self._log_table_skip(fqn, "SKIP_HEALTHY")
            self._write_log(table_row, "hk_run", "SKIPPED", skip_reason="SKIP_HEALTHY")
            return "skipped"

        # ── Operations ────────────────────────────────────────────────────────
        started_at     = datetime.now(UTC)
        op_errors      = []
        total_bytes_scanned = 0

        compaction_result = {}
        vacuum_result     = {}
        orphan_result     = {}

        # ── Compaction ────────────────────────────────────────────────────────
        if health.needs_compaction:
            op_start = datetime.now(UTC)
            if not wait_for_capacity(workgroup, max_wait_seconds=30):
                # Explicit timeout — workgroup saturated, skip this operation safely
                reason = f"SKIP_BACKPRESSURE_TIMEOUT (workgroup={workgroup})"
                log.warning("hk_engine.backpressure_timeout",
                            table_fqn=fqn, workgroup=workgroup, operation="compaction")
                self._write_log(
                    table_row, "compaction", "SKIPPED",
                    skip_reason=reason,
                    started_at=op_start, completed_at=datetime.now(UTC),
                    effective_dry_run=table_dry_run,
                )
            else:
                try:
                    compaction_result = compaction.run_compaction(
                        table_fqn=fqn, hk_config=hk_config,
                        health=health, tier=tier, dry_run=table_dry_run,
                    )
                    op_status = "DRY_RUN" if table_dry_run else "SUCCESS"
                except Exception as e:
                    op_errors.append(f"compaction: {e}")
                    compaction_result = {}
                    op_status = "FAILURE"
                    log.error("hk_engine.compaction_error", table_fqn=fqn, error=str(e))

                self._write_log(
                    table_row, "compaction", op_status,
                    started_at=op_start,
                    completed_at=datetime.now(UTC),
                    files_compacted=compaction_result.get("files_compacted"),
                    bytes_rewritten=compaction_result.get("bytes_rewritten"),
                    athena_query_id=compaction_result.get("athena_query_id"),
                    bytes_scanned=compaction_result.get("bytes_scanned", 0),
                    error_message=op_errors[-1] if op_status == "FAILURE" else None,
                    effective_dry_run=table_dry_run,
                )
                total_bytes_scanned += compaction_result.get("bytes_scanned", 0)

        # ── Snapshot expiry ───────────────────────────────────────────────────
        if health.needs_vacuum:
            op_start = datetime.now(UTC)
            if not wait_for_capacity(workgroup, max_wait_seconds=30):
                reason = f"SKIP_BACKPRESSURE_TIMEOUT (workgroup={workgroup})"
                log.warning("hk_engine.backpressure_timeout",
                            table_fqn=fqn, workgroup=workgroup, operation="vacuum")
                self._write_log(
                    table_row, "vacuum", "SKIPPED",
                    skip_reason=reason,
                    started_at=op_start, completed_at=datetime.now(UTC),
                    effective_dry_run=table_dry_run,
                )
            else:
                try:
                    vacuum_result = vacuum.run_expire_snapshots(
                        table_fqn=fqn, hk_config=hk_config,
                        health=health, tier=tier, dry_run=table_dry_run,
                    )
                    op_status = "DRY_RUN" if table_dry_run else "SUCCESS"
                except Exception as e:
                    op_errors.append(f"vacuum: {e}")
                    vacuum_result = {}
                    op_status = "FAILURE"
                    log.error("hk_engine.vacuum_error", table_fqn=fqn, error=str(e))

                self._write_log(
                    table_row, "vacuum", op_status,
                    started_at=op_start,
                    completed_at=datetime.now(UTC),
                    snapshots_before=health.snapshot_count,
                    athena_query_id=vacuum_result.get("athena_query_id"),
                    bytes_scanned=vacuum_result.get("bytes_scanned", 0),
                    error_message=op_errors[-1] if op_status == "FAILURE" else None,
                    effective_dry_run=table_dry_run,
                )
                total_bytes_scanned += vacuum_result.get("bytes_scanned", 0)

        # ── Orphan cleanup ────────────────────────────────────────────────────
        if health.needs_orphan_cleanup:
            op_start = datetime.now(UTC)
            if not wait_for_capacity(workgroup, max_wait_seconds=30):
                reason = f"SKIP_BACKPRESSURE_TIMEOUT (workgroup={workgroup})"
                log.warning("hk_engine.backpressure_timeout",
                            table_fqn=fqn, workgroup=workgroup, operation="orphan_cleanup")
                self._write_log(
                    table_row, "orphan_cleanup", "SKIPPED",
                    skip_reason=reason,
                    started_at=op_start, completed_at=datetime.now(UTC),
                    effective_dry_run=table_dry_run,
                )
            else:
                try:
                    orphan_result = vacuum.run_orphan_cleanup(
                        table_fqn=fqn, hk_config=hk_config,
                        tier=tier, dry_run=table_dry_run,
                    )
                    op_status = "DRY_RUN" if table_dry_run else "SUCCESS"
                except Exception as e:
                    op_errors.append(f"orphan_cleanup: {e}")
                    orphan_result = {}
                    op_status = "FAILURE"
                    log.error("hk_engine.orphan_error", table_fqn=fqn, error=str(e))

                self._write_log(
                    table_row, "orphan_cleanup", op_status,
                    started_at=op_start,
                    completed_at=datetime.now(UTC),
                    orphan_files_deleted=orphan_result.get("orphan_files_deleted"),
                    athena_query_id=orphan_result.get("athena_query_id"),
                    bytes_scanned=orphan_result.get("bytes_scanned", 0),
                    error_message=op_errors[-1] if op_status == "FAILURE" else None,
                    effective_dry_run=table_dry_run,
                )
                total_bytes_scanned += orphan_result.get("bytes_scanned", 0)

        # ── Summary hk_run record ─────────────────────────────────────────────
        completed_at = datetime.now(UTC)
        status       = "FAILURE" if op_errors else ("DRY_RUN" if table_dry_run else "SUCCESS")

        self._write_log(
            table_row=table_row,
            operation="hk_run",
            status=status,
            error_message="; ".join(op_errors) if op_errors else None,
            started_at=started_at,
            completed_at=completed_at,
            snapshots_before=health.snapshot_count,
            files_compacted=compaction_result.get("files_compacted"),
            bytes_rewritten=compaction_result.get("bytes_rewritten"),
            athena_query_id=(
                compaction_result.get("athena_query_id")
                or vacuum_result.get("athena_query_id")
            ),
            bytes_scanned=total_bytes_scanned,
            effective_dry_run=table_dry_run,
        )

        # ── B.5 — Mark execution complete on success ─────────────────────────
        if not op_errors and not table_dry_run:
            mark_executed(execution_id, fqn, dry_run=False)

        # ── Circuit breaker trip check ────────────────────────────────────────
        if op_errors and not table_dry_run:
            failure_count = execution_log.get_failure_count(fqn)
            if circuit_breaker.should_trip(failure_count):
                circuit_breaker.trip(fqn, failure_count, dry_run=self.dry_run)

        return "failed" if op_errors else "succeeded"

    # ── run_frequency + dedupe (H1 + H2) ─────────────────────────────────────

    def _is_due(self, table_fqn: str, hk_config: dict) -> tuple[bool, str]:
        """
        Check if a table is due for HK based on run_frequency.
        Also dedupes: skips if a successful run just happened recently.

        Returns: (is_due, skip_reason)
        """
        freq = hk_config.get("run_frequency", "daily")

        if freq == "every_trigger":
            return True, ""

        threshold_hours = _FREQUENCY_HOURS.get(freq, 20)

        try:
            last = execution_log.get_last_run(
                table_fqn,
                operation="hk_run",
                only_success=True,
            )
        except Exception:
            return True, ""  # Can't check — allow run

        if not last:
            return True, ""  # Never run — always due

        last_completed = last.get("completed_at")
        if not last_completed:
            return True, ""

        # Parse timestamp
        if isinstance(last_completed, str):
            try:
                from dateutil import parser as dtparser
                last_completed = dtparser.parse(last_completed)
            except Exception:
                return True, ""

        # Make timezone-aware
        if last_completed.tzinfo is None:
            from pytz import utc
            last_completed = utc.localize(last_completed)

        hours_since = (
            datetime.now(UTC) - last_completed
        ).total_seconds() / 3600

        if hours_since < threshold_hours:
            reason = (
                f"SKIP_NOT_DUE ({freq} — last run "
                f"{hours_since:.1f}h ago, threshold {threshold_hours}h)"
            )
            log.info(
                "hk_engine.skip_not_due",
                table_fqn=table_fqn,
                freq=freq,
                hours_since=round(hours_since, 1),
                threshold=threshold_hours,
            )
            return False, reason

        return True, ""

    # ── Execution log helper ──────────────────────────────────────────────────

    def _write_log(
        self,
        table_row: dict,
        operation: str,
        status: str,
        skip_reason: str | None = None,
        error_message: str | None = None,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
        effective_dry_run: bool | None = None,
        **metrics,
    ) -> None:
        """
        Write a log entry.

        Routing (Gap 1):
          - EXECUTION_LOG_MODE = insert          -> execution_log.write() directly
          - EXECUTION_LOG_MODE = parquet/both/auto -> self._log_buffer.append()
            Buffer is flushed once at end of run() — no duplicate writes.

        effective_dry_run is the per-table dry_run flag (global OR ramp-up).
        When None it falls back to engine-level self.dry_run (v2 A.2).
        """
        eff_dry = effective_dry_run if effective_dry_run is not None else self.dry_run
        entry = LogEntry(
            run_id=self.run_id,
            engine="hk",
            operation=operation,
            table_fqn=table_row.get("table_fqn", ""),
            stream_id=table_row.get("stream_id"),
            domain=table_row.get("domain", ""),
            layer=table_row.get("layer", ""),
            tier=table_row.get("tier", "standard"),
            environment=table_row.get("environment", "prod"),
            status=status,
            dry_run=eff_dry,
            skip_reason=skip_reason,
            error_message=error_message,
            started_at=started_at,
            completed_at=completed_at,
            **metrics,
        )
        mode = (EXECUTION_LOG_MODE or "auto").lower()
        if mode == "insert":
            # Legacy path — write immediately via Athena INSERT
            execution_log.write(entry, dry_run=eff_dry)
        else:
            # Parquet/both/auto — buffer during run, flush once at end
            log_buf = getattr(self, "_log_buffer", None)
            if log_buf is not None:
                log_buf.append(entry)
            else:
                # Fallback: no buffer on instance (e.g. called outside run())
                execution_log.write(entry, dry_run=eff_dry)

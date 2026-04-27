"""
Zamboni — HK Engine
The core housekeeping engine. Processes all enabled Iceberg tables:
  1. Gate 1  — upstream batch completion check
  2. Window  — safe window evaluation
  3. Health  — assess what operations are needed
  4. Circuit — skip if circuit breaker is open
  5. Compact — bin-pack (Athena) or sort/zorder (Glue)
  6. Vacuum  — expire snapshots + orphan file cleanup
  7. Log     — record every outcome to execution_log
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from config.settings import MAX_CONCURRENT_HK_TABLES
from engine.core import (
    circuit_breaker,
    execution_log,
    health_checker,
    notifier,
    registry,
)
from engine.core.config import get_hk_config
from engine.core.execution_log import LogEntry
from engine.core.health_checker import is_healthy
from engine.core.window_evaluator import evaluate, EXECUTE
from engine.engines.base import BaseEngine
from engine.operations import compaction, vacuum
from engine.utils.glue_client import is_upstream_job_complete
from engine.monitoring.metrics import publish_engine_run
from engine.utils.logger import get_logger

log = get_logger(__name__)


class HKEngine(BaseEngine):
    """
    HK Engine — automated compaction, snapshot expiry, orphan file cleanup.
    Triggered post-batch via Control-M or EventBridge.
    """

    def run(
        self,
        domain: Optional[str] = None,
        layer: Optional[str] = None,
        tier: Optional[str] = None,
        table_fqn: Optional[str] = None,
        environment: str = "prod",
    ) -> dict:
        """
        Run HK Engine.

        Scope options (mutually exclusive — use one):
          - table_fqn   : process a single table
          - domain+layer: process all tables in a domain/layer
          - domain      : process all tables in a domain
          - (none)      : process all enabled tables

        Args:
            domain:      Filter by domain
            layer:       Filter by layer
            tier:        Filter by tier
            table_fqn:   Process a single specific table
            environment: Target environment (default: prod)

        Returns:
            Run summary dict
        """
        self._log_start(
            scope=table_fqn or domain or "all",
            environment=environment,
            domain=domain,
            layer=layer,
            tier=tier,
        )

        # ── Fetch tables to process ───────────────────────────────────────────
        if table_fqn:
            row = registry.get_table(table_fqn)
            tables = [row] if row else []
        else:
            tables = registry.get_enabled_tables(
                environment=environment,
                domain=domain,
                tier=tier,
                layer=layer,
            )

        log.info("hk_engine.tables_fetched", count=len(tables), run_id=self.run_id)

        # ── Process each table ────────────────────────────────────────────────
        succeeded    = 0
        failed       = 0
        skipped      = 0
        failed_tables= []

        max_workers = max(1, min(MAX_CONCURRENT_HK_TABLES, len(tables) or 1))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(self._process_table, table_row): table_row
                for table_row in tables
            }

            for future in as_completed(futures):
                table_row = futures[future]
                fqn = table_row["table_fqn"]
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

        # ── Summary alert if failures ─────────────────────────────────────────
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
        publish_engine_run(
            engine='hk', run_id=self.run_id,
            succeeded=succeeded, failed=failed, skipped=skipped,
            duration_s=result.get('elapsed_seconds', 0),
            dry_run=self.dry_run,
        )
        return result

    # ── Single table processing ───────────────────────────────────────────────

    def _process_table(self, table_row: dict) -> str:
        """
        Process a single table through all HK gates and operations.
        Returns: 'succeeded' | 'skipped' | 'failed'
        """
        fqn  = table_row["table_fqn"]
        tier = table_row.get("tier", "standard")

        log.info("hk_engine.processing", table_fqn=fqn, tier=tier, run_id=self.run_id)

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
                self._write_log(
                    table_row, "hk_run", "SKIPPED",
                    skip_reason="SKIP_UPSTREAM_PENDING"
                )
                return "skipped"

        # ── Gate 2 — Safe window check ────────────────────────────────────────
        window_json = hk_config.get("window_config", "")
        force       = table_row.get("force_run", False)
        decision    = evaluate(window_json, force=force)

        if decision != EXECUTE:
            self._log_table_skip(fqn, decision)
            self._write_log(table_row, "hk_run", "SKIPPED", skip_reason=decision)
            return "skipped"

        frequency_skip = self._frequency_skip_reason(fqn, hk_config, force=force)
        if frequency_skip:
            self._log_table_skip(fqn, frequency_skip)
            self._write_log(table_row, "hk_run", "SKIPPED", skip_reason=frequency_skip)
            return "skipped"

        # ── Gate 3 — Circuit breaker ──────────────────────────────────────────
        cb_state = circuit_breaker.check(fqn)
        if cb_state == circuit_breaker.OPEN:
            self._log_table_skip(fqn, "SKIP_CIRCUIT_OPEN")
            self._write_log(
                table_row, "hk_run", "SKIPPED",
                skip_reason="SKIP_CIRCUIT_OPEN"
            )
            return "skipped"

        # ── Health check ──────────────────────────────────────────────────────
        workgroup = tier if tier in ("critical", "standard", "low") else "standard"
        health    = health_checker.check(fqn, hk_config, workgroup=workgroup)

        if not health.check_success:
            self._write_log(
                table_row, "hk_run", "FAILURE",
                error_message=f"Health check failed: {health.check_error}"
            )
            return "failed"

        if is_healthy(health):
            self._log_table_skip(fqn, "SKIP_HEALTHY")
            self._write_log(table_row, "hk_run", "SKIPPED", skip_reason="SKIP_HEALTHY")
            return "skipped"

        # ── Operations ────────────────────────────────────────────────────────
        started_at = datetime.now(timezone.utc)
        op_errors  = []
        effective_dry_run = self._effective_dry_run(table_row)

        compaction_result = {}
        vacuum_result     = {}
        orphan_result     = {}

        # Compaction
        if health.needs_compaction:
            try:
                compaction_result = compaction.run_compaction(
                    table_fqn=fqn,
                    hk_config=hk_config,
                    health=health,
                    tier=tier,
                    dry_run=effective_dry_run,
                )
                self._write_operation_log(
                    table_row, "compaction", compaction_result, health,
                    effective_dry_run=effective_dry_run,
                )
            except Exception as e:
                op_errors.append(f"compaction: {e}")
                log.error("hk_engine.compaction_error", table_fqn=fqn, error=str(e))
                self._write_operation_log(
                    table_row, "compaction", {}, health,
                    error_message=str(e), effective_dry_run=effective_dry_run,
                )

        # Snapshot expiry
        if health.needs_vacuum:
            try:
                vacuum_result = vacuum.run_expire_snapshots(
                    table_fqn=fqn,
                    hk_config=hk_config,
                    health=health,
                    tier=tier,
                    dry_run=effective_dry_run,
                )
                self._write_operation_log(
                    table_row, "vacuum", vacuum_result, health,
                    effective_dry_run=effective_dry_run,
                )
            except Exception as e:
                op_errors.append(f"vacuum: {e}")
                log.error("hk_engine.vacuum_error", table_fqn=fqn, error=str(e))
                self._write_operation_log(
                    table_row, "vacuum", {}, health,
                    error_message=str(e), effective_dry_run=effective_dry_run,
                )

        # Orphan cleanup
        if health.needs_orphan_cleanup:
            try:
                orphan_result = vacuum.run_orphan_cleanup(
                    table_fqn=fqn,
                    hk_config=hk_config,
                    tier=tier,
                    dry_run=effective_dry_run,
                )
                self._write_operation_log(
                    table_row, "orphan_cleanup", orphan_result, health,
                    effective_dry_run=effective_dry_run,
                )
            except Exception as e:
                op_errors.append(f"orphan_cleanup: {e}")
                log.error("hk_engine.orphan_error", table_fqn=fqn, error=str(e))
                self._write_operation_log(
                    table_row, "orphan_cleanup", {}, health,
                    error_message=str(e), effective_dry_run=effective_dry_run,
                )

        completed_at = datetime.now(timezone.utc)
        status = "FAILURE" if op_errors else ("DRY_RUN" if effective_dry_run else "SUCCESS")

        # ── Write execution log ───────────────────────────────────────────────
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
            bytes_scanned=(
                compaction_result.get("bytes_scanned", 0)
                + vacuum_result.get("bytes_scanned", 0)
                + orphan_result.get("bytes_scanned", 0)
            ),
            effective_dry_run=effective_dry_run,
        )

        # ── Circuit breaker trip check ────────────────────────────────────────
        if op_errors and not self.dry_run:
            failure_count = execution_log.get_failure_count(fqn)
            if circuit_breaker.should_trip(failure_count):
                circuit_breaker.trip(fqn, failure_count, dry_run=self.dry_run)

        return "failed" if op_errors else "succeeded"

    def _frequency_skip_reason(
        self,
        table_fqn: str,
        hk_config: dict,
        force: bool = False,
    ) -> Optional[str]:
        """Return a skip reason when run_frequency says the table is not due."""
        if force:
            return None

        frequency = (hk_config.get("run_frequency") or "every_trigger").lower()
        if frequency == "every_trigger":
            return None

        last_run = execution_log.get_last_run(table_fqn)
        if not last_run or last_run.get("status") not in ("SUCCESS", "DRY_RUN"):
            return None

        last_started = self._parse_dt(last_run.get("completed_at") or last_run.get("started_at"))
        if not last_started:
            return None

        now = datetime.now(timezone.utc)
        if frequency == "daily" and last_started.date() == now.date():
            return "SKIP_NOT_DUE"
        if frequency == "weekly" and now - last_started < timedelta(days=7):
            return "SKIP_NOT_DUE"
        return None

    def _effective_dry_run(self, table_row: dict) -> bool:
        """Global dry run or table ramp-up dry_run_until both block data changes."""
        if self.dry_run:
            return True

        dry_run_until = table_row.get("dry_run_until")
        if not dry_run_until:
            return False

        if isinstance(dry_run_until, datetime):
            until = dry_run_until.date()
        elif isinstance(dry_run_until, date):
            until = dry_run_until
        else:
            try:
                until = date.fromisoformat(str(dry_run_until)[:10])
            except ValueError:
                return False
        return until >= datetime.now(timezone.utc).date()

    def _parse_dt(self, value) -> Optional[datetime]:
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            return None

    def _write_operation_log(
        self,
        table_row: dict,
        operation: str,
        result: dict,
        health,
        error_message: Optional[str] = None,
        effective_dry_run: bool = False,
    ) -> None:
        status = "FAILURE" if error_message else ("DRY_RUN" if effective_dry_run else "SUCCESS")
        self._write_log(
            table_row=table_row,
            operation=operation,
            status=status,
            error_message=error_message,
            snapshots_before=getattr(health, "snapshot_count", None),
            snapshots_expired=result.get("snapshots_expired"),
            orphan_files_deleted=result.get("orphan_files_deleted"),
            files_compacted=result.get("files_compacted"),
            bytes_rewritten=result.get("bytes_rewritten"),
            athena_query_id=result.get("athena_query_id"),
            bytes_scanned=result.get("bytes_scanned"),
            effective_dry_run=effective_dry_run,
        )

    # ── Execution log helper ──────────────────────────────────────────────────

    def _write_log(
        self,
        table_row: dict,
        operation: str,
        status: str,
        skip_reason: Optional[str] = None,
        error_message: Optional[str] = None,
        started_at: Optional[datetime] = None,
        completed_at: Optional[datetime] = None,
        effective_dry_run: Optional[bool] = None,
        **metrics,
    ) -> None:
        entry_dry_run = self.dry_run if effective_dry_run is None else effective_dry_run
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
            dry_run=entry_dry_run,
            skip_reason=skip_reason,
            error_message=error_message,
            started_at=started_at,
            completed_at=completed_at,
            **metrics,
        )
        execution_log.write(entry, dry_run=self.dry_run)

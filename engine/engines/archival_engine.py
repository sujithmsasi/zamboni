"""
Zamboni — Archival Engine
Exports cold staging partitions to S3 Intelligent-Tiering then deletes from staging.
Runs weekly via Control-M (Sunday 04:00 UTC).

Flow per table:
  1. Read archive config from stream_registry
  2. Discover cold partitions (older than archive_retention_days)
  3. For each cold partition — archive_partition() with 4-step gate
  4. Log every partition outcome to execution_log
  5. Alert on failures
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

from config.settings import MAX_CONCURRENT_PARTITIONS
from engine.core import execution_log, notifier, registry
from engine.core.execution_log import LogEntry
from engine.engines.base import BaseEngine
from engine.operations.archival import (
    _resolve_partition_column,
    archive_partition,
    discover_cold_partitions,
)
from engine.utils.logger import get_logger

log = get_logger(__name__)


class ArchivalEngine(BaseEngine):
    """
    Archival Engine — Export-then-Delete for cold staging partitions.
    Runs weekly. Only processes tables with archive_enabled=true, layer=staging.
    """

    def run(
        self,
        domain: str | None = None,
        environment: str = "prod",
    ) -> dict:
        """
        Run Archival Engine.

        Args:
            domain:      Limit to a specific domain (None = all archivable domains)
            environment: Target environment (default: prod)

        Returns:
            Run summary dict
        """
        self._log_start(scope=domain or "all", environment=environment)

        # ── Fetch archivable tables ───────────────────────────────────────────
        tables = registry.get_archivable_tables(domain=domain)
        log.info(
            "archival_engine.tables_fetched",
            count=len(tables),
            run_id=self.run_id,
        )

        total_partitions = 0
        succeeded        = 0
        failed           = 0
        skipped          = 0
        failed_tables    = []

        # ── Process each table ────────────────────────────────────────────────
        for table_row in tables:
            fqn = table_row["table_fqn"]
            try:
                table_result = self._process_table(table_row)
                total_partitions += table_result["partitions_found"]
                succeeded        += table_result["succeeded"]
                failed           += table_result["failed"]
                skipped          += table_result["skipped"]
                if table_result["failed"] > 0:
                    failed_tables.append(fqn)
            except Exception as e:
                self._log_table_error(fqn, e)
                failed += 1
                failed_tables.append(fqn)

        # ── Summary alert if failures ─────────────────────────────────────────
        if failed_tables and not self.dry_run:
            notifier.send_engine_failure_summary(
                engine="archival",
                run_id=self.run_id,
                failed_tables=failed_tables,
            )

        result = self.summary_dict(
            tables_processed=len(tables),
            succeeded=succeeded,
            failed=failed,
            skipped=skipped,
            total_partitions=total_partitions,
        )
        self._log_complete(result)
        return result

    # ── Single table processing ───────────────────────────────────────────────

    def _process_table(self, table_row: dict) -> dict:
        """
        Discover and archive all cold partitions for one table.
        Runs partitions concurrently up to MAX_CONCURRENT_PARTITIONS.
        """
        fqn             = table_row["table_fqn"]
        retention_days  = table_row.get("archive_retention_days") or 30
        table_row.get("tier", "standard")
        workgroup       = "archival"

        # Resolve partition column
        try:
            partition_col = _resolve_partition_column(table_row, fqn, workgroup)
        except ValueError as e:
            log.error("archival_engine.no_partition_col", table_fqn=fqn, error=str(e))
            return {"partitions_found": 0, "succeeded": 0, "failed": 1, "skipped": 0}

        # Discover cold partitions
        cold_partitions = discover_cold_partitions(
            table_fqn=fqn,
            partition_col=partition_col,
            retention_days=retention_days,
            workgroup=workgroup,
        )

        if not cold_partitions:
            log.info("archival_engine.no_cold_partitions", table_fqn=fqn)
            return {"partitions_found": 0, "succeeded": 0, "failed": 0, "skipped": 0}

        log.info(
            "archival_engine.cold_partitions_found",
            table_fqn=fqn,
            count=len(cold_partitions),
            oldest=str(cold_partitions[0]),
            newest=str(cold_partitions[-1]),
        )

        succeeded = 0
        failed    = 0
        skipped   = 0

        # ── Concurrent partition archival ─────────────────────────────────────
        with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_PARTITIONS) as executor:
            futures = {
                executor.submit(
                    self._archive_one_partition,
                    table_row,
                    partition_date,
                    workgroup,
                ): partition_date
                for partition_date in cold_partitions
            }

            for future in as_completed(futures):
                partition_date = futures[future]
                try:
                    arch_result = future.result()
                    status      = arch_result.get("status", "FAILURE")

                    if status in ("SUCCESS", "DRY_RUN"):
                        succeeded += 1
                    elif status == "SKIPPED":
                        skipped += 1
                    else:
                        failed += 1

                    # Write to execution log
                    self._write_log(
                        table_row=table_row,
                        arch_result=arch_result,
                        partition_date=partition_date,
                    )

                except Exception as e:
                    failed += 1
                    log.error(
                        "archival_engine.partition_error",
                        table_fqn=fqn,
                        partition_date=str(partition_date),
                        error=str(e),
                    )

        return {
            "partitions_found": len(cold_partitions),
            "succeeded":        succeeded,
            "failed":           failed,
            "skipped":          skipped,
        }

    def _archive_one_partition(
        self,
        table_row: dict,
        partition_date,
        workgroup: str,
    ) -> dict:
        """Archive a single partition — called in thread pool."""
        return archive_partition(
            table_row=table_row,
            partition_date=partition_date,
            workgroup=workgroup,
            dry_run=self.dry_run,
        )

    # ── Execution log ─────────────────────────────────────────────────────────

    def _write_log(
        self,
        table_row: dict,
        arch_result: dict,
        partition_date,
    ) -> None:
        status = arch_result.get("status", "FAILURE")
        if status == "DRY_RUN":
            log_status = "DRY_RUN"
        elif status == "SUCCESS":
            log_status = "SUCCESS"
        else:
            log_status = "FAILURE"

        entry = LogEntry(
            run_id=self.run_id,
            engine="archival",
            operation="archival",
            table_fqn=table_row.get("table_fqn", ""),
            stream_id=table_row.get("stream_id"),
            domain=table_row.get("domain", ""),
            layer=table_row.get("layer", "staging"),
            tier=table_row.get("tier", "standard"),
            environment=table_row.get("environment", "prod"),
            status=log_status,
            dry_run=self.dry_run,
            error_message=arch_result.get("error"),
            partition_date=partition_date,
            rows_archived=arch_result.get("rows_archived", 0),
            bytes_archived=arch_result.get("bytes_archived", 0),
            archive_s3_path=arch_result.get("archive_s3_path"),
            pre_validation=arch_result.get("pre_validation"),
            post_validation=arch_result.get("post_validation"),
        )
        execution_log.write(entry, dry_run=self.dry_run)

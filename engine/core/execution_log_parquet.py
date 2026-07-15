"""
Zamboni — Execution Log: Batch Parquet Writer
v2 Design D.10–D.12 — write execution_log records to S3 as a single Parquet
file per engine run, then register into the Iceberg log table via add_files.
Falls back to per-row Athena INSERT if Parquet/add_files path fails or is
disabled via EXECUTION_LOG_MODE.

EXECUTION_LOG_MODE values:
  parquet  — Parquet + add_files only (raises on failure)
  insert   — Per-row Athena INSERT only (legacy, slow)
  both     — Try Parquet first, fall back to Athena INSERT
  auto     — (default) Use Parquet if available, else INSERT

This module is independent of execution_log.write() — engines call into a
ParquetLogBuffer instance, populate it during the run, and flush at end.
"""
import io
from datetime import UTC, datetime

from config.settings import (
    AWS_REGION,
    EXECUTION_LOG_MODE,
    EXECUTION_LOG_TABLE,
    ZAMBONI_METADATA_BUCKET,
    get_boto3_session,
)
from engine.core.execution_log import LogEntry
from engine.utils.logger import get_logger

log = get_logger(__name__)


class ParquetLogBuffer:
    """
    Buffers LogEntry records during an engine run, flushes once to S3 as a
    single Parquet file, then registers via Iceberg add_files.

    Usage:
        buffer = ParquetLogBuffer(run_id="hk-2026-04-27")
        buffer.append(log_entry_1)
        buffer.append(log_entry_2)
        buffer.flush()   # writes Parquet + calls add_files
    """

    def __init__(self, run_id: str, engine: str = "hk"):
        self.run_id  = run_id
        self.engine  = engine
        self.entries: list[LogEntry] = []
        self.flushed = False

    def append(self, entry: LogEntry) -> None:
        self.entries.append(entry)

    def flush(self, dry_run: bool = False) -> dict:
        """
        Write buffered entries to S3 + register into Iceberg.
        Returns a result dict with {mode, rows_written, s3_path, error}.
        Never raises — falls back gracefully and logs.
        """
        if self.flushed:
            return {"mode": "noop", "rows_written": 0,
                    "s3_path": None, "error": "already flushed"}

        if not self.entries:
            self.flushed = True
            return {"mode": "noop", "rows_written": 0,
                    "s3_path": None, "error": None}

        mode = (EXECUTION_LOG_MODE or "auto").lower()
        result: dict = {"mode": mode, "rows_written": 0,
                        "s3_path": None, "error": None}

        # ── Mode: insert only ─────────────────────────────────────────────────
        if mode == "insert":
            return self._write_via_insert(dry_run=dry_run)

        # ── Mode: parquet | both | auto ─────────────────────────────────────
        try:
            result = self._write_via_parquet(dry_run=dry_run)
            self.flushed = True
            return result
        except Exception as e:
            log.warning(
                "execution_log_parquet.flush_failed",
                run_id=self.run_id,
                rows=len(self.entries),
                error=str(e),
            )
            if mode == "parquet":
                # Strict mode — propagate
                self.flushed = True
                result["error"] = str(e)
                return result
            # auto / both → fall back to Athena INSERT
            log.info("execution_log_parquet.falling_back_to_insert",
                     run_id=self.run_id)
            return self._write_via_insert(dry_run=dry_run)

    # ── Parquet + add_files path ─────────────────────────────────────────────

    def _write_via_parquet(self, dry_run: bool = False) -> dict:
        """Write records as Parquet to S3 then register via add_files."""
        s3_path = self._build_s3_path()

        if dry_run:
            log.info(
                "execution_log_parquet.dry_run",
                run_id=self.run_id,
                rows=len(self.entries),
                s3_path=s3_path,
            )
            self.flushed = True
            return {"mode": "parquet_dry_run",
                    "rows_written": len(self.entries),
                    "s3_path": s3_path, "error": None}

        # Build Parquet file from LogEntries
        try:
            import pandas as pd
        except ImportError:
            raise RuntimeError("pandas not installed — required for Parquet mode")

        records = [self._entry_to_dict(e) for e in self.entries]
        df = pd.DataFrame(records)

        # Write to in-memory buffer
        buf = io.BytesIO()
        df.to_parquet(buf, engine="pyarrow", compression="snappy", index=False)
        buf.seek(0)

        # Upload to S3
        s3 = get_boto3_session().client("s3", region_name=AWS_REGION)
        bucket, key = self._parse_s3_path(s3_path)
        s3.put_object(Bucket=bucket, Key=key, Body=buf.getvalue())

        log.info(
            "execution_log_parquet.parquet_written",
            run_id=self.run_id, s3_path=s3_path, rows=len(records),
        )

        # Register into Iceberg via add_files
        self._register_add_files(s3_path)

        self.flushed = True
        return {"mode": "parquet",
                "rows_written": len(records),
                "s3_path": s3_path, "error": None}

    def _register_add_files(self, s3_path: str) -> None:
        """
        Call Iceberg's add_files procedure to register the Parquet into the
        execution_log table without a slow INSERT.
        Uses Athena's CALL syntax: CALL system.add_files(...).
        """
        from engine.utils.athena_client import run_query

        # Athena Iceberg add_files procedure call (v2 design)
        sql = f"""
            CALL system.add_files(
                table => '{EXECUTION_LOG_TABLE}',
                source => '{s3_path}'
            )
        """
        try:
            run_query(sql, workgroup="app")
            log.info("execution_log_parquet.add_files_registered",
                     s3_path=s3_path, table=EXECUTION_LOG_TABLE)
        except Exception as e:
            # add_files failure is recoverable — Parquet is on S3
            log.warning(
                "execution_log_parquet.add_files_failed",
                error=str(e), s3_path=s3_path,
            )
            raise

    # ── Athena INSERT fallback ────────────────────────────────────────────────

    def _write_via_insert(self, dry_run: bool = False) -> dict:
        """
        Athena INSERT fallback — one multi-row INSERT for the whole buffer
        instead of one INSERT per entry (2026-07-09 audit: this path did a
        real per-table Athena round trip on every HK run whenever
        EXECUTION_LOG_MODE=insert, or as the auto/both fallback whenever the
        Parquet path failed). Falls back to the old one-row-at-a-time loop
        only if the batched INSERT itself fails, so one malformed entry
        can't silently drop every other table's log row for the run.
        """
        from engine.core import execution_log
        try:
            rows_written = execution_log.write_many(self.entries, dry_run=dry_run)
            self.flushed = True
            return {"mode": "insert", "rows_written": rows_written,
                    "s3_path": None, "error": None}
        except Exception as e:
            log.warning(
                "execution_log_parquet.batched_insert_failed_falling_back_per_row",
                error=str(e), rows=len(self.entries),
            )

        rows_written = 0
        for entry in self.entries:
            try:
                execution_log.write(entry, dry_run=dry_run)
                rows_written += 1
            except Exception as e:
                log.error(
                    "execution_log_parquet.insert_fallback_error",
                    error=str(e),
                    table_fqn=entry.table_fqn,
                )
        self.flushed = True
        return {"mode": "insert", "rows_written": rows_written,
                "s3_path": None, "error": None}

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _build_s3_path(self) -> str:
        """Build S3 path: <metadata_bucket>/execution_log/_pending/<run_id>.parquet."""
        ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        bucket = ZAMBONI_METADATA_BUCKET.rstrip("/")
        return f"{bucket}/execution_log/_pending/{self.engine}_{self.run_id}_{ts}.parquet"

    def _parse_s3_path(self, s3_path: str) -> tuple[str, str]:
        assert s3_path.startswith("s3://"), f"Not an S3 URI: {s3_path}"
        rest = s3_path[5:]
        bucket, _, key = rest.partition("/")
        return bucket, key

    def _entry_to_dict(self, entry: LogEntry) -> dict:
        """
        Serialize LogEntry to a dict matching execution_log Iceberg schema.
        All DDL columns are present with safe defaults to avoid schema mismatch
        on add_files registration. Null/missing LogEntry fields default to None.
        """
        now_utc = datetime.now(UTC)
        def g(attr, default=None):
            return getattr(entry, attr, default)

        # Compute duration_seconds if both timestamps present
        started   = g("started_at")
        completed = g("completed_at")
        duration_s = None
        if started and completed:
            try:
                duration_s = (completed - started).total_seconds()
            except Exception:
                pass

        return {
            # ── Identity ─────────────────────────────────────────
            "execution_id":       g("execution_id", entry.run_id),
            "run_id":             entry.run_id,
            "engine":             entry.engine,
            "operation":          entry.operation,
            "table_fqn":          entry.table_fqn,
            "domain":             entry.domain,
            "layer":              entry.layer,
            "tier":               entry.tier,
            "environment":        entry.environment,
            # ── Status ───────────────────────────────────────────
            "status":             entry.status,
            "dry_run":            bool(g("dry_run", False)),
            "skip_reason":        g("skip_reason"),
            "error_message":      g("error_message"),
            # ── Timing ───────────────────────────────────────────
            "started_at":         started,
            "completed_at":       completed or now_utc,
            "duration_seconds":   duration_s,
            # ── Snapshot metrics ─────────────────────────────────
            "snapshots_before":   g("snapshots_before"),
            "snapshots_after":    g("snapshots_after"),
            "snapshots_expired":  g("snapshots_expired"),
            # ── Compaction metrics ───────────────────────────────
            "files_compacted":    g("files_compacted"),
            "bytes_rewritten":    g("bytes_rewritten"),
            # ── Archival metrics ─────────────────────────────────
            "partition_date":     g("partition_date"),
            "rows_archived":      g("rows_archived"),
            "bytes_archived":     g("bytes_archived"),
            "archive_s3_path":    g("archive_s3_path"),
            "pre_validation":     g("pre_validation"),
            "post_validation":    g("post_validation"),
            # ── Orphan metrics ───────────────────────────────────
            "orphan_files_deleted": g("orphan_files_deleted"),
            # ── Athena ───────────────────────────────────────────
            "athena_query_id":    g("athena_query_id"),
            "bytes_scanned":      g("bytes_scanned", 0) or 0,
            # ── Partition ────────────────────────────────────────
            "execution_date":     now_utc.date().isoformat(),
            # ── Safety Core (Phase 1a) ───────────────────────────
            "lock_id":                  g("lock_id"),
            "metadata_location_before": g("metadata_location_before"),
            "metadata_location_after":  g("metadata_location_after"),
            "snapshot_id_before":       g("snapshot_id_before"),
            "snapshot_id_after":        g("snapshot_id_after"),
            "integrity_status":         g("integrity_status", "SKIPPED"),
        }

"""
Zamboni — Lifecycle Engine
Manages the full lifecycle of non-production Iceberg tables.

State Machine:
  ACTIVE
    └── days_since_activity > stale_threshold_days
          └── STALE_CANDIDATE
                └── GREENZONE (send notification, 14-day window)
                      ├── owner exempted → ACTIVE
                      └── expired → PENDING_DROP (48h final notice)
                              ├── owner exempted → ACTIVE
                              └── expired → DROPPED (delete + sweep)

Three Control-M jobs:
  ZAMBONI-NONPROD-SCAN      → discover tables, update activity signals
  ZAMBONI-NONPROD-LIFECYCLE → evaluate states, send notifications
  ZAMBONI-NONPROD-CLEANUP   → execute drops for PENDING_DROP tables
"""
from datetime import datetime, timedelta, timezone, date
from typing import Optional

from config.settings import NONPROD_ENVIRONMENTS, NONPROD_REGISTRY_TABLE
from engine.core import execution_log, notifier
from engine.core.execution_log import LogEntry
from engine.engines.base import BaseEngine
from engine.operations.catalog_cleanup import cleanup_table, is_backup_pattern
from engine.utils.athena_client import read_sql, run_query
from engine.utils.glue_client import get_databases, get_tables, is_iceberg_table
from engine.utils.logger import get_logger

log = get_logger(__name__)

# State constants
ACTIVE          = "ACTIVE"
STALE_CANDIDATE = "STALE_CANDIDATE"
GREENZONE       = "GREENZONE"
PENDING_DROP    = "PENDING_DROP"
DROPPED         = "DROPPED"

# Default thresholds (overridden by domain_registry per domain)
DEFAULT_STALE_DAYS        = 60
DEFAULT_GREENZONE_DAYS    = 14
DEFAULT_PENDING_DROP_DAYS = 2


class LifecycleEngine(BaseEngine):
    """Lifecycle Engine — non-prod table auto-discovery, stale detection, cleanup."""

    # ── Job 1: SCAN ───────────────────────────────────────────────────────────

    def run_scan(self, environment: str = "preprod") -> dict:
        """
        ZAMBONI-NONPROD-SCAN
        Discovers all tables in non-prod Glue databases.
        Updates nonprod_registry with new tables and refreshes activity signals.
        """
        self._log_start(scope="scan", environment=environment)

        discovered = 0
        updated    = 0
        errors     = 0

        databases = get_databases()
        log.info("lifecycle_engine.scan.databases", count=len(databases))

        for database in databases:
            try:
                tables = get_tables(database)
                for table in tables:
                    try:
                        self._upsert_nonprod_registry(table, database, environment)
                        discovered += 1
                    except Exception as e:
                        errors += 1
                        log.error(
                            "lifecycle_engine.scan.table_error",
                            database=database,
                            table=table.get("Name"),
                            error=str(e),
                        )
            except Exception as e:
                errors += 1
                log.error("lifecycle_engine.scan.db_error", database=database, error=str(e))

        result = self.summary_dict(
            tables_processed=discovered,
            succeeded=discovered - errors,
            failed=errors,
            skipped=0,
            discovered=discovered,
        )
        self._log_complete(result)
        return result

    # ── Job 2: LIFECYCLE ──────────────────────────────────────────────────────

    def run(self, environment: str = "preprod") -> dict:
        """
        ZAMBONI-NONPROD-LIFECYCLE
        Evaluates lifecycle states and transitions tables through the state machine.
        Sends GREENZONE and PENDING_DROP notifications.
        """
        self._log_start(scope="lifecycle", environment=environment)

        transitioned = 0
        notified     = 0
        skipped      = 0
        errors       = 0

        tables = self._get_active_registry_tables(environment)
        log.info("lifecycle_engine.lifecycle.tables", count=len(tables))

        for table_row in tables:
            fqn = table_row.get("table_fqn", "")
            try:
                outcome = self._evaluate_table(table_row)
                if outcome == "transitioned":
                    transitioned += 1
                elif outcome == "notified":
                    notified += 1
                elif outcome == "skipped":
                    skipped += 1
            except Exception as e:
                errors += 1
                log.error("lifecycle_engine.lifecycle.error", table_fqn=fqn, error=str(e))

        result = self.summary_dict(
            tables_processed=len(tables),
            succeeded=transitioned + notified,
            failed=errors,
            skipped=skipped,
            transitioned=transitioned,
            notified=notified,
        )
        self._log_complete(result)
        return result

    # ── Job 3: CLEANUP ────────────────────────────────────────────────────────

    def run_cleanup(self, environment: str = "preprod") -> dict:
        """
        ZAMBONI-NONPROD-CLEANUP
        Executes hard deletion for all PENDING_DROP tables whose window has expired.
        """
        self._log_start(scope="cleanup", environment=environment)

        succeeded = 0
        failed    = 0
        skipped   = 0

        tables = self._get_pending_drop_tables(environment)
        log.info("lifecycle_engine.cleanup.tables", count=len(tables))

        for table_row in tables:
            fqn = table_row.get("table_fqn", "")
            try:
                # Check if pending_drop window has expired
                expires_at = table_row.get("pending_drop_expires_at")
                if expires_at and _parse_ts(expires_at) > datetime.now(timezone.utc):
                    skipped += 1
                    log.info("lifecycle_engine.cleanup.not_expired", table_fqn=fqn)
                    continue

                result = cleanup_table(fqn, dry_run=self.dry_run)

                if result.get("catalog_dropped") and result.get("s3_cleaned"):
                    succeeded += 1
                    self._mark_dropped(table_row, result)
                    self._write_log(table_row, "catalog_cleanup", "SUCCESS" if not self.dry_run else "DRY_RUN",
                                    bytes_reclaimed=result.get("bytes_reclaimed", 0))
                else:
                    failed += 1
                    self._write_log(table_row, "catalog_cleanup", "FAILURE",
                                    error_message=result.get("error"))

            except Exception as e:
                failed += 1
                log.error("lifecycle_engine.cleanup.error", table_fqn=fqn, error=str(e))

        result = self.summary_dict(
            tables_processed=len(tables),
            succeeded=succeeded,
            failed=failed,
            skipped=skipped,
        )
        self._log_complete(result)
        return result

    # ── State evaluation ──────────────────────────────────────────────────────

    def _evaluate_table(self, table_row: dict) -> str:
        """
        Evaluate current state and transition if needed.
        Returns: transitioned | notified | skipped
        """
        fqn             = table_row.get("table_fqn", "")
        current_state   = table_row.get("lifecycle_state", ACTIVE)
        days_inactive   = int(table_row.get("days_since_activity") or 0)
        stale_threshold = int(table_row.get("stale_threshold_days") or DEFAULT_STALE_DAYS)
        owner_exempted  = table_row.get("owner_exempted", False)

        # ── ACTIVE → STALE_CANDIDATE ──────────────────────────────────────────
        if current_state == ACTIVE:
            if days_inactive >= stale_threshold:
                self._transition(table_row, ACTIVE, STALE_CANDIDATE)
                return "transitioned"
            return "skipped"

        # ── STALE_CANDIDATE → GREENZONE ───────────────────────────────────────
        if current_state == STALE_CANDIDATE:
            expires_at = datetime.now(timezone.utc) + timedelta(days=DEFAULT_GREENZONE_DAYS)
            self._transition(table_row, STALE_CANDIDATE, GREENZONE,
                             greenzone_expires_at=expires_at)
            # Send notification
            if not self.dry_run:
                notifier.send_greenzone_notification(
                    table_fqn=fqn,
                    owner_email=table_row.get("owner_email", ""),
                    expires_at=expires_at.date(),
                    environment=table_row.get("environment", ""),
                    days_inactive=days_inactive,
                )
            self._write_log(table_row, "lifecycle_transition", "SUCCESS" if not self.dry_run else "DRY_RUN")
            return "notified"

        # ── GREENZONE → PENDING_DROP or back to ACTIVE ────────────────────────
        if current_state == GREENZONE:
            if owner_exempted:
                self._transition(table_row, GREENZONE, ACTIVE)
                return "transitioned"

            expires_at = table_row.get("greenzone_expires_at")
            if expires_at and _parse_ts(expires_at) <= datetime.now(timezone.utc):
                drop_at = datetime.now(timezone.utc) + timedelta(days=DEFAULT_PENDING_DROP_DAYS)
                self._transition(table_row, GREENZONE, PENDING_DROP,
                                 pending_drop_expires_at=drop_at)
                if not self.dry_run:
                    notifier.send_pending_drop_notification(
                        table_fqn=fqn,
                        owner_email=table_row.get("owner_email", ""),
                        drop_at=drop_at.date(),
                        environment=table_row.get("environment", ""),
                    )
                self._write_log(table_row, "lifecycle_transition", "SUCCESS" if not self.dry_run else "DRY_RUN")
                return "notified"
            return "skipped"

        # ── PENDING_DROP → ACTIVE (exempted) ──────────────────────────────────
        if current_state == PENDING_DROP and owner_exempted:
            self._transition(table_row, PENDING_DROP, ACTIVE)
            return "transitioned"

        return "skipped"

    # ── Registry operations ───────────────────────────────────────────────────

    def _upsert_nonprod_registry(self, table: dict, database: str, environment: str) -> None:
        """Insert or update a table in nonprod_registry."""
        name       = table.get("Name", "")
        table_fqn  = f"glue_catalog.{database}.{name}"
        is_iceberg = is_iceberg_table(table)
        fmt        = "iceberg" if is_iceberg else "hive"
        created_at = table.get("CreateTime")
        is_backup, pattern = is_backup_pattern(name)
        now = _now()

        # Check if already registered
        existing = self._get_registry_row(table_fqn)

        if existing:
            # Update scan metadata
            sql = f"""
                UPDATE {NONPROD_REGISTRY_TABLE}
                SET last_scanned_at = TIMESTAMP '{now}',
                    scan_count      = scan_count + 1,
                    updated_at      = TIMESTAMP '{now}'
                WHERE table_fqn = '{table_fqn}'
            """
        else:
            # New table — insert
            created_str = (
                f"TIMESTAMP '{created_at.strftime('%Y-%m-%d %H:%M:%S')}'"
                if created_at else "NULL"
            )
            sql = f"""
                INSERT INTO {NONPROD_REGISTRY_TABLE} VALUES (
                    '{table_fqn}', '{database}', '{_esc(name)}',
                    '{environment}', '{_infer_domain(database)}', '{fmt}',
                    'ACTIVE', NULL, TIMESTAMP '{now}',
                    NULL, NULL, {created_str}, 0,
                    NULL, NULL, false, NULL, NULL,
                    NULL, NULL,
                    NULL, false, false, 0,
                    {str(is_backup).lower()}, '{pattern}',
                    TIMESTAMP '{now}', TIMESTAMP '{now}', 1
                )
            """
        run_query(sql, workgroup="nonprod", dry_run=self.dry_run)

    def _get_registry_row(self, table_fqn: str) -> Optional[dict]:
        sql = f"""
            SELECT * FROM {NONPROD_REGISTRY_TABLE}
            WHERE table_fqn = '{table_fqn}' LIMIT 1
        """
        df = read_sql(sql, workgroup="nonprod")
        return df.iloc[0].to_dict() if not df.empty else None

    def _get_active_registry_tables(self, environment: str) -> list[dict]:
        sql = f"""
            SELECT * FROM {NONPROD_REGISTRY_TABLE}
            WHERE environment   = '{environment}'
              AND lifecycle_state != 'DROPPED'
            ORDER BY lifecycle_state, days_since_activity DESC
        """
        df = read_sql(sql, workgroup="nonprod")
        return df.to_dict(orient="records")

    def _get_pending_drop_tables(self, environment: str) -> list[dict]:
        sql = f"""
            SELECT * FROM {NONPROD_REGISTRY_TABLE}
            WHERE environment   = '{environment}'
              AND lifecycle_state = 'PENDING_DROP'
        """
        df = read_sql(sql, workgroup="nonprod")
        return df.to_dict(orient="records")

    def _transition(
        self,
        table_row: dict,
        from_state: str,
        to_state: str,
        **extra_fields,
    ) -> None:
        """Update lifecycle_state in nonprod_registry."""
        fqn = table_row.get("table_fqn", "")
        now = _now()

        set_clauses = [
            f"lifecycle_state  = '{to_state}'",
            f"previous_state   = '{from_state}'",
            f"state_changed_at = TIMESTAMP '{now}'",
        ]

        for field, value in extra_fields.items():
            if isinstance(value, datetime):
                set_clauses.append(f"{field} = TIMESTAMP '{value.strftime('%Y-%m-%d %H:%M:%S')}'")
            elif isinstance(value, str):
                set_clauses.append(f"{field} = '{_esc(value)}'")
            elif isinstance(value, bool):
                set_clauses.append(f"{field} = {str(value).lower()}")
            else:
                set_clauses.append(f"{field} = {value}")

        sql = f"""
            UPDATE {NONPROD_REGISTRY_TABLE}
            SET {', '.join(set_clauses)}
            WHERE table_fqn = '{fqn}'
        """
        log.info(
            "lifecycle_engine.transition",
            table_fqn=fqn,
            from_state=from_state,
            to_state=to_state,
            dry_run=self.dry_run,
        )
        run_query(sql, workgroup="nonprod", dry_run=self.dry_run)

    def _mark_dropped(self, table_row: dict, cleanup_result: dict) -> None:
        """Mark a table as DROPPED in nonprod_registry."""
        fqn = table_row.get("table_fqn", "")
        now = _now()
        sql = f"""
            UPDATE {NONPROD_REGISTRY_TABLE}
            SET lifecycle_state  = 'DROPPED',
                previous_state   = 'PENDING_DROP',
                state_changed_at = TIMESTAMP '{now}',
                dropped_at       = TIMESTAMP '{now}',
                s3_cleaned       = {str(cleanup_result.get('s3_cleaned', False)).lower()},
                catalog_dropped  = {str(cleanup_result.get('catalog_dropped', False)).lower()},
                bytes_reclaimed  = {cleanup_result.get('bytes_reclaimed', 0)}
            WHERE table_fqn = '{fqn}'
        """
        run_query(sql, workgroup="nonprod", dry_run=self.dry_run)

    def _write_log(
        self,
        table_row: dict,
        operation: str,
        status: str,
        error_message: Optional[str] = None,
        bytes_reclaimed: int = 0,
    ) -> None:
        entry = LogEntry(
            run_id=self.run_id,
            engine="lifecycle",
            operation=operation,
            table_fqn=table_row.get("table_fqn", ""),
            domain=table_row.get("domain", ""),
            layer="nonprod",
            tier="low",
            environment=table_row.get("environment", ""),
            status=status,
            dry_run=self.dry_run,
            error_message=error_message,
            bytes_archived=bytes_reclaimed,
        )
        execution_log.write(entry, dry_run=self.dry_run)


# ══════════════════════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _esc(value: str) -> str:
    return str(value).replace("'", "''")


def _parse_ts(value) -> datetime:
    """Parse a timestamp string or datetime to timezone-aware datetime."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return datetime.now(timezone.utc)
    return datetime.now(timezone.utc)


def _infer_domain(database: str) -> str:
    """Infer domain from Glue database name. e.g. finance_preprod → finance."""
    for suffix in ["_preprod", "_dev", "_test", "_uat", "_staging"]:
        if database.endswith(suffix):
            return database[: -len(suffix)]
    return database.split("_")[0] if "_" in database else database

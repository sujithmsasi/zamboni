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
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from config.settings import NONPROD_REGISTRY_TABLE
from engine.core import execution_log, notifier, registry
from engine.core.control_plane import read_sql, run_query
from engine.core.execution_log import LogEntry
from engine.core.lock_service import LockHeartbeat, LockService
from engine.engines.base import BaseEngine
from engine.monitoring.activity_scanner import get_activity_signals
from engine.operations.catalog_cleanup import cleanup_table, is_backup_pattern
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
        Discovers all tables in non-prod Glue databases whose inferred
        domain is a registered, active domain_registry row. Updates
        nonprod_registry with new tables and refreshes activity signals.

        Control added here (2026-07-09): a database whose inferred domain
        has no active domain_registry row is skipped entirely -- not just
        excluded from later state-machine evaluation. An unregistered or
        deactivated domain's tables never get a nonprod_registry row
        created (or refreshed) in the first place, so they can never be
        candidate-marked or dropped downstream either -- this is the first
        of three gates (scan / evaluate / pre-delete) that all enforce the
        same rule independently, see run_cleanup() for the last one.
        """
        self._log_start(scope="scan", environment=environment)

        discovered      = 0
        errors          = 0
        skipped_domains = 0

        active_domains = {d["domain_name"] for d in registry.get_all_domains(active_only=True)}

        databases = get_databases()
        log.info("lifecycle_engine.scan.databases", count=len(databases))

        for database in databases:
            domain = _infer_domain(database)
            if domain not in active_domains:
                skipped_domains += 1
                log.info(
                    "lifecycle_engine.scan.domain_not_registered_or_inactive",
                    database=database, domain=domain,
                )
                continue

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
            skipped=skipped_domains,
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

        transitioned  = 0
        notified      = 0
        skipped       = 0
        errors        = 0
        notify_failed = 0

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
                elif outcome == "notify_failed":
                    notify_failed += 1
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
            notify_failed=notify_failed,
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
                if expires_at and _parse_ts(expires_at) > datetime.now(UTC):
                    skipped += 1
                    log.info("lifecycle_engine.cleanup.not_expired", table_fqn=fqn)
                    continue

                # Hold the same per-table lock HKEngine/orchestrator.py uses
                # (keyed on table_fqn alone, engine/core/lock_service.py) for
                # the state re-check through the delete -- real gap fixed
                # here (2026-07-09 audit): without it, a concurrent HK
                # orchestrated run on this same table (or two overlapping
                # cleanup runs) could mutate/read the table's Iceberg
                # metadata at the same moment this hard delete runs.
                lock_service = LockService()
                lock = lock_service.acquire(fqn, "lifecycle_cleanup")
                if lock is None:
                    skipped += 1
                    log.warning("lifecycle_engine.cleanup.lock_held", table_fqn=fqn)
                    continue

                heartbeat = LockHeartbeat(lock_service, lock)
                heartbeat.start()
                try:
                    # Re-check state immediately before the hard delete, not
                    # just at the top-of-run snapshot fetched into `tables`
                    # above. Real gap fixed here (2026-07-09 audit): `tables`
                    # is fetched once at the start of this run and this loop
                    # can take real wall-clock time (per-table Glue/S3 calls)
                    # working through it -- an owner exempting or claiming a
                    # table (both flip lifecycle_state to ACTIVE immediately,
                    # see api/services/lifecycle_svc.py) *after* this
                    # snapshot was taken but *before* this row's turn in the
                    # loop would otherwise still get hard-deleted on stale
                    # state. A fresh re-read right here closes that window.
                    current = self._get_current_state(fqn)
                    if current is None or current.get("lifecycle_state") != PENDING_DROP:
                        skipped += 1
                        log.info(
                            "lifecycle_engine.cleanup.state_changed_since_scan",
                            table_fqn=fqn,
                            current_state=(current or {}).get("lifecycle_state"),
                        )
                        continue

                    # Third gate, and the one that matters most: even if
                    # this table was legitimately marked PENDING_DROP while
                    # its domain was active, the domain may have been
                    # disabled since -- during the multi-day grace window,
                    # or mid-run right now. A disabled domain must block
                    # the drop unconditionally, fail-closed if the check
                    # itself can't determine an active domain (missing/None
                    # is treated the same as inactive).
                    if not current.get("domain_active"):
                        skipped += 1
                        log.warning(
                            "lifecycle_engine.cleanup.domain_not_registered_or_inactive",
                            table_fqn=fqn,
                            domain=current.get("domain"),
                        )
                        continue

                    # 2026-07-11 audit fix: cancel_check refuses the Glue
                    # DROP if this table's lock lease was lost between
                    # acquire() above and here (e.g. a slow domain-active
                    # re-check) or during the drop/sweep itself.
                    result = cleanup_table(fqn, dry_run=self.dry_run, cancel_check=lambda: heartbeat.lost)

                    if result.get("catalog_dropped") and result.get("s3_cleaned"):
                        succeeded += 1
                        self._mark_dropped(table_row, result)
                        self._write_log(table_row, "catalog_cleanup", "SUCCESS" if not self.dry_run else "DRY_RUN",
                                        bytes_reclaimed=result.get("bytes_reclaimed", 0))
                    else:
                        failed += 1
                        self._write_log(table_row, "catalog_cleanup", "FAILURE",
                                        error_message=result.get("error"))
                finally:
                    heartbeat.stop()
                    lock_service.release(lock)

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
            expires_at = datetime.now(UTC) + timedelta(days=DEFAULT_GREENZONE_DAYS)
            # 2026-07-10 audit fix: notify BEFORE transitioning, not after.
            # The old order wrote greenzone_expires_at unconditionally, then
            # attempted the SNS send -- notifier.send() swallows failures
            # and returns None with no retry, so a transient SNS failure
            # meant the deletion countdown started silently with the owner
            # never actually warned. Only start the countdown once the
            # owner has genuinely been notified; on failure, leave the
            # table in STALE_CANDIDATE so the next scheduled scan retries
            # both the notification and the countdown from scratch instead
            # of advancing on a best-effort basis.
            if not self.dry_run:
                msg_id = notifier.send_greenzone_notification(
                    table_fqn=fqn,
                    owner_email=table_row.get("owner_email", ""),
                    expires_at=expires_at.date(),
                    environment=table_row.get("environment", ""),
                    days_inactive=days_inactive,
                )
                if msg_id is None:
                    log.error("lifecycle_engine.greenzone_notify_failed_deferring_transition", table_fqn=fqn)
                    notifier.send_alert(
                        subject="GREENZONE notification failed — transition deferred",
                        message=(
                            f"Table: {fqn}\n"
                            f"Failed to notify owner ({table_row.get('owner_email', '')}) before "
                            "starting the GREENZONE countdown. State was NOT advanced -- will "
                            "retry on the next scheduled Lifecycle scan."
                        ),
                        table_fqn=fqn,
                    )
                    self._write_log(table_row, "lifecycle_transition", "NOTIFY_FAILED")
                    return "notify_failed"
            self._transition(table_row, STALE_CANDIDATE, GREENZONE,
                             greenzone_expires_at=expires_at)
            self._write_log(table_row, "lifecycle_transition", "SUCCESS" if not self.dry_run else "DRY_RUN")
            return "notified"

        # ── GREENZONE → PENDING_DROP or back to ACTIVE ────────────────────────
        if current_state == GREENZONE:
            if owner_exempted:
                self._transition(table_row, GREENZONE, ACTIVE)
                return "transitioned"

            expires_at = table_row.get("greenzone_expires_at")
            if expires_at and _parse_ts(expires_at) <= datetime.now(UTC):
                drop_at = datetime.now(UTC) + timedelta(days=DEFAULT_PENDING_DROP_DAYS)
                # Same notify-before-transition fix as GREENZONE above --
                # matters even more here, since this is the FINAL notice
                # before a real Glue DROP.
                if not self.dry_run:
                    msg_id = notifier.send_pending_drop_notification(
                        table_fqn=fqn,
                        owner_email=table_row.get("owner_email", ""),
                        drop_at=drop_at.date(),
                        environment=table_row.get("environment", ""),
                    )
                    if msg_id is None:
                        log.error("lifecycle_engine.pending_drop_notify_failed_deferring_transition", table_fqn=fqn)
                        notifier.send_alert(
                            subject="PENDING DROP final-notice failed — transition deferred",
                            message=(
                                f"Table: {fqn}\n"
                                f"Failed to send the final-notice notification to "
                                f"{table_row.get('owner_email', '')} before starting the "
                                "PENDING_DROP countdown to a real DROP. State was NOT "
                                "advanced -- will retry on the next scheduled Lifecycle scan."
                            ),
                            table_fqn=fqn,
                        )
                        self._write_log(table_row, "lifecycle_transition", "NOTIFY_FAILED")
                        return "notify_failed"
                self._transition(table_row, GREENZONE, PENDING_DROP,
                                 pending_drop_expires_at=drop_at)
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
        """
        Insert or update a table in nonprod_registry.
        On every scan, refreshes activity signals (last_query_at, last_write_at,
        days_since_activity) via CloudTrail or falls back to Glue CreateTime.

        Column list matches config/control_plane_schema.py's 27-column
        nonprod_registry shape (the SQLite control plane this table has been
        primary-written to since the 2026-07-09 migration) -- NOT the
        legacy 29-column sql/create_nonprod_registry.sql Athena DDL this
        statement was originally positional-VALUES-written against. A
        positional INSERT against the old column count/order silently
        failed here on every new-table discovery post-migration (caught by
        run_scan's per-table try/except, only visible as an error count),
        and the old UPDATE branch referenced last_scanned_at, a column that
        was never part of the new schema either -- so scans of
        already-registered tables were silently failing too. Named columns
        + ON CONFLICT below is deliberately schema-order-independent so this
        class of drift can't reoccur silently.
        """
        name       = table.get("Name", "")
        table_fqn  = f"glue_catalog.{database}.{name}"
        is_iceberg = is_iceberg_table(table)
        fmt        = "iceberg" if is_iceberg else "hive"
        created_at = table.get("CreateTime")
        is_backup, pattern = is_backup_pattern(name)
        now = _now()

        # ── Activity signals refresh ──────────────────────────────────────────
        signals = get_activity_signals(
            table_fqn=table_fqn,
            database=database,
            table_name=name,
            glue_create_time=created_at,
        )

        last_query_str = (
            f"TIMESTAMP '{signals.last_query_at.strftime('%Y-%m-%d %H:%M:%S')}'"
            if signals.last_query_at else "NULL"
        )
        last_write_str = (
            f"TIMESTAMP '{signals.last_write_at.strftime('%Y-%m-%d %H:%M:%S')}'"
            if signals.last_write_at else "NULL"
        )
        days_since_str = (
            str(signals.days_since_activity)
            if signals.days_since_activity is not None else "NULL"
        )
        created_str = (
            f"TIMESTAMP '{created_at.strftime('%Y-%m-%d %H:%M:%S')}'"
            if created_at else "NULL"
        )

        sql = f"""
            INSERT INTO {NONPROD_REGISTRY_TABLE} (
                table_fqn, domain, environment, table_format, database_name,
                lifecycle_state, last_query_at, last_write_at,
                days_since_activity, created_at, is_backup, is_backup_pattern,
                pattern_matched, first_seen_at, scan_count
            ) VALUES (
                '{table_fqn}', '{_infer_domain(database)}', '{environment}',
                '{fmt}', '{database}',
                'ACTIVE', {last_query_str}, {last_write_str},
                {days_since_str}, {created_str}, {str(is_backup).lower()}, {str(is_backup).lower()},
                '{pattern}', TIMESTAMP '{now}', 1
            )
            ON CONFLICT (table_fqn) DO UPDATE SET
                last_query_at       = excluded.last_query_at,
                last_write_at       = excluded.last_write_at,
                days_since_activity = excluded.days_since_activity,
                scan_count          = scan_count + 1
        """
        run_query(sql, workgroup="nonprod", dry_run=self.dry_run)

    def _get_active_registry_tables(self, environment: str) -> list[dict]:
        # Second gate: only registered, active domains get state-machine
        # evaluation (transitions into STALE_CANDIDATE/GREENZONE/
        # PENDING_DROP happen in _evaluate_table(), called per row this
        # returns). Deliberately the stricter allowlist filter (see
        # registry.domain_registered_active_filter_sql()'s docstring), not
        # HK/Archival's permissive blocklist -- a table under a domain that
        # was never registered must not progress toward a drop.
        sql = f"""
            SELECT * FROM {NONPROD_REGISTRY_TABLE}
            WHERE environment   = '{environment}'
              AND lifecycle_state != 'DROPPED'
              AND {registry.domain_registered_active_filter_sql()}
            ORDER BY lifecycle_state, days_since_activity DESC
        """
        df = read_sql(sql, workgroup="nonprod")
        return df.to_dict(orient="records")

    def _get_pending_drop_tables(self, environment: str) -> list[dict]:
        sql = f"""
            SELECT * FROM {NONPROD_REGISTRY_TABLE}
            WHERE environment   = '{environment}'
              AND lifecycle_state = 'PENDING_DROP'
              AND {registry.domain_registered_active_filter_sql()}
        """
        df = read_sql(sql, workgroup="nonprod")
        return df.to_dict(orient="records")

    def _get_current_state(self, table_fqn: str) -> dict | None:
        """
        Fresh single-row read used immediately before a hard delete in
        run_cleanup() -- see the comment there for why this can't just
        reuse the row from the run's opening scan.

        Also re-verifies the table's domain is still a registered, active
        domain_registry row (`domain_active`) -- the third and final gate.
        A domain disabled any time after this table entered PENDING_DROP
        (during the multi-day grace window, or even mid-run between
        _get_pending_drop_tables()'s fetch and this specific table's turn
        in the loop) must block the drop, the same way an owner exemption
        does -- see run_cleanup()'s domain_active check.
        """
        sql = f"""
            SELECT r.lifecycle_state, r.owner_exempted, r.pending_drop_expires_at,
                   r.domain, {registry.domain_registered_active_filter_sql('r.domain')} AS domain_active
            FROM {NONPROD_REGISTRY_TABLE} r
            WHERE r.table_fqn = '{_esc(table_fqn)}'
            LIMIT 1
        """
        df = read_sql(sql, workgroup="nonprod")
        return df.iloc[0].to_dict() if not df.empty else None

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
        error_message: str | None = None,
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
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")


def _esc(value: str) -> str:
    return str(value).replace("'", "''")


def _parse_ts(value) -> datetime:
    """Parse a timestamp string or datetime to timezone-aware datetime."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value)
            return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
        except ValueError:
            return datetime.now(UTC)
    return datetime.now(UTC)


def _infer_domain(database: str) -> str:
    """Infer domain from Glue database name. e.g. finance_preprod → finance."""
    for suffix in ["_preprod", "_dev", "_test", "_uat", "_staging"]:
        if database.endswith(suffix):
            return database[: -len(suffix)]
    return database.split("_")[0] if "_" in database else database

"""
Zamboni -- Central Audit Log
Records all significant user/system actions across every page and engine.

Design principles:
  - Never raises: audit failures must never block the actual action.
  - Always writes: even dry-run and rejected actions are recorded.
  - Caller-first: callers pass structured AuditEvent; this module writes it.
  - Phase 2 ready: write() currently uses Athena INSERT; switching to
    Parquet/add_files later requires only changing _persist().

Usage:
    from engine.core.audit import AuditEvent, audit

    audit(AuditEvent(
        actor        = current_user(),
        action_type  = AuditAction.HK_ENABLE,
        page_source  = "3_Policy_Configuration",
        target_type  = "table",
        target_id    = table_fqn,
        domain       = domain,
        environment  = APP_ENV,
        dry_run      = is_dry_run(),
        status       = "SUCCESS",
        reason       = reason,
        ticket_number= ticket,
        before_value = json.dumps({"hk_enabled": False}),
        after_value  = json.dumps({"hk_enabled": True}),
    ))
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from config.settings import APP_ENV, AUDIT_LOG_TABLE
from engine.utils.logger import get_logger

log = get_logger(__name__)


# ── Action type constants ─────────────────────────────────────────────────────

class AuditAction:
    """Structured constants for action_type field."""
    DOMAIN_CREATE            = "domain_create"
    DOMAIN_UPDATE            = "domain_update"
    TABLE_REGISTER           = "table_register"
    TABLE_UNREGISTER         = "table_unregister"
    POLICY_CHANGE            = "policy_change"
    BULK_TEMPLATE_APPLY      = "bulk_template_apply"
    HK_ENABLE                = "hk_enable"
    HK_DISABLE               = "hk_disable"
    DRY_RUN_PROMOTE          = "dry_run_promote"
    RUN_HK_NOW               = "run_hk_now"
    CANCEL_QUERY             = "cancel_query"
    KILL_SWITCH              = "kill_switch"
    CIRCUIT_BREAKER_REENABLE = "circuit_breaker_reenable"
    LIFECYCLE_EXEMPTION      = "lifecycle_exemption"
    CLAIM_TABLE              = "claim_table"
    REPORT_EXPORT            = "report_export"
    SETTINGS_CHANGE          = "settings_change"
    ESCALATION_CHANGE        = "escalation_change"
    DRY_RUN_UNTIL_SET        = "dry_run_until_set"


# ── AuditEvent dataclass ──────────────────────────────────────────────────────

@dataclass
class AuditEvent:
    """
    Represents one auditable action. All fields have safe defaults so callers
    can construct minimal events and fill only what is relevant.
    """
    # Required
    actor:          str
    action_type:    str
    page_source:    str
    target_type:    str
    target_id:      str

    # Context
    domain:         str          = ""
    environment:    str          = ""
    dry_run:        bool         = True
    status:         str          = "SUCCESS"    # SUCCESS|FAILURE|DRY_RUN|REJECTED

    # Human inputs
    reason:         str          = ""
    ticket_number:  str          = ""

    # State diff
    before_value:   str          = ""           # JSON string or plain text
    after_value:    str          = ""           # JSON string or plain text

    # Error
    error_message:  str          = ""

    # Auto-populated
    audit_id:       str          = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp:      datetime     = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        return {
            "audit_id":      self.audit_id,
            "timestamp":     self.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            "actor":         self.actor,
            "action_type":   self.action_type,
            "page_source":   self.page_source,
            "target_type":   self.target_type,
            "target_id":     self.target_id,
            "domain":        self.domain,
            "environment":   self.environment or APP_ENV,
            "dry_run":       self.dry_run,
            "status":        self.status,
            "reason":        self.reason,
            "ticket_number": self.ticket_number,
            "before_value":  self.before_value,
            "after_value":   self.after_value,
            "error_message": self.error_message,
            "audit_date":    self.timestamp.strftime("%Y-%m-%d"),
        }

    @classmethod
    def failure(cls, base: AuditEvent, error: str) -> AuditEvent:
        """Convenience: clone a base event and mark it as FAILURE."""
        import copy
        ev = copy.copy(base)
        ev.status        = "FAILURE"
        ev.error_message = error
        ev.audit_id      = str(uuid.uuid4())
        return ev

    @classmethod
    def rejected(cls, base: AuditEvent, reason: str) -> AuditEvent:
        """Convenience: clone a base event and mark it as REJECTED."""
        import copy
        ev = copy.copy(base)
        ev.status        = "REJECTED"
        ev.error_message = reason
        ev.audit_id      = str(uuid.uuid4())
        return ev


# ── Public write function ─────────────────────────────────────────────────────

def audit(event: AuditEvent) -> bool:
    """
    Write an audit event. Never raises -- returns True on success.

    Thread-safe: each call generates a fresh audit_id and SQL statement.
    """
    try:
        _persist(event)
        log.info(
            "audit.written",
            audit_id=event.audit_id,
            action=event.action_type,
            actor=event.actor,
            target=event.target_id,
            status=event.status,
            dry_run=event.dry_run,
        )
        # Fire Teams notification as a side effect (best-effort, never blocks)
        try:
            _maybe_notify_teams(event)
        except Exception:
            pass  # never block audit on Teams failure
        return True
    except Exception as e:
        # Audit failures must never block the caller
        log.error(
            "audit.write_failed",
            action=event.action_type,
            actor=event.actor,
            error=str(e),
        )
        return False


def audit_action(
    *,
    actor:        str,
    action_type:  str,
    page_source:  str,
    target_type:  str,
    target_id:    str,
    domain:       str  = "",
    environment:  str  = "",
    dry_run:      bool = True,
    status:       str  = "SUCCESS",
    reason:       str  = "",
    ticket_number: str = "",
    before_value: str  = "",
    after_value:  str  = "",
    error_message: str = "",
) -> bool:
    """
    Convenience wrapper — build and write an AuditEvent in one call.
    Preferred for simple one-liner audit calls in page code.
    """
    return audit(AuditEvent(
        actor=actor, action_type=action_type,
        page_source=page_source, target_type=target_type,
        target_id=target_id, domain=domain,
        environment=environment, dry_run=dry_run,
        status=status, reason=reason,
        ticket_number=ticket_number, before_value=before_value,
        after_value=after_value, error_message=error_message,
    ))


def diff_json(before: dict, after: dict) -> tuple[str, str]:
    """
    Serialise before/after dicts to JSON strings for audit records.
    Returns (before_value, after_value).
    """
    return json.dumps(before, default=str), json.dumps(after, default=str)


# ── Persistence ───────────────────────────────────────────────────────────────

def _persist(event: AuditEvent) -> None:
    """
    Write the audit event to Athena via INSERT INTO.
    Phase 2: swap this for ParquetLogBuffer.append() + flush() pattern.
    """
    from engine.utils.athena_client import run_query

    d = event.to_dict()

    def _esc(s: str) -> str:
        return str(s or "").replace("'", "''")

    dry_run_val = "true" if d["dry_run"] else "false"

    sql = f"""
        INSERT INTO {AUDIT_LOG_TABLE}
        VALUES (
            '{_esc(d["audit_id"])}',
            TIMESTAMP '{_esc(d["timestamp"])}',
            '{_esc(d["actor"])}',
            '{_esc(d["action_type"])}',
            '{_esc(d["page_source"])}',
            '{_esc(d["target_type"])}',
            '{_esc(d["target_id"])}',
            '{_esc(d["domain"])}',
            '{_esc(d["environment"])}',
            {dry_run_val},
            '{_esc(d["status"])}',
            '{_esc(d["reason"])}',
            '{_esc(d["ticket_number"])}',
            '{_esc(d["before_value"])}',
            '{_esc(d["after_value"])}',
            '{_esc(d["error_message"])}',
            DATE '{_esc(d["audit_date"])}'
        )
    """
    run_query(sql, workgroup="app")


# ── Query helpers (for audit viewer page) ────────────────────────────────────

def get_recent_events(
    limit:       int  = 100,
    actor:       str | None = None,
    action_type: str | None = None,
    target_id:   str | None = None,
    domain:      str | None = None,
    days:        int  = 7,
):
    """
    Fetch recent audit events for the audit viewer page.
    Returns empty DataFrame on error.
    """
    import pandas as pd

    from engine.utils.athena_client import read_sql

    conditions = [f"audit_date >= CURRENT_DATE - INTERVAL '{days}' DAY"]
    if actor:
        conditions.append(f"actor = '{actor}'")
    if action_type:
        conditions.append(f"action_type = '{action_type}'")
    if target_id:
        conditions.append(f"target_id = '{target_id}'")
    if domain:
        conditions.append(f"domain = '{domain}'")

    where = "WHERE " + " AND ".join(conditions)

    sql = f"""
        SELECT
            timestamp, actor, action_type, page_source,
            target_type, target_id, domain, environment,
            dry_run, status, reason, ticket_number,
            before_value, after_value, error_message, audit_id
        FROM {AUDIT_LOG_TABLE}
        {where}
        ORDER BY timestamp DESC
        LIMIT {limit}
    """
    try:
        return read_sql(sql, workgroup="app")
    except Exception as e:
        log.error("audit.get_recent_events_failed", error=str(e))
        return pd.DataFrame()


# ── Teams integration ─────────────────────────────────────────────────────────

def _maybe_notify_teams(event: AuditEvent) -> None:
    """
    Fire a Teams notification as a post-audit side effect.
    Never raises. Skips if Teams is disabled or event does not qualify.
    """
    try:
        from engine.core.teams_notifier import (
            TeamsEvent,
            notify_teams,
            should_notify,
        )
        if not should_notify(event.action_type, event.dry_run, event.status):
            return
        notify_teams(TeamsEvent(
            title       = f"Zamboni: {event.action_type.replace('_',' ').title()}",
            summary     = (
                f"{event.actor} performed {event.action_type} "
                f"on {event.target_id} [{event.status}]"
            ),
            actor       = event.actor,
            action_type = event.action_type,
            target      = event.target_id,
            domain      = event.domain,
            environment = event.environment,
            status      = event.status,
            reason      = event.reason,
        ))
    except Exception:
        pass  # Teams failure must never propagate

"""
Zamboni -- Weekly Digest Builder
Phase 2.1: builds per-domain weekly digest content from execution_log.

The digest email sender (SNS) is Phase 2.2.
This module builds the digest as a structured dict usable by:
  - The Settings page "Preview Digest" button
  - Phase 2.2 email sender (renders as HTML email)
  - Phase 2.2 Teams digest (renders as MessageCard)

Content per domain:
  - Tables processed (success / failure / skipped)
  - Operations breakdown (compaction / vacuum / orphan_cleanup)
  - GB compacted, snapshots expired, orphan files deleted
  - Athena cost for the week
  - SLA breaches (tables overdue based on run_frequency)
  - Top 5 failures with error messages

Domain-level opt-in:
  - digest_enabled field in domain_registry controls per-domain sending
  - digest_email overrides owner_email for digest delivery
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from config.settings import (
    DOMAIN_REGISTRY_TABLE,
    EXECUTION_LOG_TABLE,
    HK_CONFIG_TABLE,
    STREAM_REGISTRY_TABLE,
)
from engine.utils.athena_client import read_sql
from engine.utils.logger import get_logger

log = get_logger(__name__)


def build_digest(domain: str, days: int = 7) -> dict[str, Any]:
    """
    Build weekly digest content for a domain.

    Returns a dict with:
      domain, period_start, period_end,
      summary (total counts), operations_breakdown,
      sla_breaches (list of overdue tables),
      top_failures (list of failed operations),
      cost_usd (estimate from bytes_scanned),
      recipient_email (from digest_email or owner_email)

    Never raises -- returns error dict on failure.
    """
    try:
        return _build(domain, days)
    except Exception as e:
        log.error("digest.build_failed", domain=domain, error=str(e))
        return {
            "domain":    domain,
            "error":     str(e),
            "generated": datetime.now(UTC).isoformat(),
        }


def get_digest_domains() -> list[dict]:
    """
    Return all domains with digest_enabled=true.
    Used by the EventBridge-triggered weekly sender (Phase 2.2).
    """
    try:
        sql = f"""
            SELECT domain_name, owner_email, digest_email, digest_enabled
            FROM {DOMAIN_REGISTRY_TABLE}
            WHERE digest_enabled = true
            ORDER BY domain_name
        """
        df = read_sql(sql, workgroup="app")
        return df.to_dict("records") if not df.empty else []
    except Exception as e:
        log.error("digest.get_domains_failed", error=str(e))
        return []


# ── Internal ──────────────────────────────────────────────────────────────────

def _build(domain: str, days: int) -> dict[str, Any]:


    end_dt   = datetime.now(UTC).date()
    start_dt = end_dt - timedelta(days=days)

    # ── Summary counts ────────────────────────────────────────────────────────
    summary_sql = f"""
        SELECT
            COUNT(DISTINCT table_fqn)                             AS tables_touched,
            SUM(CASE WHEN status = 'SUCCESS'  THEN 1 ELSE 0 END) AS successes,
            SUM(CASE WHEN status LIKE 'FAIL%' THEN 1 ELSE 0 END) AS failures,
            SUM(CASE WHEN status = 'SKIPPED'  THEN 1 ELSE 0 END) AS skips,
            ROUND(SUM(bytes_rewritten) / 1e9, 2)                  AS gb_compacted,
            SUM(snapshots_before - COALESCE(snapshots_after, snapshots_before))
                                                                  AS snapshots_expired,
            SUM(orphan_files_deleted)                             AS orphan_files_deleted,
            ROUND(SUM(bytes_scanned) / 1e12 * 5.0, 4)            AS athena_cost_usd
        FROM {EXECUTION_LOG_TABLE}
        WHERE domain          = '{domain}'
          AND operation       = 'hk_run'
          AND dry_run         = false
          AND execution_date  >= DATE '{start_dt.isoformat()}'
          AND execution_date  <= DATE '{end_dt.isoformat()}'
    """
    summary_df = read_sql(summary_sql, workgroup="app")
    summary    = summary_df.iloc[0].to_dict() if not summary_df.empty else {}

    # ── Operations breakdown ──────────────────────────────────────────────────
    ops_sql = f"""
        SELECT operation,
               SUM(CASE WHEN status = 'SUCCESS'  THEN 1 ELSE 0 END) AS successes,
               SUM(CASE WHEN status LIKE 'FAIL%' THEN 1 ELSE 0 END) AS failures
        FROM {EXECUTION_LOG_TABLE}
        WHERE domain         = '{domain}'
          AND dry_run        = false
          AND execution_date >= DATE '{start_dt.isoformat()}'
          AND execution_date <= DATE '{end_dt.isoformat()}'
          AND operation IN ('compaction', 'vacuum', 'orphan_cleanup')
        GROUP BY operation
        ORDER BY operation
    """
    ops_df = read_sql(ops_sql, workgroup="app")
    ops    = ops_df.to_dict("records") if not ops_df.empty else []

    # ── SLA breaches ──────────────────────────────────────────────────────────
    sla_sql = f"""
        SELECT r.table_fqn, c.run_frequency,
               MAX(e.completed_at)                           AS last_success,
               DATE_DIFF('day', MAX(e.completed_at), NOW())  AS days_overdue
        FROM {STREAM_REGISTRY_TABLE} r
        JOIN {HK_CONFIG_TABLE} c ON r.table_fqn = c.table_fqn
        LEFT JOIN {EXECUTION_LOG_TABLE} e
               ON r.table_fqn = e.table_fqn
              AND e.operation  = 'hk_run'
              AND e.status     = 'SUCCESS'
              AND e.execution_date >= DATE '{start_dt.isoformat()}'
        WHERE r.domain      = '{domain}'
          AND r.hk_enabled  = true
          AND r.environment = 'prod'
        GROUP BY r.table_fqn, c.run_frequency
        HAVING
            (c.run_frequency = 'daily'   AND (MAX(e.completed_at) IS NULL
                OR DATE_DIFF('day', MAX(e.completed_at), NOW()) > 2))
         OR (c.run_frequency = 'weekly'  AND (MAX(e.completed_at) IS NULL
                OR DATE_DIFF('day', MAX(e.completed_at), NOW()) > 9))
         OR (c.run_frequency = 'monthly' AND (MAX(e.completed_at) IS NULL
                OR DATE_DIFF('day', MAX(e.completed_at), NOW()) > 32))
        ORDER BY days_overdue DESC NULLS FIRST
        LIMIT 20
    """
    sla_df     = read_sql(sla_sql, workgroup="app")
    sla_breach = sla_df.to_dict("records") if not sla_df.empty else []

    # ── Top failures ──────────────────────────────────────────────────────────
    fail_sql = f"""
        SELECT table_fqn, operation, error_message,
               MAX(started_at) AS last_failure_at
        FROM {EXECUTION_LOG_TABLE}
        WHERE domain         = '{domain}'
          AND status         LIKE 'FAIL%'
          AND dry_run        = false
          AND execution_date >= DATE '{start_dt.isoformat()}'
        GROUP BY table_fqn, operation, error_message
        ORDER BY last_failure_at DESC
        LIMIT 5
    """
    fail_df      = read_sql(fail_sql, workgroup="app")
    top_failures = fail_df.to_dict("records") if not fail_df.empty else []

    # ── Recipient email ───────────────────────────────────────────────────────
    domain_sql = f"""
        SELECT owner_email, digest_email
        FROM {DOMAIN_REGISTRY_TABLE}
        WHERE domain_name = '{domain}'
        LIMIT 1
    """
    domain_df = read_sql(domain_sql, workgroup="app")
    recipient = ""
    if not domain_df.empty:
        row       = domain_df.iloc[0]
        recipient = (
            str(row.get("digest_email") or "").strip()
            or str(row.get("owner_email") or "").strip()
        )

    return {
        "domain":              domain,
        "period_start":        start_dt.isoformat(),
        "period_end":          end_dt.isoformat(),
        "summary":             {k: (float(v) if v is not None else 0)
                                for k, v in summary.items()},
        "operations":          ops,
        "sla_breaches":        sla_breach,
        "top_failures":        top_failures,
        "recipient_email":     recipient,
        "generated":           datetime.now(UTC).isoformat(),
        "email_sender_status": "phase_2_2_pending",
    }

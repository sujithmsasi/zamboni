"""
Zamboni -- Escalation Matrix
Central routing table for alert escalation by domain/tier/environment/severity.

Lookup priority (most specific → least specific):
  1. domain + tier + environment
  2. domain + environment
  3. domain
  4. tier + environment
  5. environment
  6. default

Phase 1: lookup + display only.
Phase 2: integrate with SNS routing and Teams/Slack webhooks.

Config is driven by escalation_matrix in zamboni_settings.json.
Each entry can specify:
  primary_owner_email    -- domain team DL
  escalation_email       -- escalation contact (manager, oncall)
  zamboni_owner_email    -- Zamboni platform team
  notify_sns_topic       -- override SNS topic for this domain/tier
  severity_rules         -- {"FAILURE_TIMEOUT": "high", "CIRCUIT_OPEN": "critical"}
"""
from __future__ import annotations

from engine.utils.logger import get_logger

log = get_logger(__name__)

_SEVERITY_ORDER = ["low", "medium", "high", "critical"]


def lookup(
    domain:      str | None = None,
    tier:        str | None = None,
    environment: str | None = None,
    action_type: str | None = None,
) -> dict:
    """
    Return the most specific escalation entry for the given combination.
    Falls back through the priority chain to the default entry.

    Returns a dict with escalation fields (never raises).
    Returns empty dict if no matrix is configured.
    """
    from config.platform_settings import get_settings
    settings = get_settings()
    matrix   = settings.get("escalation_matrix", {})

    if not matrix:
        log.debug("escalation.no_matrix_configured")
        return _default_entry()

    candidates = [
        _make_key(domain, tier, environment),
        _make_key(domain, None, environment),
        _make_key(domain, None, None),
        _make_key(None, tier, environment),
        _make_key(None, None, environment),
        "default",
    ]

    for key in candidates:
        if key in matrix:
            entry = dict(matrix[key])
            log.debug("escalation.resolved", key=key,
                      domain=domain, tier=tier, env=environment)
            return _fill_defaults(entry)

    return _default_entry()


def get_severity(
    failure_reason: str,
    domain:         str | None = None,
    tier:           str | None = None,
    environment:    str | None = None,
) -> str:
    """
    Determine severity for a given failure_reason string.
    Returns one of: low | medium | high | critical.
    """
    entry = lookup(domain, tier, environment)
    rules = entry.get("severity_rules", {})

    # Exact match first
    if failure_reason in rules:
        return rules[failure_reason]

    # Prefix match (e.g. "FAILURE_" → "high")
    for pattern, severity in rules.items():
        if failure_reason.startswith(pattern):
            return severity

    # Tier-based defaults
    tier_defaults = {
        "critical": "high",
        "standard": "medium",
        "low":      "low",
    }
    return tier_defaults.get(tier or "standard", "medium")


def get_notify_topic(
    domain:      str | None = None,
    tier:        str | None = None,
    environment: str | None = None,
) -> str:
    """
    Return the SNS topic ARN for this domain/tier/environment.
    Falls back to SNS_ALERT_TOPIC_ARN from settings.
    """
    from config.settings import SNS_ALERT_TOPIC_ARN
    entry = lookup(domain, tier, environment)
    return entry.get("notify_sns_topic") or SNS_ALERT_TOPIC_ARN


def list_matrix() -> list[dict]:
    """
    Return all escalation matrix entries as a list for the admin UI.
    Each entry includes the lookup key for display.
    """
    from config.platform_settings import get_settings
    matrix = get_settings().get("escalation_matrix", {})
    result = []
    for key, entry in matrix.items():
        row = {"_key": key}
        row.update(entry)
        result.append(row)
    return result


def upsert_entry(
    key:         str,
    entry:       dict,
    actor:       str,
    dry_run:     bool = True,
) -> bool:
    """
    Add or update an escalation matrix entry.
    Writes to zamboni_settings.json. Audits the change.
    Returns True on success.
    """
    if dry_run:
        log.info("escalation.upsert.dry_run", key=key)
        return True
    try:
        from config.platform_settings import get_settings, save_settings
        from engine.core.audit import AuditAction, audit_action
        settings = get_settings()
        before   = dict(settings.get("escalation_matrix", {}).get(key, {}))
        settings.setdefault("escalation_matrix", {})[key] = entry
        save_settings(settings)
        audit_action(
            actor=actor, action_type=AuditAction.ESCALATION_CHANGE,
            page_source="11_Settings", target_type="escalation",
            target_id=key, dry_run=False, status="SUCCESS",
            before_value=str(before), after_value=str(entry),
        )
        return True
    except Exception as e:
        log.error("escalation.upsert_failed", key=key, error=str(e))
        return False


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_key(
    domain: str | None,
    tier:   str | None,
    env:    str | None,
) -> str:
    parts = []
    if domain:
        parts.append(f"domain:{domain}")
    if tier:
        parts.append(f"tier:{tier}")
    if env:
        parts.append(f"env:{env}")
    return "|".join(parts) if parts else "default"


def _fill_defaults(entry: dict) -> dict:
    defaults = _default_entry()
    defaults.update(entry)
    return defaults


def _default_entry() -> dict:
    from config.settings import SNS_ALERT_TOPIC_ARN
    return {
        "primary_owner_email":   "",
        "escalation_email":      "",
        "zamboni_owner_email":   "",
        "notify_sns_topic":      SNS_ALERT_TOPIC_ARN,
        "severity_rules": {
            "FAILURE_TIMEOUT":           "high",
            "FAILURE_BACKPRESSURE":      "medium",
            "FAILURE_SAFETY_BLOCKED":    "critical",
            "SKIP_CIRCUIT_OPEN":         "high",
        },
    }

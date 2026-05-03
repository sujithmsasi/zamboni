"""
Zamboni -- Microsoft Teams Webhook Notifier
Phase 2.1: fire-and-forget Teams notifications for key user-facing events.

Design decisions:
  - Complements SNS (engine failures) not replaces it.
    SNS fires for EventBridge-triggered engine events.
    Teams fires for UI-triggered actions: policy changes, HK enable/disable,
    circuit breaker re-enables, exemptions, query cancels.
  - Never blocks the caller. Any failure is logged and swallowed.
  - Never fires on dry-run actions (would create noise from testing).
  - Webhook URL is stored in zamboni_settings.json (not .env).
    The URL contains a token so it is masked in the UI after save.
  - Payload format: Adaptive Card (Teams MessageCard fallback for older tenants).

Usage:
    from engine.core.teams_notifier import notify_teams, TeamsEvent

    notify_teams(TeamsEvent(
        title       = "HK Enabled",
        summary     = f"Housekeeping enabled for {table_fqn}",
        actor       = current_user(),
        action_type = "hk_enable",
        target      = table_fqn,
        domain      = domain,
        environment = APP_ENV,
        status      = "SUCCESS",
        reason      = reason,
        url         = None,   # optional deeplink back to Zamboni
    ))
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from engine.utils.logger import get_logger

log = get_logger(__name__)

# Actions that should trigger a Teams notification when they succeed
_NOTIFY_ACTIONS = {
    "hk_enable",
    "hk_disable",
    "dry_run_promote",
    "run_hk_now",
    "cancel_query",
    "kill_switch",
    "circuit_breaker_reenable",
    "lifecycle_exemption",
    "claim_table",
    "bulk_template_apply",
    "policy_change",
}

# Colour strip per status (Teams themeColor hex)
_STATUS_COLOUR = {
    "SUCCESS":  "00b894",   # green
    "FAILURE":  "d63031",   # red
    "REJECTED": "e17055",   # amber
    "DRY_RUN":  "0984e3",   # blue
}


@dataclass
class TeamsEvent:
    """Structured Teams notification event."""
    title:       str
    summary:     str
    actor:       str
    action_type: str
    target:      str
    domain:      str       = ""
    environment: str       = "prod"
    status:      str       = "SUCCESS"
    reason:      str       = ""
    url:         str | None = None
    timestamp:   datetime  = field(
        default_factory=lambda: datetime.now(UTC)
    )


def notify_teams(event: TeamsEvent) -> bool:
    """
    Post a Teams notification for a user-facing action.

    Returns True if posted successfully, False otherwise.
    Never raises -- Teams failure must never block the caller.

    Skips posting if:
      - Teams integration is disabled in settings
      - No webhook URL configured
      - Action type is not in _NOTIFY_ACTIONS
    """
    try:
        return _post(event)
    except Exception as e:
        log.error(
            "teams_notifier.unexpected_error",
            action=event.action_type,
            error=str(e),
        )
        return False


def is_enabled() -> bool:
    """Return True if Teams notifications are configured and enabled."""
    from config.platform_settings import get_settings
    settings = get_settings()
    return bool(
        settings.get("teams_enabled", False)
        and settings.get("teams_webhook_url", "")
    )


def should_notify(action_type: str, dry_run: bool, status: str) -> bool:
    """
    Determine if a Teams notification should fire for this event.
    Dry-run actions never notify (too noisy for testing workflows).
    Only SUCCESS and FAILURE statuses notify.
    """
    if dry_run:
        return False
    if status not in ("SUCCESS", "FAILURE", "REJECTED"):
        return False
    return action_type in _NOTIFY_ACTIONS


# ── Internal ──────────────────────────────────────────────────────────────────

def _post(event: TeamsEvent) -> bool:
    """Build payload and POST to configured webhook URL."""
    from config.platform_settings import get_settings
    settings    = get_settings()
    enabled     = settings.get("teams_enabled", False)
    webhook_url = settings.get("teams_webhook_url", "")

    if not enabled:
        log.debug("teams_notifier.disabled")
        return False

    if not webhook_url:
        log.warning("teams_notifier.no_webhook_url")
        return False

    if event.action_type not in _NOTIFY_ACTIONS:
        log.debug("teams_notifier.action_not_in_notify_list",
                  action=event.action_type)
        return False

    payload = _build_payload(event)
    return _http_post(webhook_url, payload)


def _build_payload(event: TeamsEvent) -> dict[str, Any]:
    """
    Build a Teams MessageCard payload.
    Uses the legacy MessageCard format (supported by all Teams tenants).
    Adaptive Card format is Phase 2.2.
    """
    colour = _STATUS_COLOUR.get(event.status, "636e72")
    ts     = event.timestamp.strftime("%Y-%m-%d %H:%M UTC")

    facts = [
        {"name": "Actor",       "value": event.actor},
        {"name": "Action",      "value": event.action_type.replace("_", " ").title()},
        {"name": "Target",      "value": event.target},
        {"name": "Domain",      "value": event.domain or "—"},
        {"name": "Environment", "value": event.environment},
        {"name": "Status",      "value": event.status},
        {"name": "Time",        "value": ts},
    ]
    if event.reason:
        facts.append({"name": "Reason", "value": event.reason[:200]})

    section: dict[str, Any] = {
        "activityTitle":    event.title,
        "activitySubtitle": event.summary,
        "facts":            facts,
        "markdown":         True,
    }

    payload: dict[str, Any] = {
        "@type":      "MessageCard",
        "@context":   "http://schema.org/extensions",
        "themeColor": colour,
        "summary":    event.summary,
        "sections":   [section],
    }

    if event.url:
        payload["potentialAction"] = [{
            "@type": "OpenUri",
            "name":  "Open Zamboni",
            "targets": [{"os": "default", "uri": event.url}],
        }]

    return payload


def _http_post(url: str, payload: dict) -> bool:
    """POST JSON to the Teams webhook URL."""
    import urllib.request

    body = json.dumps(payload).encode("utf-8")
    req  = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            status = resp.status
            if status == 200:
                log.info("teams_notifier.posted",
                         action=payload.get("summary", ""))
                return True
            # Teams returns 1 as body text for success in some tenants
            body_text = resp.read().decode("utf-8", errors="replace")
            if body_text.strip() == "1":
                return True
            log.warning("teams_notifier.unexpected_response",
                        status=status, body=body_text[:100])
            return False
    except Exception as e:
        log.error("teams_notifier.post_failed", url=url[:40], error=str(e))
        return False


def mask_webhook_url(url: str) -> str:
    """
    Mask a Teams webhook URL for display in the Settings UI.
    Shows only the last 12 characters so the URL is recognisable
    but the full token is not exposed.

    Example:
        https://...abc123xyz456  →  •••••••abc123xyz456
    """
    if not url:
        return ""
    visible = url[-12:]
    return f"{'•' * 8}{visible}"

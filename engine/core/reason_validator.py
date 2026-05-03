"""
Zamboni -- Reason / Ticket Enforcement
Shared validation for all actions that require a user-provided reason
and optionally a change/ticket number.

Rules (driven by platform_settings):
  - In prod: reason required for live destructive/enabling actions.
  - In prod: ticket required if require_ticket_in_prod=true in settings.
  - In dev/test: reason-only or relaxed validation based on settings.
  - Dry-run mode: reason is always optional (never blocks simulation).

Used by Streamlit pages and CLI to consistently enforce the same rules
without duplicating validation logic.
"""
from __future__ import annotations

from dataclasses import dataclass

from engine.utils.logger import get_logger

log = get_logger(__name__)

# Actions that require a reason in all non-dry-run executions
_REASON_REQUIRED_ACTIONS = {
    "hk_enable",
    "hk_disable",
    "dry_run_promote",
    "run_hk_now",
    "cancel_query",
    "kill_switch",
    "circuit_breaker_reenable",
    "lifecycle_exemption",
    "claim_table",
    "policy_change",
    "bulk_template_apply",
    "table_unregister",
}

# Actions that additionally require a ticket number in prod
_TICKET_REQUIRED_ACTIONS_PROD = {
    "dry_run_promote",
    "run_hk_now",
    "kill_switch",
    "circuit_breaker_reenable",
    "lifecycle_exemption",
}


@dataclass
class ValidationResult:
    valid:         bool
    errors:        list[str]
    warnings:      list[str]

    @classmethod
    def ok(cls) -> ValidationResult:
        return cls(valid=True, errors=[], warnings=[])

    @classmethod
    def fail(cls, *errors: str) -> ValidationResult:
        return cls(valid=False, errors=list(errors), warnings=[])

    def with_warning(self, *warnings: str) -> ValidationResult:
        self.warnings.extend(warnings)
        return self


def validate(
    action_type:   str,
    reason:        str,
    ticket_number: str,
    environment:   str,
    dry_run:       bool,
) -> ValidationResult:
    """
    Validate reason/ticket for an action before execution.

    Args:
        action_type:   AuditAction constant (e.g. AuditAction.HK_ENABLE)
        reason:        User-provided reason string
        ticket_number: Change/ITSM ticket number (may be empty)
        environment:   prod | preprod | dev | test
        dry_run:       If True, validation is relaxed (reason never required)

    Returns:
        ValidationResult with valid=True/False and error messages
    """
    from config.platform_settings import get_settings
    settings = get_settings()

    # Dry-run mode -- always valid, at most a gentle warning
    if dry_run:
        result = ValidationResult.ok()
        if not reason:
            result.with_warning(
                "Adding a reason is recommended even for dry-run actions "
                "so the audit log is meaningful."
            )
        return result

    errors: list[str] = []

    # Reason required?
    reason_required = (
        action_type in _REASON_REQUIRED_ACTIONS
        and _env_requires_reason(environment, settings)
    )
    if reason_required and not reason.strip():
        errors.append(
            f"A reason is required for '{action_type}' in {environment}. "
            "Please describe why this action is being taken."
        )

    # Minimum reason length
    if reason.strip() and len(reason.strip()) < 10:
        errors.append(
            "Reason is too short (minimum 10 characters). "
            "Please provide a meaningful description."
        )

    # Ticket required?
    ticket_required = (
        environment == "prod"
        and action_type in _TICKET_REQUIRED_ACTIONS_PROD
        and settings.get("require_ticket_in_prod", False)
    )
    if ticket_required and not ticket_number.strip():
        errors.append(
            f"A change/ticket number is required for '{action_type}' "
            f"in production. Configure require_ticket_in_prod=false in "
            "Settings to relax this."
        )

    if errors:
        return ValidationResult.fail(*errors)
    return ValidationResult.ok()


def require_reason_for_action(action_type: str, environment: str) -> bool:
    """
    Quick check: does this action require a reason in this environment?
    Used by UI pages to decide whether to show the reason input field.
    """
    from config.platform_settings import get_settings
    settings = get_settings()
    return (
        action_type in _REASON_REQUIRED_ACTIONS
        and _env_requires_reason(environment, settings)
    )


def require_ticket_for_action(action_type: str, environment: str) -> bool:
    """
    Quick check: does this action require a ticket in this environment?
    """
    from config.platform_settings import get_settings
    settings = get_settings()
    return (
        environment == "prod"
        and action_type in _TICKET_REQUIRED_ACTIONS_PROD
        and settings.get("require_ticket_in_prod", False)
    )


def _env_requires_reason(environment: str, settings: dict) -> bool:
    """Determine if reason is required based on environment + settings."""
    if environment == "prod":
        return True
    if environment == "preprod":
        return settings.get("require_reason_in_preprod", True)
    # dev/test: configurable, default off
    return settings.get("require_reason_in_dev", False)


def render_reason_form(
    action_type: str,
    environment: str,
    key_prefix:  str = "reason",
) -> tuple[str, str]:
    """
    Render a Streamlit reason + ticket form.
    Returns (reason, ticket_number).
    Call this inside a Streamlit form or container.

    Only renders the ticket field if required for this action + environment.
    """
    import streamlit as st

    reason_required = require_reason_for_action(action_type, environment)
    ticket_required = require_ticket_for_action(action_type, environment)

    reason = st.text_area(
        "Reason" + (" *" if reason_required else " (optional)"),
        placeholder="Describe why this action is being taken...",
        key=f"{key_prefix}_reason",
        help="Required for production live actions." if reason_required else
             "Optional but recommended for audit trail.",
    )

    ticket = ""
    if ticket_required or environment == "prod":
        ticket = st.text_input(
            "Change / Ticket Number" + (" *" if ticket_required else " (optional)"),
            placeholder="e.g. CHG0012345 or JIRA-456",
            key=f"{key_prefix}_ticket",
        )

    return reason.strip(), ticket.strip()

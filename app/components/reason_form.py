"""
Zamboni -- Shared Reason / Ticket Form Component
Renders a consistent reason + optional ticket field for all pages
that require justification before a live or destructive action.

Usage:
    from app.components.reason_form import render_reason_form, validate_and_gate

    reason, ticket = render_reason_form(
        action_type="hk_enable",
        environment=APP_ENV,
        key_prefix="hk_enable_form",
    )
    if st.button("Enable HK"):
        result = validate_and_gate(action_type, reason, ticket, APP_ENV, dry_run)
        if result.valid:
            ...proceed...
        else:
            for err in result.errors:
                st.error(err)
"""
from __future__ import annotations

import streamlit as st

from engine.core.reason_validator import ValidationResult, validate


def render_reason_form(
    action_type: str,
    environment: str,
    dry_run:     bool = False,
    key_prefix:  str  = "reason_form",
    collapsed:   bool = False,
) -> tuple[str, str]:
    """
    Render reason + ticket inputs in a consistent style.
    Returns (reason, ticket_number) -- both stripped strings.

    In dry_run mode the fields are shown but marked optional.
    In prod live mode the fields are marked required per settings.
    """
    from engine.core.reason_validator import (
        require_reason_for_action,
        require_ticket_for_action,
    )

    reason_required = (not dry_run) and require_reason_for_action(action_type, environment)
    ticket_required = (not dry_run) and require_ticket_for_action(action_type, environment)

    label_suffix = " (dry run — optional)" if dry_run else (" *" if reason_required else " (optional)")

    if collapsed:
        with st.expander("📝 Reason / Change Ticket", expanded=reason_required):
            return _render_fields(key_prefix, reason_required, ticket_required,
                                  dry_run, label_suffix)
    return _render_fields(key_prefix, reason_required, ticket_required,
                          dry_run, label_suffix)


def _render_fields(
    key_prefix:      str,
    reason_required: bool,
    ticket_required: bool,
    dry_run:         bool,
    label_suffix:    str,
) -> tuple[str, str]:
    reason = st.text_area(
        f"Reason{label_suffix}",
        placeholder="Describe why this action is being taken (min 10 characters)...",
        key=f"{key_prefix}_reason",
        help=(
            "Required for live production actions."
            if reason_required
            else "Optional but recommended for audit trail."
        ),
    )

    ticket = ""
    if ticket_required:
        ticket = st.text_input(
            "Change / Ticket Number *",
            placeholder="e.g. CHG0012345",
            key=f"{key_prefix}_ticket",
            help="Change management ticket required for this action in production.",
        )
    elif not dry_run:
        ticket = st.text_input(
            "Change / Ticket Number (optional)",
            placeholder="e.g. CHG0012345",
            key=f"{key_prefix}_ticket",
        )

    return reason.strip(), ticket.strip()


def validate_and_gate(
    action_type:   str,
    reason:        str,
    ticket_number: str,
    environment:   str,
    dry_run:       bool,
) -> ValidationResult:
    """
    Validate reason/ticket. Shows st.error() messages for each failure.
    Returns ValidationResult so caller can check result.valid.
    """
    result = validate(action_type, reason, ticket_number, environment, dry_run)
    for err in result.errors:
        st.error(f"⛔ {err}")
    for warn in result.warnings:
        st.warning(f"⚠️ {warn}")
    return result


def dry_run_banner(dry_run: bool, environment: str) -> None:
    """Show a consistent dry-run / live banner at the top of action sections."""
    if dry_run:
        st.info(
            "🔵 **Dry Run Mode** — This action will be simulated. "
            "No changes will be written to AWS.",
            icon="🔵",
        )
    elif environment == "prod":
        st.warning(
            "🔴 **Live Mode — Production** — This action will execute immediately "
            "against production resources.",
            icon="🔴",
        )
    else:
        st.warning(
            f"🟡 **Live Mode — {environment.upper()}** — This action will execute "
            f"against {environment} resources.",
            icon="🟡",
        )

"""
Zamboni -- Settings / Administration Page
Central configuration for platform-wide behaviour.

Phase 1 scope:
  - execution_log and audit_log retention
  - dry-run defaults per environment
  - live activity refresh interval
  - budget alert threshold
  - reason/ticket enforcement rules
  - approval-required flags
  - backup/stale naming patterns
  - escalation matrix viewer and editor

Phase 2 placeholders (visible but disabled):
  - CloudWatch budget integration
  - Teams/Slack webhook config
  - SSO/LDAP integration
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


import streamlit as st

from app.components.auth import check_login, current_user
from app.components.header import render as render_header
from app.components.sidebar import is_dry_run
from app.components.sidebar import render as render_sidebar
from config.platform_settings import get_settings, save_settings, set_setting
from config.settings import APP_ENV
from engine.core.audit import AuditAction, AuditEvent, audit, diff_json
from engine.core.escalation import list_matrix, upsert_entry

st.set_page_config(
    page_title="Zamboni -- Settings",
    page_icon="⚙️",
    layout="wide",
)
check_login()
render_sidebar()
render_header(page_title="Settings", page_icon="🔧")
st.caption(
    "Platform-wide configuration for Zamboni. "
    "All changes are audited. Changes take effect immediately without restart."
)

settings = get_settings()
actor    = current_user()

tab_general, tab_enforcement, tab_escalation, tab_advanced = st.tabs([
    "📋 General",
    "🔒 Enforcement",
    "📣 Escalation Matrix",
    "🔧 Advanced",
])


# ── Tab 1: General ────────────────────────────────────────────────────────────
with tab_general:
    st.markdown("#### Retention")
    col1, col2 = st.columns(2)

    with col1:
        exec_retention = st.number_input(
            "Execution Log Retention (days)",
            min_value=7, max_value=3650,
            value=int(settings.get("execution_log_retention_days", 90)),
            help="execution_log rows older than this are eligible for archival.",
        )
    with col2:
        audit_retention = st.number_input(
            "Audit Log Retention (days)",
            min_value=30, max_value=3650,
            value=int(settings.get("audit_log_retention_days", 365)),
            help="audit_log rows older than this are eligible for archival. "
                 "Regulatory minimum is typically 365 days.",
        )

    st.divider()
    st.markdown("#### Live Activity")

    refresh_interval = st.selectbox(
        "Default Refresh Interval (seconds)",
        options=[10, 15, 30, 60, 120, 300],
        index=[10, 15, 30, 60, 120, 300].index(
            int(settings.get("live_activity_refresh_interval_seconds", 30))
        ),
        help="Default auto-refresh rate for the Live Activity page.",
    )

    st.divider()
    st.markdown("#### Budget Alert")

    budget_threshold = st.number_input(
        "Monthly Athena Budget Threshold (USD, 0 = disabled)",
        min_value=0, max_value=100000,
        value=int(settings.get("budget_alert_threshold_usd_monthly", 0)),
        help="SNS alert sent when estimated monthly Athena cost exceeds this. "
             "Set to 0 to disable.",
    )

    st.divider()
    st.markdown("#### Default Dry-Run Mode per Environment")
    dry_run_defaults = settings.get("default_dry_run", {})
    col_p, col_pp, col_d, col_t = st.columns(4)
    dr_prod    = col_p.checkbox("prod",    value=dry_run_defaults.get("prod",    True))
    dr_preprod = col_pp.checkbox("preprod", value=dry_run_defaults.get("preprod", True))
    dr_dev     = col_d.checkbox("dev",     value=dry_run_defaults.get("dev",     True))
    dr_test    = col_t.checkbox("test",    value=dry_run_defaults.get("test",    True))

    if is_dry_run():
        st.warning("⚠️ Sidebar Dry Run is ON — settings changes will be simulated.")

    if st.button("💾 Save General Settings", type="primary"):
        new_settings = dict(settings)
        before = {
            "execution_log_retention_days":          settings.get("execution_log_retention_days"),
            "audit_log_retention_days":              settings.get("audit_log_retention_days"),
            "live_activity_refresh_interval_seconds": settings.get("live_activity_refresh_interval_seconds"),
            "budget_alert_threshold_usd_monthly":    settings.get("budget_alert_threshold_usd_monthly"),
            "default_dry_run":                       settings.get("default_dry_run"),
        }
        new_settings.update({
            "execution_log_retention_days":          exec_retention,
            "audit_log_retention_days":              audit_retention,
            "live_activity_refresh_interval_seconds": refresh_interval,
            "budget_alert_threshold_usd_monthly":    budget_threshold,
            "default_dry_run": {
                "prod": dr_prod, "preprod": dr_preprod,
                "dev": dr_dev,   "test": dr_test,
            },
        })
        bv, av = diff_json(before, {k: new_settings[k] for k in before})

        if not is_dry_run():
            ok = save_settings(new_settings)
        else:
            ok = True

        audit(AuditEvent(
            actor=actor, action_type=AuditAction.SETTINGS_CHANGE,
            page_source="11_Settings", target_type="setting",
            target_id="general", environment=APP_ENV,
            dry_run=is_dry_run(),
            status="DRY_RUN" if is_dry_run() else ("SUCCESS" if ok else "FAILURE"),
            before_value=bv, after_value=av,
        ))

        if ok:
            st.success(
                "✅ General settings saved"
                + (" (dry run)" if is_dry_run() else "")
            )
        else:
            st.error("Failed to save settings.")


# ── Tab 2: Enforcement ────────────────────────────────────────────────────────
with tab_enforcement:
    st.markdown("#### Reason / Ticket Requirements")
    st.info(
        "These settings control when users must provide a reason and/or a "
        "change ticket before executing live or destructive actions."
    )

    col1, col2 = st.columns(2)
    with col1:
        req_reason_preprod = st.checkbox(
            "Require reason in preprod",
            value=settings.get("require_reason_in_preprod", True),
        )
        req_reason_dev = st.checkbox(
            "Require reason in dev/test",
            value=settings.get("require_reason_in_dev", False),
        )
    with col2:
        req_ticket_prod = st.checkbox(
            "Require change ticket in prod",
            value=settings.get("require_ticket_in_prod", False),
            help="If enabled, Run HK Now, Promote to Live, Kill Switch, "
                 "and circuit breaker re-enable require a change ticket number in production.",
        )

    st.divider()
    st.markdown("#### Approval Gates for Destructive Operations")
    st.caption("When enabled, these actions require a second user to approve before executing.")

    approval = settings.get("approval_required_for", {})
    ap_drop   = st.checkbox("Approve before dropping non-prod table",
                            value=approval.get("drop_table", False))
    ap_bulk   = st.checkbox("Approve before bulk HK enable (> 20 tables)",
                            value=approval.get("bulk_hk_enable", False))
    ap_promote = st.checkbox("Approve before dry-run promote to live",
                             value=approval.get("promote_to_live", False))

    st.caption(
        "Note: Phase 1 gates block the action and require re-run by the same user "
        "after confirmation. Full two-user approval workflow is Phase 2."
    )

    if st.button("💾 Save Enforcement Settings", type="primary"):
        new_settings = dict(settings)
        new_settings.update({
            "require_reason_in_preprod": req_reason_preprod,
            "require_reason_in_dev":     req_reason_dev,
            "require_ticket_in_prod":    req_ticket_prod,
            "approval_required_for": {
                "drop_table":      ap_drop,
                "bulk_hk_enable":  ap_bulk,
                "promote_to_live": ap_promote,
            },
        })

        if not is_dry_run():
            ok = save_settings(new_settings)
        else:
            ok = True

        audit(AuditEvent(
            actor=actor, action_type=AuditAction.SETTINGS_CHANGE,
            page_source="11_Settings", target_type="setting",
            target_id="enforcement", environment=APP_ENV,
            dry_run=is_dry_run(),
            status="DRY_RUN" if is_dry_run() else ("SUCCESS" if ok else "FAILURE"),
        ))
        st.success("✅ Enforcement settings saved"
                   + (" (dry run)" if is_dry_run() else ""))


# ── Tab 3: Escalation Matrix ─────────────────────────────────────────────────
with tab_escalation:
    st.markdown("#### Escalation Matrix")
    st.info(
        "Route alerts and notifications by domain, tier, and environment. "
        "Lookup is most-specific-first: domain+tier+env → domain+env → domain → "
        "tier+env → env → default."
    )

    matrix_entries = list_matrix()
    if matrix_entries:
        import pandas as pd
        df = pd.DataFrame(matrix_entries)
        display_cols = [c for c in ["_key", "primary_owner_email",
                                    "escalation_email", "zamboni_owner_email",
                                    "notify_sns_topic"] if c in df.columns]
        st.dataframe(df[display_cols], use_container_width=True, hide_index=True)
    else:
        st.caption("No entries configured yet. Add entries below.")

    st.divider()
    st.markdown("#### Add / Update Entry")
    with st.form("escalation_form"):
        key_help = (
            "Lookup key format — examples:\n"
            "  domain:finance|tier:critical|env:prod\n"
            "  domain:finance|env:prod\n"
            "  domain:finance\n"
            "  tier:critical|env:prod\n"
            "  default"
        )
        esc_key     = st.text_input("Lookup Key *", help=key_help)
        esc_primary = st.text_input("Primary Owner Email")
        esc_esc     = st.text_input("Escalation Email")
        esc_zamboni = st.text_input("Zamboni Owner Email")
        esc_sns     = st.text_input("SNS Topic ARN Override (blank = use default)")

        submitted = st.form_submit_button("Save Entry", type="primary")

    if submitted and esc_key:
        entry = {
            "primary_owner_email":  esc_primary,
            "escalation_email":     esc_esc,
            "zamboni_owner_email":  esc_zamboni,
            "notify_sns_topic":     esc_sns,
        }
        ok = upsert_entry(esc_key, entry, actor=actor, dry_run=is_dry_run())
        st.success(
            f"✅ Escalation entry '{esc_key}' saved"
            + (" (dry run)" if is_dry_run() else "")
        )


# ── Tab 4: Advanced ───────────────────────────────────────────────────────────
with tab_advanced:
    st.markdown("#### Backup / Stale Table Naming Patterns")
    st.caption(
        "Tables matching these patterns are auto-flagged as backup candidates "
        "by the Lifecycle Engine."
    )

    patterns = settings.get("backup_stale_name_patterns", [])
    patterns_str = st.text_area(
        "Patterns (one per line)",
        value="\n".join(patterns),
        height=150,
        help="Plain strings or regex patterns. Example: _bkp, _\\d{8}$",
    )

    if st.button("💾 Save Patterns", type="primary"):
        new_patterns = [p.strip() for p in patterns_str.splitlines() if p.strip()]
        set_setting("backup_stale_name_patterns", new_patterns,
                    actor=actor, dry_run=is_dry_run())
        st.success("✅ Patterns saved" + (" (dry run)" if is_dry_run() else ""))

    st.divider()
    st.markdown("#### Phase 2 Placeholders")
    st.caption("These features are planned for Phase 2 and are not active.")

    # ── Teams Webhook ────────────────────────────────────────────────────────
    st.markdown("#### Microsoft Teams Notifications")
    st.caption(
        "Fire Teams messages for key user actions: HK enable/disable, "
        "policy changes, circuit breaker re-enables, lifecycle exemptions. "
        "Does NOT fire on dry-run actions."
    )

    teams_enabled = st.checkbox(
        "Enable Teams notifications",
        value=bool(settings.get("teams_enabled", False)),
        help="Requires a valid incoming webhook URL below.",
    )

    from engine.core.teams_notifier import mask_webhook_url
    existing_url  = settings.get("teams_webhook_url", "")
    masked        = mask_webhook_url(existing_url) if existing_url else ""

    teams_url = st.text_input(
        "Teams Incoming Webhook URL",
        value="",
        placeholder=masked or "https://outlook.office.com/webhook/...",
        help=(
            "Leave blank to keep the existing URL. "
            "The URL is masked after save for security."
            + (f"  Current: {masked}" if masked else "")
        ),
        type="password",
    )

    col_t1, col_t2 = st.columns(2)
    with col_t1:
        if st.button("💾 Save Teams Settings", type="primary"):
            new_settings = dict(settings)
            new_settings["teams_enabled"] = teams_enabled
            # Only update URL if a new one was entered
            if teams_url.strip():
                new_settings["teams_webhook_url"] = teams_url.strip()

            if not is_dry_run():
                ok = save_settings(new_settings)
            else:
                ok = True

            audit(AuditEvent(
                actor=actor, action_type=AuditAction.SETTINGS_CHANGE,
                page_source="11_Settings", target_type="setting",
                target_id="teams", environment=APP_ENV,
                dry_run=is_dry_run(),
                status="DRY_RUN" if is_dry_run() else ("SUCCESS" if ok else "FAILURE"),
                after_value=f"teams_enabled={teams_enabled}",
            ))
            st.success("✅ Teams settings saved"
                       + (" (dry run)" if is_dry_run() else ""))

    with col_t2:
        if existing_url and st.button("🧪 Send Test Message"):
            from engine.core.teams_notifier import TeamsEvent, notify_teams
            ok = notify_teams(TeamsEvent(
                title="Zamboni Test Notification",
                summary="This is a test message from Zamboni Settings page.",
                actor=actor,
                action_type="hk_enable",
                target="test",
                environment=APP_ENV,
                status="SUCCESS",
                reason="Settings page test",
            ))
            if ok:
                st.success("✅ Test message sent successfully.")
            else:
                st.error("❌ Test message failed. Check the webhook URL and Teams channel.")

    st.caption(
        "Note: Slack webhook support is Phase 2.3. "
        "SNS remains the primary engine-level notification channel."
    )

    st.divider()

    # ── AWS Cost Explorer ─────────────────────────────────────────────────────
    st.markdown("#### AWS Cost Explorer Integration")
    st.caption(
        "Pull real Athena billing data instead of estimating from bytes_scanned. "
        "Results are cached for 24h to minimise API cost ($0.01/request)."
    )

    ce_enabled = st.checkbox(
        "Enable Cost Explorer integration",
        value=bool(settings.get("cost_explorer_enabled", False)),
        help="Requires ce:GetCostAndUsage IAM permission on zamboni-ec2-role.",
    )

    if ce_enabled:
        st.info(
            "**IAM requirement:** `ce:GetCostAndUsage` must be in `deploy/iam_policy.json`. "
            "This is already included — re-run `deploy/create_athena_tables.sh` "
            "or update the IAM role manually if deploying to a new environment."
        )
        tag_key   = st.text_input(
            "Cost allocation tag key",
            value=settings.get("cost_explorer_tag_key", "zamboni:managed"),
            help="Tag applied to zamboni-* Athena workgroups for cost filtering.",
        )
        tag_value = st.text_input(
            "Cost allocation tag value",
            value=settings.get("cost_explorer_tag_value", "true"),
        )
        st.caption(
            "Without cost allocation tags, the Cost Report shows ALL Athena "
            "cost in the AWS account. Add the tag to your zamboni-* workgroups "
            "in the AWS console to filter to Zamboni-only spend."
        )
    else:
        tag_key   = settings.get("cost_explorer_tag_key", "zamboni:managed")
        tag_value = settings.get("cost_explorer_tag_value", "true")

    if st.button("💾 Save Cost Explorer Settings", type="primary"):
        new_settings = dict(settings)
        new_settings.update({
            "cost_explorer_enabled":   ce_enabled,
            "cost_explorer_tag_key":   tag_key,
            "cost_explorer_tag_value": tag_value,
        })
        if not is_dry_run():
            ok = save_settings(new_settings)
        else:
            ok = True
        audit(AuditEvent(
            actor=actor, action_type=AuditAction.SETTINGS_CHANGE,
            page_source="11_Settings", target_type="setting",
            target_id="cost_explorer", environment=APP_ENV,
            dry_run=is_dry_run(),
            status="DRY_RUN" if is_dry_run() else ("SUCCESS" if ok else "FAILURE"),
            after_value=f"ce_enabled={ce_enabled}",
        ))
        st.success("✅ Cost Explorer settings saved"
                   + (" (dry run)" if is_dry_run() else ""))

    with st.expander("SSO / LDAP Integration (Phase 2.3)"):
        st.caption(
            "Phase 2: Replace the current username/password auth with SSO "
            "for automatic current_user() population and LDAP-based role mapping."
        )

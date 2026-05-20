"""
Zamboni -- Domain Management
List, register, edit, activate/deactivate domains.

Fixes (UI review):
  - display_name + registered_at now shown in list
  - Environment removed from register form (Zamboni is deployed per-env)
  - Edit form now shows ALL editable fields with current values
  - Edit form uses domain-keyed Streamlit widget keys to prevent stale state
  - domain_filter SQL uses 1 not true (SQLite compat)
  - Tooltips added to all fields
  - Cache cleared on register/update so list refreshes immediately
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import streamlit as st

from app.components.athena_runner import cached_read_registry, execute_write
from app.components.auth import check_login, current_user
from app.components.header import render as render_header
from app.components.sidebar import is_dry_run
from app.components.sidebar import render as render_sidebar
from app.components.status_badge import yes_no
from config.settings import APP_ENV, DOMAIN_REGISTRY_TABLE
from engine.core import registry
from engine.core.audit import AuditAction, AuditEvent, audit

st.set_page_config(
    page_title="Zamboni -- Domain Management",
    page_icon="📋",
    layout="wide",
)

if not check_login():
    st.stop()
render_sidebar()
render_header(page_title="Domain Management", page_icon="📋")

tab_list, tab_register, tab_edit = st.tabs([
    "📊 All Domains",
    "➕ Register New",
    "✏️ Edit Domain",
])


# ── Tab 1: List all domains ───────────────────────────────────────────────────
with tab_list:
    st.subheader("Registered Domains")

    # Auto-refresh: if a write happened (register/update sets this flag),
    # clear cache and rerun so the table reflects the change immediately.
    if st.session_state.get("domain_needs_refresh", False):
        st.session_state["domain_needs_refresh"] = False
        cached_read_registry.clear()
        st.rerun()

    col_refresh, _ = st.columns([1, 5])
    with col_refresh:
        if st.button("🔄 Refresh", key="domain_list_refresh"):
            cached_read_registry.clear()
            st.rerun()

    sql = f"SELECT * FROM {DOMAIN_REGISTRY_TABLE} ORDER BY domain_name"
    try:
        df = cached_read_registry(sql)
        if df.empty:
            st.info("No domains registered yet. Use **Register New** tab to add one.")
        else:
            # Build display — only show columns that exist
            want_cols = [
                "domain_name", "display_name", "owner_email",
                "archive_enabled", "hot_retention_days",
                "archive_duration_days", "stale_threshold_days",
                "is_active", "digest_enabled", "registered_at",
            ]
            show_cols = [c for c in want_cols if c in df.columns]
            display = df[show_cols].copy()

            for bool_col in ["archive_enabled", "is_active", "digest_enabled"]:
                if bool_col in display.columns:
                    display[bool_col] = display[bool_col].apply(yes_no)

            if "registered_at" in display.columns:
                display["registered_at"] = display["registered_at"].astype(str).str[:10]

            display.rename(columns={
                "domain_name":          "Domain",
                "display_name":         "Display Name",
                "owner_email":          "Owner Email",
                "archive_enabled":      "Archival",
                "hot_retention_days":   "Hot Retention (d)",
                "archive_duration_days":"Archive Duration (d)",
                "stale_threshold_days": "Stale Threshold (d)",
                "is_active":            "Active",
                "digest_enabled":       "Digest",
                "registered_at":        "Registered",
            }, inplace=True, errors="ignore")

            # Enrich with table count per domain
            try:
                from config.settings import STREAM_REGISTRY_TABLE
                counts_df = cached_read_registry(
                    f"SELECT domain, COUNT(*) AS table_count "
                    f"FROM {STREAM_REGISTRY_TABLE} GROUP BY domain"
                )
                if not counts_df.empty:
                    counts_map = dict(zip(counts_df["domain"], counts_df["table_count"].astype(int)))
                    display.insert(
                        1, "Tables",
                        display["Domain"].map(counts_map).fillna(0).astype(int)
                    )
            except Exception:
                pass

            st.dataframe(display, use_container_width=True, hide_index=True)
            st.caption(f"{len(df)} domain(s) registered")
    except Exception as e:
        st.error(f"Could not load domains: {e}")


# ── Tab 2: Register a new domain ──────────────────────────────────────────────
with tab_register:
    st.subheader("Register a New Domain")
    st.caption(
        "Domains are the top-level grouping for all tables in Zamboni. "
        "Every table must belong to a domain before it can be registered."
    )

    with st.form("register_domain_form", clear_on_submit=True):
        col1, col2 = st.columns(2)

        with col1:
            domain_name = st.text_input(
                "Domain Name *",
                help="Lowercase, no spaces. Used as the key throughout Zamboni. "
                     "Examples: finance, ers, membership",
                placeholder="finance",
            )
            display_name = st.text_input(
                "Display Name *",
                help="Human-readable name shown in the UI. Example: Finance",
                placeholder="Finance",
            )
            owner_name = st.text_input(
                "Owner Name",
                help="Name of the responsible person or team lead.",
                placeholder="John Smith",
            )
            owner_email = st.text_input(
                "Owner Email *",
                help="Team distribution list or individual email. "
                     "Used for GREENZONE and lifecycle notifications.",
                placeholder="da-finance@company.com",
            )
            team_name = st.text_input(
                "Team",
                help="Team or squad name for display purposes.",
                placeholder="Data & Analytics - Finance",
            )
            ci_number = st.text_input(
                "CI Number",
                help="ITSM Configuration Item number for change management traceability.",
                placeholder="CI-10234",
            )

        with col2:
            archive_enabled = st.checkbox(
                "Enable Archival",
                value=True,
                help="Allow the Archival Engine to export and delete cold staging partitions "
                     "for tables in this domain. Individual tables can override this.",
            )
            hot_retention_days = st.number_input(
                "Hot Retention (days)",
                min_value=1, value=30,
                help="How long staging data stays in S3 Standard before archival. "
                     "Finance=30, ERS=7, Claims=90.",
            )
            archive_duration_days = st.number_input(
                "Archive Duration (days)",
                min_value=1, value=365,
                help="How long archived data is kept in S3 Intelligent-Tiering "
                     "before it can be deleted.",
            )
            stale_threshold_days = st.number_input(
                "Stale Threshold (days)",
                min_value=7, value=60,
                help="Non-prod tables inactive beyond this threshold are flagged "
                     "as STALE_CANDIDATE by the Lifecycle Engine.",
            )
            auto_delete_after_days = st.number_input(
                "Auto-Delete After (days)",
                min_value=30, value=120,
                help="Non-prod tables that have completed GREENZONE review and are "
                     "still inactive are dropped after this many days.",
            )

        description = st.text_area(
            "Description",
            help="Brief description of what this domain covers. "
                 "Shown in the domain list for onboarding.",
            placeholder="Finance domain covering payment, claims and reconciliation pipelines",
        )
        notes = st.text_area(
            "Notes",
            help="Internal notes — not shown in the main UI. Use for decisions, "
                 "dependencies, or escalation context.",
        )

        st.divider()
        st.markdown("**📬 Weekly HK Digest**")
        st.caption("Send a weekly housekeeping summary to domain owners.")

        col_d1, col_d2 = st.columns(2)
        with col_d1:
            digest_enabled = st.checkbox(
                "Include in weekly digest",
                value=False,
                help="Domain owner receives a weekly email summary of HK activity, "
                     "SLA breaches, and cost. Email sender wires in Phase 2.2.",
            )
        with col_d2:
            digest_email = st.text_input(
                "Digest recipient email (optional)",
                placeholder="Leave blank to use Owner Email above",
                help="Override where the weekly digest is sent. "
                     "Useful when a team DL differs from the owner's personal email.",
            )

        if is_dry_run():
            st.info("🔵 Dry Run — form will be validated but nothing will be written.")

        submitted = st.form_submit_button("Register Domain", type="primary")

        if submitted:
            if not domain_name.strip() or not display_name.strip() or not owner_email.strip():
                st.error("Domain Name, Display Name, and Owner Email are required.")
            else:
                try:
                    from engine.core.teams_notifier import TeamsEvent, notify_teams, should_notify
                    registry.register_domain(
                        domain_name=domain_name.strip().lower(),
                        display_name=display_name.strip(),
                        description=description.strip(),
                        owner_name=owner_name.strip(),
                        owner_email=owner_email.strip(),
                        team_name=team_name.strip(),
                        archive_enabled=archive_enabled,
                        hot_retention_days=int(hot_retention_days),
                        archive_duration_days=int(archive_duration_days),
                        stale_threshold_days=int(stale_threshold_days),
                        auto_delete_after_days=int(auto_delete_after_days),
                        environment=APP_ENV,
                        registered_by=f"streamlit:{current_user()}",
                        notes=notes.strip(),
                        dry_run=is_dry_run(),
                    )
                    if should_notify("hk_enable", is_dry_run(), "SUCCESS"):
                        notify_teams(TeamsEvent(
                            title=f"Domain Registered: {domain_name}",
                            summary=(f"New domain '{domain_name}' registered in Zamboni "
                                     f"by {current_user()}"),
                            actor=current_user(),
                            action_type="domain_create",
                            target=domain_name,
                            environment=APP_ENV,
                            status="SUCCESS",
                            reason=notes or "",
                        ))
                    audit(AuditEvent(
                        actor=current_user(),
                        action_type=AuditAction.DOMAIN_CREATE,
                        page_source="1_Domain_Management",
                        target_type="domain",
                        target_id=domain_name.strip().lower(),
                        dry_run=is_dry_run(),
                        status="DRY_RUN" if is_dry_run() else "SUCCESS",
                        after_value=(f"owner={owner_email},"
                                     f"retention={hot_retention_days}"),
                    ))
                    st.success(
                        f"✅ Domain **{domain_name}** registered."
                        + (" (dry run)" if is_dry_run() else "")
                    )
                    cached_read_registry.clear()
                    st.session_state["edit_domain_last"] = domain_name.strip().lower()
                    st.session_state["domain_needs_refresh"] = True  # triggers tab1 rerun
                except ValueError as e:
                    st.error(str(e))
                except Exception as e:
                    st.error(f"Registration failed: {e}")


# ── Tab 3: Edit a domain ──────────────────────────────────────────────────────
with tab_edit:
    st.subheader("Edit Domain")

    # Load domain list
    try:
        dom_df = cached_read_registry(
            f"SELECT domain_name FROM {DOMAIN_REGISTRY_TABLE} ORDER BY domain_name"
        )
        domain_names = dom_df["domain_name"].tolist() if not dom_df.empty else []
    except Exception:
        domain_names = []

    if not domain_names:
        st.info("No domains to edit. Register one first.")
    else:
        # Remember the last selected domain across reruns using session_state
        _prev = st.session_state.get("edit_domain_last", domain_names[0])
        _default_idx = domain_names.index(_prev) if _prev in domain_names else 0

        selected_domain = st.selectbox(
            "Select Domain to Edit",
            domain_names,
            index=_default_idx,
            key="edit_domain_selector",
            help="Select the domain to edit. Your selection is remembered "
                 "within this session.",
        )
        st.session_state["edit_domain_last"] = selected_domain

        if selected_domain:
            domain = registry.get_domain(selected_domain)

        if selected_domain and domain:
            # Use domain-keyed widget keys to prevent stale state
            # when switching between domains
            k = selected_domain  # key prefix — changes per domain

            with st.form(f"edit_domain_form_{k}"):
                col1, col2 = st.columns(2)

                with col1:
                    e_display_name = st.text_input(
                        "Display Name",
                        value=str(domain.get("display_name") or ""),
                        key=f"{k}_display",
                        help="Human-readable name shown in the UI.",
                    )
                    e_owner_name = st.text_input(
                        "Owner Name",
                        value=str(domain.get("owner_name") or ""),
                        key=f"{k}_owner_name",
                        help="Responsible person or team lead name.",
                    )
                    e_owner_email = st.text_input(
                        "Owner Email *",
                        value=str(domain.get("owner_email") or ""),
                        key=f"{k}_owner_email",
                        help="Team DL or individual email for notifications.",
                    )
                    e_team_name = st.text_input(
                        "Team",
                        value=str(domain.get("team_name") or ""),
                        key=f"{k}_team",
                        help="Team or squad name.",
                    )
                    e_ci = st.text_input(
                        "CI Number",
                        value=str(domain.get("ci_number") or ""),
                        key=f"{k}_ci",
                        help="ITSM Configuration Item number.",
                    )

                with col2:
                    e_archive_enabled = st.checkbox(
                        "Archival Enabled",
                        value=bool(domain.get("archive_enabled", False)),
                        key=f"{k}_archive",
                        help="Allow Archival Engine to process tables in this domain.",
                    )
                    e_hot_retention = st.number_input(
                        "Hot Retention (days)",
                        min_value=1,
                        value=int(domain.get("hot_retention_days") or 30),
                        key=f"{k}_hot_ret",
                        help="Days staging data stays in S3 Standard before archival.",
                    )
                    e_archive_duration = st.number_input(
                        "Archive Duration (days)",
                        min_value=1,
                        value=int(domain.get("archive_duration_days") or 365),
                        key=f"{k}_arch_dur",
                        help="How long archived data is kept in S3 Intelligent-Tiering.",
                    )
                    e_stale = st.number_input(
                        "Stale Threshold (days)",
                        min_value=7,
                        value=int(domain.get("stale_threshold_days") or 60),
                        key=f"{k}_stale",
                        help="Non-prod tables inactive beyond this are flagged STALE_CANDIDATE.",
                    )
                    e_auto_delete = st.number_input(
                        "Auto-Delete After (days)",
                        min_value=30,
                        value=int(domain.get("auto_delete_after_days") or 120),
                        key=f"{k}_auto_del",
                        help="Non-prod tables dropped after GREENZONE review + this many days.",
                    )
                    e_active = st.checkbox(
                        "Domain Active",
                        value=bool(domain.get("is_active", True)),
                        key=f"{k}_active",
                        help="Inactive domains are retained for audit but HK is suspended.",
                    )
                    e_digest_enabled = st.checkbox(
                        "Weekly Digest Enabled",
                        value=bool(domain.get("digest_enabled", False)),
                        key=f"{k}_digest",
                        help="Include this domain in the weekly HK summary email.",
                    )
                    e_digest_email = st.text_input(
                        "Digest Email Override",
                        value=str(domain.get("digest_email") or ""),
                        key=f"{k}_digest_email",
                        placeholder="Leave blank to use Owner Email",
                        help="Override digest recipient.",
                    )

                e_notes = st.text_area(
                    "Notes",
                    value=str(domain.get("notes") or ""),
                    key=f"{k}_notes",
                    help="Internal notes — not shown in public domain list.",
                )

                if is_dry_run():
                    st.info("🔵 Dry Run — changes will be validated but not written.")

                if st.form_submit_button("💾 Update Domain", type="primary"):
                    try:
                        sql_upd = f"""
                            UPDATE {DOMAIN_REGISTRY_TABLE}
                            SET display_name          = '{e_display_name}',
                                owner_name            = '{e_owner_name}',
                                owner_email           = '{e_owner_email}',
                                team_name             = '{e_team_name}',
                                ci_number             = '{e_ci}',
                                archive_enabled       = {'1' if e_archive_enabled else '0'},
                                hot_retention_days    = {int(e_hot_retention)},
                                archive_duration_days = {int(e_archive_duration)},
                                stale_threshold_days  = {int(e_stale)},
                                auto_delete_after_days= {int(e_auto_delete)},
                                is_active             = {'1' if e_active else '0'},
                                digest_enabled        = {'1' if e_digest_enabled else '0'},
                                digest_email          = '{e_digest_email}',
                                notes                 = '{e_notes}',
                                updated_at            = CURRENT_TIMESTAMP
                            WHERE domain_name = '{selected_domain}'
                        """
                        execute_write(sql_upd, dry_run=is_dry_run())
                        audit(AuditEvent(
                            actor=current_user(),
                            action_type=AuditAction.DOMAIN_UPDATE,
                            page_source="1_Domain_Management",
                            target_type="domain",
                            target_id=selected_domain,
                            dry_run=is_dry_run(),
                            status="DRY_RUN" if is_dry_run() else "SUCCESS",
                            reason=e_notes,
                        ))
                        cached_read_registry.clear()
                        st.session_state["edit_domain_last"] = selected_domain
                        st.session_state["domain_needs_refresh"] = True  # triggers tab1 rerun
                        st.success(
                            f"✅ Domain **{selected_domain}** updated."
                            + (" (dry run)" if is_dry_run() else "")
                        )
                    except Exception as e:
                        st.error(f"Update failed: {e}")

            # ── Digest Preview ─────────────────────────────────────────────
            st.divider()
            st.markdown("**📬 Weekly Digest Preview**")
            st.caption("See what the weekly HK summary would contain. Email sender: Phase 2.2.")
            if st.button("👁️ Preview Digest", key=f"digest_preview_{k}"):
                from engine.core.digest import build_digest
                with st.spinner("Building digest..."):
                    digest = build_digest(selected_domain, days=7)
                if "error" in digest:
                    st.error(f"Digest failed: {digest['error']}")
                else:
                    s = digest.get("summary", {})
                    d1, d2, d3, d4 = st.columns(4)
                    d1.metric("Tables Touched",   int(s.get("tables_touched", 0)))
                    d2.metric("Successes",        int(s.get("successes", 0)))
                    d3.metric("Failures",         int(s.get("failures", 0)))
                    d4.metric("Athena Cost (7d)", f"${float(s.get('athena_cost_usd', 0)):.4f}")
                    if digest.get("sla_breaches"):
                        st.warning(f"⚠️ {len(digest['sla_breaches'])} SLA breaches this week.")
                    if digest.get("top_failures"):
                        st.error(f"❌ {len(digest['top_failures'])} failure types.")
                    st.caption(f"Recipient: {digest.get('recipient_email') or 'not configured'}")
                    st.caption(f"Email sender: {digest.get('email_sender_status', '—')}")

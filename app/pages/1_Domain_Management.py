"""
Zamboni — Domain Management
List, register, edit, activate/deactivate domains.
"""
import streamlit as st

from app.components.athena_runner import cached_read_registry, execute_write
from app.components.auth import check_login, current_user
from app.components.header import render as render_header
from app.components.sidebar import is_dry_run
from app.components.sidebar import render as render_sidebar
from app.components.status_badge import yes_no
from config.settings import APP_ENV, DOMAIN_REGISTRY_TABLE, VALID_ENVIRONMENTS
from engine.core import registry
from engine.core.audit import AuditAction, AuditEvent, audit

st.set_page_config(page_title="Zamboni — Domain Management", page_icon="📋", layout="wide")

if not check_login():
    st.stop()
render_sidebar()
render_header()


st.markdown("# 📋 Domain Management")
st.markdown("Register, configure, and manage Zamboni-tracked domains.")
st.markdown("---")


# ── Tabs ──────────────────────────────────────────────────────────────────────
tab_list, tab_register, tab_edit = st.tabs([
    "📊 All Domains",
    "➕ Register New",
    "✏️ Edit Domain",
])


# ── Tab 1: List all domains ──────────────────────────────────────────────────
with tab_list:
    st.subheader("Registered Domains")

    sql = f"SELECT * FROM {DOMAIN_REGISTRY_TABLE} ORDER BY domain_name"
    try:
        df = cached_read_registry(sql)
        if df.empty:
            st.info("No domains registered yet. Use **Register New** tab to add one.")
        else:
            display = df[[
                "domain_name", "display_name", "owner_email",
                "archive_enabled", "hot_retention_days",
                "stale_threshold_days", "is_active", "registered_at",
            ]].copy()
            display["archive_enabled"] = display["archive_enabled"].apply(yes_no)
            display["is_active"]       = display["is_active"].apply(yes_no)
            display["registered_at"]   = display["registered_at"].astype(str).str[:19]
            st.dataframe(display, use_container_width=True, hide_index=True)
    except Exception as e:
        st.error(f"Could not load domains: {e}")


# ── Tab 2: Register a new domain ─────────────────────────────────────────────
with tab_register:
    st.subheader("Register a New Domain")

    with st.form("register_domain_form", clear_on_submit=True):
        col1, col2 = st.columns(2)

        with col1:
            domain_name  = st.text_input("Domain Name *", help="lowercase, no spaces (e.g. finance, ers)")
            display_name = st.text_input("Display Name *", help="Human-readable")
            owner_name   = st.text_input("Owner Name")
            owner_email  = st.text_input("Owner Email *")
            team_name    = st.text_input("Team")
            environment  = st.selectbox("Environment", VALID_ENVIRONMENTS, index=0)

        with col2:
            archive_enabled         = st.checkbox("Enable Archival", value=True)
            hot_retention_days      = st.number_input("Hot Retention (days)", min_value=1, value=30)
            archive_duration_days   = st.number_input("Archive Duration (days)", min_value=1, value=365)
            stale_threshold_days    = st.number_input("Stale Threshold (days)", min_value=7, value=60)
            auto_delete_after_days  = st.number_input("Auto-Delete After (days)", min_value=30, value=120)

        description = st.text_area("Description")
        notes       = st.text_area("Notes")

        st.divider()
        st.markdown("**Weekly HK Digest**")
        col_d1, col_d2 = st.columns(2)
        with col_d1:
            digest_enabled = st.checkbox(
                "Include in weekly digest email",
                value=False,
                help="Domain owner receives a weekly summary of HK activity.",
            )
        with col_d2:
            digest_email = st.text_input(
                "Digest recipient email (optional)",
                placeholder="Leave blank to use Owner Email",
                help="Override where the weekly digest is sent for this domain.",
            )

        submitted = st.form_submit_button("Register Domain", type="primary")

        if submitted:
            if not domain_name or not display_name or not owner_email:
                st.error("Domain name, display name, and owner email are required.")
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
                        environment=environment,
                        registered_by=f"streamlit:{current_user()}",
                        notes=notes.strip(),
                        dry_run=is_dry_run(),
                    )
                    # Fire Teams notification for domain creation
                    if should_notify("hk_enable", is_dry_run(), "SUCCESS"):
                        notify_teams(TeamsEvent(
                            title=f"Domain Registered: {domain_name}",
                            summary=f"New domain '{domain_name}' registered in Zamboni by {current_user()}",
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
                        after_value=f"owner={owner_email},retention={hot_retention_days}",
                    ))
                    st.success(f"✅ Domain '{domain_name}' registered successfully.")
                    if is_dry_run():
                        st.info("ℹ️ Dry run mode — no actual writes were made.")
                    cached_read_registry.clear()
                except ValueError as e:
                    st.error(str(e))
                except Exception as e:
                    st.error(f"Registration failed: {e}")


# ── Tab 3: Edit a domain ─────────────────────────────────────────────────────
with tab_edit:
    st.subheader("Edit Domain")

    sql = f"SELECT domain_name FROM {DOMAIN_REGISTRY_TABLE} ORDER BY domain_name"
    try:
        domains_df = cached_read_registry(sql)
        domain_names = domains_df["domain_name"].tolist()
    except Exception:
        domain_names = []

    if not domain_names:
        st.info("No domains to edit. Register one first.")
    else:
        selected_domain = st.selectbox("Select Domain to Edit", domain_names)

        # Load current config
        domain = registry.get_domain(selected_domain) if selected_domain else None

        if domain:
            with st.form("edit_domain_form"):
                col1, col2 = st.columns(2)

                with col1:
                    new_owner_email = st.text_input("Owner Email", value=domain.get("owner_email", ""))
                    new_archive_enabled = st.checkbox(
                        "Archive Enabled",
                        value=bool(domain.get("archive_enabled", False)),
                    )
                    new_hot_retention = st.number_input(
                        "Hot Retention (days)",
                        min_value=1,
                        value=int(domain.get("hot_retention_days") or 30),
                    )

                with col2:
                    new_stale = st.number_input(
                        "Stale Threshold (days)",
                        min_value=7,
                        value=int(domain.get("stale_threshold_days") or 60),
                    )
                    new_active = st.checkbox(
                        "Domain Active",
                        value=bool(domain.get("is_active", True)),
                    )
                    new_digest_enabled = st.checkbox(
                        "Weekly Digest Enabled",
                        value=bool(domain.get("digest_enabled", False)),
                        help="Include this domain in the weekly HK digest email.",
                    )
                    new_digest_email = st.text_input(
                        "Digest Email Override",
                        value=str(domain.get("digest_email") or ""),
                        placeholder="Leave blank to use Owner Email",
                    )

                if st.form_submit_button("Update", type="primary"):
                    try:
                        sql_update = f"""
                            UPDATE {DOMAIN_REGISTRY_TABLE}
                            SET owner_email          = '{new_owner_email}',
                                archive_enabled      = {str(new_archive_enabled).lower()},
                                hot_retention_days   = {int(new_hot_retention)},
                                stale_threshold_days = {int(new_stale)},
                                is_active            = {str(new_active).lower()},
                                digest_enabled       = {str(new_digest_enabled).lower()},
                                digest_email         = '{new_digest_email}',
                                updated_at           = CURRENT_TIMESTAMP
                            WHERE domain_name = '{selected_domain}'
                        """
                        execute_write(sql_update, dry_run=is_dry_run())
                        st.success(f"✅ Domain '{selected_domain}' updated.")
                        if is_dry_run():
                            st.info("Dry run mode — no actual writes were made.")
                    except Exception as e:
                        st.error(f"Update failed: {e}")

            # ── Digest Preview ──────────────────────────────────
            st.divider()
            st.markdown("**📬 Weekly Digest Preview**")
            st.caption(
                "Preview what the weekly digest would contain for this domain. "
                "Email sending is Phase 2.2."
            )
            if st.button("👁️ Preview Digest", key="domain_digest_preview"):
                from engine.core.digest import build_digest
                with st.spinner("Building digest..."):
                    digest = build_digest(selected_domain, days=7)
                if "error" in digest:
                    st.error(f"Digest failed: {digest['error']}")
                else:
                    s = digest.get("summary", {})
                    d1, d2, d3, d4 = st.columns(4)
                    d1.metric("Tables Touched",    int(s.get("tables_touched",    0)))
                    d2.metric("Successes",         int(s.get("successes",         0)))
                    d3.metric("Failures",          int(s.get("failures",          0)))
                    d4.metric("Athena Cost (7d)",  f"${float(s.get('athena_cost_usd', 0)):.4f}")
                    if digest.get("sla_breaches"):
                        st.warning(f"⚠️ {len(digest['sla_breaches'])} SLA breaches this week.")
                    if digest.get("top_failures"):
                        st.error(f"❌ {len(digest['top_failures'])} failure types this week.")
                    st.caption(f"Recipient: {digest.get('recipient_email', 'not configured')}")
                    st.caption(f"Email sender: {digest.get('email_sender_status', '—')}")

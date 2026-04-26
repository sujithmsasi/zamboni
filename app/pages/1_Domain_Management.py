"""
Zamboni — Domain Management
List, register, edit, activate/deactivate domains.
"""
import streamlit as st
import pandas as pd

from app.components.auth import check_login, current_user
from app.components.sidebar import render as render_sidebar, is_dry_run
from app.components.athena_runner import cached_read_registry, execute_write
from app.components.status_badge import yes_no

from config.settings import DOMAIN_REGISTRY_TABLE, VALID_ENVIRONMENTS
from engine.core import registry


st.set_page_config(page_title="Zamboni — Domains", page_icon="📋", layout="wide")

if not check_login():
    st.stop()
render_sidebar()


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

        submitted = st.form_submit_button("Register Domain", type="primary")

        if submitted:
            if not domain_name or not display_name or not owner_email:
                st.error("Domain name, display name, and owner email are required.")
            else:
                try:
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

                if st.form_submit_button("Update", type="primary"):
                    try:
                        sql_update = f"""
                            UPDATE {DOMAIN_REGISTRY_TABLE}
                            SET owner_email          = '{new_owner_email}',
                                archive_enabled      = {str(new_archive_enabled).lower()},
                                hot_retention_days   = {int(new_hot_retention)},
                                stale_threshold_days = {int(new_stale)},
                                is_active            = {str(new_active).lower()},
                                updated_at           = CURRENT_TIMESTAMP
                            WHERE domain_name = '{selected_domain}'
                        """
                        execute_write(sql_update, dry_run=is_dry_run())
                        st.success(f"✅ Domain '{selected_domain}' updated.")
                        if is_dry_run():
                            st.info("Dry run mode — no actual writes were made.")
                    except Exception as e:
                        st.error(f"Update failed: {e}")

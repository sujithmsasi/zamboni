"""
Zamboni -- Table Registration
Browse Glue catalog, multi-select tables, register with template inference.

Fixes (UI review):
  - domain_filter now shows all domains (fixed SQL true -> 1)
  - archive_enabled KeyError fixed (apply yes_no after rename)
  - Browse tab shows domain/tier/CreateTime/Location columns
  - After registration table list refreshes immediately
  - Already-registered tables show current domain in edit section
  - Registered tables filter by domain works correctly
  - Tooltips added to all registration fields
  - Remove environment from register form (Zamboni is per-env)
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pandas as pd
import streamlit as st

from app.components.athena_runner import cached_read_registry
from app.components.auth import check_login, current_user
from app.components.header import render as render_header
from app.components.sidebar import is_dry_run
from app.components.sidebar import render as render_sidebar
from app.components.status_badge import yes_no
from config.settings import (
    DOMAIN_REGISTRY_TABLE,
    STREAM_REGISTRY_TABLE,
    VALID_LAYERS,
    VALID_TIERS,
)
from engine.core import registry
from engine.core.audit import AuditAction, AuditEvent, audit
from engine.core.config import apply_template, infer_template
from engine.utils.glue_client import get_databases, get_tables, is_iceberg_table

st.set_page_config(
    page_title="Zamboni -- Table Registration",
    page_icon="➕",
    layout="wide",
)

if not check_login():
    st.stop()
render_sidebar()
render_header()

st.markdown("# ➕ Table Registration")
st.markdown("Browse the Glue catalog and register Iceberg tables with Zamboni.")
st.markdown("---")


# ── Helper: load domain list ──────────────────────────────────────────────────
def _get_domains() -> list[str]:
    """Load domains from registry. Falls back to static list."""
    try:
        df = cached_read_registry(
            f"SELECT domain_name FROM {DOMAIN_REGISTRY_TABLE} "
            "WHERE is_active = 1 ORDER BY domain_name"
        )
        return df["domain_name"].tolist() if not df.empty else []
    except Exception:
        return ["finance", "ers", "membership", "claims", "travel"]


tab_browse, tab_registered = st.tabs([
    "🔍 Browse & Register",
    "📊 Registered Tables",
])


# ── Tab 1: Browse Glue catalog ────────────────────────────────────────────────
with tab_browse:
    st.subheader("Discover Tables from Glue Catalog")
    st.caption("Select a database to browse Iceberg tables. Select tables to bulk-register.")

    @st.cache_data(ttl=600, show_spinner=False)
    def _cached_databases():
        try:
            return sorted(get_databases())
        except Exception as e:
            st.error(f"Could not list databases: {e}")
            return []

    @st.cache_data(ttl=300, show_spinner=False)
    def _cached_tables(db: str) -> list[dict]:
        try:
            tables = get_tables(db)
            # Check which are already registered
            try:
                reg_df = cached_read_registry(
                    f"SELECT table_fqn, domain, tier, layer FROM {STREAM_REGISTRY_TABLE} "
                    f"WHERE table_fqn LIKE '%.{db}.%'"
                )
                registered = {
                    row["table_fqn"]: row.to_dict()
                    for _, row in reg_df.iterrows()
                } if not reg_df.empty else {}
            except Exception:
                registered = {}

            result = []
            for t in tables:
                name   = t.get("Name", "")
                fqn    = f"glue_catalog.{db}.{name}"
                fmt    = "iceberg" if is_iceberg_table(t) else "hive"
                reg    = registered.get(fqn, {})
                result.append({
                    "Name":       name,
                    "Format":     fmt,
                    "Registered": "✅" if bool(reg) else "—",
                    "Domain":     reg.get("domain", ""),
                    "Tier":       reg.get("tier", ""),
                    "Layer":      reg.get("layer", ""),
                    "CreateTime": str(t.get("CreateTime", ""))[:10],
                    "Location":   (t.get("StorageDescriptor", {}).get("Location", "")
                                   or fqn)[:60],
                })
            return result
        except Exception as e:
            st.error(f"Could not list tables in {db}: {e}")
            return []

    databases = _cached_databases()
    if not databases:
        st.warning("No Glue databases found.")
    else:
        selected_db = st.selectbox(
            "Glue Database",
            databases,
            help="Select a Glue database to browse its Iceberg tables.",
        )

        if selected_db:
            with st.spinner(f"Loading tables from {selected_db}..."):
                tables = _cached_tables(selected_db)

            if not tables:
                st.info(f"No tables found in `{selected_db}`.")
            else:
                iceberg_count = sum(1 for t in tables if t["Format"] == "iceberg")
                reg_count     = sum(1 for t in tables if t["Registered"] == "✅")

                c1, c2, c3 = st.columns(3)
                c1.metric("Total Tables",      len(tables))
                c2.metric("Iceberg",           iceberg_count)
                c3.metric("Already Registered", reg_count)

                df_tables = pd.DataFrame(tables)
                df_tables.insert(0, "Select", False)

                edited = st.data_editor(
                    df_tables,
                    column_config={
                        "Select":     st.column_config.CheckboxColumn(
                            "Select", default=False,
                            help="Select tables to register with Zamboni",
                        ),
                        "Registered": st.column_config.TextColumn("Registered"),
                        "Domain":     st.column_config.TextColumn("Current Domain"),
                    },
                    use_container_width=True,
                    hide_index=True,
                    disabled=["Name","Format","Registered","Domain",
                              "Tier","Layer","CreateTime","Location"],
                    key=f"browse_{selected_db}",
                )

                selected_rows = edited[edited["Select"]]
                already_reg   = selected_rows[selected_rows["Registered"] == "✅"]
                new_rows      = selected_rows[selected_rows["Registered"] == "—"]

                if not selected_rows.empty:
                    st.markdown(f"**{len(selected_rows)} selected** "
                                f"({len(new_rows)} new · "
                                f"{len(already_reg)} already registered)")

                    if not already_reg.empty:
                        st.warning(
                            f"⚠️ {len(already_reg)} selected table(s) are already registered. "
                            "They will be updated with the settings below."
                        )

                    domains = _get_domains()
                    if not domains:
                        st.error("No domains registered. Add a domain first.")
                    else:
                        with st.form("bulk_register_form"):
                            st.markdown("**Registration Settings**")

                            col1, col2, col3 = st.columns(3)
                            with col1:
                                domain = st.selectbox(
                                    "Domain *",
                                    domains,
                                    help="Business domain this table belongs to. "
                                         "Drives default retention and escalation routing.",
                                )
                            with col2:
                                layer = st.selectbox(
                                    "Layer *",
                                    VALID_LAYERS,
                                    help="Pipeline layer: staging (raw ingest), "
                                         "datalake (deduped), base (SCD2/CDC), "
                                         "master (aggregated).",
                                )
                            with col3:
                                tier = st.selectbox(
                                    "Tier *",
                                    VALID_TIERS,
                                    index=1,
                                    help="Criticality tier — controls Athena workgroup routing: "
                                         "critical=zamboni-critical, "
                                         "standard=zamboni-standard, "
                                         "low=zamboni-low.",
                                )

                            col4, col5 = st.columns(2)
                            with col4:
                                owner_email = st.text_input(
                                    "Owner Email",
                                    help="Team DL or individual responsible for these tables. "
                                         "Defaults to domain owner if blank.",
                                )
                            with col5:
                                ci_number = st.text_input(
                                    "CI Number",
                                    help="ITSM Configuration Item for change management.",
                                )

                            stream_id = st.text_input(
                                "Stream ID (optional)",
                                placeholder="STR-FIN-APS-0001",
                                help="Groups related tables in a pipeline. "
                                     "Format: STR-{DOMAIN}-{SOURCE}-{SEQ}. "
                                     "Leave blank for standalone tables.",
                            )
                            notes = st.text_area(
                                "Notes",
                                help="Registration notes — stored in stream_registry. "
                                     "Document why this table is being registered.",
                            )

                            template = infer_template(layer, tier)
                            st.info(
                                f"📋 Auto-inferred policy template: **{template}** "
                                f"(layer={layer}, tier={tier}). "
                                "Editable after registration in Policy Configuration."
                            )

                            if is_dry_run():
                                st.info("🔵 Dry Run — form validated, no writes.")

                            if st.form_submit_button(
                                "📥 Register Selected Tables", type="primary"
                            ):
                                ok_count, fail_count = 0, 0
                                for _, row in selected_rows.iterrows():
                                    fqn = f"glue_catalog.{selected_db}.{row['Name']}"
                                    fmt = row.get("Format", "iceberg")
                                    try:
                                        registry.register_table(
                                            table_fqn=fqn,
                                            domain=domain,
                                            layer=layer,
                                            tier=tier,
                                            environment="prod",
                                            table_format=fmt,
                                            owner_email=owner_email or "",
                                            ci_number=ci_number or "",
                                            stream_id=stream_id.strip() or None,
                                            registered_by=f"streamlit:{current_user()}",
                                            notes=notes or "",
                                            dry_run=is_dry_run(),
                                        )
                                        apply_template(
                                            table_fqn=fqn,
                                            template_name=template,
                                            dry_run=is_dry_run(),
                                        )
                                        ok_count += 1
                                    except Exception as e:
                                        fail_count += 1
                                        st.error(f"❌ `{fqn}`: {e}")

                                if ok_count:
                                    audit(AuditEvent(
                                        actor=current_user(),
                                        action_type=AuditAction.TABLE_REGISTER,
                                        page_source="2_Table_Registration",
                                        target_type="table",
                                        target_id=f"{domain}/{selected_db}",
                                        domain=domain,
                                        environment="prod",
                                        dry_run=is_dry_run(),
                                        status="DRY_RUN" if is_dry_run() else "SUCCESS",
                                        reason=notes,
                                        after_value=(
                                            f"layer={layer},tier={tier},"
                                            f"template={template},count={ok_count}"
                                        ),
                                    ))
                                    cached_read_registry.clear()   # refresh list immediately
                                    _cached_tables.clear()         # refresh browse table
                                    st.success(
                                        f"✅ {ok_count} table(s) registered "
                                        f"with template `{template}`."
                                        + (" (dry run)" if is_dry_run() else "")
                                    )
                                if fail_count:
                                    st.warning(f"⚠️ {fail_count} table(s) failed.")


# ── Tab 2: Registered Tables ──────────────────────────────────────────────────
with tab_registered:
    st.subheader("Registered Tables")

    col_f1, col_f2, col_f3 = st.columns(3)
    with col_f1:
        domains     = _get_domains()
        sel_domain  = st.selectbox(
            "Filter by Domain",
            ["All"] + domains,
            key="reg_tab_domain",
            help="Show tables for a specific domain, or All.",
        )
    with col_f2:
        sel_layer   = st.selectbox(
            "Filter by Layer",
            ["All"] + VALID_LAYERS,
            key="reg_tab_layer",
        )
    with col_f3:
        sel_tier    = st.selectbox(
            "Filter by Tier",
            ["All"] + VALID_TIERS,
            key="reg_tab_tier",
        )

    hk_only = st.checkbox("Show only HK-enabled tables", key="reg_hk_only")

    if st.button("🔄 Refresh", key="reg_refresh"):
        cached_read_registry.clear()

    # Build WHERE conditions
    conds = []
    if sel_domain != "All":
        conds.append(f"domain = '{sel_domain}'")
    if sel_layer != "All":
        conds.append(f"layer = '{sel_layer}'")
    if sel_tier != "All":
        conds.append(f"tier = '{sel_tier}'")
    if hk_only:
        conds.append("hk_enabled = 1")

    where = ("WHERE " + " AND ".join(conds)) if conds else ""

    sql = f"""
        SELECT
            table_fqn, stream_id, domain, layer, tier,
            table_format, hk_enabled, archive_enabled,
            lifecycle_enabled, dry_run_until, processing_cadence,
            owner_email, registered_at
        FROM {STREAM_REGISTRY_TABLE}
        {where}
        ORDER BY domain, layer, table_fqn
        LIMIT 500
    """
    try:
        df = cached_read_registry(sql)
        if df.empty:
            st.info("No registered tables match your filters.")
        else:
            # Apply bool formatting in a safe order
            for col, label in [
                ("hk_enabled",        "Housekeeping"),
                ("archive_enabled",   "Archival"),
                ("lifecycle_enabled", "Lifecycle"),
            ]:
                if col in df.columns:
                    df[col] = df[col].apply(yes_no)
                    df.rename(columns={col: label}, inplace=True, errors="ignore")

            if "registered_at" in df.columns:
                df["registered_at"] = df["registered_at"].astype(str).str[:10]

            if "dry_run_until" in df.columns:
                df["dry_run_until"] = df["dry_run_until"].astype(str).replace("None","")

            st.dataframe(df, use_container_width=True, hide_index=True, height=450)
            st.caption(f"{len(df)} table(s) shown")

            # Download
            csv = df.to_csv(index=False).encode("utf-8")
            st.download_button(
                "⬇️ Export CSV",
                data=csv,
                file_name=f"zamboni_tables_{sel_domain}.csv",
                mime="text/csv",
            )
    except Exception as e:
        st.error(f"Could not load registered tables: {e}")

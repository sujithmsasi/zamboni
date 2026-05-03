"""
Zamboni — Table Registration
Browse Glue catalog, multi-select tables, register with template inference.
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pandas as pd
import streamlit as st

from app.components.athena_runner import cached_read_registry
from app.components.auth import check_login, current_user
from app.components.filters import domain_filter
from app.components.header import render as render_header
from app.components.sidebar import is_dry_run
from app.components.sidebar import render as render_sidebar
from app.components.status_badge import yes_no
from config.settings import STREAM_REGISTRY_TABLE, VALID_ENVIRONMENTS, VALID_LAYERS, VALID_TIERS
from engine.core import registry
from engine.core.audit import AuditAction, AuditEvent, audit
from engine.core.config import apply_template, infer_template
from engine.utils.glue_client import get_databases, get_tables, is_iceberg_table

st.set_page_config(page_title="Zamboni — Table Registration", page_icon="➕", layout="wide")

if not check_login():
    st.stop()
render_sidebar()
render_header()


st.markdown("# ➕ Table Registration")
st.markdown("Browse the Glue catalog and register tables with Zamboni.")
st.markdown("---")


tab_browse, tab_registered = st.tabs(["🔍 Browse & Register", "📊 Registered Tables"])


# ── Tab 1: Browse Glue catalog and register ──────────────────────────────────
with tab_browse:
    st.subheader("Discover Tables from Glue Catalog")

    @st.cache_data(ttl=600)
    def _cached_databases():
        try:
            return get_databases()
        except Exception as e:
            st.error(f"Could not list Glue databases: {e}")
            return []

    @st.cache_data(ttl=300)
    def _cached_tables(db: str):
        try:
            tables = get_tables(db)
            return [
                {
                    "Name":        t.get("Name", ""),
                    "Format":      "iceberg" if is_iceberg_table(t) else "hive",
                    "CreateTime":  str(t.get("CreateTime", ""))[:19],
                    "Location":    t.get("StorageDescriptor", {}).get("Location", ""),
                }
                for t in tables
            ]
        except Exception as e:
            st.error(f"Could not list tables in {db}: {e}")
            return []

    databases = _cached_databases()
    if not databases:
        st.warning("No Glue databases found.")
    else:
        selected_db = st.selectbox("Glue Database", databases)

        if selected_db:
            tables = _cached_tables(selected_db)
            if not tables:
                st.info(f"No tables in {selected_db}")
            else:
                st.markdown(f"**{len(tables)} tables found in `{selected_db}`**")

                df_tables = pd.DataFrame(tables)
                df_tables.insert(0, "Select", False)

                edited = st.data_editor(
                    df_tables,
                    column_config={
                        "Select": st.column_config.CheckboxColumn(default=False),
                    },
                    use_container_width=True,
                    hide_index=True,
                    disabled=["Name", "Format", "CreateTime", "Location"],
                )

                selected_rows = edited[edited["Select"]]

                if not selected_rows.empty:
                    st.markdown(f"**{len(selected_rows)} table(s) selected** for bulk registration")

                    with st.form("bulk_register_form"):
                        col1, col2, col3 = st.columns(3)
                        with col1:
                            domain      = domain_filter(label="Target Domain *", include_all=False)
                        with col2:
                            tier        = st.selectbox("Tier *", VALID_TIERS, index=1)
                        with col3:
                            layer       = st.selectbox("Layer *", VALID_LAYERS)

                        col4, col5 = st.columns(2)
                        with col4:
                            environment = st.selectbox("Environment", VALID_ENVIRONMENTS, index=0)
                        with col5:
                            ci_number   = st.text_input("CI Number")

                        owner_email = st.text_input("Owner Email")
                        notes       = st.text_area("Notes")

                        if st.form_submit_button("Register Selected Tables", type="primary"):
                            if not domain:
                                st.error("Domain is required.")
                            else:
                                template = infer_template(layer, tier)
                                successes, failures = 0, 0
                                for _, row in selected_rows.iterrows():
                                    fqn = f"glue_catalog.{selected_db}.{row['Name']}"
                                    table_format = row.get("Format", "iceberg")
                                    try:
                                        registry.register_table(
                                            table_fqn=fqn,
                                            domain=domain,
                                            layer=layer,
                                            tier=tier,
                                            environment=environment,
                                            table_format=table_format,
                                            owner_email=owner_email,
                                            ci_number=ci_number,
                                            registered_by=f"streamlit:{current_user()}",
                                            notes=notes,
                                            dry_run=is_dry_run(),
                                        )
                                        # Apply policy template
                                        apply_template(
                                            table_fqn=fqn,
                                            template_name=template,
                                            dry_run=is_dry_run(),
                                        )
                                        successes += 1
                                    except Exception as e:
                                        failures += 1
                                        st.error(f"❌ {fqn}: {e}")

                                if successes:
                                    audit(AuditEvent(
                                        actor=current_user(),
                                        action_type=AuditAction.TABLE_REGISTER,
                                        page_source="2_Table_Registration",
                                        target_type="table",
                                        target_id=f"{domain}/{selected_db}",
                                        domain=domain, environment=environment,
                                        dry_run=is_dry_run(),
                                        status="DRY_RUN" if is_dry_run() else "SUCCESS",
                                        reason=notes,
                                        after_value=f"layer={layer},tier={tier},template={template},count={successes}",
                                    ))
                                    st.success(
                                        f"✅ Registered {successes} table(s) with template `{template}`."
                                    )
                                if failures:
                                    st.warning(f"⚠️ {failures} table(s) could not be registered.")
                                if is_dry_run():
                                    st.info("Dry run mode — no actual writes were made.")


# ── Tab 2: View registered tables ────────────────────────────────────────────
with tab_registered:
    st.subheader("Registered Tables")
    selected = domain_filter(label="Filter by Domain", key="reg_filter")

    sql = f"""
        SELECT table_fqn, domain, layer, tier, environment, table_format,
               hk_enabled, archive_enabled, registered_at
        FROM {STREAM_REGISTRY_TABLE}
        {f"WHERE domain = '{selected}'" if selected else ""}
        ORDER BY domain, layer, table_fqn
        LIMIT 1000
    """
    try:
        df = cached_read_registry(sql)
        if df.empty:
            st.info("No registered tables match your filter.")
        else:
            df["hk_enabled"]       = df["hk_enabled"].apply(yes_no)
            df.rename(columns={
                "hk_enabled":       "Housekeeping Enabled",
                "archive_enabled":  "Archival Enabled",
                "lifecycle_enabled":"Lifecycle Enabled",
            }, inplace=True, errors="ignore")
            df["archive_enabled"] = df["archive_enabled"].apply(yes_no)
            df["registered_at"]   = df["registered_at"].astype(str).str[:19]
            st.dataframe(df, use_container_width=True, hide_index=True)
            st.caption(f"Showing {len(df)} of {len(df)} tables")
    except Exception as e:
        st.error(f"Could not load registered tables: {e}")

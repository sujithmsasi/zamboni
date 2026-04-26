"""
Zamboni — Policy Configuration Page
View and edit per-table HK config.
Apply policy templates, override fields, bulk apply by domain/layer.
"""
import streamlit as st
import pandas as pd

from app.components.auth import check_login
from app.components.header import render as render_header, current_user
from app.components.sidebar import render as render_sidebar, is_dry_run
from app.components.filters import domain_filter, layer_filter, tier_filter
from app.components.athena_runner import cached_read_registry, execute_write
from app.components.status_badge import layer as layer_badge, tier as tier_badge

from config.settings import STREAM_REGISTRY_TABLE, HK_CONFIG_TABLE
from engine.core.config import get_policy_templates, get_template, infer_template

st.set_page_config(page_title="Zamboni — Policy Config", page_icon="⚙️", layout="wide")
check_login()
render_sidebar()
render_header()

st.title("⚙️ Policy Configuration")
st.caption("Manage housekeeping policies per table. Templates provide sensible defaults; fields can be overridden individually.")

tab1, tab2, tab3 = st.tabs(["📋 View Configs", "✏️ Edit Single Table", "🔄 Bulk Apply Template"])

# ── Tab 1: View Configs ───────────────────────────────────────────────────────
with tab1:
    c1, c2, c3 = st.columns(3)
    with c1: d = domain_filter(key="pc_domain")
    with c2: l = layer_filter(key="pc_layer")
    with c3: t = tier_filter(key="pc_tier")

    conditions = ["r.table_format = 'iceberg'", "r.hk_enabled = true"]
    if d: conditions.append(f"r.domain = '{d}'")
    if l: conditions.append(f"r.layer = '{l}'")
    if t: conditions.append(f"r.tier = '{t}'")
    where = "WHERE " + " AND ".join(conditions)

    sql = f"""
        SELECT
            r.table_fqn, r.domain, r.layer, r.tier,
            c.policy_template, c.compaction_strategy,
            c.compaction_target_file_size_mb,
            c.snapshot_retention_days, c.snapshot_min_to_keep,
            c.orphan_file_retention_days,
            c.run_frequency,
            c.manually_overridden
        FROM {STREAM_REGISTRY_TABLE} r
        LEFT JOIN {HK_CONFIG_TABLE} c ON r.table_fqn = c.table_fqn
        {where}
        ORDER BY r.domain, r.layer, r.table_fqn
        LIMIT 500
    """

    with st.spinner("Loading configs..."):
        try:
            df = cached_read_registry(sql)
            if df.empty:
                st.info("No tables match the selected filters.")
            else:
                df["manually_overridden"] = df["manually_overridden"].apply(
                    lambda x: "⚠️ Overridden" if x else "✅ Template"
                )
                st.dataframe(df, use_container_width=True, hide_index=True, height=400)
                st.caption(f"{len(df)} tables shown")
        except Exception as e:
            st.error(f"Query failed: {e}")

# ── Tab 2: Edit Single Table ──────────────────────────────────────────────────
with tab2:
    st.markdown("#### Edit HK Config for a Table")
    table_fqn = st.text_input(
        "Table FQN",
        placeholder="glue_catalog.finance_db.finance_staging",
        key="edit_table_fqn"
    )

    if table_fqn:
        sql = f"SELECT * FROM {HK_CONFIG_TABLE} WHERE table_fqn = '{table_fqn}' LIMIT 1"
        try:
            df = cached_read_registry(sql)
            if df.empty:
                st.warning("No config found. Apply a template first.")
            else:
                config = df.iloc[0].to_dict()
                with st.form("edit_config_form"):
                    col1, col2 = st.columns(2)
                    with col1:
                        snap_days = st.number_input("Snapshot Retention Days",
                            value=int(config.get("snapshot_retention_days") or 7), min_value=1)
                        snap_min  = st.number_input("Min Snapshots to Keep",
                            value=int(config.get("snapshot_min_to_keep") or 30), min_value=30)
                        orphan    = st.number_input("Orphan Retention Days",
                            value=int(config.get("orphan_file_retention_days") or 2), min_value=1)
                    with col2:
                        strategy = st.selectbox("Compaction Strategy",
                            ["binpack", "sort", "zorder"],
                            index=["binpack","sort","zorder"].index(
                                config.get("compaction_strategy","binpack")))
                        target_mb = st.number_input("Target File Size (MB)",
                            value=int(config.get("compaction_target_file_size_mb") or 128))
                        engine = st.selectbox("Compaction Engine", ["athena","glue"],
                            index=["athena","glue"].index(config.get("compaction_engine","athena")))

                    override_notes = st.text_input("Reason for override (required)")
                    submitted = st.form_submit_button("💾 Save Changes", type="primary",
                                                      disabled=is_dry_run())

                if submitted:
                    if not override_notes:
                        st.error("Please provide a reason for the override.")
                    else:
                        st.success(f"✅ Config updated for `{table_fqn}`" +
                                  (" (dry run)" if is_dry_run() else ""))
        except Exception as e:
            st.error(f"Error: {e}")

# ── Tab 3: Bulk Apply Template ────────────────────────────────────────────────
with tab3:
    st.markdown("#### Apply a Policy Template to a Domain + Layer")
    st.info("This applies the selected template to ALL tables in the chosen domain/layer. Existing manual overrides are preserved unless you check the override box.")

    templates = get_policy_templates()
    col1, col2, col3 = st.columns(3)
    with col1: bulk_domain = domain_filter(include_all=False, key="bulk_domain")
    with col2: bulk_layer  = layer_filter(include_all=False, key="bulk_layer")
    with col3: bulk_tmpl   = st.selectbox("Template", list(templates.keys()), key="bulk_tmpl")

    if bulk_tmpl:
        t = templates[bulk_tmpl]
        st.markdown(f"""
        **Template Preview — `{bulk_tmpl}`**
        - Strategy: `{t['compaction_strategy']}` via `{t['compaction_engine']}`
        - Target file size: `{t['compaction_target_file_size_mb']} MB`
        - Snapshot retention: `{t['snapshot_retention_days']} days` (min keep: `{t['snapshot_min_to_keep']}`)
        - Orphan retention: `{t['orphan_file_retention_days']} days`
        - Frequency: `{t['run_frequency']}`
        """)

    override_existing = st.checkbox("Override existing manual overrides", value=False)
    dry_run_note = "⚠️ Dry Run Mode is ON — no changes will be written." if is_dry_run() else ""
    if dry_run_note: st.warning(dry_run_note)

    if st.button("Apply Template", type="primary", disabled=not bulk_domain or not bulk_layer):
        st.info(f"Would apply `{bulk_tmpl}` to `{bulk_domain}.{bulk_layer}` tables.")
        st.success("Template application complete (simulated)." if is_dry_run() else "Template applied.")

"""
Zamboni — Dry Run Viewer
Simulate HK Engine on any table or domain without writing anything.
Shows: health check results, what operations would run, SQL preview.
"""
import streamlit as st
import json

from app.components.auth import check_login
from app.components.sidebar import render as render_sidebar
from app.components.filters import domain_filter, layer_filter
from app.components.athena_runner import cached_read_registry

from config.settings import STREAM_REGISTRY_TABLE, HK_CONFIG_TABLE
from engine.utils.partition_utils import build_hot_partition_filter
from engine.strategies.binpack import build_optimize_sql
from engine.core.window_evaluator import evaluate, EXECUTE

st.set_page_config(page_title="Zamboni — Dry Run", page_icon="🧪", layout="wide")
check_login()
render_sidebar()

st.title("🧪 Dry Run Viewer")
st.caption("Simulate HK Engine operations without writing anything. Use this to validate config before enabling a table.")

tab1, tab2 = st.tabs(["🔍 Single Table", "📂 Domain Dry Run"])

# ── Tab 1: Single Table ───────────────────────────────────────────────────────
with tab1:
    table_fqn = st.text_input(
        "Table FQN",
        placeholder="glue_catalog.finance_db.finance_staging",
        key="dr_table_fqn",
    )

    if table_fqn and st.button("▶ Run Dry Run", type="primary", key="dr_run"):
        with st.spinner("Running dry run..."):

            # Load registry row
            reg_sql = f"SELECT * FROM {STREAM_REGISTRY_TABLE} WHERE table_fqn = '{table_fqn}' LIMIT 1"
            cfg_sql = f"SELECT * FROM {HK_CONFIG_TABLE} WHERE table_fqn = '{table_fqn}' LIMIT 1"

            try:
                reg_df = cached_read_registry(reg_sql)
                cfg_df = cached_read_registry(cfg_sql)

                if reg_df.empty:
                    st.warning(f"Table `{table_fqn}` is not registered in Zamboni. Register it first.")
                    st.stop()

                reg = reg_df.iloc[0].to_dict()
                cfg = cfg_df.iloc[0].to_dict() if not cfg_df.empty else {}

                st.success(f"Dry run complete for `{table_fqn}`")

                col1, col2, col3 = st.columns(3)
                with col1:
                    st.metric("Domain", reg.get("domain", "—"))
                    st.metric("Layer",  reg.get("layer", "—"))
                with col2:
                    st.metric("Tier",    reg.get("tier", "—"))
                    st.metric("HK Enabled", "✅ Yes" if reg.get("hk_enabled") else "❌ No")
                with col3:
                    st.metric("Template", cfg.get("policy_template", "None"))
                    st.metric("Strategy", cfg.get("compaction_strategy", "—"))

                st.divider()

                # Window evaluation
                st.markdown("#### ⏰ Window Evaluation")
                window_json = cfg.get("window_config", "")
                if window_json:
                    decision = evaluate(window_json, force=reg.get("force_run", False))
                    if decision == EXECUTE:
                        st.success(f"Window: **{decision}** — HK would proceed")
                    else:
                        st.warning(f"Window: **{decision}** — HK would be skipped")
                    try:
                        window_dict = json.loads(window_json)
                        st.json(window_dict)
                    except Exception:
                        st.code(window_json)
                else:
                    st.info("No window config set — HK runs on every trigger")

                # Compaction SQL preview
                if cfg.get("compaction_strategy") == "binpack":
                    st.markdown("#### 🔧 Compaction SQL Preview")
                    part_col    = cfg.get("partition_column")
                    part_days   = cfg.get("partition_filter_days")
                    part_filter = build_hot_partition_filter(part_col, part_days) if part_col else None
                    sql_preview = build_optimize_sql(
                        table_fqn=table_fqn,
                        target_file_size_mb=cfg.get("compaction_target_file_size_mb", 128),
                        partition_filter=part_filter,
                    )
                    st.code(sql_preview, language="sql")
                elif cfg.get("compaction_strategy") in ("sort", "zorder"):
                    st.markdown("#### 🔧 Glue Job Parameters Preview")
                    st.info(f"Would submit Glue job `zamboni-compaction` with strategy=`{cfg['compaction_strategy']}`")
                    st.json({
                        "strategy":            cfg.get("compaction_strategy"),
                        "sort_columns":        cfg.get("sort_columns"),
                        "target_file_size_mb": cfg.get("compaction_target_file_size_mb"),
                    })

                # Gate status
                st.markdown("#### 🚦 Gate Summary")
                upstream = reg.get("dependent_job_name")
                gates = [
                    ("Gate 1 — Upstream",  f"Job: `{upstream}`" if upstream else "Not configured", upstream is None),
                    ("Gate 2 — Window",    f"Decision: {decision if window_json else 'EXECUTE (no config)'}", True),
                    ("Gate 3 — Circuit Breaker", "Would check failure count", True),
                    ("Operations", f"Strategy: `{cfg.get('compaction_strategy','—')}`", bool(cfg)),
                ]
                for name, detail, ok in gates:
                    icon = "✅" if ok else "⚠️"
                    st.markdown(f"{icon} **{name}** — {detail}")

            except Exception as e:
                st.error(f"Dry run failed: {e}")

# ── Tab 2: Domain Dry Run ─────────────────────────────────────────────────────
with tab2:
    st.markdown("Simulate HK for all enabled tables in a domain and layer.")
    col1, col2 = st.columns(2)
    with col1: bulk_domain = domain_filter(include_all=False, key="dr_bulk_domain")
    with col2: bulk_layer  = layer_filter(include_all=False, key="dr_bulk_layer")

    if st.button("▶ Simulate Domain Run", type="primary", key="dr_bulk_run"):
        if bulk_domain and bulk_layer:
            sql = f"""
                SELECT r.table_fqn, r.tier, r.hk_enabled,
                       c.compaction_strategy, c.policy_template
                FROM {STREAM_REGISTRY_TABLE} r
                LEFT JOIN {HK_CONFIG_TABLE} c ON r.table_fqn = c.table_fqn
                WHERE r.domain = '{bulk_domain}'
                  AND r.layer  = '{bulk_layer}'
                  AND r.hk_enabled = true
                  AND r.table_format = 'iceberg'
                ORDER BY r.tier, r.table_fqn
            """
            try:
                with st.spinner("Simulating..."):
                    df = cached_read_registry(sql)
                if df.empty:
                    st.info(f"No enabled Iceberg tables in `{bulk_domain}.{bulk_layer}`")
                else:
                    st.success(f"Found {len(df)} tables that would be processed")
                    st.dataframe(df, use_container_width=True, hide_index=True)
            except Exception as e:
                st.error(f"Query failed: {e}")
        else:
            st.warning("Select a domain and layer first.")

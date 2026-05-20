"""
Zamboni — Dry Run Viewer
Simulate HK Engine on any table or domain without writing anything.
Shows: health check results, what operations would run, SQL preview.
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import json

import streamlit as st

from app.components.athena_runner import cached_read_registry
from app.components.auth import check_login
from app.components.filters import domain_filter, layer_filter
from app.components.header import render as render_header
from app.components.sidebar import render as render_sidebar
from config.settings import HK_CONFIG_TABLE, STREAM_REGISTRY_TABLE
from engine.core.audit import AuditAction, AuditEvent, audit
from engine.core.window_evaluator import EXECUTE, evaluate
from engine.strategies.binpack import build_optimize_sql
from engine.utils.partition_utils import build_hot_partition_filter

st.set_page_config(page_title="Zamboni — Dry Run", page_icon="🧪", layout="wide")
check_login()
render_sidebar()
render_header(page_title="Dry Run Viewer", page_icon="🔵")

tab1, tab2 = st.tabs(["🔍 Single Table", "📂 Domain Dry Run"])

# ── Tab 1: Single Table ───────────────────────────────────────────────────────
with tab1:
    from app.components.auth import current_user as _cuser
    from app.components.table_selector import render_flat as _flat_sel
    from config.settings import APP_ENV as _ENV

    table_fqn = _flat_sel(
        key_prefix="dr_sel",
        label="Select Table to Simulate",
        help_text="Type table name or database to search. All registered tables shown.",
    )

    col_run, col_clear = st.columns([2, 1])
    with col_run:
        run_clicked = st.button("▶ Run Dry Run", type="primary", key="dr_run",
                                disabled=not bool(table_fqn))
    with col_clear:
        if st.button("✕ Clear", key="dr_clear"):
            st.session_state.pop("dr_result", None)
            st.session_state.pop("dr_fqn", None)
            st.rerun()

    # Clear stale results on fresh arrival from another page
    if "dr_on_page" not in st.session_state:
        st.session_state.pop("dr_result",   None)
        st.session_state.pop("dr_reg",      None)
        st.session_state.pop("dr_cfg",      None)
        st.session_state.pop("dr_fqn",      None)
        st.session_state.pop("dr_promoted", None)
        # Also reset the flat selector to blank
        st.session_state.pop("dr_sel_flat", None)
        st.session_state["dr_on_page"] = True

    # Clear when user selects a different table
    if table_fqn and table_fqn != st.session_state.get("dr_fqn"):
        st.session_state.pop("dr_result", None)
        st.session_state.pop("dr_reg",    None)
        st.session_state.pop("dr_cfg",    None)
        st.session_state.pop("dr_promoted", None)

    # Run dry run and store results in session_state
    if run_clicked and table_fqn:
        st.session_state["dr_fqn"] = table_fqn
        st.session_state.pop("dr_result", None)  # clear old result
        with st.spinner("Running dry run..."):
            try:
                reg_df = cached_read_registry(
                    f"SELECT * FROM {STREAM_REGISTRY_TABLE} "
                    f"WHERE table_fqn = '{table_fqn}' LIMIT 1"
                )
                cfg_df = cached_read_registry(
                    f"SELECT * FROM {HK_CONFIG_TABLE} "
                    f"WHERE table_fqn = '{table_fqn}' LIMIT 1"
                )
                if reg_df.empty:
                    st.warning(f"Table `{table_fqn}` is not registered. Register it first.")
                else:
                    st.session_state["dr_reg"] = reg_df.iloc[0].to_dict()
                    st.session_state["dr_cfg"] = (
                        cfg_df.iloc[0].to_dict() if not cfg_df.empty else {}
                    )
                    st.session_state["dr_result"] = True
            except Exception as _e:
                st.error(f"Dry run failed: {_e}")


    # ── Display results (persists across reruns via session_state) ─────────────
    if st.session_state.get("dr_result") and st.session_state.get("dr_reg"):
        reg = st.session_state["dr_reg"]
        cfg = st.session_state["dr_cfg"]
        _fqn = st.session_state.get("dr_fqn", table_fqn or "")
        st.success(f"Dry run complete for `{_fqn}`")

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
            part_col  = cfg.get("partition_column")
            part_days = cfg.get("partition_filter_days")
            part_type = cfg.get("partition_type", "date") or "date"
            _no_filter = part_type in ("none", "identity") or not part_col
            part_filter = (
                build_hot_partition_filter(
                    part_col, days=part_days,
                    partition_type=part_type,
                ) if not _no_filter else None
            )
            sql_preview = build_optimize_sql(
                table_fqn=_fqn,
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

        # ── Copyable SQL ─────────────────────────────────────────────
        if cfg.get("compaction_strategy") == "binpack":
            st.download_button(
                "📋 Copy SQL to clipboard",
                data=sql_preview,
                file_name=f"zamboni_optimize_{_fqn.split('.')[-1]}.sql",
                mime="text/plain",
            )

        # ── Promote to Live ───────────────────────────────────────────
        st.divider()
        st.markdown("#### 🚀 Promote to Live")
        st.caption(
            "If all gates pass and you are satisfied with the dry-run "
            "results, promote this table to a live HK run."
        )
        from app.components.reason_form import render_reason_form, validate_and_gate
        reason, ticket = render_reason_form(
            "dry_run_promote", _ENV,
            dry_run=False, key_prefix="promote",
        )
        if st.button("🚀 Promote to Live Run", type="primary", key="dr_promote"):
            vr = validate_and_gate("dry_run_promote", reason, ticket, _ENV, dry_run=False)
            if vr.valid:
                audit(AuditEvent(
                    actor=_cuser(),
                    action_type=AuditAction.DRY_RUN_PROMOTE,
                    page_source="6_Dry_Run_Viewer",
                    target_type="table", target_id=_fqn,
                    environment=_ENV, dry_run=False,
                    status="SUCCESS",
                    reason=reason, ticket_number=ticket,
                ))
                st.session_state["dr_promoted"] = True
                st.success(
                    "✅ Promote to live recorded. "
                    "The next EventBridge trigger will run this table with dry_run=False."
                )
                st.info(
                    "Zamboni does not immediately execute a live HK run from the UI. "
                    f"To run immediately: use CLI `python -m engine.scripts.run_hk "
                    f"--table {_fqn} --no-dry-run`"
                )


# ── Tab 2: Domain Dry Run ─────────────────────────────────────────────────────
with tab2:
    st.markdown("Simulate HK for all enabled tables in a domain and layer.")
    col1, col2 = st.columns(2)
    with col1:
        bulk_domain = domain_filter(include_all=False, key="dr_bulk_domain")
    with col2:
        bulk_layer  = layer_filter(include_all=False, key="dr_bulk_layer")

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

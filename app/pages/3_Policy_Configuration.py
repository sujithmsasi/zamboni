"""
Zamboni — Policy Configuration Page
View and edit per-table HK config.
Apply policy templates, override individual fields, bulk apply by domain/layer.
All writes honour the sidebar dry-run toggle.
"""
import streamlit as st

from app.components.athena_runner import cached_read_registry, clear_caches
from app.components.auth import check_login
from app.components.filters import domain_filter, layer_filter, tier_filter
from app.components.header import render as render_header
from app.components.sidebar import is_dry_run
from app.components.sidebar import render as render_sidebar
from config.settings import HK_CONFIG_TABLE, STREAM_REGISTRY_TABLE
from engine.core.audit import AuditAction, AuditEvent, audit
from engine.core.config import get_policy_templates

st.set_page_config(page_title="Zamboni — Policy Config", page_icon="⚙️", layout="wide")
check_login()
render_sidebar()
render_header()

st.title("⚙️ Policy Configuration")
st.caption("Manage housekeeping policies per table. Templates provide sensible defaults; individual fields can be overridden.")

tab1, tab2, tab3 = st.tabs(["📋 View Configs", "✏️ Edit Single Table", "🔄 Bulk Apply Template"])

# ── Tab 1: View Configs ───────────────────────────────────────────────────────
with tab1:
    c1, c2, c3 = st.columns(3)
    with c1:
        d = domain_filter(key="pc_domain")
    with c2:
        layer_sel = layer_filter(key="pc_layer")
    with c3:
        t = tier_filter(key="pc_tier")

    conditions = ["r.table_format = 'iceberg'", "r.hk_enabled = true"]
    if d:
        conditions.append(f"r.domain = '{d}'")
    if layer_sel:
        conditions.append(f"r.layer = '{layer_sel}'")
    if t:
        conditions.append(f"r.tier = '{t}'")
    where = "WHERE " + " AND ".join(conditions)

    sql = f"""
        SELECT
            r.table_fqn, r.domain, r.layer, r.tier,
            c.policy_template, c.compaction_strategy,
            c.compaction_target_file_size_mb,
            c.snapshot_retention_days, c.snapshot_min_to_keep,
            c.orphan_file_retention_days, c.run_frequency,
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
        key="edit_table_fqn",
    )

    if table_fqn:
        sql = f"SELECT * FROM {HK_CONFIG_TABLE} WHERE table_fqn = '{table_fqn}' LIMIT 1"
        try:
            df = cached_read_registry(sql)
            if df.empty:
                st.warning("No config found for this table. Apply a template first in the Bulk tab.")
            else:
                config = df.iloc[0].to_dict()

                with st.form("edit_config_form"):
                    col1, col2 = st.columns(2)
                    with col1:
                        snap_days = st.number_input(
                            "Snapshot Retention Days",
                            value=int(config.get("snapshot_retention_days") or 7),
                            min_value=1,
                        )
                        snap_min = st.number_input(
                            "Min Snapshots to Keep",
                            value=int(config.get("snapshot_min_to_keep") or 30),
                            min_value=30,
                            help="Hard floor: 30. Never goes below this.",
                        )
                        orphan = st.number_input(
                            "Orphan Retention Days",
                            value=int(config.get("orphan_file_retention_days") or 2),
                            min_value=2,
                            help="Min 2 days — safety floor to protect in-flight writers.",
                        )
                        orphan_cadence = st.number_input(
                            "Orphan Cleanup Cadence (days)",
                            value=int(config.get("orphan_cleanup_cadence_days") or 7),
                            min_value=0,
                            help="How often to run orphan cleanup. 0 = disabled.",
                        )
                    with col2:
                        strategy = st.selectbox(
                            "Compaction Strategy",
                            ["binpack", "sort", "zorder"],
                            index=["binpack", "sort", "zorder"].index(
                                config.get("compaction_strategy", "binpack")
                            ),
                        )
                        sort_order_cols = st.text_input(
                            "Sort / Z-Order Columns (comma-separated)",
                            value=config.get("sort_order_cols") or "",
                            placeholder="col_a, col_b",
                            help="Required for sort/zorder strategies. "
                                 "Ignored for binpack.",
                        )
                        target_mb = st.number_input(
                            "Target File Size (MB)",
                            value=int(config.get("compaction_target_file_size_mb") or 128),
                            min_value=64,
                        )
                        engine_choice = st.selectbox(
                            "Compaction Engine",
                            ["athena", "glue"],
                            index=["athena", "glue"].index(
                                config.get("compaction_engine", "athena")
                            ),
                        )
                        run_freq = st.selectbox(
                            "Run Frequency",
                            ["every_trigger", "daily", "weekly", "monthly"],
                            index=["every_trigger", "daily", "weekly", "monthly"].index(
                                config.get("run_frequency", "daily")
                            ),
                        )

                    override_notes = st.text_input("Reason for this override (required)")
                    dry_note = "⚠️ Dry Run ON — changes will be simulated only." if is_dry_run() else ""
                    if dry_note:
                        st.warning(dry_note)

                    submitted = st.form_submit_button("💾 Save Changes", type="primary")

                if submitted:
                    if not override_notes:
                        st.error("Please provide a reason for the override.")
                    else:
                        try:
                            from engine.core.config import update_config_field
                            fields = {
                                "snapshot_retention_days":        snap_days,
                                "snapshot_min_to_keep":           snap_min,
                                "orphan_file_retention_days":     orphan,
                                "orphan_cleanup_cadence_days":    orphan_cadence,
                                "compaction_strategy":            strategy,
                                "sort_order_cols":                sort_order_cols,
                                "compaction_target_file_size_mb": target_mb,
                                "compaction_engine":              engine_choice,
                                "run_frequency":                  run_freq,
                            }
                            for field, value in fields.items():
                                update_config_field(
                                    table_fqn=table_fqn,
                                    field=field,
                                    value=value,
                                    override_notes=override_notes,
                                    dry_run=is_dry_run(),
                                )
                            clear_caches()
                            from app.components.auth import current_user as _cu
                            from config.settings import APP_ENV as _ENV
                            audit(AuditEvent(
                                actor=_cu(), action_type=AuditAction.POLICY_CHANGE,
                                page_source="3_Policy_Configuration",
                                target_type="table", target_id=table_fqn,
                                environment=_ENV, dry_run=is_dry_run(),
                                status="DRY_RUN" if is_dry_run() else "SUCCESS",
                                reason=override_notes,
                                after_value=str(fields),
                            ))
                            st.success(
                                f"✅ Config updated for `{table_fqn}`"
                                + (" (dry run — no actual changes)" if is_dry_run() else "")
                            )
                        except Exception as e:
                            st.error(f"Save failed: {e}")
        except Exception as e:
            st.error(f"Error loading config: {e}")

# ── Tab 3: Bulk Apply Template ────────────────────────────────────────────────
with tab3:
    st.markdown("#### Apply a Policy Template to a Domain + Layer")
    st.info(
        "Applies the selected template to ALL registered Iceberg tables in the "
        "chosen domain and layer. Each table gets its hk_config row replaced with "
        "template defaults. Existing manual overrides are preserved unless you "
        "check the override box below."
    )

    templates = get_policy_templates()
    col1, col2, col3 = st.columns(3)
    with col1:
        bulk_domain = domain_filter(include_all=False, key="bulk_domain")
    with col2:
        bulk_layer  = layer_filter(include_all=False, key="bulk_layer")
    with col3:
        bulk_tmpl   = st.selectbox("Template", list(templates.keys()), key="bulk_tmpl")

    # Show template preview
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

    override_manual = st.checkbox(
        "Override existing manual overrides",
        value=False,
        help="If unchecked, tables with manually_overridden=true will be skipped",
    )

    dry_note2 = "⚠️ Dry Run ON — no changes will be written." if is_dry_run() else ""
    if dry_note2:
        st.warning(dry_note2)

    apply_disabled = not bulk_domain or not bulk_layer or not bulk_tmpl
    if st.button("🔄 Apply Template", type="primary", disabled=apply_disabled):
        # Fetch target tables
        override_clause = "" if override_manual else "AND (c.manually_overridden IS NULL OR c.manually_overridden = false)"
        target_sql = f"""
            SELECT r.table_fqn, r.tier,
                   c.partition_column, c.sort_columns, c.glue_job_name
            FROM {STREAM_REGISTRY_TABLE} r
            LEFT JOIN {HK_CONFIG_TABLE} c ON r.table_fqn = c.table_fqn
            WHERE r.domain        = '{bulk_domain}'
              AND r.layer         = '{bulk_layer}'
              AND r.hk_enabled    = true
              AND r.table_format  = 'iceberg'
              {override_clause}
            ORDER BY r.table_fqn
        """
        try:
            with st.spinner("Fetching tables..."):
                target_df = cached_read_registry(target_sql)

            if target_df.empty:
                st.info(f"No eligible tables found in `{bulk_domain}.{bulk_layer}`.")
            else:
                st.info(f"Applying `{bulk_tmpl}` to {len(target_df)} tables...")
                from engine.core.config import apply_template

                succeeded = 0
                failed    = 0
                errors    = []

                progress = st.progress(0)
                for i, (_, row) in enumerate(target_df.iterrows()):
                    fqn = row["table_fqn"]
                    try:
                        apply_template(
                            table_fqn=fqn,
                            template_name=bulk_tmpl,
                            partition_column=row.get("partition_column") or "partition_date",
                            dry_run=is_dry_run(),
                        )
                        succeeded += 1
                    except Exception as e:
                        failed += 1
                        errors.append(f"{fqn}: {e}")
                    progress.progress((i + 1) / len(target_df))

                clear_caches()
                progress.empty()

                if failed == 0:
                    st.success(
                        f"✅ Template `{bulk_tmpl}` applied to {succeeded} tables"
                        + (" (dry run)" if is_dry_run() else "")
                    )
                else:
                    st.warning(f"Applied to {succeeded} tables, {failed} failed.")
                    for err in errors[:
                        5]:
                        st.error(err)

        except Exception as e:
            st.error(f"Bulk apply failed: {e}")

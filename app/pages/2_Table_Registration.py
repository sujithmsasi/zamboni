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

from datetime import UTC, datetime

import pandas as pd
import streamlit as st

from app.components.athena_runner import cached_read_registry, execute_write
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
st.session_state["_current_page"] = "table_reg"
render_header(page_title="Table Registration", page_icon="➕")


# ── Helper: load domain list ──────────────────────────────────────────────────
def _table_short_name(fqn: str) -> str:
    """Convert FQN to readable label: 'glue_catalog.finance_db.t' -> 't  (finance_db)'"""
    parts = fqn.split(".")
    return f"{parts[2]}  ({parts[1]})" if len(parts) == 3 else fqn


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


tab_browse, tab_registered, tab_edit_reg, tab_engine_flags, tab_bulk_ctrlm = st.tabs([
    "🔍 Browse & Register",
    "📊 Registered Tables",
    "✏️ Edit Table",
    "⚙️ Engine Flags",
    "🔗 Bulk Control-M",
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
            ["-- All Databases --"] + databases,
            help="Select a specific database or browse all databases.",
        )
        show_unregistered_only = st.checkbox(
            "Show only unregistered tables",
            value=False,
            key="browse_unreg_only",
            help="Filter to tables not yet registered with Zamboni.",
        )

        # If All selected, aggregate tables across all databases
        if selected_db == "-- All Databases --":
            all_tables = []
            for db in databases:
                for t in _cached_tables(db):
                    t["Database"] = db
                    all_tables.append(t)
            tables     = all_tables
            selected_db = None   # signal for FQN building below
        else:
            tables = _cached_tables(selected_db)
            for t in tables:
                t["Database"] = selected_db

        if not tables:
            st.info("No tables found.")
        else:
            # Apply unregistered filter
            if show_unregistered_only:
                tables = [t for t in tables if t["Registered"] == "—"]

            if not tables:
                st.success("✅ All tables in this database are already registered.")
            else:
                iceberg_count = sum(1 for t in tables if t["Format"] == "iceberg")
                reg_count     = sum(1 for t in tables if t["Registered"] == "✅")

                c1, c2, c3 = st.columns(3)
                c1.metric("Shown",             len(tables))
                c2.metric("Iceberg",           iceberg_count)
                c3.metric("Already Registered", reg_count)

                df_tables = pd.DataFrame(tables)

                # ── Pattern search filter ─────────────────────────────────────
                _br_col1, _br_col2 = st.columns([3, 1])
                with _br_col1:
                    _br_pattern = st.text_input(
                        "Filter by name pattern",
                        key=f"browse_pattern_{selected_db}",
                        placeholder="aps_%  or  %_staging  or  fin_aps",
                        help="Wildcards: % = any chars, _ = single char. "
                             "Or plain substring (no wildcards needed).",
                    )
                with _br_col2:
                    st.markdown("<div style='padding-top:28px;display:flex;gap:6px'>",
                                unsafe_allow_html=True)
                    _br_sel_col, _br_des_col = st.columns(2)
                    with _br_sel_col:
                        _br_select_all = st.button(
                            "✅ Select All",
                            key=f"browse_selall_{selected_db}",
                            use_container_width=True,
                            help="Select all visible (filtered) rows",
                        )
                    with _br_des_col:
                        _br_deselect_all = st.button(
                            "⬜ Clear All",
                            key=f"browse_desall_{selected_db}",
                            use_container_width=True,
                            help="Clear all selections",
                        )
                    st.markdown("</div>", unsafe_allow_html=True)

                _br_unreg_only = st.checkbox(
                    "Show only unregistered tables",
                    value=False,
                    key=f"browse_unreg_{selected_db}",
                    help="Uncheck to show all tables — useful for moving tables between domains.",
                )

                # Apply filter
                _df_filtered = df_tables.copy()
                if _br_unreg_only and "Registered" in _df_filtered.columns:
                    _df_filtered = _df_filtered[_df_filtered["Registered"] == "—"]
                if _br_pattern.strip():
                    _raw = _br_pattern.strip()
                    # Convert SQL % wildcard to regex, otherwise substring match
                    if "%" in _raw or _raw.startswith("_"):
                        _re_pat = _raw.replace("%", ".*").replace("_", ".")
                        _df_filtered = _df_filtered[
                            _df_filtered["Name"].str.contains(
                                _re_pat, case=False, regex=True, na=False
                            )
                        ]
                    else:
                        _df_filtered = _df_filtered[
                            _df_filtered["Name"].str.contains(
                                _raw, case=False, regex=False, na=False
                            )
                        ]

                # Use a filter-dependent key so state resets when filter changes
                _filter_sig = f"{selected_db}_{_br_pattern.strip()}"
                _editor_key = f"browse_editor_{hash(_filter_sig) % 999999}"

                # Select All / Clear All
                if _br_select_all:
                    _sa_df = _df_filtered.copy()
                    _sa_df.insert(0, "Select", True)
                    st.session_state[f"browse_selall_data_{_filter_sig}"] = _sa_df
                    st.rerun()
                if _br_deselect_all:
                    _sa_df = _df_filtered.copy()
                    _sa_df.insert(0, "Select", False)
                    st.session_state[f"browse_selall_data_{_filter_sig}"] = _sa_df
                    st.rerun()

                # Load state from Select/Clear All, or default to all unchecked
                _sa_data = st.session_state.get(f"browse_selall_data_{_filter_sig}")
                if _sa_data is not None:
                    _display_df = _sa_data
                else:
                    _display_df = _df_filtered.copy()
                    _display_df.insert(0, "Select", False)

                edited = st.data_editor(
                    _display_df,
                    column_config={
                        "Select":     st.column_config.CheckboxColumn(
                            "✓", default=False,
                            help="Check to include in registration",
                        ),
                        "Registered": st.column_config.TextColumn("Registered"),
                        "Domain":     st.column_config.TextColumn("Current Domain"),
                        "Name":       st.column_config.TextColumn("Table Name"),
                    },
                    use_container_width=True,
                    hide_index=True,
                    disabled=["Name","Format","Registered","Domain",
                              "Tier","Layer","CreateTime","Location"],
                    key=_editor_key,
                    height=min(420, max(200, len(_display_df) * 35 + 40)),
                )

                st.caption(
                    f"Showing **{len(_df_filtered)}** of **{len(df_tables)}** tables"
                    + (f" (filtered by `{_br_pattern.strip()}`)" if _br_pattern.strip() else "")

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
                                    ["— select domain —"] + domains,
                                    index=0,
                                    help="Business domain this table belongs to.",
                                )
                            with col2:
                                layer = st.selectbox(
                                    "Layer *",
                                    ["— select layer —"] + VALID_LAYERS,
                                    index=0,
                                    help="Pipeline layer: staging, datalake, base, master.",
                                )
                            with col3:
                                tier = st.selectbox(
                                    "Tier *",
                                    ["— select tier —"] + VALID_TIERS,
                                    index=0,
                                    help="Criticality: critical / standard / low.",
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

                            st.markdown("**🔗 Control-M Integration (optional)**")
                            _rc1, _rc2, _rc3, _rc4 = st.columns(4)
                            with _rc1:
                                reg_pipeline_job = st.text_input(
                                    "Control-M Job Name *",
                                    placeholder="ACE-DA-FIN-APS-INGEST-PRD",
                                    key="reg_ctrlm_pipeline",
                                    help="The Control-M job that writes data to these tables.",
                                )
                            with _rc2:
                                reg_hk_job = st.text_input(
                                    "HK Control-M Job",
                                    placeholder="ACE-DA-FIN-HK-PRD",
                                    key="reg_ctrlm_hk",
                                    help="Control-M job that triggers Zamboni HK.",
                                )
                            with _rc3:
                                reg_gate1_job = st.text_input(
                                    "AWS Job Name — Gate 1 (optional)",
                                    placeholder="Leave blank to use Control-M Job Name",
                                    key="reg_ctrlm_gate1",
                                    help="AWS job that must complete before HK starts. "
                                         "Can be a Glue job, Lambda, Step Function etc. "
                                         "Leave blank to use the Control-M Job Name.",
                                )
                            with _rc4:
                                reg_job_type = st.selectbox(
                                    "Job Type",
                                    ["controlm", "glue", "lambda",
                                     "step_functions", "airflow", "other"],
                                    key="reg_ctrlm_jtype",
                                    help="AWS service type. Used by Gate 1 to call "
                                         "the correct completion-check API.",
                                )
                            import datetime as _dt_reg
                            _rt1, _rt2, _rt3 = st.columns(3)
                            with _rt1:
                                reg_job_start = st.time_input(
                                    "Job start time",
                                    value=_dt_reg.time(2, 0),
                                    key="reg_ctrlm_start",
                                    step=_dt_reg.timedelta(minutes=15),
                                )
                            with _rt2:
                                reg_job_dur = st.number_input(
                                    "Expected duration (min)",
                                    value=0, min_value=0, max_value=480,
                                    key="reg_ctrlm_dur",
                                )
                            with _rt3:
                                st.empty()  # spacer

                            notes = st.text_area(
                                "Notes",
                                help="Registration notes — stored in stream_registry.",
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
                                    _db = row.get("Database") or selected_db or ""
                                    fqn = f"glue_catalog.{_db}.{row['Name']}"
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
                                            controlm_pipeline_job=reg_pipeline_job.strip() or None,
                                            controlm_hk_job=reg_hk_job.strip() or None,
                                            dependent_on_controlm_job=reg_gate1_job.strip() or reg_pipeline_job.strip() or None,
                                            controlm_job_start_time=reg_job_start.strftime("%H:%M"),
                                            controlm_expected_duration_min=int(reg_job_dur),
                                            dependent_job_type=reg_job_type,
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
            table_fqn, domain, layer, tier,
            ci_number, owner_email,
            controlm_pipeline_job, controlm_hk_job,
            dependent_on_controlm_job,
            controlm_job_start_time, controlm_expected_duration_min,
            hk_enabled, archive_enabled, lifecycle_enabled,
            processing_cadence, dry_run_until
        FROM {STREAM_REGISTRY_TABLE}
        {where}
        ORDER BY domain, layer, table_fqn
        LIMIT 5000
    """
    try:
        df = cached_read_registry(sql)
        if df.empty:
            st.info("No registered tables match your filters.")
        else:
            # Apply bool formatting without renaming — rename happens in display block
            for col in ["hk_enabled", "archive_enabled", "lifecycle_enabled"]:
                if col in df.columns:
                    df[col] = df[col].apply(yes_no)

            if "registered_at" in df.columns:
                df["registered_at"] = df["registered_at"].astype(str).str[:10]

            if "dry_run_until" in df.columns:
                df["dry_run_until"] = df["dry_run_until"].astype(str).replace("None","")

            from itables.streamlit import interactive_table as _itr
            _d = df.copy()
            # Strip glue_catalog. prefix for readability
            if "table_fqn" in _d.columns:
                _d["table_fqn"] = _d["table_fqn"].str.replace(
                    r"^glue_catalog[.]", "", regex=True)
            # Show all columns in a logical order
            _col_order = [
                "table_fqn", "domain", "layer", "tier",
                "ci_number", "owner_email",
                "controlm_pipeline_job", "controlm_hk_job",
                "dependent_on_controlm_job",
                "controlm_job_start_time", "controlm_expected_duration_min",
                "hk_enabled", "archive_enabled", "lifecycle_enabled",
                "processing_cadence", "dry_run_until",
            ]
            _k = [c for c in _col_order if c in _d.columns]
            _d = _d[_k].rename(columns={
                "table_fqn":                    "Table",
                "ci_number":                    "CI",
                "owner_email":                  "Owner",
                "controlm_pipeline_job":        "Control-M Job",
                "controlm_hk_job":              "HK ControlM Job",
                "dependent_on_controlm_job":    "AWS Gate 1 Job",
                "controlm_job_start_time":      "Job Start",
                "controlm_expected_duration_min": "Job Dur (min)",
                "hk_enabled":                   "HK",
                "archive_enabled":              "Archive",
                "lifecycle_enabled":            "Lifecycle",
                "processing_cadence":           "Cadence",
                "dry_run_until":                "Dry Run Until",
            })
            _d.insert(0, "#", range(1, len(_d) + 1))
            _itr(
                _d, key="tr_it",
                style="width:100%;font-size:12px;",
                classes="display compact cell-border stripe hover nowrap",
                maxBytes=0,
                downsampling_warning=False,
                lengthMenu=[[15,25,50,100,250,-1],["15","25","50","100","250","All"]],
                pageLength=15,
                scrollX=True,
                caption=f"{len(df):,} table(s)",
            )

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


# ── Tab 3: Edit Table ────────────────────────────────────────────────────────
with tab_edit_reg:
    st.subheader("✏️ Edit Registered Table")
    st.caption(
        "Select a table from the list above to update its "
        "domain, tier, layer, or owner."
    )
    # Rebuild unformatted FQN list from raw query
    try:
        raw_df = cached_read_registry(
            f"SELECT table_fqn, domain, layer, tier, "
            f"owner_email, ci_number, hk_enabled, "
            f"archive_enabled, lifecycle_enabled, processing_cadence, "
            f"dry_run_until, "
            f"controlm_pipeline_job, controlm_hk_job, "
            f"dependent_on_controlm_job, controlm_job_start_time, "
            f"controlm_expected_duration_min "
            f"FROM {STREAM_REGISTRY_TABLE} "
            f"{('WHERE domain = ' + chr(39) + sel_domain + chr(39)) if sel_domain != 'All' else ''} "
            f"ORDER BY domain, table_fqn LIMIT 5000"
        )
        if not raw_df.empty:
            edit_fqn = st.selectbox(
                "Select table to edit",
                ["-- select --"] + raw_df["table_fqn"].tolist(),
                key="edit_table_fqn_sel",
                help="Pick a registered table to edit its metadata.",
            )
            if edit_fqn and edit_fqn != "-- select --":
                trow = raw_df[raw_df["table_fqn"] == edit_fqn].iloc[0].to_dict()
                _ek  = edit_fqn.replace(".", "_")  # key prefix

                with st.form(f"edit_table_form_{_ek}"):
                    st.markdown(f"**Editing:** `{edit_fqn}`")
                    ec1, ec2, ec3 = st.columns(3)
                    with ec1:
                        e_domain = st.selectbox(
                            "Domain",
                            _get_domains(),
                            index=(_get_domains().index(trow.get("domain",""))
                                   if trow.get("domain","") in _get_domains() else 0),
                            key=f"{_ek}_domain",
                        )
                        e_layer = st.selectbox(
                            "Layer",
                            VALID_LAYERS,
                            index=(VALID_LAYERS.index(trow.get("layer","staging"))
                                   if trow.get("layer","staging") in VALID_LAYERS else 0),
                            key=f"{_ek}_layer",
                        )
                    with ec2:
                        e_tier = st.selectbox(
                            "Tier",
                            VALID_TIERS,
                            index=(VALID_TIERS.index(trow.get("tier","standard"))
                                   if trow.get("tier","standard") in VALID_TIERS else 1),
                            key=f"{_ek}_tier",
                        )
                    with ec3:
                        e_owner = st.text_input(
                            "Owner Email",
                            value=str(trow.get("owner_email") or ""),
                            key=f"{_ek}_owner",
                        )
                        e_ci = st.text_input(
                            "CI Number",
                            value=str(trow.get("ci_number") or ""),
                            key=f"{_ek}_ci",
                        )

                    ef1, ef2, ef3 = st.columns(3)
                    with ef1:
                        e_hk = st.checkbox(
                            "🔧 Housekeeping",
                            value=bool(trow.get("hk_enabled", False)),
                            key=f"{_ek}_hk",
                            help="Enable HK Engine (compaction + vacuum).",
                        )
                    with ef2:
                        e_archive = st.checkbox(
                            "📦 Archival",
                            value=bool(trow.get("archive_enabled", False)),
                            key=f"{_ek}_archive",
                            help="Enable Archival Engine for cold partition export.",
                        )
                    with ef3:
                        e_lifecycle = st.checkbox(
                            "♻️ Lifecycle",
                            value=bool(trow.get("lifecycle_enabled", False)),
                            key=f"{_ek}_lifecycle",
                            help="Enable Lifecycle Engine (non-prod state machine).",
                        )
                    e_cadence = st.selectbox(
                        "Processing Cadence",
                        ["daily", "weekly", "monthly", "hourly", "every_trigger"],
                        index=(["daily","weekly","monthly","hourly","every_trigger"]
                               .index(trow.get("processing_cadence") or "daily")
                               if trow.get("processing_cadence") in
                               ["daily","weekly","monthly","hourly","every_trigger"]
                               else 0),
                        key=f"{_ek}_cadence",
                        help="How often this table is processed — "
                             "drives partition filter window.",
                    )

                    st.markdown("**🔗 Control-M Integration**")
                    cm1, cm2, cm3, cm4 = st.columns(4)
                    with cm1:
                        e_pipeline_job = st.text_input(
                            "Control-M Job Name *",
                            value=str(trow.get("controlm_pipeline_job") or ""),
                            key=f"{_ek}_pipeline_job",
                            placeholder="ACE-DA-FIN-APS-INGEST-PRD",
                            help="Control-M job that loads data into this table.",
                        )
                    with cm2:
                        e_hk_job = st.text_input(
                            "HK Control-M Job",
                            value=str(trow.get("controlm_hk_job") or ""),
                            key=f"{_ek}_hk_job",
                            placeholder="ACE-DA-FIN-HK-PRD",
                            help="Control-M job that triggers Zamboni HK.",
                        )
                    with cm3:
                        e_upstream_job = st.text_input(
                            "AWS Job Name — Gate 1 (optional)",
                            value=str(trow.get("dependent_on_controlm_job") or ""),
                            key=f"{_ek}_upstream_job",
                            placeholder="Leave blank to use Control-M Job Name",
                            help="AWS job (Glue/Lambda/Step Function) that must "
                                 "complete before HK starts. Blank = use Control-M Job Name.",
                        )
                    with cm4:
                        _jtype_opts = ["controlm","glue","lambda","step_functions","airflow","other"]
                        _cur_jtype  = str(trow.get("dependent_job_type") or "controlm")
                        _jtype_idx  = _jtype_opts.index(_cur_jtype) if _cur_jtype in _jtype_opts else 0
                        e_job_type = st.selectbox(
                            "Job Type",
                            _jtype_opts,
                            index=_jtype_idx,
                            key=f"{_ek}_job_type",
                            help="AWS service type for Gate 1 API check.",
                        )

                    # Job run schedule (time picker)
                    import datetime as _dt
                    _raw_start = str(trow.get("controlm_job_start_time") or "02:00")
                    try:
                        _h, _m = [int(x) for x in _raw_start.split(":")]
                    except Exception:
                        _h, _m = 2, 0
                    cm4, cm5, _cm6 = st.columns([1, 1, 2])
                    with cm4:
                        e_job_start = st.time_input(
                            "Job run start time",
                            value=_dt.time(_h, _m),
                            key=f"{_ek}_job_start",
                            step=_dt.timedelta(minutes=15),
                            help="Scheduled start time of the upstream pipeline job. "
                                 "Zamboni uses this for Gate 1 timing calculations.",
                        )
                    with cm5:
                        e_expected_dur = st.number_input(
                            "Expected job duration (min)",
                            value=int(trow.get("controlm_expected_duration_min") or 0),
                            min_value=0,
                            max_value=480,
                            key=f"{_ek}_expected_dur",
                            help="Expected pipeline run duration. "
                                 "HK delay starts after start_time + duration.",
                        )

                    if is_dry_run():
                        st.info("🔵 Dry Run — no writes.")

                    if st.form_submit_button("💾 Save Changes", type="primary"):
                            try:
                                _now_edit = datetime.now(UTC).strftime(
                                    "%Y-%m-%d %H:%M:%S"
                                )
                                def _esc_v(s): return str(s).replace("'","''")
                                upd_sql = f"""
                                    UPDATE {STREAM_REGISTRY_TABLE}
                                    SET domain                     = '{e_domain}',
                                        layer                      = '{e_layer}',
                                        tier                       = '{e_tier}',
                                        owner_email                = '{e_owner}',
                                        ci_number                  = '{e_ci}',
                                        hk_enabled                 = {'1' if e_hk else '0'},
                                        archive_enabled            = {'1' if e_archive else '0'},
                                        lifecycle_enabled          = {'1' if e_lifecycle else '0'},
                                        processing_cadence         = '{e_cadence}',
                                        controlm_pipeline_job           = '{_esc_v(e_pipeline_job)}',
                                        controlm_hk_job                 = '{_esc_v(e_hk_job)}',
                                        dependent_on_controlm_job       = '{_esc_v(e_upstream_job)}',
                                        dependent_job_type              = '{e_job_type}',
                                        controlm_job_start_time         = '{e_job_start.strftime("%H:%M")}',
                                        controlm_expected_duration_min  = {int(e_expected_dur)},
                                        updated_at                 = '{_now_edit}'
                                    WHERE table_fqn = '{edit_fqn}'
                                """
                                execute_write(upd_sql, dry_run=is_dry_run())
                                audit(AuditEvent(
                                    actor=current_user(),
                                    action_type=AuditAction.TABLE_REGISTER,
                                    page_source="2_Table_Registration",
                                    target_type="table",
                                    target_id=edit_fqn,
                                    domain=e_domain,
                                    environment="prod",
                                    dry_run=is_dry_run(),
                                    status="DRY_RUN" if is_dry_run() else "SUCCESS",
                                ))
                                cached_read_registry.clear()
                                st.success(
                                    f"✅ `{edit_fqn}` updated."
                                    + (" (dry run)" if is_dry_run() else "")
                                )
                            except Exception as e:
                                st.error(f"Update failed: {e}")
    except Exception as e:
        st.error(f"Could not load table list for editing: {e}")

# ── Tab 4: Engine Flags ─────────────────────────────────────────────────────
with tab_engine_flags:
    st.subheader("⚙️ Engine Flags")
    st.caption(
        "Enable or disable Archival and Lifecycle engines per table, "
        "or bulk-apply to a whole domain or database."
    )

    flag_tab_single, flag_tab_bulk = st.tabs([
        "Single Table", "Bulk Apply"
    ])

    with flag_tab_single:
        _all_fqns = []
        try:
            _fqn_df = cached_read_registry(
                f"SELECT table_fqn, domain, hk_enabled, archive_enabled, "
                f"lifecycle_enabled FROM {STREAM_REGISTRY_TABLE} ORDER BY table_fqn"
            )
            if not _fqn_df.empty:
                _all_fqns = _fqn_df["table_fqn"].tolist()
        except Exception:
            pass

        if not _all_fqns:
            st.info("No tables registered.")
        else:
            _label_map = {_table_short_name(f): f for f in _all_fqns}
            _flag_sel_label = st.selectbox(
                "Table (type to search)",
                ["-- select --"] + list(_label_map.keys()),
                key="flag_single_sel",
                help="Type table name to filter.",
            )
            _flag_fqn = _label_map.get(_flag_sel_label)

            if _flag_fqn and not _fqn_df.empty:
                _row = _fqn_df[_fqn_df["table_fqn"] == _flag_fqn]
                if not _row.empty:
                    _r = _row.iloc[0]
                    st.caption(f"`{_flag_fqn}`")

                    # Key includes FQN so switching tables resets all checkboxes
                    _ffk = _flag_fqn.replace(".", "_").replace("/", "_")
                    with st.form(f"flag_single_form_{_ffk}"):
                        fc1, fc2, fc3 = st.columns(3)
                        with fc1:
                            new_hk = st.checkbox(
                                "🔧 Housekeeping Enabled",
                                value=bool(int(_r.get("hk_enabled") or 0)),
                                key=f"{_ffk}_flag_hk",
                                help="Enable HK Engine (compaction + vacuum) for this table.",
                            )
                        with fc2:
                            new_archive = st.checkbox(
                                "📦 Archival Enabled",
                                value=bool(int(_r.get("archive_enabled") or 0)),
                                key=f"{_ffk}_flag_archive",
                                help="Enable Archival Engine to export and delete "
                                     "cold staging partitions.",
                            )
                        with fc3:
                            new_lifecycle = st.checkbox(
                                "♻️ Lifecycle Enabled",
                                value=bool(int(_r.get("lifecycle_enabled") or 0)),
                                key=f"{_ffk}_flag_lifecycle",
                                help="Enable Lifecycle Engine for non-prod state machine "
                                     "(ACTIVE→STALE→GREENZONE→DROPPED).",
                            )

                        if is_dry_run():
                            st.info("🔵 Dry Run — no writes.")

                        if st.form_submit_button("💾 Save Flags", type="primary"):
                            try:
                                _now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
                                _upd = f"""
                                    UPDATE {STREAM_REGISTRY_TABLE}
                                    SET hk_enabled         = {'1' if new_hk else '0'},
                                        archive_enabled    = {'1' if new_archive else '0'},
                                        lifecycle_enabled  = {'1' if new_lifecycle else '0'},
                                        updated_at         = '{_now}'
                                    WHERE table_fqn = '{_flag_fqn}'
                                """
                                execute_write(_upd, dry_run=is_dry_run())
                                audit(AuditEvent(
                                    actor=current_user(),
                                    action_type=AuditAction.HK_ENABLE
                                              if new_hk else AuditAction.HK_DISABLE,
                                    page_source="2_Table_Registration",
                                    target_type="table",
                                    target_id=_flag_fqn,
                                    dry_run=is_dry_run(),
                                    status="DRY_RUN" if is_dry_run() else "SUCCESS",
                                    after_value=f"hk={new_hk},archive={new_archive},"
                                               f"lifecycle={new_lifecycle}",
                                ))
                                cached_read_registry.clear()
                                st.success(
                                    f"✅ Flags updated for `{_flag_fqn}`."
                                    + (" (dry run)" if is_dry_run() else "")
                                )
                            except Exception as e:
                                st.error(f"Failed: {e}")

    with flag_tab_bulk:
        st.caption(
            "Apply engine flag settings to all tables in a domain or database. "
            "Useful for onboarding a new domain or disabling archival for a whole layer."
        )

        _bulk_domains = ["All"] + _get_domains()
        bc1, bc2, bc3 = st.columns(3)
        with bc1:
            bulk_flag_domain = st.selectbox(
                "Domain",
                _bulk_domains,
                key="bulk_flag_domain",
                help="Apply to all tables in this domain.",
            )
        with bc2:
            bulk_flag_layer = st.selectbox(
                "Layer",
                ["All"] + VALID_LAYERS,
                key="bulk_flag_layer",
                help="Restrict to a specific pipeline layer.",
            )
        with bc3:
            # Auto-populate databases from stream_registry based on domain selection
            @st.cache_data(ttl=120, show_spinner=False)
            def _get_databases_for_domain(domain: str) -> list[str]:
                try:
                    where = f"WHERE domain = '{domain}'" if domain != "All" else ""
                    df = cached_read_registry(
                        f"SELECT DISTINCT database_name FROM {STREAM_REGISTRY_TABLE} "
                        f"{where} ORDER BY database_name"
                    )
                    if not df.empty and "database_name" in df.columns:
                        return ["All"] + df["database_name"].dropna().tolist()
                except Exception:
                    pass
                return ["All"]

            _db_options = _get_databases_for_domain(bulk_flag_domain)
            bulk_flag_db_sel = st.selectbox(
                "Database (optional)",
                _db_options,
                key="bulk_flag_db",
                help="Auto-populated from selected domain. "
                     "Choose All or a specific database.",
            )
            bulk_flag_db = "" if bulk_flag_db_sel == "All" else bulk_flag_db_sel

        bf1, bf2, bf3 = st.columns(3)
        with bf1:
            bulk_hk = st.selectbox(
                "🔧 Housekeeping",
                ["no change", "enable", "disable"],
                key="bulk_flag_hk",
            )
        with bf2:
            bulk_archive = st.selectbox(
                "📦 Archival",
                ["no change", "enable", "disable"],
                key="bulk_flag_archive",
            )
        with bf3:
            bulk_lifecycle = st.selectbox(
                "♻️ Lifecycle",
                ["no change", "enable", "disable"],
                key="bulk_flag_lifecycle",
            )

        all_no_change = (
            bulk_hk == "no change"
            and bulk_archive == "no change"
            and bulk_lifecycle == "no change"
        )

        if is_dry_run():
            st.info("🔵 Dry Run — no writes.")

        if st.button(
            "⚙️ Apply Bulk Flags",
            type="primary",
            disabled=all_no_change,
            key="bulk_flag_apply",
        ):
            # Build SET clauses only for changed flags
            set_parts = []
            if bulk_hk       != "no change":
                set_parts.append(f"hk_enabled = {'1' if bulk_hk == 'enable' else '0'}")
            if bulk_archive   != "no change":
                set_parts.append(f"archive_enabled = {'1' if bulk_archive == 'enable' else '0'}")
            if bulk_lifecycle != "no change":
                set_parts.append(f"lifecycle_enabled = {'1' if bulk_lifecycle == 'enable' else '0'}")

            _now2 = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
            set_parts.append(f"updated_at = '{_now2}'")

            # Build WHERE
            where_parts = []
            if bulk_flag_domain != "All":
                where_parts.append(f"domain = '{bulk_flag_domain}'")
            if bulk_flag_layer  != "All":
                where_parts.append(f"layer = '{bulk_flag_layer}'")
            if bulk_flag_db:
                where_parts.append(f"database_name = '{bulk_flag_db}'")
            where_clause = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""

            bulk_sql = f"""
                UPDATE {STREAM_REGISTRY_TABLE}
                SET {', '.join(set_parts)}
                {where_clause}
            """

            # Preview count first
            count_sql = f"""
                SELECT COUNT(*) AS cnt FROM {STREAM_REGISTRY_TABLE} {where_clause}
            """
            try:
                cnt_df = cached_read_registry(count_sql)
                cnt = int(cnt_df.iloc[0]["cnt"]) if not cnt_df.empty else 0
                st.info(
                    f"{cnt} table(s) will be updated: "
                    f"hk={bulk_hk}, archive={bulk_archive}, lifecycle={bulk_lifecycle}"
                )

                if cnt > 0:
                    execute_write(bulk_sql, dry_run=is_dry_run())
                    audit(AuditEvent(
                        actor=current_user(),
                        action_type=AuditAction.HK_ENABLE,
                        page_source="2_Table_Registration",
                        target_type="domain",
                        target_id=bulk_flag_domain,
                        dry_run=is_dry_run(),
                        status="DRY_RUN" if is_dry_run() else "SUCCESS",
                        after_value=f"hk={bulk_hk},archive={bulk_archive},"
                                   f"lifecycle={bulk_lifecycle},count={cnt}",
                    ))
                    cached_read_registry.clear()
                    st.success(
                        f"✅ Flags updated for {cnt} table(s)."
                        + (" (dry run)" if is_dry_run() else "")
                    )
            except Exception as e:
                st.error(f"Bulk flag update failed: {e}")


# ── Tab 5: Bulk Control-M ─────────────────────────────────────────────────────
with tab_bulk_ctrlm:
    st.subheader("🔗 Bulk Apply Control-M Job Names")

    bc_tab_manual, bc_tab_import, bc_tab_export, bc_tab_jobs = st.tabs([
        "🖊️ Manual Bulk Apply",
        "📥 Import Job Mapping",
        "📤 Export Mapping Template",
        "🗂️ Control-M Job Registry",
    ])

    # ── Sub-tab: Manual Bulk Apply ────────────────────────────────────────────
    with bc_tab_manual:
        st.subheader("🔗 Bulk Apply Control-M Job Names")
        if "bulk_ctrlm_flash" in st.session_state:
            _flash_msg = st.session_state["bulk_ctrlm_flash"]
            st.success(_flash_msg)
            # Clear after showing (uses a shown flag so it persists through one rerun)
            if st.session_state.get("bulk_ctrlm_flash_shown"):
                del st.session_state["bulk_ctrlm_flash"]
                st.session_state.pop("bulk_ctrlm_flash_shown", None)
            else:
                st.session_state["bulk_ctrlm_flash_shown"] = True
        st.caption(
            "One Control-M job typically loads multiple tables. "
            "Apply the same job names, start time, and CI number to all matching tables."
        )

        _bc1, _bc2 = st.columns(2)
        import datetime as _dt_bc
        # Row 1: Control-M Job Name + start time + duration
        _brow1a, _brow1b, _brow1c = st.columns([3, 1, 1])
        with _brow1a:
            _bulk_pipeline = st.text_input(
                "Control-M Job Name *",
                placeholder="ACE-DA-FIN-APS-INGEST-PRD",
                key="bulk_ctrlm_pipeline",
                help="Control-M job that loads data into the matched tables.",
            )
        with _brow1b:
            _bulk_start = st.time_input(
                "Job run start time",
                value=_dt_bc.time(2, 0),
                key="bulk_ctrlm_start",
                step=_dt_bc.timedelta(minutes=15),
            )
        with _brow1c:
            _bulk_dur = st.number_input(
                "Expected job duration (min)",
                value=0, min_value=0, max_value=480,
                key="bulk_ctrlm_dur",
            )

        # Row 2: HK Job + Gate 1 + Job Type
        _brow2a, _brow2b, _brow2c = st.columns(3)
        with _brow2a:
            _bulk_hk = st.text_input(
                "HK Control-M Job",
                placeholder="ACE-DA-FIN-HK-PRD",
                key="bulk_ctrlm_hk",
                help="Control-M job that triggers Zamboni HK (optional).",
            )
        with _brow2b:
            _bulk_gate1 = st.text_input(
                "AWS Job Name — Gate 1 (optional)",
                placeholder="Leave blank to use Control-M Job Name",
                key="bulk_ctrlm_gate1",
                help="AWS Glue/Lambda/Step Function job for Gate 1 check.",
            )
        with _brow2c:
            _bulk_jtype = st.selectbox(
                "Job Type",
                ["controlm", "glue", "lambda", "step_functions", "airflow", "other"],
                key="bulk_ctrlm_jtype",
                help="AWS service type used by Gate 1 engine.",
            )

        # Row 3: CI Number
        _brow3a, _brow3b, _brow3c = st.columns(3)
        with _brow3a:
            _bulk_ci = st.text_input(
                "CI Number (optional — leave blank to keep existing)",
                placeholder="CI-10300",
                key="bulk_ctrlm_ci",
            )
        with _brow3b:
            st.empty()
        with _brow3c:
            st.empty()

        st.markdown("**Apply to tables matching:**")
        _bm1, _bm2, _bm3 = st.columns(3)
        with _bm1:
            _bulk_domain = st.selectbox("Domain", ["All"] + _get_domains(), key="bulk_ctrlm_domain")
        with _bm2:
            _bulk_layer  = st.selectbox("Layer",  ["All"] + VALID_LAYERS,   key="bulk_ctrlm_layer")
        with _bm3:
            _bulk_db = st.text_input("Database (optional)", key="bulk_ctrlm_db",
                                      placeholder="finance_staging_db")

        _bulk_pattern = st.text_input(
            "Table name pattern (optional)",
            key="bulk_ctrlm_pattern",
            placeholder="aps_%_staging  or  %_ingest_%  or  fin_aps%",
            help=(
                "SQL LIKE pattern to match table names (not the full FQN). "
                "Use `%` as wildcard (matches any characters) and `_` for single character. "
                "Examples: `aps_%` matches aps_booking, aps_invoice … "
                "`%_stg_%` matches anything with _stg_ in the name."
            ),
        )
        if _bulk_pattern.strip():
            st.caption(
                f"Pattern `{_bulk_pattern.strip()}` will match tables where "
                f"`table_name LIKE '{_bulk_pattern.strip()}'`"
            )

        # ── Preview + confirm before applying ───────────────────────────────────────
        def _build_where(domain, layer, db, pat):
            _conds = list(filter(None, [
                f"domain = '{domain}'"          if domain != "All" else "",
                f"layer = '{layer}'"             if layer  != "All" else "",
                f"database_name = '{db.strip()}'" if db.strip() else "",
                (f"table_fqn LIKE '%.' || '{pat}'" if "%" in pat or pat.startswith("_")
                 else f"table_fqn LIKE '%.{pat}%'")
                if pat else "",
            ]))
            return ("WHERE " + " AND ".join(_conds)) if _conds else ""

        _pat = _bulk_pattern.strip()
        _where_prev = _build_where(_bulk_domain, _bulk_layer, _bulk_db, _pat)

        # Preview button — loads matching tables
        _prev_col, _apply_col = st.columns([1, 1])
        with _prev_col:
            _preview_clicked = st.button(
                "🔍 Preview Matching Tables",
                key="bulk_ctrlm_preview",
                disabled=not _bulk_pipeline.strip(),
            )
        with _apply_col:
            if is_dry_run():
                st.info("🔵 Dry Run — no writes.")

        if _preview_clicked and _bulk_pipeline.strip():
            try:
                _prev_df = cached_read_registry(
                    f"SELECT table_fqn, domain, layer, tier, ci_number, "
                    f"controlm_pipeline_job, controlm_hk_job "
                    f"FROM {STREAM_REGISTRY_TABLE} {_where_prev} "
                    f"ORDER BY domain, table_fqn LIMIT 500"
                )
                if _prev_df.empty:
                    st.warning("No tables match the current filters.")
                else:
                    # Store in session_state so it persists after preview click
                    _prev_df["table_fqn"] = _prev_df["table_fqn"].str.replace(
                        r"^glue_catalog[.]", "", regex=True
                    )
                    st.session_state["bulk_ctrlm_preview_df"]    = _prev_df
                    st.session_state["bulk_ctrlm_excluded"]      = set()
                    st.session_state["bulk_ctrlm_preview_where"] = _where_prev
            except Exception as e:
                st.error(f"Preview failed: {e}")

        # Show preview table with per-row exclude checkboxes
        if "bulk_ctrlm_preview_df" in st.session_state:
            _pdf = st.session_state["bulk_ctrlm_preview_df"]
            _excluded = st.session_state.get("bulk_ctrlm_excluded", set())

            st.markdown(f"**{len(_pdf)} table(s) matched**")

            # Show in compact itables grid
            from itables.streamlit import interactive_table as _it_prev
            _show = _pdf.copy()
            _show.insert(0, "#", range(1, len(_show)+1))
            _it_prev(
                _show, key="bulk_prev_it",
                style="width:100%;font-size:12px;",
                classes="display compact cell-border stripe hover nowrap",
                maxBytes=0, downsampling_warning=False,
                lengthMenu=[[15,25,50,100,-1],["15","25","50","100","All"]],
                pageLength=15, scrollX=True,
                caption=f"{len(_pdf)} table(s) will receive: {_bulk_pipeline.strip()}",
            )

            # Exclude specific tables via multiselect
            _all_fqns = list(_pdf["table_fqn"].values)
            _excl_list = st.multiselect(
                "Exclude tables (optional — select any you want to skip)",
                options=_all_fqns,
                default=list(_excluded),
                key="bulk_ctrlm_exclude_sel",
                help="Any tables selected here will NOT be updated.",
            )
            _excluded = set(_excl_list)
            st.session_state["bulk_ctrlm_excluded"] = _excluded

            _to_apply = [f for f in _all_fqns if f not in _excluded]
            if _excluded:
                st.caption(f"✅ {len(_to_apply)} will be updated  ·  ⛔ {len(_excluded)} excluded")
            else:
                st.caption(f"✅ All {len(_to_apply)} table(s) will be updated")

            if st.button(
                f"🔗 Apply to {len(_to_apply)} Table(s)",
                type="primary",
                disabled=(not _to_apply),
                key="bulk_ctrlm_apply",
            ):
                from datetime import UTC as _UTC_bc
                from datetime import datetime as _ddt_bc
                try:
                    _now = _ddt_bc.now(_UTC_bc).strftime("%Y-%m-%d %H:%M:%S")
                    _sets = [
                        f"controlm_pipeline_job = '{_bulk_pipeline.strip()}'",
                        f"controlm_hk_job = '{_bulk_hk.strip()}'",
                        f"dependent_on_controlm_job = '{_bulk_gate1.strip() or _bulk_pipeline.strip()}'",
                        f"dependent_job_type = '{_bulk_jtype}'",
                    "controlm_job_start_time = '" + _bulk_start.strftime("%H:%M") + "'",
                        f"controlm_expected_duration_min = {int(_bulk_dur)}",
                        f"updated_at = '{_now}'",
                    ]
                    if _bulk_ci.strip():
                        _sets.append(f"ci_number = '{_bulk_ci.strip()}'")

                    _applied = 0
                    for _tfqn in _to_apply:
                        # Restore full FQN for the UPDATE
                        _full_fqn = f"glue_catalog.{_tfqn}" if not _tfqn.startswith("glue_catalog") else _tfqn
                        execute_write(
                            f"UPDATE {STREAM_REGISTRY_TABLE} "
                            f"SET {', '.join(_sets)} "
                            f"WHERE table_fqn = '{_full_fqn}'",
                            dry_run=is_dry_run(),
                        )
                        _applied += 1

                    cached_read_registry.clear()
                    # Clear preview after apply
                    st.session_state.pop("bulk_ctrlm_preview_df", None)
                    st.session_state.pop("bulk_ctrlm_excluded", None)
                    st.session_state.pop("bulk_ctrlm_preview_where", None)
                    audit(AuditEvent(
                        actor=current_user(),
                        action_type=AuditAction.HK_ENABLE,
                        page_source="2_Table_Registration",
                        target_type="domain", target_id=_bulk_domain,
                        dry_run=is_dry_run(),
                        status="DRY_RUN" if is_dry_run() else "SUCCESS",
                        after_value=f"pipeline={_bulk_pipeline},count={_applied}",
                    ))
                    st.session_state["bulk_ctrlm_flash"] = (
                        f"✅ Applied to {_applied} table(s)."
                        + (" (dry run)" if is_dry_run() else "")
                    )
                    # No st.rerun() — let the flash message render first
                except Exception as e:
                    st.error(f"Bulk apply failed: {e}")

    # ── Sub-tab: Import Job Mapping ───────────────────────────────────────────
    with bc_tab_import:
        st.markdown(
            "Upload a **Job Mapping CSV** from domain teams. "
            "Each row maps one job to a set of tables by domain/layer/pattern. "
            "Zamboni applies the mapping in bulk — no per-table editing needed."
        )
        st.caption(
            "💡 Use the **Export Mapping Template** tab to generate a pre-filled "
            "CSV for domain teams. They fill in job names and return the file."
        )

        _EXPECTED_COLS = [
            "domain", "layer", "database_name", "table_pattern",
            "controlm_job_name", "hk_controlm_job", "aws_gate1_job",
            "job_type", "job_start_time", "expected_duration_min",
        ]

        uploaded = st.file_uploader(
            "Upload Job Mapping CSV",
            type=["csv"],
            key="bulk_ctrlm_upload",
            help="CSV columns: domain, layer, database_name, table_pattern, controlm_job_name, hk_controlm_job, aws_gate1_job, job_type, job_start_time, expected_duration_min. "
                 "job_type describes aws_gate1_job (the AWS service Gate 1 calls to check completion), not controlm_job_name.",
        )

        if uploaded:
            import io as _io_imp

            import pandas as _pd_imp

            try:
                _map_df = _pd_imp.read_csv(_io_imp.BytesIO(uploaded.read()), skip_blank_lines=True)
                # Drop fully blank rows and clean strings
                _map_df.dropna(how="all", inplace=True)
                _map_df.reset_index(drop=True, inplace=True)
                _map_df.columns = [c.strip().lower() for c in _map_df.columns]
                # An all-blank column (e.g. table_pattern) reads as all-NaN
                # float64, which the object-dtype-only loop below would skip
                # -- leaving a raw NaN to later stringify as literal "nan"
                # and get used as a LIKE pattern that matches no tables.
                _map_df = _map_df.fillna("")
                for _sc in _map_df.select_dtypes(include="object").columns:
                    _map_df[_sc] = _map_df[_sc].astype(str).str.strip().replace({"nan":"","None":""})
                # Drop rows with empty domain
                if "domain" in _map_df.columns:
                    _map_df = _map_df[_map_df["domain"].ne("")].reset_index(drop=True)

                _missing = [c for c in ["domain", "controlm_job_name"] if c not in _map_df.columns]
                if _missing:
                    st.error(f"CSV missing required columns: {_missing}. Required: domain, controlm_job_name. Download the template from Export tab for correct headers.")
                else:
                    # Fill optional columns with defaults
                    # Support both old and new column names for backward compat
                    _rename_map = {
                        "pipeline_job": "controlm_job_name",
                        "hk_job": "hk_controlm_job",
                        "gate1_upstream_job": "aws_gate1_job",
                    }
                    _map_df.rename(columns=_rename_map, inplace=True, errors="ignore")

                    for _col, _def in [
                        ("layer", ""), ("database_name", ""), ("table_pattern", ""),
                        ("job_type", "controlm"), ("hk_controlm_job", ""),
                        ("aws_gate1_job", ""), ("job_start_time", "02:00"),
                        ("expected_duration_min", 0),
                    ]:
                        if _col not in _map_df.columns:
                            _map_df[_col] = _def
                    _map_df["expected_duration_min"] = (
                        _pd_imp.to_numeric(
                            _map_df["expected_duration_min"], errors="coerce"
                        ).fillna(0).astype(int)
                    )

                    st.markdown(f"**{len(_map_df)} job mapping row(s) uploaded:**")
                    from itables.streamlit import interactive_table as _it_imp
                    _it_imp(
                        _map_df, key="import_preview_it",
                        style="width:100%;font-size:12px;",
                        classes="display compact cell-border stripe hover nowrap",
                        maxBytes=0, downsampling_warning=False,
                        pageLength=15, scrollX=True,
                    )

                    # Preview match count per row
                    st.markdown("**Match preview — tables that will be updated per mapping row:**")
                    _prev_rows = []
                    for _, _mr in _map_df.iterrows():
                        _mc = list(filter(None, [
                            f"domain = '{str(_mr.get('domain','')).strip()}'"
                            if str(_mr.get("domain","")).strip() else "",
                            f"layer = '{str(_mr.get('layer','')).strip()}'"
                            if str(_mr.get("layer","")).strip() else "",
                            f"database_name = '{str(_mr.get('database_name','')).strip()}'"
                            if str(_mr.get("database_name","")).strip() else "",
                            f"table_fqn LIKE '%.{str(_mr.get('table_pattern','')).strip()}%'"
                            if str(_mr.get("table_pattern","")).strip() else "",
                        ]))
                        _mw = ("WHERE " + " AND ".join(_mc)) if _mc else ""
                        try:
                            _n = int(cached_read_registry(
                                f"SELECT COUNT(*) AS n FROM {STREAM_REGISTRY_TABLE} {_mw}"
                            ).iloc[0]["n"])
                        except Exception:
                            _n = -1
                        _prev_rows.append({
                            "Job": str(_mr.get("controlm_job_name","")),
                            "Type": str(_mr.get("job_type","controlm")),
                            "Domain": str(_mr.get("domain","")),
                            "Layer": str(_mr.get("layer","")),
                            "DB": str(_mr.get("database_name","")),
                            "Pattern": str(_mr.get("table_pattern","")),
                            "Tables Matched": _n,
                        })
                    _prev_df2 = _pd_imp.DataFrame(_prev_rows)
                    _it_imp(
                        _prev_df2, key="import_match_it",
                        style="width:100%;font-size:12px;",
                        classes="display compact cell-border stripe hover nowrap",
                        maxBytes=0, downsampling_warning=False,
                        pageLength=15, scrollX=True,
                    )
                    _total = sum(r for r in _prev_df2["Tables Matched"] if r >= 0)
                    st.info(f"**{_total} table(s)** will be updated across all rows.")

                    if is_dry_run():
                        st.info("🔵 Dry Run — no writes.")

                    if st.button(
                        f"📥 Apply Mapping to {_total} Table(s)",
                        type="primary",
                        disabled=(_total == 0),
                        key="bulk_import_apply",
                    ):
                        from datetime import UTC as _UTC_imp
                        from datetime import datetime as _ddt_imp
                        _now_imp = _ddt_imp.now(_UTC_imp).strftime("%Y-%m-%d %H:%M:%S")
                        _ok = _fail = 0
                        for _, _mr in _map_df.iterrows():
                            _mc = list(filter(None, [
                                f"domain = '{str(_mr.get('domain','')).strip()}'"
                                if str(_mr.get("domain","")).strip() else "",
                                f"layer = '{str(_mr.get('layer','')).strip()}'"
                                if str(_mr.get("layer","")).strip() else "",
                                f"database_name = '{str(_mr.get('database_name','')).strip()}'"
                                if str(_mr.get("database_name","")).strip() else "",
                                f"table_fqn LIKE '%.{str(_mr.get('table_pattern','')).strip()}%'"
                                if str(_mr.get("table_pattern","")).strip() else "",
                            ]))
                            _mw = ("WHERE " + " AND ".join(_mc)) if _mc else ""
                            if not _mw:
                                continue
                            _pj  = str(_mr.get("controlm_job_name","")).strip()
                            _g1  = str(_mr.get("aws_gate1_job","")).strip() or _pj
                            _hj  = str(_mr.get("hk_controlm_job","")).strip()
                            _jt  = str(_mr.get("job_type","controlm")).strip()
                            _js  = str(_mr.get("job_start_time","02:00")).strip()
                            _jd  = int(_mr.get("expected_duration_min", 0) or 0)
                            try:
                                execute_write(
                                    f"UPDATE {STREAM_REGISTRY_TABLE} "
                                    f"SET controlm_pipeline_job='{_pj}', "
                                    f"controlm_hk_job='{_hj}', "
                                    f"dependent_on_controlm_job='{_g1}', "
                                    f"dependent_job_type='{_jt}', "
                                    f"controlm_job_start_time='{_js}', "
                                    f"controlm_expected_duration_min={_jd}, "
                                    f"updated_at='{_now_imp}' "
                                    f"{_mw}",
                                    dry_run=is_dry_run(),
                                )
                                _ok += 1
                            except Exception as _be:
                                _fail += 1
                                st.error(f"`{_pj}` failed: {_be}")
                        cached_read_registry.clear()
                        st.success(
                            f"✅ Applied {_ok} row(s)"
                            + (f", {_fail} failed" if _fail else "")
                            + (" (dry run)" if is_dry_run() else "")
                        )
            except Exception as _pe:
                st.error(f"CSV parse failed: {_pe}")

    # ── Sub-tab: Export Mapping Template ─────────────────────────────────────
    with bc_tab_export:
        st.markdown(
            "Export a pre-filled CSV showing all unique domain/layer/database "
            "combinations. Share with domain teams — they fill in the job names "
            "and return the file for import."
        )
        try:
            _exp_df = cached_read_registry(f"""
                SELECT DISTINCT
                    domain, layer, database_name,
                    '' AS table_pattern,
                    COALESCE(controlm_pipeline_job, '')         AS controlm_job_name,
                    COALESCE(controlm_hk_job, '')               AS hk_controlm_job,
                    COALESCE(dependent_on_controlm_job, '')    AS aws_gate1_job,
                    COALESCE(dependent_job_type, 'controlm')   AS job_type,
                    COALESCE(controlm_job_start_time, '02:00') AS job_start_time,
                    COALESCE(controlm_expected_duration_min, 0)  AS expected_duration_min
                FROM {STREAM_REGISTRY_TABLE}
                WHERE table_format = 'iceberg'
                ORDER BY domain, layer, database_name
            """)

            if _exp_df.empty:
                st.warning("No registered tables found.")
            else:
                st.info(
                    f"**{len(_exp_df)} domain/layer/database rows** — one row per "
                    "combination. Add `table_pattern` (e.g. `aps_%`) to target a subset."
                )
                from itables.streamlit import interactive_table as _it_exp
                _it_exp(
                    _exp_df, key="export_preview_it",
                    style="width:100%;font-size:12px;",
                    classes="display compact cell-border stripe hover nowrap",
                    maxBytes=0, downsampling_warning=False,
                    pageLength=15, scrollX=True,
                )
                st.download_button(
                    "📥 Download Job Mapping Template CSV",
                    data=_exp_df.to_csv(index=False).encode("utf-8"),
                    file_name="zamboni_job_mapping_template.csv",
                    mime="text/csv",
                )
                st.markdown("""
**CSV column guide for domain teams:**

| Column | Required | Example | Notes |
|---|---|---|---|
| `domain` | ✅ | finance | Must match Zamboni domain |
| `layer` | optional | staging | Leave blank = all layers |
| `database_name` | optional | finance_staging_db | Leave blank = all databases |
| `table_pattern` | optional | `aps_%` | SQL LIKE wildcard |
| `controlm_job_name` | ✅ | ACE-DA-FIN-APS-INGEST-PRD | Control-M job that loads data |
| `job_type` | optional | `controlm` / `glue` / `lambda` / `step_functions` / `airflow` | Defaults to `controlm` |
| `hk_controlm_job` | optional | ACE-DA-FIN-HK-PRD | Control-M job that runs Zamboni HK |
| `aws_gate1_job` | optional | — | AWS job for Gate 1 check. Blank = use `controlm_job_name` |
| `job_start_time` | optional | `02:00` | 24h HH:MM |
| `expected_duration_min` | optional | `45` | Typical job run time |
""")
        except Exception as _ee:
            st.error(f"Export failed: {_ee}")


    # ── Sub-tab: Control-M Job Registry ──────────────────────────────────────
    with bc_tab_jobs:
        st.markdown(
            "Maintain a registry of all active Control-M (and AWS) jobs. "
            "Jobs added here appear as **search suggestions** in all Control-M "
            "job name fields across the app — no need to remember exact names."
        )

        _CTRLM_JOBS_TABLE = "controlm_jobs"

        # Helper to load jobs
        @st.cache_data(ttl=60, show_spinner=False)
        def _load_ctrlm_jobs():
            try:
                return cached_read_registry(
                    f"SELECT job_name, job_type, domain, description, "
                    f"expected_start_time, expected_duration_min, active "
                    f"FROM {_CTRLM_JOBS_TABLE} ORDER BY job_name"
                )
            except Exception:
                import pandas as _pd_jobs
                return _pd_jobs.DataFrame(columns=[
                    "job_name","job_type","domain","description",
                    "expected_start_time","expected_duration_min","active"
                ])

        _jobs_df = _load_ctrlm_jobs()

        # ── View existing jobs ────────────────────────────────────────────────
        if not _jobs_df.empty:
            st.markdown(f"**{len(_jobs_df)} registered job(s)**")
            from itables.streamlit import interactive_table as _it_jobs
            _disp_jobs = _jobs_df.copy()
            _disp_jobs.insert(0, "#", range(1, len(_disp_jobs)+1))
            _it_jobs(
                _disp_jobs, key="ctrlm_jobs_it",
                style="width:100%;font-size:12px;",
                classes="display compact cell-border stripe hover nowrap",
                maxBytes=0, downsampling_warning=False,
                lengthMenu=[[15,25,50,100,-1],["15","25","50","All"]],
                pageLength=15, scrollX=True,
            )

        st.divider()

        # ── Add / bulk upload ──────────────────────────────────────────────────
        bj_tab_add, bj_tab_upload = st.tabs(["➕ Add Single Job", "📥 Bulk Upload CSV"])

        with bj_tab_add:
            _bj1, _bj2, _bj3 = st.columns(3)
            with _bj1:
                _new_job_name = st.text_input(
                    "Job Name *",
                    placeholder="ACE-DA-FIN-APS-INGEST-PRD",
                    key="new_ctrlm_job_name",
                )
                _new_job_type = st.selectbox(
                    "Job Type",
                    ["controlm","glue","lambda","step_functions","airflow","other"],
                    key="new_ctrlm_job_type",
                )
            with _bj2:
                _new_job_domain = st.selectbox(
                    "Domain (optional)",
                    [""] + _get_domains(),
                    key="new_ctrlm_job_domain",
                )
                _new_job_start = st.text_input(
                    "Expected start time (HH:MM)",
                    placeholder="02:00",
                    key="new_ctrlm_job_start",
                )
            with _bj3:
                _new_job_dur = st.number_input(
                    "Expected duration (min)",
                    value=0, min_value=0, max_value=480,
                    key="new_ctrlm_job_dur",
                )
                _new_job_desc = st.text_input(
                    "Description",
                    placeholder="Finance APS ingest pipeline",
                    key="new_ctrlm_job_desc",
                )

            if st.button("➕ Add Job", type="primary", key="add_ctrlm_job"):
                if not _new_job_name.strip():
                    st.error("Job Name is required.")
                else:
                    from datetime import UTC as _JUTC
                    from datetime import datetime as _jdt
                    _jnow = _jdt.now(_JUTC).strftime("%Y-%m-%d %H:%M:%S")
                    try:
                        execute_write(
                            f"INSERT OR REPLACE INTO {_CTRLM_JOBS_TABLE} "
                            f"(job_name, job_type, domain, description, "
                            f"expected_start_time, expected_duration_min, "
                            f"active, registered_by, created_at, updated_at) "
                            f"VALUES ('{_new_job_name.strip()}', "
                            f"'{_new_job_type}', '{_new_job_domain}', "
                            f"'{_new_job_desc.strip()}', "
                            f"'{_new_job_start.strip()}', {int(_new_job_dur)}, "
                            f"1, '{current_user()}', '{_jnow}', '{_jnow}')",
                            dry_run=False,
                        )
                        _load_ctrlm_jobs.clear()
                        st.success(f"✅ `{_new_job_name.strip()}` added to registry.")
                        st.rerun()
                    except Exception as _je:
                        st.error(f"Failed: {_je}")

        with bj_tab_upload:
            st.markdown(
                "Upload a CSV with your full Control-M job list. "
                "Existing jobs are updated (upsert). "
                "Required column: `job_name`. Optional: `job_type`, `domain`, "
                "`description`, `expected_start_time`, `expected_duration_min`."
            )
            _jobs_upload = st.file_uploader(
                "Upload Control-M Jobs CSV",
                type=["csv"],
                key="ctrlm_jobs_upload",
            )
            if _jobs_upload:
                import io as _jio

                import pandas as _pd_jobs
                try:
                    _jdf = _pd_jobs.read_csv(_jio.BytesIO(_jobs_upload.read()),
                                             skip_blank_lines=True)
                    _jdf.dropna(how="all", inplace=True)
                    _jdf.columns = [c.strip().lower() for c in _jdf.columns]
                    # Same all-blank-column NaN fix as the job mapping import
                    # above -- see the comment there.
                    _jdf = _jdf.fillna("")
                    for _jsc in _jdf.select_dtypes(include="object").columns:
                        _jdf[_jsc] = _jdf[_jsc].astype(str).str.strip().replace(
                            {"nan":"","None":""}
                        )
                    _jdf = _jdf[_jdf.get("job_name", _pd_jobs.Series()).ne("")]

                    if "job_name" not in _jdf.columns:
                        st.error("CSV must have a `job_name` column.")
                    else:
                        # Set defaults
                        for _jc, _jd in [
                            ("job_type","controlm"),("domain",""),
                            ("description",""),("expected_start_time",""),
                            ("expected_duration_min",0),
                        ]:
                            if _jc not in _jdf.columns:
                                _jdf[_jc] = _jd
                        _jdf["expected_duration_min"] = (
                            _pd_jobs.to_numeric(
                                _jdf["expected_duration_min"], errors="coerce"
                            ).fillna(0).astype(int)
                        )

                        st.info(f"**{len(_jdf)} job(s)** in CSV. Preview:")
                        from itables.streamlit import interactive_table as _it_jup
                        _it_jup(_jdf, key="jobs_upload_preview",
                                style="width:100%;font-size:12px;",
                                classes="display compact cell-border stripe hover nowrap",
                                maxBytes=0, downsampling_warning=False,
                                pageLength=15, scrollX=True)

                        if st.button(
                            f"📥 Import {len(_jdf)} Job(s)",
                            type="primary",
                            key="import_ctrlm_jobs",
                        ):
                            from datetime import UTC as _JUTC2
                            from datetime import datetime as _jdt2
                            _jnow2 = _jdt2.now(_JUTC2).strftime("%Y-%m-%d %H:%M:%S")
                            _j_ok = _j_fail = 0
                            for _, _jr in _jdf.iterrows():
                                try:
                                    execute_write(
                                        f"INSERT OR REPLACE INTO {_CTRLM_JOBS_TABLE} "
                                        f"(job_name, job_type, domain, description, "
                                        f"expected_start_time, expected_duration_min, "
                                        f"active, registered_by, created_at, updated_at) "
                                        f"VALUES ('{str(_jr['job_name'])}', "
                                        f"'{str(_jr.get('job_type','controlm'))}', "
                                        f"'{str(_jr.get('domain',''))}', "
                                        f"'{str(_jr.get('description',''))}', "
                                        f"'{str(_jr.get('expected_start_time',''))}', "
                                        f"{int(_jr.get('expected_duration_min',0))}, "
                                        f"1, '{current_user()}', '{_jnow2}', '{_jnow2}')",
                                        dry_run=False,
                                    )
                                    _j_ok += 1
                                except Exception as _jbe:
                                    _j_fail += 1
                                    st.error(f"`{_jr['job_name']}` failed: {_jbe}")
                            _load_ctrlm_jobs.clear()
                            st.success(
                                f"✅ Imported {_j_ok} job(s)"
                                + (f", {_j_fail} failed" if _j_fail else "")
                            )
                            st.rerun()
                except Exception as _jpe:
                    st.error(f"CSV parse failed: {_jpe}")

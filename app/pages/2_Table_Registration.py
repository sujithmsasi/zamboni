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


            # ── Edit a registered table ───────────────────────────────────────
            st.divider()
            st.subheader("✏️ Edit Registered Table")
            st.caption(
                "Select a table from the list above to update its "
                "domain, tier, layer, owner, or stream ID."
            )
            # Rebuild unformatted FQN list from raw query
            try:
                raw_df = cached_read_registry(
                    f"SELECT table_fqn, domain, layer, tier, "
                    f"owner_email, ci_number, stream_id, hk_enabled, "
                    f"archive_enabled, lifecycle_enabled, processing_cadence, "
                    f"dry_run_until "
                    f"FROM {STREAM_REGISTRY_TABLE} "
                    f"{('WHERE domain = ' + chr(39) + sel_domain + chr(39)) if sel_domain != 'All' else ''} "
                    f"ORDER BY domain, table_fqn LIMIT 500"
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
                                e_stream = st.text_input(
                                    "Stream ID",
                                    value=str(trow.get("stream_id") or ""),
                                    key=f"{_ek}_stream",
                                    placeholder="STR-FIN-APS-0001",
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
                        cm1, cm2, cm3 = st.columns(3)
                        with cm1:
                            e_pipeline_job = st.text_input(
                                "Pipeline Job (writes to table)",
                                value=str(trow.get("controlm_pipeline_job") or ""),
                                key=f"{_ek}_pipeline_job",
                                placeholder="ACE-DA-FIN-APS-INGEST-PRD",
                                help="The Control-M job that loads data into this table.",
                            )
                        with cm2:
                            e_hk_job = st.text_input(
                                "HK Job (runs Zamboni)",
                                value=str(trow.get("controlm_hk_job") or ""),
                                key=f"{_ek}_hk_job",
                                placeholder="ACE-DA-FIN-APS-HK-PRD",
                                help="The Control-M job that triggers Zamboni HK.",
                            )
                        with cm3:
                            e_upstream_job = st.text_input(
                                "Gate 1 — Upstream Job",
                                value=str(trow.get("dependent_on_controlm_job") or ""),
                                key=f"{_ek}_upstream_job",
                                placeholder="ACE-DA-FIN-APS-INGEST-PRD",
                                help="Must SUCCEED before HK starts (Gate 1 check).",
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
                                            stream_id                  = '{e_stream}',
                                            owner_email                = '{e_owner}',
                                            ci_number                  = '{e_ci}',
                                            hk_enabled                 = {'1' if e_hk else '0'},
                                            archive_enabled            = {'1' if e_archive else '0'},
                                            lifecycle_enabled          = {'1' if e_lifecycle else '0'},
                                            processing_cadence         = '{e_cadence}',
                                            controlm_pipeline_job           = '{_esc_v(e_pipeline_job)}',
                                            controlm_hk_job                 = '{_esc_v(e_hk_job)}',
                                            dependent_on_controlm_job       = '{_esc_v(e_upstream_job)}',
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
    except Exception as e:
        st.error(f"Could not load registered tables: {e}")

    # ── Engine Flags: Enable / Disable per table or bulk ─────────────────────
    st.divider()
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

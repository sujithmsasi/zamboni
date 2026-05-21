"""
Zamboni -- Policy Configuration Page
View and edit per-table HK config, manage templates.

Tabs:
  1. View Configs       -- full grid with engine column, filters
  2. Edit Single Table  -- cascading selector (no FQN typing), single UPDATE
  3. Bulk Apply         -- apply template to domain+layer
  4. Templates          -- view/add/edit policy templates
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import json
from datetime import UTC

import streamlit as st

from app.components.athena_runner import cached_read_registry, clear_caches
from app.components.auth import check_login, current_user
from app.components.filters import domain_filter, layer_filter, tier_filter
from app.components.header import render as render_header
from app.components.sidebar import is_dry_run
from app.components.sidebar import render as render_sidebar
from config.settings import APP_ENV, HK_CONFIG_TABLE, STREAM_REGISTRY_TABLE
from engine.core.audit import AuditAction, AuditEvent, audit
from engine.core.config import get_policy_templates

st.set_page_config(
    page_title="Zamboni — Policy Config",
    page_icon="⚙️",
    layout="wide",
)
check_login()
render_sidebar()
render_header(page_title="Policy Configuration", page_icon="⚙️")
st.caption(
    "Manage housekeeping policies per table. "
    "Templates provide sensible defaults; individual fields can be overridden."
)

# Track navigation using a shared "current_page" key set by every page.
_MY_PAGE = "policy_config"
if st.session_state.get("_current_page") != _MY_PAGE:
    st.session_state.pop("pc_edit_table_label", None)
    st.session_state.pop("pc_edit_table_sel",   None)
    # Reset view filters to sane defaults on fresh arrival
    st.session_state["pc_show_all"] = True   # show all tables by default
st.session_state["_current_page"] = _MY_PAGE

tab_view, tab_edit, tab_bulk, tab_templates = st.tabs([
    "📋 View Configs",
    "✏️ Edit Single Table",
    "🔄 Bulk Apply Template",
    "🗂️ Templates",
])


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_all_tables() -> list[str]:
    """Return all registered table FQNs for the table selector."""
    try:
        df = cached_read_registry(
            f"SELECT table_fqn FROM {STREAM_REGISTRY_TABLE} ORDER BY table_fqn"
        )
        return df["table_fqn"].tolist() if not df.empty else []
    except Exception:
        return []


def _table_short_name(fqn: str) -> str:
    """'glue_catalog.finance_db.fin_payment' → 'fin_payment  (finance_db)'"""
    parts = fqn.split(".")
    if len(parts) == 3:
        return f"{parts[2]}  ({parts[1]})"
    return fqn


# ── Tab 1: View Configs ───────────────────────────────────────────────────────
with tab_view:
    c1, c2, c3 = st.columns(3)
    with c1:
        d = domain_filter(key="pc_domain")
    with c2:
        layer_sel = layer_filter(key="pc_layer")
    with c3:
        t = tier_filter(key="pc_tier")

    show_all = st.checkbox(
        "Show all tables (including HK-disabled)",
        value=True,
        key="pc_show_all",
        help="When unchecked, only shows tables with Housekeeping enabled.",
    )

    if st.button("🔄 Refresh", key="pc_view_refresh"):
        cached_read_registry.clear()
        st.rerun()
    _pc_limit = 5000  # itables handles client-side pagination

    conditions = ["r.table_format = 'iceberg'"]
    if not show_all:
        conditions.append("r.hk_enabled = 1")
    if d:
        conditions.append(f"r.domain = '{d}'")
    if layer_sel:
        conditions.append(f"r.layer = '{layer_sel}'")
    if t:
        conditions.append(f"r.tier = '{t}'")
    where = "WHERE " + " AND ".join(conditions)

    sql = f"""
        SELECT
            r.table_fqn,
            r.domain,
            r.layer,
            r.tier,
            c.policy_template,
            c.compaction_strategy,
            c.compaction_engine,
            c.compaction_target_file_size_mb  AS target_mb,
            c.snapshot_retention_days,
            c.snapshot_min_to_keep,
            c.orphan_file_retention_days,
            c.run_frequency,
            c.gate1_enabled,
            c.gate2_enabled,
            c.gate3_enabled,
            c.manually_overridden
        FROM {STREAM_REGISTRY_TABLE} r
        LEFT JOIN {HK_CONFIG_TABLE} c ON r.table_fqn = c.table_fqn
        {where}
        ORDER BY r.domain, r.layer, r.table_fqn
        LIMIT {_pc_limit}
    """
    try:
        df = cached_read_registry(sql)
        if df.empty:
            st.info("No tables match the selected filters.")
        else:
            import pandas as _pd_pc
            df["manually_overridden"] = df["manually_overridden"].apply(
                lambda x: "⚠️ Override" if x else "✅ Template"
            )
            for _gc in ["gate1_enabled", "gate2_enabled", "gate3_enabled"]:
                if _gc in df.columns:
                    df[_gc] = df[_gc].apply(
                        lambda x: "✅" if (not _pd_pc.isna(x) and int(x or 0)) else "❌"
                    )
            # Friendly column names
            df.rename(columns={
                "table_fqn":                   "Table",
                "compaction_strategy":         "Strategy",
                "compaction_engine":           "Engine",
                "compaction_target_file_size_mb": "Target MB",
                "snapshot_retention_days":     "Snap Days",
                "snapshot_min_to_keep":        "Snap Floor",
                "orphan_file_retention_days":  "Orphan Days",
                "run_frequency":               "Frequency",
                "manually_overridden":         "Status",
                "gate1_enabled":               "Gate 1",
                "gate2_enabled":               "Gate 2",
                "gate3_enabled":               "Gate 3",
            }, inplace=True, errors="ignore")

            from itables.streamlit import interactive_table as _it

            # Strip glue_catalog. prefix from Table FQN for readability
            if "Table" in df.columns:
                df["Table"] = df["Table"].str.replace(
                    r"^glue_catalog\.", "", regex=True
                )
            elif "table_fqn" in df.columns:
                df["table_fqn"] = df["table_fqn"].str.replace(
                    r"^glue_catalog\.", "", regex=True
                )

            # Add serial number column
            df.insert(0, "#", range(1, len(df) + 1))

            _it(
                df,
                key="pc_view_it",
                style="width:100%;font-size:12px;",
                classes="display compact cell-border stripe hover nowrap",
                maxBytes=0,
                downsampling_warning=False,
                lengthMenu=[[15, 25, 50, 100, 250, -1],
                            ["15", "25", "50", "100", "250", "All"]],
                pageLength=15,
                scrollX=True,
                columnDefs=[
                    {"width": "30px",  "targets": 0},
                    {"width": "220px", "targets": 1},
                    {"width": "70px",  "targets": [2, 3, 4]},
                    {"width": "85px",  "targets": [5, 6]},
                    {"width": "60px",  "targets": "_all"},
                    {"className": "dt-center", "targets": "_all"},
                    {"className": "dt-left",   "targets": [0, 1, 2]},
                ],
                caption=f"{len(df):,} table(s)",
            )

            csv = df.to_csv(index=False).encode("utf-8")
            st.download_button("⬇️ Export CSV", csv,
                               "hk_config.csv", "text/csv")
    except Exception as e:
        st.error(f"Query failed: {e}")


# ── Tab 2: Edit Single Table ──────────────────────────────────────────────────
with tab_edit:
    st.subheader("Edit HK Config for a Table")
    st.caption(
        "Select a table — type to search. "
        "Fields are pre-populated from the current config."
    )

    all_tables = _get_all_tables()

    if not all_tables:
        st.warning("No tables registered. Register tables in Table Registration first.")
    else:
        label_to_fqn = {_table_short_name(fqn): fqn for fqn in all_tables}
        labels = ["-- select a table --"] + list(label_to_fqn.keys())

        _prev = st.session_state.get("pc_edit_table_label", labels[0])
        if _prev not in labels:
            _prev = labels[0]
        _default_idx = labels.index(_prev)

        selected_label = st.selectbox(
            "Table (type to search)",
            labels,
            index=_default_idx,
            key="pc_edit_table_sel",
            help="Type the table name or database to filter.",
        )
        if selected_label != labels[0]:
            st.session_state["pc_edit_table_label"] = selected_label
        else:
            st.session_state["pc_edit_table_label"] = labels[0]

        table_fqn = label_to_fqn.get(selected_label)

        if table_fqn:
            try:
                df_cfg = cached_read_registry(
                    f"SELECT * FROM {HK_CONFIG_TABLE} "
                    f"WHERE table_fqn = '{table_fqn}' LIMIT 1"
                )
                if df_cfg.empty:
                    st.warning(
                        f"No HK config found for `{table_fqn}`. "
                        "Apply a template in the **Bulk Apply** tab first."
                    )
                else:
                    cfg = df_cfg.iloc[0].to_dict()
                    st.caption(
                        f"**FQN:** `{table_fqn}` · "
                        f"Template: `{cfg.get('policy_template','—')}` · "
                        f"{'⚠️ Manually overridden' if cfg.get('manually_overridden') else '✅ On template'}"
                    )

                    # Unique key prefix per table
                    _fk = table_fqn.replace(".", "_").replace("/", "_")

                    # ── Gate Flags (outside form — immediate toggle) ──────────────
                    st.markdown("**🚦 Gate Enable / Disable**")
                    st.caption(
                        "Gates control what checks run before HK starts. "
                        "Disable Gate 1 until Control-M API integration is ready."
                    )
                    _gc1, _gc2, _gc3, _gc4 = st.columns(4)
                    with _gc1:
                        _g1_val = bool(cfg.get("gate1_enabled", 0))
                        gate1_en = st.toggle(
                            "Gate 1 — Control-M upstream check",
                            value=_g1_val,
                            key=f"{_fk}_gate1",
                            help=(
                                "Checks that the upstream pipeline job completed successfully "
                                "before starting HK. Requires Control-M API integration. "
                                "**Disable until integration is available.**"
                            ),
                        )
                    with _gc2:
                        gate2_en = st.toggle(
                            "Gate 2 — Blackout window",
                            value=bool(cfg.get("gate2_enabled", 1)),
                            key=f"{_fk}_gate2",
                            help="Prevents HK from starting during configured blackout hours.",
                        )
                    with _gc3:
                        gate3_en = st.toggle(
                            "Gate 3 — Circuit breaker",
                            value=bool(cfg.get("gate3_enabled", 1)),
                            key=f"{_fk}_gate3",
                            help="Blocks HK if recent failure count exceeds threshold.",
                        )
                    with _gc4:
                        _gate_status = []
                        if not gate1_en:
                            _gate_status.append("⚠️ Gate 1 disabled (no ControlM check)")
                        if not gate2_en:
                            _gate_status.append("⚠️ Gate 2 disabled (no blackout)")
                        if not gate3_en:
                            _gate_status.append("⚠️ Gate 3 disabled (no circuit breaker)")
                        if _gate_status:
                            st.warning("  \n".join(_gate_status))
                        else:
                            st.success("All gates active")
                    st.divider()

                    # ── Window & Blackout (OUTSIDE form — reacts immediately) ─────
                    import json as _wjson
                    _wc = cfg.get("window_config")
                    try:
                        _wd = (_wjson.loads(_wc) if isinstance(_wc, str) and _wc
                               else (_wc if isinstance(_wc, dict) else {}))
                    except Exception:
                        _wd = {}

                    st.markdown("**⏰ Safe Window & Blackout**")
                    _BH_PRESETS = {
                        "— manual —":              None,
                        "Business hours (6–18)":   list(range(6, 19)),
                        "Midnight window (22–5)":  [22,23,0,1,2,3,4,5],
                        "Peak hours (7–9, 17–20)": [7,8,9,17,18,19,20],
                        "Weekday peak (7–20)":     list(range(7, 21)),
                        "None (always allowed)":   [],
                        "Always blocked":           list(range(24)),
                    }
                    _wcol_left, _wcol_right = st.columns(2)

                    # ── Left pane: window settings ────────────────────────────
                    with _wcol_left:
                        w_type = st.selectbox(
                            "Window Type",
                            ["post_batch", "scheduled"],
                            index=0 if _wd.get("type", "post_batch") == "post_batch" else 1,
                            key=f"{_fk}_wtype",
                            help="post_batch: Gate 1 + delay. scheduled: fixed daily time.",
                        )
                        if w_type == "scheduled":
                            w_start_time = st.text_input(
                                "Start time (HH:MM) *",
                                value=str(_wd.get("start_time", "02:00")),
                                key=f"{_fk}_wstart",
                                placeholder="02:00",
                                help="24h format. Must not fall in a blackout hour.",
                            )
                        else:
                            w_start_time = str(_wd.get("start_time", "02:00"))
                        _wl1, _wl2 = st.columns(2)
                        with _wl1:
                            w_delay = st.number_input(
                                "Delay after job (min)",
                                value=int(_wd.get("delay_minutes", 30)),
                                min_value=0, max_value=240,
                                key=f"{_fk}_wdelay",
                                disabled=(w_type == "scheduled"),
                                help="post_batch only.",
                            )
                        with _wl2:
                            w_duration = st.number_input(
                                "Duration (hours)",
                                value=int(_wd.get("duration_hours", 4)),
                                min_value=1, max_value=12,
                                key=f"{_fk}_wdur",
                            )
                        w_tz = st.selectbox(
                            "Timezone",
                            ["America/Los_Angeles", "America/New_York",
                             "America/Chicago", "UTC"],
                            index=(
                                ["America/Los_Angeles", "America/New_York",
                                 "America/Chicago", "UTC"]
                                .index(_wd.get("timezone", "America/Los_Angeles"))
                                if _wd.get("timezone") in
                                ["America/Los_Angeles", "America/New_York",
                                 "America/Chicago", "UTC"] else 0
                            ),
                            key=f"{_fk}_wtz",
                        )

                    # ── Right pane: blackout ──────────────────────────────────
                    with _wcol_right:
                        _prev_preset_key = f"{_fk}_bh_preset_prev"
                        _preset_sel = st.selectbox(
                            "Blackout preset",
                            list(_BH_PRESETS.keys()),
                            key=f"{_fk}_bh_preset",
                            help="Quick-fill the checkboxes below.",
                        )
                        _preset_hours = _BH_PRESETS[_preset_sel]
                        if st.session_state.get(_prev_preset_key) != _preset_sel:
                            st.session_state[_prev_preset_key] = _preset_sel
                            if _preset_hours is not None:
                                for _h in range(24):
                                    st.session_state[f"{_fk}_bh_{_h}"] = (_h in _preset_hours)
                                st.rerun()
                        _cur_bh = (
                            _preset_hours if _preset_hours is not None
                            else _wd.get("blackout_hours", [6, 7, 8, 9, 18, 19, 20, 21])
                        )
                        st.caption("HK will not start during checked hours")
                        _bh_cols = st.columns(4)
                        _new_bh = []
                        for _h in range(24):
                            if _bh_cols[_h % 4].checkbox(
                                f"{_h:02d}:00",
                                value=(_h in _cur_bh),
                                key=f"{_fk}_bh_{_h}",
                            ):
                                _new_bh.append(_h)
                    st.divider()

                    # ── Main config fields (INSIDE form) ─────────────────────────
                    st.caption(r"Fields marked \* are required")
                    with st.form(f"edit_config_form_{_fk}", clear_on_submit=False):
                        # Row 1: Snapshot settings
                        r1c1, r1c2, r1c3 = st.columns(3)
                        with r1c1:
                            snap_days = st.number_input(
                                "Snapshot Retention (days) *",
                                value=int(cfg.get("snapshot_retention_days") or 7),
                                min_value=1,
                                key=f"{_fk}_snap_days",
                                help="How long to keep snapshots before expiry.",
                            )
                        with r1c2:
                            snap_min = st.number_input(
                                "Min Snapshots to Keep *",
                                value=max(int(cfg.get("snapshot_min_to_keep") or 30), 2),
                                min_value=2,
                                key=f"{_fk}_snap_min",
                                help="Safety floor — VACUUM never goes below this count.",
                            )
                        with r1c3:
                            run_freq = st.selectbox(
                                "Run Frequency *",
                                ["every_trigger", "daily", "weekly", "monthly"],
                                index=(
                                    ["every_trigger", "daily", "weekly", "monthly"]
                                    .index(cfg.get("run_frequency", "daily"))
                                    if cfg.get("run_frequency", "daily") in
                                    ["every_trigger", "daily", "weekly", "monthly"]
                                    else 1
                                ),
                                key=f"{_fk}_run_freq",
                                help="How often Zamboni HK runs on this table.",
                            )

                        # Row 2: Orphan settings
                        r2c1, r2c2, r2c3 = st.columns(3)
                        with r2c1:
                            orphan = st.number_input(
                                "Orphan Retention (days) *",
                                value=max(int(cfg.get("orphan_file_retention_days") or 2), 2),
                                min_value=2,
                                key=f"{_fk}_orphan",
                                help="Min 2 days — protects in-flight writers.",
                            )
                        with r2c2:
                            orphan_cadence = st.number_input(
                                "Orphan Cleanup Cadence (days)",
                                value=int(cfg.get("orphan_cleanup_cadence_days") or 7),
                                min_value=0,
                                key=f"{_fk}_orphan_cad",
                                help="How often orphan cleanup runs. 0 = disabled.",
                            )
                        with r2c3:
                            target_mb = st.number_input(
                                "Target File Size (MB) *",
                                value=int(cfg.get("compaction_target_file_size_mb") or 128),
                                min_value=64,
                                key=f"{_fk}_target_mb",
                                help="128 MB staging · 256 MB datalake · 512 MB base/master",
                            )

                        # Row 3: Compaction + Partition
                        r3c1, r3c2, r3c3 = st.columns(3)
                        with r3c1:
                            strategy = st.selectbox(
                                "Compaction Strategy *",
                                ["binpack", "sort", "zorder"],
                                index=(
                                    ["binpack", "sort", "zorder"]
                                    .index(cfg.get("compaction_strategy", "binpack"))
                                    if cfg.get("compaction_strategy", "binpack")
                                    in ["binpack", "sort", "zorder"] else 0
                                ),
                                key=f"{_fk}_strategy",
                                help="binpack → Athena. sort/zorder → Glue PySpark.",
                            )
                        with r3c2:
                            engine_choice = st.selectbox(
                                "Compaction Engine *",
                                ["athena", "glue"],
                                index=(
                                    ["athena", "glue"]
                                    .index(cfg.get("compaction_engine", "athena"))
                                    if cfg.get("compaction_engine", "athena")
                                    in ["athena", "glue"] else 0
                                ),
                                key=f"{_fk}_engine",
                                help="athena = OPTIMIZE SQL. glue = PySpark.",
                            )
                        with r3c3:
                            part_type_opts = [
                                "date", "timestamp", "int_yyyymmdd",
                                "string", "identity", "none",
                            ]
                            cur_pt = str(cfg.get("partition_type") or "date")
                            part_type = st.selectbox(
                                "Partition Type",
                                part_type_opts,
                                index=(part_type_opts.index(cur_pt)
                                       if cur_pt in part_type_opts else 0),
                                key=f"{_fk}_part_type",
                                help="'none' or 'identity' → no date filter (full table).",
                            )

                        # Row 4: Partition column + Sort cols
                        _no_date_filter = part_type in ("none", "identity")
                        r4c1, r4c2 = st.columns(2)
                        with r4c1:
                            partition_col = st.text_input(
                                "Partition Column",
                                value=str(cfg.get("partition_column") or ""),
                                key=f"{_fk}_part_col",
                                placeholder="partition_date" if not _no_date_filter else "N/A",
                                disabled=_no_date_filter,
                                help="Ignored when Partition Type is none/identity.",
                            )
                        with r4c2:
                            sort_cols = st.text_input(
                                "Sort / Z-Order Columns",
                                value=str(cfg.get("sort_order_cols") or ""),
                                key=f"{_fk}_sort_cols",
                                placeholder="partition_date, customer_id",
                                help="Comma-separated. Only for sort and zorder.",
                            )

                        override_notes = st.text_input(
                            "Reason for override *",
                            key=f"{_fk}_reason",
                            placeholder="e.g. High-volume table needs shorter retention",
                            help="Required. Stored in audit log and hk_config.",
                        )

                        if is_dry_run():
                            st.info("🔵 Dry Run — changes simulated, not written.")

                        submitted = st.form_submit_button(
                            "💾 Save Changes", type="primary"
                        )

                    # ── Save handler (outside form, after submit) ─────────────────
                    if submitted:
                        _save_errors = []
                        # Required field validation
                        if not override_notes.strip():
                            _save_errors.append("Reason for override is required.")
                        if snap_days < 1:
                            _save_errors.append("Snapshot Retention must be at least 1 day.")
                        if snap_min < 2:
                            _save_errors.append("Min Snapshots to Keep must be at least 2.")
                        if orphan < 2:
                            _save_errors.append("Orphan Retention must be at least 2 days.")
                        if target_mb < 64:
                            _save_errors.append("Target File Size must be at least 64 MB.")
                        if strategy in ("sort", "zorder") and engine_choice == "athena":
                            _save_errors.append(
                                f"Strategy '{strategy}' requires Glue engine. "
                                "Athena only supports 'binpack'."
                            )
                        if w_type == "scheduled":
                            import re as _re
                            _st_val = (w_start_time or "").strip()
                            if not _st_val:
                                _save_errors.append("Start time is required for scheduled windows.")
                            elif not _re.match(r"^([01]?\d|2[0-3]):[0-5]\d$", _st_val):
                                _save_errors.append(
                                    f"Start time `{_st_val}` is not valid HH:MM (24h format)."
                                )
                            else:
                                _sh = int(_st_val.split(":")[0])
                                if _sh in _new_bh:
                                    _save_errors.append(
                                        f"Start time {_st_val} falls in blackout hour "
                                        f"{_sh:02d}:00. Change start time or uncheck "
                                        "that blackout hour."
                                    )

                        if _save_errors:
                            for _err in _save_errors:
                                st.error(_err)
                        else:
                            try:
                                import json as _json_upd
                                from datetime import UTC, datetime

                                _window_cfg = _json_upd.dumps({
                                    "type":           w_type,
                                    "timezone":       w_tz,
                                    "delay_minutes":  int(w_delay),
                                    "start_time":     w_start_time.strip() or "02:00",
                                    "duration_hours": int(w_duration),
                                    "blackout_hours": sorted(_new_bh),
                                })

                                def _esc(s):
                                    return str(s).replace("'", "''")

                                now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
                                upd_sql = f"""
                                    UPDATE {HK_CONFIG_TABLE}
                                    SET gate1_enabled                  = {1 if gate1_en else 0},
                                        gate2_enabled                  = {1 if gate2_en else 0},
                                        gate3_enabled                  = {1 if gate3_en else 0},
                                        snapshot_retention_days        = {int(snap_days)},
                                        snapshot_min_to_keep           = {int(snap_min)},
                                        orphan_file_retention_days     = {int(orphan)},
                                        orphan_cleanup_cadence_days    = {int(orphan_cadence)},
                                        run_frequency                  = '{run_freq}',
                                        compaction_strategy            = '{strategy}',
                                        compaction_engine              = '{engine_choice}',
                                        compaction_target_file_size_mb = {int(target_mb)},
                                        sort_order_cols                = '{_esc(sort_cols)}',
                                        partition_column               = '{_esc(partition_col)}',
                                        partition_type                 = '{part_type}',
                                        window_config                  = '{_esc(_window_cfg)}',
                                        manually_overridden            = 1,
                                        override_notes                 = '{_esc(override_notes)}',
                                        updated_at                     = '{now}'
                                    WHERE table_fqn = '{table_fqn}'
                                """
                                from engine.utils.athena_client import run_query as _rq2
                                _rq2(upd_sql, workgroup="app", dry_run=is_dry_run())

                                audit(AuditEvent(
                                    actor=current_user(),
                                    action_type=AuditAction.POLICY_CHANGE,
                                    page_source="3_Policy_Configuration",
                                    target_type="table",
                                    target_id=table_fqn,
                                    environment=APP_ENV,
                                    dry_run=is_dry_run(),
                                    status="DRY_RUN" if is_dry_run() else "SUCCESS",
                                    reason=override_notes,
                                    after_value=json.dumps({
                                        "strategy": strategy,
                                        "engine": engine_choice,
                                        "snap_days": snap_days,
                                        "snap_min": snap_min,
                                        "window_type": w_type,
                                    }),
                                ))
                                clear_caches()
                                st.session_state["pc_just_saved"] = True
                                st.success(
                                    f"✅ Config saved for `{table_fqn}`."
                                    + (" (dry run)" if is_dry_run() else "")
                                )
                            except Exception as e:
                                st.error(f"Save failed: {e}")

            except Exception as e:
                st.error(f"Error loading config: {e}")


# ── Tab 3: Bulk Apply Template ────────────────────────────────────────────────
with tab_bulk:
    st.subheader("Apply a Policy Template to a Domain + Layer")
    st.caption(
        "Applies the selected template to ALL registered Iceberg tables in the "
        "chosen domain and layer. Existing manual overrides are preserved unless "
        "you check the override box."
    )

    templates = get_policy_templates()
    col1, col2, col3 = st.columns(3)
    with col1:
        bulk_domain = domain_filter(include_all=False, key="bulk_domain")
    with col2:
        bulk_layer  = layer_filter(include_all=False,  key="bulk_layer")
    with col3:
        bulk_tmpl   = st.selectbox(
            "Template",
            list(templates.keys()),
            key="bulk_tmpl",
            help="Policy template to apply. Preview shown below.",
        )

    # Template preview
    if bulk_tmpl and bulk_tmpl in templates:
        tp = templates[bulk_tmpl]
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Strategy",  f"{tp['compaction_strategy']} / {tp['compaction_engine']}")
        c2.metric("Retention", f"{tp['snapshot_retention_days']}d")
        c3.metric("Orphan",    f"{tp['orphan_file_retention_days']}d")
        c4.metric("Frequency", tp['run_frequency'])

    override_manual = st.checkbox(
        "Override existing manual overrides",
        value=False,
        help="If unchecked, tables with manually_overridden=1 will be skipped.",
    )

    if is_dry_run():
        st.info("🔵 Dry Run — no changes will be written.")

    apply_disabled = not bulk_domain or not bulk_layer or not bulk_tmpl
    if st.button("🔄 Apply Template", type="primary", disabled=apply_disabled):
        override_clause = (
            "" if override_manual
            else "AND (c.manually_overridden IS NULL OR c.manually_overridden = 0)"
        )
        target_sql = f"""
            SELECT r.table_fqn, r.tier,
                   c.partition_column
            FROM {STREAM_REGISTRY_TABLE} r
            LEFT JOIN {HK_CONFIG_TABLE} c ON r.table_fqn = c.table_fqn
            WHERE r.domain       = '{bulk_domain}'
              AND r.layer        = '{bulk_layer}'
              AND r.hk_enabled   = 1
              AND r.table_format = 'iceberg'
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

                succeeded, failed, errors = 0, 0, []
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
                    audit(AuditEvent(
                        actor=current_user(),
                        action_type=AuditAction.POLICY_CHANGE,
                        page_source="3_Policy_Configuration",
                        target_type="domain",
                        target_id=f"{bulk_domain}/{bulk_layer}",
                        domain=bulk_domain,
                        environment=APP_ENV,
                        dry_run=is_dry_run(),
                        status="DRY_RUN" if is_dry_run() else "SUCCESS",
                        after_value=f"template={bulk_tmpl},count={succeeded}",
                    ))
                else:
                    st.warning(
                        f"Applied to {succeeded} tables, {failed} failed."
                    )
                    for err in errors[:5]:
                        st.error(err)

        except Exception as e:
            st.error(f"Bulk apply failed: {e}")


# ── Tab 4: Templates ──────────────────────────────────────────────────────────
with tab_templates:
    st.subheader("Policy Templates")
    st.caption(
        "Templates define default HK settings applied at registration. "
        "Changes here affect future registrations and bulk applies — "
        "existing table configs are not changed automatically."
    )

    templates = get_policy_templates()
    tmpl_tab_view, tmpl_tab_edit, tmpl_tab_add, tmpl_tab_del = st.tabs([
        "📋 View All",
        "✏️ Edit Template",
        "➕ Add Template",
        "🗑️ Delete Template",
    ])

    # ── View All Templates ────────────────────────────────────────────────────
    with tmpl_tab_view:
        rows = []
        for name, t in templates.items():
            rows.append({
                "Template":        name,
                "Description":     t.get("description",""),
                "Strategy":        t.get("compaction_strategy",""),
                "Engine":          t.get("compaction_engine",""),
                "Target MB":       t.get("compaction_target_file_size_mb",""),
                "Snap Retention":  f"{t.get('snapshot_retention_days','')}d",
                "Snap Floor":      t.get("snapshot_min_to_keep",""),
                "Orphan Days":     t.get("orphan_file_retention_days",""),
                "Frequency":       t.get("run_frequency",""),
            })
        import pandas as pd
        st.dataframe(
            pd.DataFrame(rows),
            use_container_width=True,
            hide_index=True,
        )

    # ── Edit Template ─────────────────────────────────────────────────────────
    with tmpl_tab_edit:
        # Show flash message if present (from previous save)
        if st.session_state.get("tmpl_edit_flash"):
            st.success(st.session_state.pop("tmpl_edit_flash"))

        tmpl_names = list(templates.keys())
        # Restore last-used selection if still valid
        _prev_tmpl = st.session_state.get("tmpl_edit_last", tmpl_names[0])
        _tmpl_idx  = tmpl_names.index(_prev_tmpl) if _prev_tmpl in tmpl_names else 0

        tmpl_to_edit = st.selectbox(
            "Select template to edit",
            tmpl_names,
            index=_tmpl_idx,
            key="tmpl_edit_sel",
        )
        st.session_state["tmpl_edit_last"] = tmpl_to_edit

        if tmpl_to_edit:
            t = templates[tmpl_to_edit].copy()
            _k = tmpl_to_edit

            # ── Window / Blackout editor (outside form — reacts immediately) ──
            import json as _tj
            _twc = t.get("window_config", {})
            if isinstance(_twc, str):
                try:
                    _twc = _tj.loads(_twc)
                except Exception:
                    _twc = {}


            st.markdown("**⏰ Window & Blackout**")
            _BH_PRESETS_T = {
                "--- manual ---":            None,
                "Business hours (6-18)":     list(range(6, 19)),
                "Midnight window (22-5)":    [22,23,0,1,2,3,4,5],
                "Peak hours (7-9, 17-20)":   [7,8,9,17,18,19,20],
                "Weekday peak (7-20)":       list(range(7, 21)),
                "None (always allowed)":     [],
                "Always blocked":             list(range(24)),
            }
            _tl, _tr = st.columns(2)

            with _tl:
                te_wtype = st.selectbox(
                    "Window Type",
                    ["post_batch", "scheduled"],
                    index=0 if _twc.get("type","post_batch") == "post_batch" else 1,
                    key=f"{_k}_te_wtype",
                )
                if te_wtype == "scheduled":
                    te_start = st.text_input(
                        "Start time (HH:MM)",
                        value=str(_twc.get("start_time","02:00")),
                        key=f"{_k}_te_start", placeholder="02:00",
                    )
                else:
                    te_start = str(_twc.get("start_time","02:00"))
                _tl1, _tl2 = st.columns(2)
                with _tl1:
                    te_delay = st.number_input(
                        "Delay (min)", value=int(_twc.get("delay_minutes",30)),
                        min_value=0, max_value=240, key=f"{_k}_te_delay",
                        disabled=(te_wtype == "scheduled"),
                    )
                with _tl2:
                    te_dur = st.number_input(
                        "Duration (hrs)", value=int(_twc.get("duration_hours",4)),
                        min_value=1, max_value=12, key=f"{_k}_te_dur",
                    )
                te_tz = st.selectbox(
                    "Timezone",
                    ["America/Los_Angeles","America/New_York","America/Chicago","UTC"],
                    index=(
                        ["America/Los_Angeles","America/New_York","America/Chicago","UTC"]
                        .index(_twc.get("timezone","America/Los_Angeles"))
                        if _twc.get("timezone") in
                        ["America/Los_Angeles","America/New_York","America/Chicago","UTC"]
                        else 0
                    ),
                    key=f"{_k}_te_tz",
                )

            with _tr:
                _te_prev_key = f"{_k}_te_bh_preset_prev"
                _te_preset = st.selectbox(
                    "Blackout preset",
                    list(_BH_PRESETS_T.keys()),
                    key=f"{_k}_te_bh_preset",
                    help="Quick-fill the checkboxes below.",
                )
                _te_preset_hours = _BH_PRESETS_T[_te_preset]
                if st.session_state.get(_te_prev_key) != _te_preset:
                    st.session_state[_te_prev_key] = _te_preset
                    if _te_preset_hours is not None:
                        for _h in range(24):
                            st.session_state[f"{_k}_te_bh_{_h}"] = (_h in _te_preset_hours)
                        st.rerun()
                _te_cur_bh = (
                    _te_preset_hours if _te_preset_hours is not None
                    else _twc.get("blackout_hours", [6,7,8,9,18,19,20,21])
                )
                st.caption("HK will not start during checked hours")
                _te_bh_cols = st.columns(4)
                _te_new_bh = []
                for _h in range(24):
                    if _te_bh_cols[_h % 4].checkbox(
                        f"{_h:02d}:00",
                        value=(_h in _te_cur_bh),
                        key=f"{_k}_te_bh_{_h}",
                    ):
                        _te_new_bh.append(_h)
                    _te_new_bh.append(_h)
            st.divider()

            with st.form(f"tmpl_edit_form_{_k}"):
                col1, col2 = st.columns(2)

                with col1:
                    e_desc = st.text_input(
                        "Description",
                        value=t.get("description",""),
                        key=f"{_k}_desc",
                    )
                    e_strategy = st.selectbox(
                        "Compaction Strategy",
                        ["binpack","sort","zorder"],
                        index=(["binpack","sort","zorder"]
                               .index(t.get("compaction_strategy","binpack"))),
                        key=f"{_k}_strat",
                    )
                    e_engine = st.selectbox(
                        "Compaction Engine",
                        ["athena","glue"],
                        index=(["athena","glue"]
                               .index(t.get("compaction_engine","athena"))),
                        key=f"{_k}_eng",
                    )
                    e_target_mb = st.number_input(
                        "Target File Size (MB)",
                        value=int(t.get("compaction_target_file_size_mb", 128)),
                        min_value=64,
                        key=f"{_k}_mb",
                    )

                with col2:
                    e_snap_days = st.number_input(
                        "Snapshot Retention (days)",
                        value=int(t.get("snapshot_retention_days", 7)),
                        min_value=1,
                        key=f"{_k}_snap",
                    )
                    e_snap_min = st.number_input(
                        "Min Snapshots to Keep",
                        value=max(int(t.get("snapshot_min_to_keep") or 2), 2),
                        min_value=2,
                        key=f"{_k}_snap_min",
                    )
                    e_orphan = st.number_input(
                        "Orphan Retention (days)",
                        value=max(int(t.get("orphan_file_retention_days") or 2), 2),
                        min_value=2,
                        key=f"{_k}_orp",
                    )
                    e_freq = st.selectbox(
                        "Run Frequency",
                        ["every_trigger","daily","weekly","monthly"],
                        index=(["every_trigger","daily","weekly","monthly"]
                               .index(t.get("run_frequency","daily"))),
                        key=f"{_k}_freq",
                    )

                if st.form_submit_button("💾 Save Template", type="primary"):
                    try:
                        import json as _json
                        tmpl_path = _ROOT / "config" / "policy_templates.json"
                        with open(tmpl_path) as f:
                            all_tmpls = _json.load(f)

                        all_tmpls[tmpl_to_edit].update({
                            "description":                   e_desc,
                            "compaction_strategy":           e_strategy,
                            "compaction_engine":             e_engine,
                            "compaction_target_file_size_mb":int(e_target_mb),
                            "snapshot_retention_days":       int(e_snap_days),
                            "snapshot_min_to_keep":          int(e_snap_min),
                            "orphan_file_retention_days":    int(e_orphan),
                            "run_frequency":                 e_freq,
                            "window_config": {
                                "type":           te_wtype,
                                "timezone":       te_tz,
                                "delay_minutes":  int(te_delay),
                                "start_time":     te_start,
                                "duration_hours": int(te_dur),
                                "blackout_hours": sorted(_te_new_bh),
                            },
                        })

                        with open(tmpl_path, 'w') as f:
                            _json.dump(all_tmpls, f, indent=2)

                        # Bust module-level template cache so reload picks up changes
                        from engine.core.config import reload_templates
                        reload_templates()

                        audit(AuditEvent(
                            actor=current_user(),
                            action_type=AuditAction.POLICY_CHANGE,
                            page_source="3_Policy_Configuration",
                            target_type="template",
                            target_id=tmpl_to_edit,
                            environment=APP_ENV,
                            dry_run=False,
                            status="SUCCESS",
                            after_value=f"strategy={e_strategy},freq={e_freq}",
                        ))
                        st.session_state["tmpl_edit_flash"] = f"✅ Template `{tmpl_to_edit}` saved."
                        st.rerun()
                    except Exception as e:
                        st.error(f"Failed to save template: {e}")

    # ── Add Template ──────────────────────────────────────────────────────────
    with tmpl_tab_add:
        if st.session_state.get("tmpl_add_flash"):
            st.success(st.session_state.pop("tmpl_add_flash"))
        st.caption(
            "Add a new custom template. "
            "It will appear in the Bulk Apply dropdown and on table registration."
        )

        with st.form("tmpl_add_form", clear_on_submit=True):
            tmpl_name = st.text_input(
                "Template Name *",
                placeholder="CUSTOM_HIGH_VOLUME",
                help="Uppercase, underscores. This becomes the key in policy_templates.json.",
            )
            tmpl_desc = st.text_area(
                "Description",
                placeholder="High-volume tables with hourly CDC",
            )

            col1, col2 = st.columns(2)
            with col1:
                a_strategy = st.selectbox("Strategy", ["binpack","sort","zorder"], key="add_strat")
                a_engine   = st.selectbox("Engine",   ["athena","glue"],           key="add_eng")
                a_mb       = st.number_input("Target MB", value=256, min_value=64, key="add_mb")
                a_freq     = st.selectbox("Frequency",
                                          ["every_trigger","daily","weekly","monthly"],
                                          index=1, key="add_freq")
            with col2:
                a_snap     = st.number_input("Snapshot Retention (days)", value=7,  min_value=1, key="add_snap")
                a_snap_min = st.number_input("Min Snapshots",             value=30, min_value=2, key="add_snap_min")
                a_orphan   = st.number_input("Orphan Retention (days)",   value=3,  min_value=2, key="add_orp")

            if st.form_submit_button("➕ Add Template", type="primary"):
                if not tmpl_name.strip():
                    st.error("Template name is required.")
                elif tmpl_name.strip() in templates:
                    st.error(f"Template `{tmpl_name}` already exists. Use Edit tab.")
                else:
                    try:
                        import json as _json
                        tmpl_path = _ROOT / "config" / "policy_templates.json"
                        with open(tmpl_path) as f:
                            all_tmpls = _json.load(f)

                        all_tmpls[tmpl_name.strip().upper()] = {
                            "description":                   tmpl_desc.strip(),
                            "compaction_strategy":           a_strategy,
                            "compaction_engine":             a_engine,
                            "compaction_target_file_size_mb":int(a_mb),
                            "snapshot_retention_days":       int(a_snap),
                            "snapshot_min_to_keep":          int(a_snap_min),
                            "orphan_file_retention_days":    int(a_orphan),
                            "run_frequency":                 a_freq,
                            "window_config": {
                                "type": "post_batch",
                                "timezone": "America/Los_Angeles",
                                "delay_minutes": 30,
                                "duration_hours": 4,
                                "blackout_hours": [6,7,8,9,18,19,20,21],
                            },
                        }

                        with open(tmpl_path, 'w') as f:
                            _json.dump(all_tmpls, f, indent=2)

                        # Bust module-level template cache
                        from engine.core.config import reload_templates
                        reload_templates()

                        audit(AuditEvent(
                            actor=current_user(),
                            action_type=AuditAction.POLICY_CHANGE,
                            page_source="3_Policy_Configuration",
                            target_type="template",
                            target_id=tmpl_name.strip().upper(),
                            environment=APP_ENV,
                            dry_run=False,
                            status="SUCCESS",
                            after_value=f"new template: strategy={a_strategy}",
                        ))
                        st.session_state["tmpl_add_flash"] = f"✅ Template `{tmpl_name.strip().upper()}` added."
                        st.session_state["tmpl_edit_last"] = tmpl_name.strip().upper()
                        st.rerun()
                    except Exception as e:
                        st.error(f"Failed to add template: {e}")

    # ── Delete Template ───────────────────────────────────────────────────────
    with tmpl_tab_del:
        st.caption(
            "Delete a custom template. Built-in templates cannot be deleted. "
            "Templates assigned to registered tables must be unassigned first."
        )

        if st.session_state.get("tmpl_del_flash"):
            msg, is_error = st.session_state.pop("tmpl_del_flash")
            (st.error if is_error else st.success)(msg)

        _BUILTIN = {
            "STAGING_DEFAULT", "DATALAKE_DEFAULT", "BASE_SCD2",
            "MASTER_DEFAULT", "CRITICAL_HIGH_VOL", "NON_PROD_DEFAULT",
        }
        _deletable = [k for k in templates if k not in _BUILTIN]

        if not _deletable:
            st.info(
                "No custom templates to delete. "
                "Built-in templates cannot be removed."
            )
        else:
            tmpl_to_del = st.selectbox(
                "Select custom template to delete",
                ["-- select --"] + _deletable,
                key="tmpl_del_sel",
            )
            if tmpl_to_del and tmpl_to_del != "-- select --":
                # Check usage
                try:
                    _usage_df = cached_read_registry(
                        f"SELECT table_fqn FROM {HK_CONFIG_TABLE} "
                        f"WHERE policy_template = '{tmpl_to_del}'"
                    )
                    _usage_count = len(_usage_df) if not _usage_df.empty else 0
                except Exception:
                    _usage_count = 0

                if _usage_count > 0:
                    st.warning(
                        f"⚠️ Cannot delete `{tmpl_to_del}` — assigned to "
                        f"**{_usage_count} table(s)**. "
                        "Apply a different template to those tables first."
                    )
                    with st.expander(f"Show {_usage_count} affected table(s)"):
                        st.dataframe(_usage_df, use_container_width=True, hide_index=True)
                else:
                    st.info(f"`{tmpl_to_del}` is not assigned to any tables — safe to delete.")
                    confirm = st.checkbox(
                        f"Yes, permanently delete `{tmpl_to_del}`",
                        value=False,
                        key="tmpl_del_confirm",
                    )
                    if st.button(
                        "🗑️ Delete Template",
                        type="primary",
                        disabled=not confirm,
                        key="tmpl_del_btn",
                    ):
                        try:
                            import json as _json2
                            _tp = _ROOT / "config" / "policy_templates.json"
                            with open(_tp) as f:
                                _all = _json2.load(f)
                            if tmpl_to_del in _all:
                                del _all[tmpl_to_del]
                                with open(_tp, 'w') as f:
                                    _json2.dump(_all, f, indent=2)
                                from engine.core.config import reload_templates
                                reload_templates()
                                audit(AuditEvent(
                                    actor=current_user(),
                                    action_type=AuditAction.POLICY_CHANGE,
                                    page_source="3_Policy_Configuration",
                                    target_type="template",
                                    target_id=tmpl_to_del,
                                    environment=APP_ENV,
                                    dry_run=False,
                                    status="SUCCESS",
                                    after_value="DELETED",
                                ))
                                st.session_state["tmpl_del_flash"] = (
                                    f"✅ Template `{tmpl_to_del}` deleted.", False
                                )
                                for _k2 in ("tmpl_del_sel","tmpl_del_confirm","tmpl_edit_last"):
                                    st.session_state.pop(_k2, None)
                                st.rerun()
                        except Exception as e:
                            st.error(f"Delete failed: {e}")

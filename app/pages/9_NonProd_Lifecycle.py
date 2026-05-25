"""
Zamboni — Non-Prod Lifecycle Manager
View tables by lifecycle state, submit exemptions, view deletion history.
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from datetime import UTC

import streamlit as st

from app.components.athena_runner import cached_read_sql, execute_write
from app.components.auth import check_login
from app.components.header import render as render_header
from app.components.sidebar import is_dry_run
from app.components.sidebar import render as render_sidebar
from app.components.status_badge import lifecycle as lifecycle_badge
from config.settings import NONPROD_REGISTRY_TABLE
from engine.core.audit import AuditAction, AuditEvent, audit

st.set_page_config(page_title="Zamboni — Non-Prod Lifecycle", page_icon="🗑️", layout="wide")
check_login()
render_sidebar()
render_header(page_title="Non-Prod Lifecycle", page_icon="♻️")

# ── Environment selector ───────────────────────────────────────────────────────
env = st.selectbox("Environment", ["preprod", "dev", "test"], key="np_env")

tab1, tab2, tab3, tab4 = st.tabs([
    "📊 State Overview",
    "🛡️ Bulk Exemption / Claim",
    "🙋 Single Table Action",
    "⚫ Deletion History",
])

# ── Tab 1: State Overview ──────────────────────────────────────────────────────
with tab1:

    # ── How identification works ──────────────────────────────────────────────
    with st.expander("ℹ️ How are stale tables identified? What happens?", expanded=False):
        from engine.engines.lifecycle_engine import (
            DEFAULT_GREENZONE_DAYS,
            DEFAULT_PENDING_DROP_DAYS,
            DEFAULT_STALE_DAYS,
        )
        st.markdown(f"""
**Identification:**
The Lifecycle Engine scans all non-prod Glue databases on every run (`ZAMBONI-NONPROD-SCAN`).
A table is tracked from the moment it is first discovered.
Activity is measured from `last_query_at` and `last_write_at` timestamps (read from CloudTrail / Glue API).

**Thresholds (defaults — overridden per domain in domain_registry):**

| Stage | Default | Description |
|---|---|---|
| **ACTIVE → STALE_CANDIDATE** | `{DEFAULT_STALE_DAYS}` days inactive | No query or write in this many days |
| **STALE_CANDIDATE → GREENZONE** | Immediate (next scan) | Owner is notified via email/SNS. Table has a grace window. |
| **GREENZONE window** | `{DEFAULT_GREENZONE_DAYS}` days | Owner can claim or exempt the table during this period |
| **GREENZONE → PENDING_DROP** | After {DEFAULT_GREENZONE_DAYS} days | Final 48-hour notice sent |
| **PENDING_DROP → DROPPED** | `{DEFAULT_PENDING_DROP_DAYS}` days | Table is physically deleted from Glue + S3 |

**What happens at each stage:**
- 🟡 **STALE_CANDIDATE** — flagged as inactive, no action yet
- 🟠 **GREENZONE** — owner notification sent, `greenzone_expires_at` set. Owner can claim or exempt.
- 🔴 **PENDING_DROP** — final notification, countdown shown in UI. No more extensions.
- ⚫ **DROPPED** — Glue table deleted, S3 data deleted. Logged in Deletion History tab.

**Exemptions:**
Any table can be exempted by checking the **Submit Exemption** tab. Exempt tables stay ACTIVE permanently.
Backup-pattern tables (`_bkp`, `_backup`, `_copy`) are auto-flagged and skip straight to exemption review.
""")

    state_filter = st.selectbox(
        "Lifecycle State",
        ["All", "ACTIVE", "STALE_CANDIDATE", "GREENZONE", "PENDING_DROP"],
        key="np_state",
    )

    state_clause = f"AND lifecycle_state = '{state_filter}'" if state_filter != "All" else ""
    sql = f"""
        SELECT
            table_fqn, domain, table_format,
            lifecycle_state, days_since_activity,
            last_query_at, last_write_at, created_at,
            greenzone_expires_at, pending_drop_expires_at,
            owner_exempted, is_backup_pattern, pattern_matched,
            first_seen_at
        FROM {NONPROD_REGISTRY_TABLE}
        WHERE environment = '{env}'
          AND lifecycle_state != 'DROPPED'
          {state_clause}
        ORDER BY lifecycle_state, days_since_activity DESC
        LIMIT 300
    """
    with st.spinner("Loading lifecycle data..."):
        try:
            df = cached_read_sql(sql)

            if df.empty:
                st.info(f"No tables found in `{env}` with the selected state.")
            else:
                # State distribution
                state_counts = df["lifecycle_state"].value_counts()
                cols = st.columns(len(state_counts))
                state_colors = {
                    "ACTIVE":          "🟢",
                    "STALE_CANDIDATE": "🟡",
                    "GREENZONE":       "🟠",
                    "PENDING_DROP":    "🔴",
                }
                for col, (state, count) in zip(cols, state_counts.items()):
                    icon = state_colors.get(state, "⚪")
                    col.metric(f"{icon} {state}", count)

                st.divider()

                # Highlight GREENZONE and PENDING_DROP
                df["lifecycle_state"] = df["lifecycle_state"].apply(lifecycle_badge)
                df["owner_exempted"]  = df["owner_exempted"].apply(
                    lambda x: "✅ Exempt" if x else ""
                )
                df["is_backup_pattern"] = df["is_backup_pattern"].apply(
                    lambda x: "🗂️ Backup" if x else ""
                )

                st.dataframe(df, use_container_width=True, hide_index=True, height=450)
                st.caption(f"{len(df)} tables shown")

        except Exception as e:
            st.error(f"Query failed: {e}")

# ── Tab 2: Submit Exemption ────────────────────────────────────────────────────
with tab2:
    st.markdown("#### 🛡️ Bulk Exemption / Claim")
    st.caption(
        "Select tables from the list below. "
        "Exemption resets to ACTIVE for one more cycle. "
        "Claim assigns you as owner and resets to ACTIVE. "
        "Both require a business reason."
    )

    # Load actionable tables (not ACTIVE/DROPPED)
    _bulk_sql = f"""
        SELECT table_fqn, domain, environment, lifecycle_state,
               days_since_activity, greenzone_expires_at,
               pending_drop_expires_at, owner_email
        FROM {NONPROD_REGISTRY_TABLE}
        WHERE environment = '{env}'
          AND lifecycle_state NOT IN ('ACTIVE','DROPPED')
        ORDER BY
            CASE lifecycle_state
                WHEN 'PENDING_DROP'    THEN 1
                WHEN 'GREENZONE'       THEN 2
                WHEN 'STALE_CANDIDATE' THEN 3
                ELSE 4
            END,
            days_since_activity DESC
        LIMIT 500
    """
    try:
        _bulk_df = cached_read_sql(_bulk_sql)
    except Exception as _be:
        _bulk_df = None
        st.error(f"Load failed: {_be}")

    if _bulk_df is not None and not _bulk_df.empty:
        st.markdown(f"**{len(_bulk_df)} table(s) requiring action:**")

        # Badge the state column
        _disp_bulk = _bulk_df.copy()
        _disp_bulk["lifecycle_state"] = _disp_bulk["lifecycle_state"].apply(lifecycle_badge)
        _disp_bulk["table_fqn"] = _disp_bulk["table_fqn"].str.replace(
            r"^glue_catalog[.]", "", regex=True
        )
        _disp_bulk.insert(0, "Select", False)

        _edited_bulk = st.data_editor(
            _disp_bulk,
            column_config={
                "Select":          st.column_config.CheckboxColumn("✓", default=False),
                "lifecycle_state": st.column_config.TextColumn("State"),
                "table_fqn":       st.column_config.TextColumn("Table"),
                "days_since_activity": st.column_config.NumberColumn("Days Inactive"),
                "greenzone_expires_at": st.column_config.TextColumn("GZ Expires"),
                "pending_drop_expires_at": st.column_config.TextColumn("Drop At"),
            },
            use_container_width=True,
            hide_index=True,
            disabled=["table_fqn","domain","environment","lifecycle_state",
                      "days_since_activity","greenzone_expires_at",
                      "pending_drop_expires_at","owner_email"],
            key=f"bulk_action_editor_{env}",
            height=min(400, max(150, len(_bulk_df) * 35 + 40)),
        )

        _selected_bulk = _edited_bulk[_edited_bulk["Select"]]
        if not _selected_bulk.empty:
            st.markdown(f"**{len(_selected_bulk)} table(s) selected**")

        _action_reason = st.text_area(
            "Business Reason (required for all selected tables)",
            placeholder="Used by Q2 reporting sprint — will be cleaned up by 2026-06-30",
            key="bulk_action_reason",
        )

        _ba_col1, _ba_col2 = st.columns(2)
        with _ba_col1:
            _do_exempt = st.button(
                f"🛡️ Exempt {len(_selected_bulk)} Table(s)",
                type="primary",
                disabled=(_selected_bulk.empty),
                key="bulk_exempt_btn",
            )
        with _ba_col2:
            _do_claim = st.button(
                f"🙋 Claim {len(_selected_bulk)} Table(s)",
                disabled=(_selected_bulk.empty),
                key="bulk_claim_btn",
            )

        if (_do_exempt or _do_claim) and not _selected_bulk.empty:
            if not _action_reason.strip():
                st.error("Business reason is required.")
            else:
                from datetime import datetime as _dt_bulk

                from app.components.auth import current_user as _cu_bulk
                _now_b = _dt_bulk.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
                _action_label = "Exempt" if _do_exempt else "Claim"
                _ok_b = _fail_b = 0

                for _, _br in _selected_bulk.iterrows():
                    # Restore full FQN
                    _fqn_b = _bulk_df[_bulk_df["table_fqn"].str.endswith(
                        str(_br["table_fqn"]).split(".")[-1]
                    )]["table_fqn"].iloc[0] if "glue_catalog" not in str(_br["table_fqn"]) else str(_br["table_fqn"])

                    _state_b = str(_br.get("lifecycle_state","")).replace("✅","").replace("🟡","").replace("🟠","").replace("🔴","").strip()
                    try:
                        execute_write(
                            f"UPDATE {NONPROD_REGISTRY_TABLE} "
                            f"SET lifecycle_state = 'ACTIVE', "
                            f"previous_state = '{_state_b}', "
                            f"owner_exempted = 1, "
                            f"exemption_reason = '{_action_reason.strip().replace(chr(39), chr(39)*2)}', "
                            f"state_changed_at = '{_now_b}' "
                            + (f", owner_email = '{_cu_bulk()}' " if _do_claim else "")
                            + f"WHERE table_fqn LIKE '%{str(_br['table_fqn']).split('.')[-1]}'",
                            workgroup="app", dry_run=is_dry_run(),
                        )
                        audit(AuditEvent(
                            actor=_cu_bulk(),
                            action_type=AuditAction.LIFECYCLE_EXEMPTION,
                            page_source="9_NonProd_Lifecycle",
                            target_type="table",
                            target_id=str(_br["table_fqn"]),
                            environment=env, dry_run=is_dry_run(),
                            status="DRY_RUN" if is_dry_run() else "SUCCESS",
                            reason=f"{_action_label}: {_action_reason.strip()}",
                        ))
                        _ok_b += 1
                    except Exception as _be2:
                        _fail_b += 1
                        st.error(f"{_br['table_fqn']}: {_be2}")

                st.success(
                    f"✅ {_action_label}d {_ok_b} table(s)"
                    + (f", {_fail_b} failed" if _fail_b else "")
                    + (" (dry run)" if is_dry_run() else "")
                )
                cached_read_sql.clear()
                st.rerun()
    elif _bulk_df is not None:
        st.success("✅ No tables currently require exemption or claiming in this environment.")

# ── Tab 3: Single Table Action (edge cases) ───────────────────────────────────
with tab3:
    st.markdown("#### 🙋 Single Table Action")
    st.caption(
        "For individual tables — search by name, review its current state, "
        "then exempt or claim. Use the Bulk tab for multiple tables."
    )
    from app.components.auth import current_user as _cu_claim
    # Searchable selectbox from live nonprod_registry
    try:
        _sa_fqns = cached_read_sql(
            f"SELECT table_fqn, lifecycle_state, days_since_activity "
            f"FROM {NONPROD_REGISTRY_TABLE} "
            f"WHERE environment = '{env}' AND lifecycle_state != 'DROPPED' "
            f"ORDER BY table_fqn"
        )
        _sa_opts = ["— search or type below —"] + _sa_fqns["table_fqn"].tolist()
    except Exception:
        _sa_fqns = None
        _sa_opts = ["— search or type below —"]

    _sa_sel = st.selectbox(
        "Search table (type to filter)",
        [f.replace("glue_catalog.", "") for f in _sa_opts],
        key="claim_table_sel",
        help="Type to search. All non-prod tables in the registry.",
    )
    if _sa_sel and not _sa_sel.startswith("—"):
        claim_fqn = "glue_catalog." + _sa_sel if not _sa_sel.startswith("glue_catalog") else _sa_sel
        # Show current state
        if _sa_fqns is not None:
            _sel_row = _sa_fqns[_sa_fqns["table_fqn"].str.endswith(_sa_sel.split(".")[-1])]
            if not _sel_row.empty:
                _sr = _sel_row.iloc[0]
                st.caption(
                    f"State: **{lifecycle_badge(_sr.get('lifecycle_state',''))}** · "
                    f"Days inactive: **{_sr.get('days_since_activity','—')}**"
                )
    else:
        claim_fqn = st.text_input(
            "Or enter FQN manually",
            placeholder="glue_catalog.preprod_db.my_table",
            key="claim_fqn_manual",
        )
    claim_reason = st.text_area("Reason for claiming *", key="claim_reason",
                                 placeholder="Why are you claiming ownership of this table?")
    if st.button("🙋 Claim This Table", type="primary", key="claim_btn"):
        if not claim_fqn or not claim_reason.strip():
            st.error("Both table FQN and reason are required.")
        elif len(claim_reason.strip()) < 10:
            st.error("Reason must be at least 10 characters.")
        else:
            try:
                from datetime import UTC, datetime
                now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
                claim_sql = f"""
                    UPDATE {NONPROD_REGISTRY_TABLE}
                    SET lifecycle_state   = 'ACTIVE',
                        owner_email       = '{_cu_claim()}',
                        state_changed_at  = TIMESTAMP '{now}'
                    WHERE table_fqn = '{claim_fqn}'
                """
                from app.components.athena_runner import execute_write
                execute_write(claim_sql, workgroup="app", dry_run=is_dry_run())
                audit(AuditEvent(
                    actor=_cu_claim(),
                    action_type=AuditAction.CLAIM_TABLE,
                    page_source="9_NonProd_Lifecycle",
                    target_type="table", target_id=claim_fqn,
                    environment=env, dry_run=is_dry_run(),
                    status="DRY_RUN" if is_dry_run() else "SUCCESS",
                    reason=claim_reason.strip(),
                    after_value=f"owner={_cu_claim()},state=ACTIVE",
                ))
                st.success(f"✅ Table `{claim_fqn}` claimed and reset to ACTIVE"
                           + (" (dry run)" if is_dry_run() else ""))
            except Exception as ce:
                st.error(f"Claim failed: {ce}")

# ── Tab 4: Deletion History ────────────────────────────────────────────────────
with tab4:
    st.markdown("#### Recently Deleted Tables")
    hist_sql = f"""
        SELECT
            table_fqn, domain, dropped_at,
            bytes_reclaimed, s3_cleaned, catalog_dropped,
            previous_state
        FROM {NONPROD_REGISTRY_TABLE}
        WHERE environment    = '{env}'
          AND lifecycle_state = 'DROPPED'
          AND dropped_at     >= CURRENT_DATE - INTERVAL '90' DAY
        ORDER BY dropped_at DESC
        LIMIT 100
    """
    try:
        hist_df = cached_read_sql(hist_sql)
        if hist_df.empty:
            st.info("No tables deleted in the last 90 days.")
        else:
            total_reclaimed = hist_df["bytes_reclaimed"].sum()
            col1, col2 = st.columns(2)
            col1.metric("Tables Deleted (90d)", len(hist_df))
            col2.metric("Storage Reclaimed", f"{total_reclaimed/1e9:.1f} GB")
            st.divider()
            hist_df["s3_cleaned"]     = hist_df["s3_cleaned"].apply(lambda x: "✅" if x else "❌")
            hist_df["catalog_dropped"]= hist_df["catalog_dropped"].apply(lambda x: "✅" if x else "❌")
            hist_df["bytes_reclaimed"]= hist_df["bytes_reclaimed"].apply(
                lambda x: f"{x/1e9:.2f} GB" if x else "0 GB"
            )
            st.dataframe(hist_df, use_container_width=True, hide_index=True, height=350)
            st.download_button(
                "⬇️ Export",
                data=hist_df.to_csv(index=False),
                file_name="zamboni_deletion_history.csv",
                mime="text/csv",
            )
    except Exception as e:
        st.error(f"Query failed: {e}")

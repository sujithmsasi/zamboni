"""
Zamboni — Execution Log Browser
Browse and filter all execution_log entries.
Drill into single executions, export to CSV.
"""
import streamlit as st
import pandas as pd

from app.components.auth import check_login
from app.components.header import render as render_header
from app.components.sidebar import render as render_sidebar
from app.components.filters import domain_filter, environment_filter
from app.components.athena_runner import cached_read_sql
from app.components.status_badge import status as status_badge

from config.settings import EXECUTION_LOG_TABLE

st.set_page_config(page_title="Zamboni — Execution Log", page_icon="📜", layout="wide")
check_login()
render_sidebar()
render_header()

st.title("📜 Execution Log")
st.caption("Unified audit log for all three engines. Every operation is recorded here.")

# ── Filters ───────────────────────────────────────────────────────────────────
with st.expander("🔍 Filters", expanded=True):
    col1, col2, col3, col4 = st.columns(4)
    with col1: sel_domain  = domain_filter(key="el_domain")
    with col2:
        sel_engine = st.selectbox("Engine", ["All", "hk", "archival", "lifecycle"], key="el_engine")
    with col3:
        sel_status = st.selectbox("Status", ["All", "SUCCESS", "FAILURE", "SKIPPED", "DRY_RUN"], key="el_status")
    with col4:
        sel_days = st.selectbox("Time Range", ["Today", "7 days", "14 days", "30 days"], key="el_days")

    days_map = {"Today": 0, "7 days": 7, "14 days": 14, "30 days": 30}
    days = days_map[sel_days]

conditions = []
if days == 0:
    conditions.append("execution_date = CURRENT_DATE")
else:
    conditions.append(f"execution_date >= CURRENT_DATE - INTERVAL '{days}' DAY")

if sel_domain != "All" and sel_domain:
    conditions.append(f"domain = '{sel_domain}'")
if sel_engine != "All":
    conditions.append(f"engine = '{sel_engine}'")
if sel_status != "All":
    conditions.append(f"status = '{sel_status}'")

where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

sql = f"""
    SELECT
        execution_date, started_at, engine, operation,
        table_fqn, domain, layer, tier, status,
        duration_seconds, skip_reason, error_message,
        snapshots_expired, orphan_files_deleted,
        bytes_rewritten, rows_archived, bytes_archived,
        dry_run, execution_id
    FROM {EXECUTION_LOG_TABLE}
    {where}
    ORDER BY started_at DESC
    LIMIT 500
"""

with st.spinner("Loading execution log..."):
    try:
        df = cached_read_sql(sql)
        st.caption(f"{len(df)} records (max 500 shown)")

        if df.empty:
            st.info("No records found with the selected filters.")
        else:
            # Status badges
            df["status_display"] = df["status"].apply(status_badge)

            # Display columns
            display_cols = [
                "execution_date", "started_at", "engine", "operation",
                "table_fqn", "domain", "layer", "tier", "status_display",
                "duration_seconds", "skip_reason", "error_message",
            ]
            display_df = df[[c for c in display_cols if c in df.columns]]
            display_df = display_df.rename(columns={"status_display": "status"})

            # Highlight failures
            def _highlight(row):
                if "FAILURE" in str(row.get("status", "")):
                    return ["background-color: #fee2e2"] * len(row)
                return [""] * len(row)

            st.dataframe(
                display_df.style.apply(_highlight, axis=1),
                use_container_width=True,
                hide_index=True,
                height=450,
            )

            # Export
            csv = df.to_csv(index=False)
            st.download_button(
                "⬇️ Export to CSV",
                data=csv,
                file_name=f"zamboni_execution_log_{sel_days.replace(' ','_')}.csv",
                mime="text/csv",
            )

            # ── Drill-in ───────────────────────────────────────────────────────
            st.divider()
            st.subheader("🔎 Drill Into a Record")
            exec_id = st.text_input("Paste Execution ID", placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx", key="el_exec_id")
            if exec_id:
                row_df = df[df["execution_id"] == exec_id]
                if row_df.empty:
                    st.warning("Execution ID not found in current results.")
                else:
                    st.json(row_df.iloc[0].dropna().to_dict())

    except Exception as e:
        st.error(f"Query failed: {e}")

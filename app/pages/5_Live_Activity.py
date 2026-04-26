"""
Zamboni — Live Activity Monitor
Real-time engine activity with auto-refresh.
Shows currently running and recently completed operations.
"""
import streamlit as st
import pandas as pd
from datetime import datetime, timezone

from app.components.auth import check_login
from app.components.header import render as render_header
from app.components.sidebar import render as render_sidebar
from app.components.athena_runner import cached_read_sql
from app.components.status_badge import status as status_badge

from config.settings import EXECUTION_LOG_TABLE

st.set_page_config(page_title="Zamboni — Live Activity", page_icon="🔴", layout="wide")
check_login()
render_sidebar()
render_header()

st.title("🔴 Live Activity Monitor")

# ── Auto-refresh ───────────────────────────────────────────────────────────────
col1, col2, col3 = st.columns([2, 1, 1])
with col1:
    st.caption(f"Last updated: {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}")
with col2:
    auto_refresh = st.toggle("Auto-refresh (30s)", value=False)
with col3:
    if st.button("🔄 Refresh now"):
        cached_read_sql.clear()
        st.rerun()

if auto_refresh:
    import time
    st.info("Auto-refreshing every 30 seconds. Keep this tab open.")
    time.sleep(30)
    st.rerun()

st.divider()

# ── Running Engines ────────────────────────────────────────────────────────────
st.subheader("⚡ Currently Running")
running_sql = f"""
    SELECT
        run_id, engine, domain, COUNT(*) AS tables_in_progress,
        MIN(started_at) AS run_started
    FROM {EXECUTION_LOG_TABLE}
    WHERE execution_date = CURRENT_DATE
      AND status = 'RUNNING'
    GROUP BY run_id, engine, domain
    ORDER BY run_started DESC
"""
try:
    run_df = cached_read_sql(running_sql)
    if run_df.empty:
        st.info("No engines currently running.")
    else:
        st.dataframe(run_df, use_container_width=True, hide_index=True)
except Exception as e:
    st.error(f"Query failed: {e}")

# ── Recent Operations ──────────────────────────────────────────────────────────
st.subheader("📜 Recent Operations (Today)")

col1, col2 = st.columns([2, 1])
with col1:
    engine_filter = st.selectbox("Engine", ["All", "hk", "archival", "lifecycle"], key="la_engine")
with col2:
    status_filter = st.selectbox("Status", ["All", "SUCCESS", "FAILURE", "SKIPPED", "DRY_RUN"], key="la_status")

conditions = ["execution_date = CURRENT_DATE"]
if engine_filter != "All": conditions.append(f"engine = '{engine_filter}'")
if status_filter != "All": conditions.append(f"status = '{status_filter}'")
where = "WHERE " + " AND ".join(conditions)

recent_sql = f"""
    SELECT
        started_at, engine, operation, table_fqn,
        domain, layer, tier, status,
        duration_seconds,
        snapshots_expired, orphan_files_deleted,
        skip_reason, error_message
    FROM {EXECUTION_LOG_TABLE}
    {where}
    ORDER BY started_at DESC
    LIMIT 200
"""
try:
    recent_df = cached_read_sql(recent_sql)
    if recent_df.empty:
        st.info("No operations found for today with the selected filters.")
    else:
        # Format status with badges
        recent_df["status"] = recent_df["status"].apply(status_badge)
        st.dataframe(recent_df, use_container_width=True, hide_index=True, height=450)
        st.caption(f"{len(recent_df)} operations shown")
except Exception as e:
    st.error(f"Query failed: {e}")

# ── Engine Stats Today ──────────────────────────────────────────────────────────
st.divider()
st.subheader("📊 Today's Engine Summary")
summary_sql = f"""
    SELECT
        engine,
        status,
        COUNT(*)                            AS operations,
        SUM(duration_seconds)               AS total_seconds,
        SUM(snapshots_expired)              AS snapshots_expired,
        SUM(orphan_files_deleted)           AS orphans_deleted,
        ROUND(SUM(bytes_rewritten)/1e9, 2)  AS gb_rewritten
    FROM {EXECUTION_LOG_TABLE}
    WHERE execution_date = CURRENT_DATE
    GROUP BY engine, status
    ORDER BY engine, status
"""
try:
    summary_df = cached_read_sql(summary_sql)
    if not summary_df.empty:
        st.dataframe(summary_df, use_container_width=True, hide_index=True)
except Exception as e:
    st.error(f"Summary query failed: {e}")

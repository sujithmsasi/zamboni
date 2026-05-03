"""
Zamboni — Execution Log Browser
Browse and filter all execution_log entries.
Drill into single executions, export to CSV.
"""
import streamlit as st

from app.components.athena_runner import cached_read_sql
from app.components.auth import check_login
from app.components.filters import domain_filter
from app.components.header import render as render_header
from app.components.sidebar import render as render_sidebar
from app.components.status_badge import status as status_badge
from config.settings import EXECUTION_LOG_TABLE
from engine.core.audit import AuditAction, AuditEvent, audit  # noqa: F401

st.set_page_config(page_title="Zamboni — Execution Log", page_icon="📜", layout="wide")
check_login()
render_sidebar()
render_header()

st.title("📜 Execution Log")
st.caption("Unified audit log for all three engines. Every operation is recorded here.")

# ── Filters ───────────────────────────────────────────────────────────────────
with st.expander("🔍 Filters", expanded=True):
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        sel_domain  = domain_filter(key="el_domain")
    with col2:
        sel_engine = st.selectbox("Engine", ["All", "hk", "archival", "lifecycle"], key="el_engine")
    with col3:
        sel_status = st.selectbox("Status", ["All", "SUCCESS", "FAILURE", "SKIPPED", "DRY_RUN"], key="el_status")
    with st.columns(1)[0]:
        hide_dry_run = st.checkbox("Hide DRY_RUN rows (default)", value=True, key="el_hide_dryrun",
                                    help="DRY_RUN rows are hidden by default to focus on real operations.")
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
elif hide_dry_run:
    conditions.append("status != 'DRY_RUN'")

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
        # ── Cost per run ───────────────────────────────────────────────
        if "bytes_scanned" in df.columns:
            df["athena_cost_usd"] = (df["bytes_scanned"].fillna(0) / 1e12 * 5.0).round(6)
            df["athena_cost_usd"] = df["athena_cost_usd"].apply(lambda x: f"${x:.5f}")

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


# ── SLA Breach Tracker ────────────────────────────────────────────────────────
st.divider()
st.subheader("⚠️ SLA Breach Tracker")
st.caption(
    "Tables expected to run based on run_frequency but with no SUCCESS "
    "in the expected window."
)
try:
    from app.components.athena_runner import cached_read_registry
    from config.settings import HK_CONFIG_TABLE, STREAM_REGISTRY_TABLE
    sla_sql = f"""
        SELECT r.table_fqn, r.domain, c.run_frequency,
               MAX(e.completed_at)                                  AS last_success,
               DATE_DIFF('hour', MAX(e.completed_at), NOW())        AS hours_since_success
        FROM {STREAM_REGISTRY_TABLE} r
        JOIN {HK_CONFIG_TABLE} c ON r.table_fqn = c.table_fqn
        LEFT JOIN {EXECUTION_LOG_TABLE} e
               ON r.table_fqn = e.table_fqn
              AND e.operation  = 'hk_run'
              AND e.status     = 'SUCCESS'
              AND e.execution_date >= CURRENT_DATE - INTERVAL '30' DAY
        WHERE r.hk_enabled = true
          AND r.environment = 'prod'
        GROUP BY r.table_fqn, r.domain, c.run_frequency
        HAVING
            (c.run_frequency = 'daily'   AND (MAX(e.completed_at) IS NULL OR DATE_DIFF('hour', MAX(e.completed_at), NOW()) > 28))
         OR (c.run_frequency = 'weekly'  AND (MAX(e.completed_at) IS NULL OR DATE_DIFF('hour', MAX(e.completed_at), NOW()) > 200))
         OR (c.run_frequency = 'monthly' AND (MAX(e.completed_at) IS NULL OR DATE_DIFF('hour', MAX(e.completed_at), NOW()) > 750))
        ORDER BY hours_since_success DESC NULLS FIRST
        LIMIT 50
    """
    sla_df = cached_read_registry(sla_sql)
    if not sla_df.empty:
        st.warning(f"⚠️ {len(sla_df)} tables are overdue for housekeeping.")
        st.dataframe(sla_df, use_container_width=True, hide_index=True)
        st.download_button(
            "⬇️ Export SLA Breach List",
            data=sla_df.to_csv(index=False).encode("utf-8"),
            file_name="zamboni_sla_breach.csv",
            mime="text/csv",
        )
    else:
        st.success("✅ No SLA breaches detected.")
except Exception as sla_e:
    st.info(f"SLA tracker requires Athena connectivity: {sla_e}")

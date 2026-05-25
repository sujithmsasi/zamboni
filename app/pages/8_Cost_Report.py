"""
Zamboni — Cost Report
Per-domain Athena cost, storage reclaimed from archival + lifecycle,
monthly trends, and top tables by scan cost.
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from app.components.athena_runner import cached_read_sql
from app.components.auth import check_login
from app.components.filters import domain_filter
from app.components.header import render as render_header
from app.components.kpi_cards import render_kpi_row
from app.components.sidebar import render as render_sidebar
from config.settings import EXECUTION_LOG_TABLE
from engine.core.audit import AuditAction, AuditEvent, audit  # noqa: F401
from engine.core.cost_explorer import get_athena_cost
from engine.core.cost_explorer import is_enabled as ce_enabled

st.set_page_config(page_title="Zamboni — Cost Report", page_icon="💰", layout="wide")
check_login()
render_sidebar()
st.session_state["_current_page"] = "cost"
render_header(page_title="Cost Report", page_icon="💰")

# ── Live billing banner ──────────────────────────────────────────────────────
if ce_enabled():
    st.success(
        "💳 **Live Billing Mode** — Cost data from AWS Cost Explorer "
        "(cached 24h). Manage in ⚙️ Settings → Advanced → Cost Explorer."
    )
    _live_cost = get_athena_cost(days=30)
    if _live_cost.get("warning"):
        st.warning(_live_cost["warning"])
else:
    st.info(
        "📊 **Estimate Mode** — Cost derived from bytes_scanned ($5/TB). "
        "Enable live billing in ⚙️ Settings → Advanced → Cost Explorer."
    )

st.title("💰 Cost Report")
st.caption("Athena scan costs, storage reclaimed by archival and lifecycle engines. All costs are estimates.")
st.info("Athena pricing: $5.00 per TB scanned (us-west-2). Storage savings calculated from bytes removed.")

# ── Filters ───────────────────────────────────────────────────────────────────
col1, col2 = st.columns([2, 2])
with col1:
    sel_domain = domain_filter(key="cr_domain")
with col2:
    sel_months = st.selectbox("Period", ["Last 30 days", "Last 90 days", "Last 6 months"], key="cr_period")

period_days = {"Last 30 days": 30, "Last 90 days": 90, "Last 6 months": 180}
days = period_days[sel_months]

domain_clause = f"AND domain = '{sel_domain}'" if sel_domain else ""

# ── Top-level KPIs ─────────────────────────────────────────────────────────────
kpi_sql = f"""
    SELECT
        ROUND(SUM(bytes_scanned) / 1e12 * 5, 2)    AS estimated_athena_cost_usd,
        ROUND(SUM(bytes_scanned) / 1e9, 1)         AS gb_scanned,
        ROUND(SUM(bytes_rewritten) / 1e9, 1)        AS gb_compacted,
        ROUND(SUM(bytes_archived) / 1e9, 1)         AS gb_archived
    FROM {EXECUTION_LOG_TABLE}
    WHERE execution_date >= CURRENT_DATE - INTERVAL '{days}' DAY
      AND status IN ('SUCCESS', 'DRY_RUN')
    {domain_clause}
"""
with st.spinner("Loading cost metrics..."):
    try:
        kpi_df = cached_read_sql(kpi_sql)
        row    = kpi_df.iloc[0]
        render_kpi_row([
            {"label": "Estimated Athena Cost",   "value": f"${float(row.get('estimated_athena_cost_usd') or 0):.2f}",
             "help": "Based on $5/TB scanned"},
            {"label": "GB Scanned (Athena)",      "value": f"{float(row.get('gb_scanned') or 0):.1f} GB"},
            {"label": "GB Compacted",             "value": f"{float(row.get('gb_compacted') or 0):.1f} GB"},
            {"label": "GB Archived (Staging→IT)", "value": f"{float(row.get('gb_archived') or 0):.1f} GB"},
        ])
    except Exception as e:
        st.error(f"KPI query failed: {e}")

st.divider()

col_left, col_right = st.columns(2)

# ── Athena Cost by Domain ──────────────────────────────────────────────────────
with col_left:
    st.subheader("Athena Cost by Domain")
    domain_cost_sql = f"""
        SELECT
            domain,
            ROUND(SUM(bytes_scanned) / 1e12 * 5, 4) AS cost_usd,
            ROUND(SUM(bytes_scanned) / 1e9, 1)       AS gb_scanned
        FROM {EXECUTION_LOG_TABLE}
        WHERE execution_date >= CURRENT_DATE - INTERVAL '{days}' DAY
          AND status = 'SUCCESS'
        GROUP BY domain
        ORDER BY cost_usd DESC
        LIMIT 15
    """
    try:
        domain_df = cached_read_sql(domain_cost_sql)
        if not domain_df.empty:
            fig = px.bar(
                domain_df, x="domain", y="cost_usd",
                labels={"cost_usd": "Estimated Cost ($)", "domain": "Domain"},
                color="cost_usd",
                color_continuous_scale=["#dbeafe","#1d4ed8"],
                text_auto=".2f",
            )
            fig.update_layout(showlegend=False, height=320, margin=dict(t=10,b=20))
            st.plotly_chart(fig, use_container_width=True)
    except Exception as e:
        st.error(f"Domain cost chart failed: {e}")

# ── Archival Savings by Domain ─────────────────────────────────────────────────
with col_right:
    st.subheader("Storage Reclaimed by Domain (Archival)")
    archival_sql = f"""
        SELECT
            domain,
            COUNT(DISTINCT table_fqn)                AS tables_archived,
            COUNT(*)                                  AS partitions_archived,
            ROUND(SUM(bytes_archived) / 1e9, 2)      AS gb_archived,
            SUM(rows_archived)                        AS rows_archived
        FROM {EXECUTION_LOG_TABLE}
        WHERE engine  = 'archival'
          AND status  = 'SUCCESS'
          AND execution_date >= CURRENT_DATE - INTERVAL '{days}' DAY
        GROUP BY domain
        ORDER BY gb_archived DESC
    """
    try:
        arch_df = cached_read_sql(archival_sql)
        if arch_df.empty:
            st.info("No archival operations found in this period.")
        else:
            fig = px.pie(
                arch_df, values="gb_archived", names="domain",
                hole=0.4,
            )
            fig.update_layout(height=320, margin=dict(t=10,b=20))
            st.plotly_chart(fig, use_container_width=True)
    except Exception as e:
        st.error(f"Archival chart failed: {e}")

# ── Monthly Trend ──────────────────────────────────────────────────────────────
st.divider()
st.subheader("Monthly Cost Trend")
trend_sql = f"""
    SELECT
        DATE_TRUNC('month', execution_date)          AS month,
        ROUND(SUM(bytes_scanned) / 1e12 * 5, 2)     AS cost_usd,
        ROUND(SUM(bytes_scanned) / 1e9, 1)           AS gb_scanned,
        ROUND(SUM(bytes_archived) / 1e9, 1)          AS gb_archived
    FROM {EXECUTION_LOG_TABLE}
    WHERE execution_date >= CURRENT_DATE - INTERVAL '180' DAY
      AND status = 'SUCCESS'
    {domain_clause}
    GROUP BY DATE_TRUNC('month', execution_date)
    ORDER BY month
"""
try:
    trend_df = cached_read_sql(trend_sql)
    if not trend_df.empty:
        fig = go.Figure()
        fig.add_trace(go.Bar(x=trend_df["month"], y=trend_df["cost_usd"],
                             name="Athena Cost ($)", marker_color="#3b82f6"))
        fig.add_trace(go.Line(x=trend_df["month"], y=trend_df["gb_archived"],
                              name="GB Archived", yaxis="y2", line=dict(color="#22c55e")))
        fig.update_layout(
            yaxis=dict(title="Estimated Athena Cost ($)"),
            yaxis2=dict(title="GB Archived", overlaying="y", side="right"),
            height=300, margin=dict(t=20, b=20),
        )
        st.plotly_chart(fig, use_container_width=True)
except Exception as e:
    st.error(f"Trend chart failed: {e}")

# ── Top Tables by Scan Cost ────────────────────────────────────────────────────
st.subheader("Top 20 Tables by Athena Scan Cost")
top_sql = f"""
    SELECT
        table_fqn, domain, layer,
        ROUND(SUM(bytes_scanned) / 1e12 * 5, 4) AS cost_usd,
        ROUND(SUM(bytes_scanned) / 1e9, 1)       AS gb_scanned,
        COUNT(*)                                  AS operations
    FROM {EXECUTION_LOG_TABLE}
    WHERE execution_date >= CURRENT_DATE - INTERVAL '{days}' DAY
      AND status = 'SUCCESS'
      AND bytes_scanned > 0
    {domain_clause}
    GROUP BY table_fqn, domain, layer
    ORDER BY cost_usd DESC
    LIMIT 20
"""
try:
    top_df = cached_read_sql(top_sql)
    if not top_df.empty:
        st.dataframe(top_df, use_container_width=True, hide_index=True)
        csv = top_df.to_csv(index=False)
        st.download_button("⬇️ Export to CSV", data=csv,
                           file_name="zamboni_cost_report.csv", mime="text/csv")
except Exception as e:
    st.error(f"Top tables query failed: {e}")

# ── Budget Alert ──────────────────────────────────────────────────────────────
st.divider()
st.subheader("💰 Budget Alert")
from config.platform_settings import get_setting

budget_threshold = float(get_setting("budget_alert_threshold_usd_monthly", 0))
if budget_threshold > 0:
    st.info(
        f"Monthly budget threshold: **${budget_threshold:,.2f}**  \n"
        "Configure in ⚙️ Settings → General → Budget Alert Threshold."
    )
else:
    st.caption(
        "No budget threshold configured. "
        "Set one in ⚙️ Settings → General to receive cost alerts."
    )

# ── ROI Dashboard ─────────────────────────────────────────────────────────────
st.divider()
st.subheader("📈 ROI Estimate")
st.caption("Estimated savings from Zamboni operations vs Athena scan cost.")
col_r1, col_r2, col_r3 = st.columns(3)
try:
    from engine.utils.athena_client import read_sql as _read
    roi_sql = f"""
        SELECT
            ROUND(SUM(bytes_scanned) / 1e12 * 5.0, 4)   AS athena_cost_usd,
            ROUND(SUM(bytes_rewritten) / 1e9, 2)         AS gb_compacted,
            ROUND(SUM(bytes_archived) / 1e9, 2)          AS gb_archived
        FROM {EXECUTION_LOG_TABLE}
        WHERE execution_date >= CURRENT_DATE - INTERVAL '30' DAY
          AND status = 'SUCCESS'
    """
    roi_df = _read(roi_sql, workgroup="app")
    if not roi_df.empty:
        row = roi_df.iloc[0]
        athena_cost = float(row.get("athena_cost_usd") or 0)
        gb_compacted = float(row.get("gb_compacted") or 0)
        gb_archived  = float(row.get("gb_archived")  or 0)
        # Estimated savings: compaction reduces S3 API calls, archival reduces storage cost
        storage_savings = gb_archived * (0.023 - 0.0025) * 30   # S3 Standard vs Intelligent-Tiering
        estimated_api_savings = gb_compacted * 0.001              # rough S3 API reduction
        net_savings = storage_savings + estimated_api_savings - athena_cost
        col_r1.metric("Athena Cost (30d)", f"${athena_cost:.4f}")
        col_r2.metric("Est. Storage Savings (30d)", f"${storage_savings:.4f}")
        col_r3.metric("Net ROI (30d)", f"${net_savings:.4f}",
                      delta="positive" if net_savings > 0 else "negative")
        st.caption(
            "Savings estimate: archival storage reduction (S3 Standard → Intelligent-Tiering) "
            "+ compaction API reduction. Cost Explorer integration is Phase 2."
        )
except Exception as roi_e:
    col_r1.metric("Athena Cost", "—")
    col_r2.metric("Savings Est.", "—")
    col_r3.metric("Net ROI", "—")
    st.caption(f"ROI data requires Athena: {roi_e}")

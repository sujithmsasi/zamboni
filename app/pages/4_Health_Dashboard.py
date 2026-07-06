"""
Zamboni — Health Dashboard
Full real-time fleet health analysis.
Deep dive into snapshot counts, small files, coverage, failures.
Always fresh — no caching (use the home snapshot for daily summary).
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pandas as pd
import plotly.express as px
import streamlit as st

from app.components.athena_runner import cached_read_sql
from app.components.auth import check_login
from app.components.filters import domain_filter, environment_filter, layer_filter, tier_filter
from app.components.grid_utils import render_grid
from app.components.header import render as render_header
from app.components.kpi_cards import format_count, render_kpi_row
from app.components.sidebar import render as render_sidebar
from config.settings import EXECUTION_LOG_TABLE, STREAM_REGISTRY_TABLE
from engine.core.audit import AuditAction, AuditEvent, audit  # noqa: F401
from engine.core.governance import dual_optimizer_report, fleet_conflict_summary

st.set_page_config(page_title="Zamboni — Health Dashboard", page_icon="📊", layout="wide")
check_login()
render_sidebar()
st.session_state["_current_page"] = "health"
render_header(page_title="Health Dashboard", page_icon="💚")

# ── Filters ───────────────────────────────────────────────────────────────────
col1, col2, col3, col4 = st.columns(4)
with col1:
    sel_domain = domain_filter(key="hd_domain")
with col2:
    sel_layer  = layer_filter(key="hd_layer")
with col3:
    sel_tier   = tier_filter(key="hd_tier")
with col4:
    sel_env    = environment_filter(key="hd_env")

st.divider()

# ── Fleet Coverage KPIs ───────────────────────────────────────────────────────
conditions = [f"environment = '{sel_env}'"]
if sel_domain:
    conditions.append(f"domain = '{sel_domain}'")
if sel_layer:
    conditions.append(f"layer = '{sel_layer}'")
if sel_tier:
    conditions.append(f"tier = '{sel_tier}'")
where = "WHERE " + " AND ".join(conditions)

kpi_sql = f"""
    SELECT
        COUNT(*)                                                AS total,
        SUM(CASE WHEN hk_enabled = true THEN 1 ELSE 0 END)     AS enabled,
        SUM(CASE WHEN table_format = 'iceberg' THEN 1 ELSE 0 END) AS iceberg,
        SUM(CASE WHEN dry_run_until >= CURRENT_DATE THEN 1 ELSE 0 END) AS in_dry_run
    FROM {STREAM_REGISTRY_TABLE}
    {where}
"""

with st.spinner("Loading fleet metrics..."):
    try:
        kpi_df = cached_read_sql(kpi_sql)
        row = kpi_df.iloc[0]
        total    = int(row.get("total") or 0)
        enabled  = int(row.get("enabled") or 0)
        iceberg  = int(row.get("iceberg") or 0)
        dry_run  = int(row.get("in_dry_run") or 0)
        pct      = round(enabled / total * 100, 1) if total > 0 else 0

        render_kpi_row([
            {"label": "Total Registered",  "value": format_count(total)},
            {"label": "HK Enabled",        "value": format_count(enabled),  "delta": f"{pct}%"},
            {"label": "Iceberg Tables",     "value": format_count(iceberg)},
            {"label": "In Dry-Run",         "value": format_count(dry_run),
             "help": "Tables that evaluate but don't execute HK yet"},
        ])
    except Exception as e:
        st.error(f"KPI query failed: {e}")

st.divider()

# ── Two Column Layout ──────────────────────────────────────────────────────────
col_left, col_right = st.columns(2)

# Coverage by Domain
with col_left:
    st.subheader("Fleet Coverage by Domain")
    coverage_sql = f"""
        SELECT
            domain,
            COUNT(*)                                                AS total,
            SUM(CASE WHEN hk_enabled = true THEN 1 ELSE 0 END)     AS enabled,
            ROUND(SUM(CASE WHEN hk_enabled = true THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 1) AS pct_enabled
        FROM {STREAM_REGISTRY_TABLE}
        WHERE environment = '{sel_env}'
        {('AND layer = ' + chr(39) + sel_layer + chr(39)) if sel_layer else ''}
        GROUP BY domain
        ORDER BY pct_enabled DESC
    """
    try:
        cov_df = cached_read_sql(coverage_sql)
        if not cov_df.empty:
            fig = px.bar(
                cov_df, x="domain", y="pct_enabled",
                color="pct_enabled",
                color_continuous_scale=["#ef4444","#f97316","#22c55e"],
                labels={"pct_enabled": "HK Enabled %", "domain": "Domain"},
                range_y=[0, 100],
            )
            fig.update_layout(showlegend=False, height=300, margin=dict(t=20, b=20))
            st.plotly_chart(fig, use_container_width=True)
    except Exception as e:
        st.error(f"Coverage chart failed: {e}")

# Recent Failures
with col_right:
    st.subheader("Failures — Last 7 Days")
    fail_sql = f"""
        SELECT
            table_fqn, domain, layer, tier,
            COUNT(*)        AS failure_count,
            MAX(started_at) AS last_failure
        FROM {EXECUTION_LOG_TABLE}
        WHERE status = 'FAILURE'
          AND execution_date >= CURRENT_DATE - INTERVAL '7' DAY
          {('AND domain = ' + chr(39) + sel_domain + chr(39)) if sel_domain else ''}
        GROUP BY table_fqn, domain, layer, tier
        ORDER BY failure_count DESC
        LIMIT 15
    """
    try:
        fail_df = cached_read_sql(fail_sql)
        if fail_df.empty:
            st.success("✅ No failures in the last 7 days!")
        else:
            st.dataframe(fail_df, use_container_width=True, hide_index=True, height=300)
    except Exception as e:
        st.error(f"Failures query failed: {e}")

st.divider()

# ── Execution Trend Chart ──────────────────────────────────────────────────────
st.subheader("Execution Trend — Last 14 Days")
trend_sql = f"""
    SELECT
        execution_date,
        status,
        COUNT(*) AS count
    FROM {EXECUTION_LOG_TABLE}
    WHERE execution_date >= CURRENT_DATE - INTERVAL '14' DAY
      AND engine = 'hk'
    GROUP BY execution_date, status
    ORDER BY execution_date
"""
try:
    trend_df = cached_read_sql(trend_sql)
    if not trend_df.empty:
        fig = px.bar(
            trend_df, x="execution_date", y="count",
            color="status",
            color_discrete_map={
                "SUCCESS": "#22c55e",
                "FAILURE": "#ef4444",
                "SKIPPED": "#f59e0b",
                "DRY_RUN": "#6366f1",
            },
            labels={"count": "Operations", "execution_date": "Date"},
        )
        fig.update_layout(height=280, margin=dict(t=20, b=20))
        st.plotly_chart(fig, use_container_width=True)
except Exception as e:
    st.error(f"Trend chart failed: {e}")

# ── Tables Needing Compaction ──────────────────────────────────────────────────
st.subheader("Tables Needing Compaction (Not Housekept in 14 Days)")
never_sql = f"""
    SELECT
        r.table_fqn, r.domain, r.layer, r.tier,
        MAX(l.completed_at) AS last_hk
    FROM {STREAM_REGISTRY_TABLE} r
    LEFT JOIN {EXECUTION_LOG_TABLE} l
        ON  r.table_fqn = l.table_fqn
        AND l.status = 'SUCCESS'
        AND l.execution_date >= CURRENT_DATE - INTERVAL '14' DAY
    {where.replace('WHERE', 'WHERE r.')}
      AND r.hk_enabled   = true
      AND r.table_format = 'iceberg'
    GROUP BY r.table_fqn, r.domain, r.layer, r.tier
    HAVING MAX(l.completed_at) IS NULL
    ORDER BY r.tier, r.domain
    LIMIT 50
"""
try:
    never_df = cached_read_sql(never_sql)
    if never_df.empty:
        st.success("✅ All enabled tables have been housekept in the last 14 days.")
    else:
        st.warning(f"{len(never_df)} tables have not been housekept in the last 14 days")
        st.dataframe(never_df, use_container_width=True, hide_index=True, height=300)
except Exception as e:
    st.error(f"Query failed: {e}")

st.divider()

# ── 🛡️ Maintenance Governance (Workstream A / Phase 1c) ───────────────────────
st.subheader("🛡️ Maintenance Governance")
st.caption(
    "Dual-Optimizer Risk Report — tables where Zamboni HK is enabled AND an "
    "AWS Glue table optimizer (compaction / retention / orphan-file deletion) "
    "is also active on the same table. Gate 0 (contracts.md §4) refuses to "
    "run Zamboni maintenance on these until the conflict clears or a "
    "time-boxed override is granted."
)

if st.session_state.get("gov_rescan_flash"):
    st.success(st.session_state.pop("gov_rescan_flash"))

try:
    gov_summary = fleet_conflict_summary()
    render_kpi_row([
        {"label": "HK-Enabled Tables", "value": format_count(gov_summary["total"])},
        {"label": "Optimizer-Scanned", "value": format_count(gov_summary["scanned"])},
        {"label": "⚠️ Conflicted",     "value": format_count(gov_summary["conflicted"]),
         "help": "hk_enabled AND at least one AWS Glue optimizer type is active"},
        {"label": "Stale Cache",       "value": format_count(gov_summary["stale_cache"]),
         "help": "aws_opt_checked_at older than CONFLICT_CACHE_TTL_HOURS"},
        {"label": "Overridden",        "value": format_count(gov_summary["overridden"]),
         "help": "gate0_override_until is in the future"},
    ])
except Exception as e:
    st.error(f"Governance summary failed: {e}")

gov_col1, gov_col2 = st.columns([3, 1])
with gov_col1:
    gov_domain = domain_filter(key="gov_domain")
with gov_col2:
    st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
    if st.button("🔄 Rescan conflicts", key="gov_rescan_btn", use_container_width=True):
        from engine.core.conflict_detector import scan_fleet
        with st.spinner("Scanning fleet for AWS Glue optimizer conflicts..."):
            scan_result = scan_fleet()
        cached_read_sql.clear()
        st.session_state["gov_rescan_flash"] = (
            f"✅ Scanned {scan_result['scanned']} table(s), "
            f"found {scan_result['conflicts']} conflict(s)."
        )
        st.rerun()

try:
    gov_report = dual_optimizer_report(page=1, size=250, domain=gov_domain or None)
    gov_df = pd.DataFrame(gov_report["data"])
    if gov_df.empty:
        st.success("✅ No dual-optimizer conflicts detected.")
    else:
        for _col in ("aws_opt_compaction", "aws_opt_retention", "aws_opt_orphan"):
            gov_df[_col] = gov_df[_col].apply(lambda x: "✅" if not pd.isna(x) and x else "")
        render_grid(gov_df, key="gov_grid", caption=f"{gov_report['total']} conflicted table(s)")
        st.download_button(
            "⬇️ Export CSV",
            data=gov_df.to_csv(index=False),
            file_name="zamboni_dual_optimizer_report.csv",
            mime="text/csv",
            key="gov_export_btn",
        )
except Exception as e:
    st.error(f"Dual-optimizer report failed: {e}")

st.markdown("##### Recent Integrity Failures — Last 7 Days")
_fail_int_sql = f"""
    SELECT table_fqn, domain, layer, tier, operation, integrity_status,
           started_at, completed_at, error_message
    FROM {EXECUTION_LOG_TABLE}
    WHERE integrity_status = 'FAILED'
      AND execution_date >= CURRENT_DATE - INTERVAL '7' DAY
    ORDER BY started_at DESC
    LIMIT 50
"""
try:
    fail_int_df = cached_read_sql(_fail_int_sql)
    if fail_int_df.empty:
        st.success("✅ No integrity failures in the last 7 days.")
    else:
        render_grid(fail_int_df, key="gov_integrity_fail_grid")
except Exception as e:
    if "column" in str(e).lower():
        st.info("integrity_status not migrated in this environment yet.")
    else:
        st.error(f"Integrity failures query failed: {e}")

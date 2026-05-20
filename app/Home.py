"""
Zamboni — Home Page
Daily cached snapshot + small live activity zone.
First user of the day triggers snapshot generation. Others read cache.
"""
import sys
from pathlib import Path

# Ensure project root is on sys.path so 'app.*' imports resolve
# regardless of which directory Streamlit is launched from.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import pandas as pd
import streamlit as st

from app.components.athena_runner import cached_read_sql
from app.components.auth import check_login, current_user
from app.components.header import render as render_header
from app.components.home_snapshot import get_or_generate
from app.components.kpi_cards import format_bytes, format_count, render_kpi_row
from app.components.sidebar import render as render_sidebar
from app.components.status_badge import status as status_badge
from config.settings import EXECUTION_LOG_TABLE

st.set_page_config(page_title="Zamboni — Home", page_icon="🏠", layout="wide")

if not check_login():
    st.stop()
render_sidebar()
st.session_state["_current_page"] = "home"
render_header(page_title="Home", page_icon="🏠")


# ── Header ────────────────────────────────────────────────────────────────────
st.markdown("# 🏠 Home")
st.markdown(f"Welcome back, **{current_user()}** 👋")

# ── Snapshot zone ─────────────────────────────────────────────────────────────
col_a, col_b = st.columns([4, 1])

with col_b:
    refresh = st.button("🔄 Refresh snapshot", use_container_width=True)

snapshot, source = get_or_generate(force_refresh=refresh, generated_by=current_user())

with col_a:
    generated_at = snapshot.get("generated_at", "")
    if source == "cached":
        st.caption(f"📊 Snapshot: {generated_at[:10]} — click Refresh for today's data")
    else:
        st.success(f"✨ Snapshot generated just now ({generated_at[:19]})")

st.markdown("---")


# ── KPI Cards ─────────────────────────────────────────────────────────────────
kpi = snapshot.get("kpi", {})
render_kpi_row([
    {"label": "📋 Tables Registered",    "value": format_count(kpi.get("total_tables", 0))},
    {"label": "✅ HK Enabled",            "value": format_count(kpi.get("hk_enabled", 0))},
    {"label": "❌ Failures (7d)",         "value": format_count(kpi.get("failures_7d", 0))},
    {"label": "💾 Reclaimed (30d)",       "value": format_bytes(kpi.get("bytes_reclaimed_30d", 0))},
])

st.markdown("---")


# ── Two columns: Fleet Coverage + Recent Failures ────────────────────────────
left, right = st.columns(2)

with left:
    st.subheader("🌐 Fleet Coverage")
    coverage = snapshot.get("fleet_coverage", [])
    if coverage:
        df_cov = pd.DataFrame(coverage)
        df_cov["pct_enabled"] = (
            df_cov.apply(
                lambda r: round(int(r["enabled"]) * 100 / int(r["total"]), 1)
                if int(r["total"]) > 0 else 0,
                axis=1,
            ).astype(str) + "%"
        )
        st.dataframe(
            df_cov[["domain", "layer", "total", "enabled", "in_dry_run", "pct_enabled"]],
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.caption("No coverage data yet.")

with right:
    st.subheader("🚨 Recent Failures (last 7 days)")
    failures = snapshot.get("recent_failures", [])
    if failures:
        df_fail = pd.DataFrame(failures)
        if "started_at" in df_fail.columns:
            df_fail["started_at"] = df_fail["started_at"].astype(str).str[:19]
        st.dataframe(
            df_fail[["table_fqn", "engine", "operation", "started_at"]].head(10),
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.success("✅ No failures in the last 7 days")

st.markdown("---")


# ── Domain Stats ──────────────────────────────────────────────────────────────
st.subheader("🏢 Domain Summary")
domain_stats = snapshot.get("domain_stats", [])
if domain_stats:
    df_dom = pd.DataFrame(domain_stats)
    st.dataframe(df_dom, use_container_width=True, hide_index=True)
else:
    st.caption("No domain stats available.")

st.markdown("---")


# ── Live Activity Zone (always fresh, cheap query) ───────────────────────────
st.subheader("🔴 Live Activity")

@st.cache_data(ttl=30)  # 30-second cache only
def _live_activity():
    sql = f"""
        SELECT
            engine, operation, table_fqn, status, dry_run,
            started_at
        FROM {EXECUTION_LOG_TABLE}
        WHERE execution_date = CURRENT_DATE
        ORDER BY started_at DESC
        LIMIT 5
    """
    try:
        return cached_read_sql(sql)
    except Exception:
        return pd.DataFrame()


live_df = _live_activity()
if not live_df.empty:
    live_df["status"] = live_df["status"].apply(status_badge)
    if "started_at" in live_df.columns:
        live_df["started_at"] = live_df["started_at"].astype(str).str[:19]
    st.dataframe(
        live_df[["started_at", "engine", "operation", "table_fqn", "status"]],
        use_container_width=True,
        hide_index=True,
    )
else:
    st.caption("No engine activity today yet.")

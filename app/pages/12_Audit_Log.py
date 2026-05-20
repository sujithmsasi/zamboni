"""
Zamboni -- Audit Log Viewer
Browse, filter, and export the central audit trail for all platform actions.
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


import streamlit as st

from app.components.auth import check_login
from app.components.header import render as render_header
from app.components.sidebar import render as render_sidebar
from engine.core.audit import AuditAction, get_recent_events

st.set_page_config(
    page_title="Zamboni -- Audit Log",
    page_icon="🔍",
    layout="wide",
)
check_login()
render_sidebar()
render_header(page_title="Audit Log", page_icon="🔍")
st.caption(
    "Complete audit trail of all user and system actions. "
    "Every domain change, table registration, policy edit, HK enable/disable, "
    "query cancel, and lifecycle action is recorded here."
)

# ── Filters ───────────────────────────────────────────────────────────────────
with st.expander("🔎 Filters", expanded=True):
    col1, col2, col3, col4 = st.columns(4)

    with col1:
        days = st.selectbox(
            "Time Range",
            [1, 7, 14, 30, 90],
            index=1,
            format_func=lambda d: f"Last {d} day{'s' if d > 1 else ''}",
        )
    with col2:
        action_filter = st.selectbox(
            "Action Type",
            ["All"] + sorted([
                v for k, v in vars(AuditAction).items()
                if not k.startswith("_")
            ]),
        )
    with col3:
        status_filter = st.multiselect(
            "Status",
            ["SUCCESS", "FAILURE", "DRY_RUN", "REJECTED"],
            default=["SUCCESS", "FAILURE", "REJECTED"],
        )
    with col4:
        actor_filter = st.text_input("Actor (username)", placeholder="all users")

    col5, col6 = st.columns(2)
    with col5:
        domain_filter = st.text_input("Domain", placeholder="all domains")
    with col6:
        target_filter = st.text_input("Table FQN / Target ID", placeholder="all targets")

limit = st.slider("Max rows", 25, 500, 100, 25)

# ── Fetch ─────────────────────────────────────────────────────────────────────
with st.spinner("Loading audit events..."):
    df = get_recent_events(
        limit=limit,
        actor=actor_filter.strip() or None,
        action_type=None if action_filter == "All" else action_filter,
        target_id=target_filter.strip() or None,
        domain=domain_filter.strip() or None,
        days=days,
    )

if df.empty:
    st.info("No audit events found for the selected filters.")
    st.stop()

# Apply status filter client-side
if status_filter and "status" in df.columns:
    df = df[df["status"].isin(status_filter)]

if df.empty:
    st.info("No events match the selected status filter.")
    st.stop()

# ── Summary KPIs ──────────────────────────────────────────────────────────────
total     = len(df)
failures  = int((df["status"] == "FAILURE").sum())  if "status" in df.columns else 0
rejected  = int((df["status"] == "REJECTED").sum()) if "status" in df.columns else 0
live_runs = int((~df["dry_run"]).sum())              if "dry_run" in df.columns else 0

k1, k2, k3, k4 = st.columns(4)
k1.metric("Total Events", f"{total:,}")
k2.metric("Failures",     failures,  delta=None if failures == 0 else f"-{failures}")
k3.metric("Rejected",     rejected)
k4.metric("Live Actions", live_runs)

st.divider()

# ── Table ─────────────────────────────────────────────────────────────────────
# Style status column
def _style_status(val: str) -> str:
    colours = {
        "SUCCESS":  "color: #10b981",
        "FAILURE":  "color: #ef4444; font-weight: bold",
        "DRY_RUN":  "color: #3b82f6",
        "REJECTED": "color: #f59e0b; font-weight: bold",
    }
    return colours.get(str(val), "")

display_cols = [
    c for c in [
        "timestamp", "actor", "action_type", "target_id",
        "domain", "environment", "dry_run", "status",
        "reason", "ticket_number", "error_message",
    ]
    if c in df.columns
]

try:
    styled = df[display_cols].style.applymap(_style_status, subset=["status"])
    st.dataframe(styled, use_container_width=True, hide_index=True, height=450)
except Exception:
    st.dataframe(df[display_cols], use_container_width=True,
                 hide_index=True, height=450)

st.caption(f"Showing {len(df)} of {total} events")

# ── Detail expander ───────────────────────────────────────────────────────────
if "audit_id" in df.columns:
    selected_id = st.selectbox(
        "View detail for event",
        ["-- select --"] + df["audit_id"].tolist(),
    )
    if selected_id != "-- select --":
        row = df[df["audit_id"] == selected_id].iloc[0].to_dict()
        col_a, col_b = st.columns(2)
        with col_a:
            st.markdown("**Before**")
            st.code(row.get("before_value", "") or "(none)", language="json")
        with col_b:
            st.markdown("**After**")
            st.code(row.get("after_value", "") or "(none)", language="json")
        if row.get("error_message"):
            st.error(row["error_message"])

# ── Export ────────────────────────────────────────────────────────────────────
st.divider()
csv_data = df[display_cols].to_csv(index=False).encode("utf-8")
st.download_button(
    "⬇️ Export CSV",
    data=csv_data,
    file_name=f"zamboni_audit_log_{days}d.csv",
    mime="text/csv",
)

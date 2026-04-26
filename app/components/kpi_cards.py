"""
Zamboni — KPI Card Components
Reusable metric card displays for dashboards.
"""
import streamlit as st


def render_kpi_row(metrics: list[dict]) -> None:
    """
    Render a row of KPI cards.

    metrics = [
        {"label": "Total Tables", "value": "30,234", "delta": "+12 today"},
        {"label": "HK Enabled",   "value": "12,891"},
        ...
    ]
    """
    cols = st.columns(len(metrics))
    for col, metric in zip(cols, metrics):
        with col:
            st.metric(
                label=metric["label"],
                value=metric["value"],
                delta=metric.get("delta"),
                help=metric.get("help"),
            )


def format_bytes(bytes_value: int) -> str:
    """Convert bytes to human-readable string."""
    if not bytes_value:
        return "0 B"
    for unit in ["B", "KB", "MB", "GB", "TB", "PB"]:
        if abs(bytes_value) < 1024:
            return f"{bytes_value:.1f} {unit}"
        bytes_value /= 1024
    return f"{bytes_value:.1f} EB"


def format_count(count: int) -> str:
    """Format integer with thousand separators."""
    return f"{int(count):,}"

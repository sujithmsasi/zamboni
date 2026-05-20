"""
Zamboni — Page Header Component
Compact sticky topbar: 48px tall, always visible, zero wasted vertical space.

Two render modes:
  render()         — compact sticky topbar (new default)
  render_classic() — original tall header (kept for reference)

Usage:
  from app.components.header import render as render_header
  render_header(page_title="Table Registration", page_icon="➕")
"""
from pathlib import Path

import streamlit as st

LOGO_PATH = Path("app/assets/da_logo.png")


def render(
    page_title:   str  = "",
    page_icon:    str  = "",
    show_divider: bool = False,
) -> None:
    """
    Compact sticky topbar.
    Logo badge | Page title                    | ENV badge
    48px tall, pinned to top, no divider needed.
    """
    env   = _get_env()
    color = {
        "prod":    "#ef4444",
        "preprod": "#f59e0b",
        "dev":     "#22c55e",
        "test":    "#6366f1",
    }.get(env, "#64748b")

    logo_html = _logo_html()
    title_html = (
        f"<span style='font-size:16px;font-weight:700;"
        f"color:#e2e8f0;letter-spacing:.3px;'>"
        f"{page_icon} {page_title}</span>"
        if page_title else
        "<span style='font-size:16px;font-weight:700;"
        "color:#e2e8f0;'>🧊 Zamboni</span>"
    )

    st.markdown(
        f"""
        <style>
          /* Sticky topbar */
          .zamboni-topbar {{
            position: sticky;
            top: 0;
            z-index: 999;
            background: #0f172a;
            border-bottom: 1px solid #1e293b;
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 0 16px;
            height: 48px;
            margin: -1rem -1rem 1rem -1rem;
          }}
          .zamboni-topbar-left  {{ display:flex; align-items:center; gap:10px; }}
          .zamboni-topbar-right {{ display:flex; align-items:center; gap:8px;  }}
          /* Pull Streamlit page content up snug */
          .main .block-container {{ padding-top: 0.5rem !important; }}
        </style>
        <div class="zamboni-topbar">
          <div class="zamboni-topbar-left">
            {logo_html}
            <span style="color:#475569;font-size:16px;">|</span>
            {title_html}
          </div>
          <div class="zamboni-topbar-right">
            <span style="background:{color};color:white;padding:2px 9px;
                         border-radius:10px;font-size:11px;font-weight:700;
                         letter-spacing:.5px;">{env.upper()}</span>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if show_divider:
        st.divider()


def render_classic(show_divider: bool = True) -> None:
    """Original tall header — kept for reference / A/B compare."""
    col_logo, col_title, col_spacer = st.columns([1.2, 4, 2])

    with col_logo:
        _render_logo_widget(height=52)

    with col_title:
        st.markdown(
            "<h2 style='margin:0;padding:8px 0 0 0;color:#e2e8f0;'>🧊 Zamboni</h2>",
            unsafe_allow_html=True,
        )
        st.caption("Iceberg Table Governance Framework — D&A Platform")

    with col_spacer:
        env   = _get_env()
        color = {"prod":"#ef4444","preprod":"#f59e0b",
                 "dev":"#22c55e","test":"#6366f1"}.get(env,"#64748b")
        st.markdown(
            f"<div style='text-align:right;padding-top:12px;'>"
            f"<span style='background:{color};color:white;padding:3px 10px;"
            f"border-radius:12px;font-size:12px;font-weight:600;'>"
            f"{env.upper()}</span></div>",
            unsafe_allow_html=True,
        )

    if show_divider:
        st.divider()


def render_inline(page_title: str, page_icon: str = "") -> None:
    """Lightweight inline title — kept for back-compat."""
    st.markdown(
        f"<h1 style='color:#e2e8f0;margin-bottom:4px;'>"
        f"{page_icon} {page_title}</h1>",
        unsafe_allow_html=True,
    )


def _logo_html() -> str:
    """Return HTML for the compact logo badge."""
    if LOGO_PATH.exists():
        import base64
        with open(LOGO_PATH, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        return (
            f"<img src='data:image/png;base64,{b64}' "
            f"style='height:28px;width:auto;display:block;' />"
        )
    return (
        "<div style='background:linear-gradient(135deg,#1e40af,#0ea5e9);"
        "border-radius:6px;padding:4px 8px;display:inline-flex;"
        "align-items:center;gap:4px;'>"
        "<span style='color:white;font-size:11px;font-weight:800;"
        "letter-spacing:1.5px;'>D&A</span>"
        "</div>"
    )


def _render_logo_widget(height: int = 52) -> None:
    """Streamlit widget logo for classic mode."""
    if LOGO_PATH.exists():
        st.image(str(LOGO_PATH), width=int(height * 3))
    else:
        st.markdown(
            "<div style='background:linear-gradient(135deg,#1e40af,#0ea5e9);"
            "border-radius:8px;padding:10px 16px;display:inline-block;margin-top:4px;'>"
            "<span style='color:white;font-size:14px;font-weight:800;"
            "letter-spacing:2px;'>D&A</span>"
            "<span style='color:#bfdbfe;font-size:10px;"
            "display:block;letter-spacing:1px;'>PLATFORM</span></div>",
            unsafe_allow_html=True,
        )


def _get_env() -> str:
    try:
        from config.settings import APP_ENV
        return APP_ENV
    except Exception:
        return "dev"

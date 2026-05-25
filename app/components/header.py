"""
Zamboni — Page Header Component

Compact sticky topbar: 48px tall, pinned to top, uses Zamboni logo.
Logo also injected into the Streamlit sidebar above the nav menu.

Usage (all pages):
    from app.components.header import render as render_header
    render_header(page_title="Table Registration", page_icon="➕")

The logo is loaded once as base64 and cached so it doesn't re-read
from disk on every Streamlit rerun.
"""
from __future__ import annotations

import base64
from functools import lru_cache
from pathlib import Path

import streamlit as st

LOGO_PATH = Path(__file__).parent.parent / "assets" / "zamboni_logo.png"


@lru_cache(maxsize=1)
def _logo_b64() -> str:
    """Load logo once and cache as base64 string."""
    if LOGO_PATH.exists():
        return base64.b64encode(LOGO_PATH.read_bytes()).decode()
    return ""


def _logo_img_html(size: int = 32) -> str:
    """Return <img> tag for the Zamboni logo, or fallback badge."""
    b64 = _logo_b64()
    if b64:
        return (
            f"<img src='data:image/png;base64,{b64}' "
            f"style='height:{size}px;width:{size}px;"
            f"object-fit:contain;border-radius:4px;display:block;' />"
        )
    # Fallback badge if logo file missing
    return (
        "<div style='background:linear-gradient(135deg,#1e40af,#0ea5e9);"
        "border-radius:6px;padding:4px 8px;'>"
        "<span style='color:white;font-size:11px;font-weight:800;"
        "letter-spacing:1.5px;'>Z</span></div>"
    )


def render(
    page_title:   str  = "",
    page_icon:    str  = "",
    show_divider: bool = False,
) -> None:
    """
    Render compact sticky topbar + sidebar logo injection.

    Layout:
      [ Logo | page_icon page_title ]       [ ENV badge ]
      ──────────────────────────────────────────────────── (1px border)

    Also injects the Zamboni logo into the Streamlit sidebar above
    the nav menu via CSS/HTML.
    """
    env   = _get_env()
    color = {
        "prod":    "#ef4444",
        "preprod": "#f59e0b",
        "dev":     "#22c55e",
        "test":    "#6366f1",
    }.get(env, "#64748b")

    logo_html = _logo_img_html(size=34)
    b64       = _logo_b64()
    sidebar_logo = (
        f"url('data:image/png;base64,{b64}')"
        if b64 else "none"
    )

    title_text = (
        f"{page_icon}&nbsp;{page_title}"
        if page_title else "Zamboni"
    )

    st.markdown(
        f"""
        <style>
          /* ── Hide itables downsampling link ──────────────────────────── */
          caption a[href*="downsampling"],
          caption a[href*="itables"] {{
            display: none !important;
          }}

          /* ── itables header: filled color + column borders ────────── */
          table.dataTable thead th,
          table.dataTable thead td {{
            background: #1e3a5f !important;
            color: #93c5fd !important;
            font-weight: 700 !important;
            font-size: 11px !important;
            letter-spacing: .4px !important;
            text-transform: uppercase !important;
            border-right: 1px solid #2d5a8e !important;
            border-bottom: 2px solid #3b82f6 !important;
            padding: 6px 8px !important;
          }}
          table.dataTable thead th:last-child,
          table.dataTable thead td:last-child {{
            border-right: none !important;
          }}
          /* Row hover */
          table.dataTable tbody tr:hover > * {{
            background: #1e293b !important;
            color: #e2e8f0 !important;
          }}
          /* Stripe rows */
          table.dataTable.stripe tbody tr.even > * {{
            background: #111827 !important;
          }}
          table.dataTable.stripe tbody tr.odd > * {{
            background: #0f172a !important;
          }}
          /* Cell borders */
          table.dataTable.cell-border tbody td {{
            border-right: 1px solid #1e293b !important;
          }}
          /* DataTables control bar */
          .dataTables_wrapper .dataTables_length,
          .dataTables_wrapper .dataTables_filter,
          .dataTables_wrapper .dataTables_info,
          .dataTables_wrapper .dataTables_paginate {{
            font-size: 12px !important;
            color: #94a3b8 !important;
          }}
          .dataTables_wrapper .dataTables_paginate .paginate_button.current {{
            background: #1e3a5f !important;
            color: #93c5fd !important;
            border: 1px solid #3b82f6 !important;
            border-radius: 4px !important;
          }}
          .dataTables_wrapper .dataTables_paginate .paginate_button:hover {{
            background: #1e293b !important;
            color: #e2e8f0 !important;
            border: 1px solid #334155 !important;
          }}

          /* ── Google Fonts: Inter ──────────────────────────────────── */
          @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap');

          html, body, [class*="css"] {{
            font-family: 'Inter', -apple-system, BlinkMacSystemFont,
                         'Segoe UI', sans-serif !important;
          }}

          /* Monospace for FQNs, SQL, code */
          code, pre, .stCode, [data-testid="stCodeBlock"] {{
            font-family: 'JetBrains Mono', 'Fira Code', monospace !important;
          }}

          /* ── Fixed topbar (sticky across Streamlit scroll container) ── */
          .zamboni-topbar {{
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            z-index: 999999;
            background: #0f172a;
            border-bottom: 1px solid #1e293b;
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 0 20px 0 calc(var(--sidebar-width, 240px) + 20px);
            height: 48px;
          }}

          /* Push page content below the fixed topbar */
          .main .block-container {{
            padding-top: 64px !important;
            max-width: 100% !important;
          }}

          /* Sidebar: push nav down so logo shows above it */
          section[data-testid="stSidebar"] > div:first-child {{
            padding-top: 0 !important;
          }}
          .ztb-left  {{ display:flex;align-items:center;gap:10px; }}
          .ztb-right {{ display:flex;align-items:center;gap:8px;  }}
          .ztb-sep   {{ color:#334155;font-size:18px;margin:0 2px; }}
          .ztb-title {{
            font-size: 15px;
            font-weight: 600;
            color: #e2e8f0;
            letter-spacing: .2px;
          }}



          /* ── Sidebar logo above nav ──────────────────────────────── */
          [data-testid="stSidebarNav"]::before {{
            content: '';
            display: block;
            background-image: {sidebar_logo};
            background-size: contain;
            background-repeat: no-repeat;
            background-position: left center;
            width: 100%;
            height: 80px;
            margin: 8px 0 4px 4px;
          }}

          /* Tighten sidebar nav spacing */
          [data-testid="stSidebarNav"] {{
            padding-top: 0 !important;
          }}
          [data-testid="stSidebarNavItems"] {{
            padding-top: 4px !important;
          }}
        </style>

        <div class="zamboni-topbar">
          <div class="ztb-left">
            {logo_html}
            <span class="ztb-sep">|</span>
            <span class="ztb-title">{title_text}</span>
          </div>
          <div class="ztb-right">
            <span style="background:{color};color:white;padding:2px 10px;
                         border-radius:10px;font-size:11px;font-weight:700;
                         letter-spacing:.6px;text-transform:uppercase;"
                  >{env}</span>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if show_divider:
        st.divider()


def render_inline(page_title: str, page_icon: str = "") -> None:
    """Back-compat stub — no longer needed but kept so old calls don't break."""
    pass


def _get_env() -> str:
    try:
        from config.settings import APP_ENV
        return APP_ENV
    except Exception:
        return "dev"

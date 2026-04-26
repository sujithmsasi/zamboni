"""
Zamboni — Page Header Component
Shows D&A logo + Zamboni branding at the top of every page.
Call render() at the top of each page after check_login().

Logo placement:
  - Top of page: full D&A logo + Zamboni title (prominent)
  - Sidebar: small D&A logo (subtle)

To use your real logo:
  Place file at: app/assets/da_logo.png
  Recommended:   400x80px PNG, transparent background
"""
import streamlit as st
from pathlib import Path

LOGO_PATH       = Path("app/assets/da_logo.png")
LOGO_SMALL_PATH = Path("app/assets/da_logo_small.png")  # optional small version


def render(show_divider: bool = True) -> None:
    """
    Render the D&A + Zamboni header at the top of a page.

    Args:
        show_divider: Whether to show a divider line below the header
    """
    col_logo, col_title, col_spacer = st.columns([1.2, 4, 2])

    with col_logo:
        _render_logo(height=52)

    with col_title:
        st.markdown(
            "<h2 style='margin:0;padding:8px 0 0 0;color:#e2e8f0;'>🧊 Zamboni</h2>",
            unsafe_allow_html=True,
        )
        st.caption("Iceberg Table Governance Framework — D&A Platform")

    with col_spacer:
        # Environment badge top-right
        env = _get_env()
        color = {
            "prod":    "#ef4444",
            "preprod": "#f59e0b",
            "dev":     "#22c55e",
            "test":    "#6366f1",
        }.get(env, "#64748b")

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
    """
    Lightweight header for individual pages — just icon + title.
    Use instead of st.title() for consistent styling.
    """
    st.markdown(
        f"<h1 style='color:#e2e8f0;margin-bottom:4px;'>{page_icon} {page_title}</h1>",
        unsafe_allow_html=True,
    )


def _render_logo(height: int = 52) -> None:
    """Render D&A logo or gradient placeholder."""
    if LOGO_PATH.exists():
        st.image(str(LOGO_PATH), width=int(height * 3))
    else:
        # Gradient placeholder — replace with real logo at app/assets/da_logo.png
        st.markdown(
            f"""
            <div style="
                background: linear-gradient(135deg, #1e40af 0%, #0ea5e9 100%);
                border-radius: 8px;
                padding: 10px 16px;
                display: inline-block;
                margin-top: 4px;
            ">
                <span style="color:white;font-size:14px;font-weight:800;
                             letter-spacing:2px;">D&A</span>
                <span style="color:#bfdbfe;font-size:10px;
                             display:block;letter-spacing:1px;">PLATFORM</span>
            </div>
            """,
            unsafe_allow_html=True,
        )


def _get_env() -> str:
    try:
        from config.settings import APP_ENV
        return APP_ENV
    except Exception:
        return "dev"

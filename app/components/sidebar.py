"""
Zamboni — Sidebar Component
Small D&A logo + nav info + dry-run toggle + logout.
Full-size logo appears in the page header (header.py).
"""
from pathlib import Path

import streamlit as st

from app.components.auth import current_user, logout_button
from config.settings import APP_ENV, AWS_REGION

LOGO_SMALL_PATH = Path("app/assets/da_logo_small.png")
LOGO_PATH       = Path("app/assets/da_logo.png")


def render() -> None:
    with st.sidebar:

        # ── Small D&A logo ────────────────────────────────────────────────────
        if LOGO_SMALL_PATH.exists():
            st.image(str(LOGO_SMALL_PATH), width=120)
        elif LOGO_PATH.exists():
            st.image(str(LOGO_PATH), width=120)
        else:
            # Compact placeholder
            st.markdown(
                """
                <div style="background:linear-gradient(135deg,#1e40af,#0ea5e9);
                            border-radius:6px;padding:6px 10px;margin-bottom:6px;
                            display:inline-block;">
                    <span style="color:white;font-size:11px;font-weight:700;
                                 letter-spacing:1.5px;">D&A</span>
                </div>
                """,
                unsafe_allow_html=True,
            )

        st.markdown("**🧊 Zamboni**")
        st.caption("Iceberg Table Governance")
        st.divider()

        # ── User & Environment ────────────────────────────────────────────────
        env_color = {
            "prod":    "🔴", "preprod": "🟡",
            "dev":     "🟢", "test":    "🟣",
        }.get(APP_ENV, "⚪")

        st.markdown(f"👤 `{current_user()}`")
        st.markdown(f"{env_color} `{APP_ENV.upper()}` · `{AWS_REGION}`")
        st.divider()

        # ── Dry Run Toggle ────────────────────────────────────────────────────
        dry_run = st.toggle(
            "🧪 Dry Run Mode",
            value=st.session_state.get("dry_run_mode", True),
            help="ON = all write actions simulated, no real changes",
        )
        st.session_state.dry_run_mode = dry_run

        if dry_run:
            st.caption("⚠️ Writes are simulated")
        else:
            st.warning("⚡ LIVE MODE")

        st.divider()
        logout_button()


def is_dry_run() -> bool:
    return st.session_state.get("dry_run_mode", True)

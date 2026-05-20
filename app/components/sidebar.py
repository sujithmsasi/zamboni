"""
Zamboni — Sidebar Component
Small D&A logo + nav info + dry-run toggle + logout.
Full-size logo appears in the page header (header.py).
"""

import streamlit as st

from app.components.auth import current_user, logout_button
from config.settings import APP_ENV, AWS_REGION


def render() -> None:
    with st.sidebar:

        # ── Zamboni logo ──────────────────────────────────────────────────────
        from pathlib import Path as _P
        _logo = _P(__file__).parent.parent / "assets" / "zamboni_logo.png"
        if _logo.exists():
            st.image(str(_logo), width=160)
        else:
            st.markdown("**🧊 Zamboni**")
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

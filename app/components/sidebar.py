"""
Zamboni — Sidebar Component
Renders user info, environment, and logout in the sidebar.
Streamlit auto-renders the page navigation from pages/ folder.
"""
import streamlit as st
from config.settings import APP_ENV, AWS_REGION
from app.components.auth import current_user, logout_button


def render() -> None:
    """Render the standard Zamboni sidebar."""
    with st.sidebar:
        st.markdown("### 🧊 Zamboni")
        st.caption("Iceberg Table Governance")
        st.markdown("---")

        st.markdown(f"**User**     : `{current_user()}`")
        st.markdown(f"**Env**      : `{APP_ENV}`")
        st.markdown(f"**Region**   : `{AWS_REGION}`")
        st.markdown("---")

        # Dry run toggle (global session state)
        dry_run = st.toggle(
            "🧪 Dry Run Mode",
            value=st.session_state.get("dry_run_mode", True),
            help="When ON, all actions are simulated without writes",
        )
        st.session_state.dry_run_mode = dry_run

        if dry_run:
            st.caption("⚠️ No writes will be made")
        else:
            st.caption("⚡ Live mode — writes are real")

        st.markdown("---")
        logout_button()


def is_dry_run() -> bool:
    """Helper to read dry_run state from session."""
    return st.session_state.get("dry_run_mode", True)

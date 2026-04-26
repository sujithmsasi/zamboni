"""
Zamboni — Sidebar Component
Renders D&A logo, user info, environment, dry-run toggle, and logout.
Streamlit auto-renders the page navigation from pages/ folder.
"""
import streamlit as st
from config.settings import APP_ENV, AWS_REGION
from app.components.auth import current_user, logout_button


# ── Logo path — replace with actual logo file when available ──────────────────
LOGO_PATH = "app/assets/da_logo.png"   # place your D&A logo here


def render() -> None:
    """Render the standard Zamboni sidebar."""
    with st.sidebar:

        # ── D&A Logo ──────────────────────────────────────────────────────────
        try:
            st.image(LOGO_PATH, use_column_width=True)
        except Exception:
            # Placeholder until real logo is added
            st.markdown(
                """
                <div style="
                    background: linear-gradient(135deg, #1e40af, #0ea5e9);
                    border-radius: 10px;
                    padding: 14px 10px;
                    text-align: center;
                    margin-bottom: 8px;
                ">
                    <span style="color:white;font-size:13px;font-weight:700;
                                 letter-spacing:2px;">D&A PLATFORM</span><br>
                    <span style="color:#bfdbfe;font-size:11px;">Data & Analytics</span>
                </div>
                """,
                unsafe_allow_html=True,
            )

        st.markdown("### 🧊 Zamboni")
        st.caption("Iceberg Table Governance")
        st.markdown("---")

        # ── User & Environment ────────────────────────────────────────────────
        st.markdown(f"👤 **User** : `{current_user()}`")
        st.markdown(f"🌍 **Env**  : `{APP_ENV.upper()}`")
        st.markdown(f"📍 **Region**: `{AWS_REGION}`")
        st.markdown("---")

        # ── Dry Run Toggle ────────────────────────────────────────────────────
        dry_run = st.toggle(
            "🧪 Dry Run Mode",
            value=st.session_state.get("dry_run_mode", True),
            help="When ON, all write actions are simulated — no real changes",
        )
        st.session_state.dry_run_mode = dry_run

        if dry_run:
            st.caption("⚠️ Writes are simulated only")
        else:
            st.warning("⚡ LIVE — writes are real")

        st.markdown("---")
        logout_button()


def is_dry_run() -> bool:
    """Helper — read dry_run state from session."""
    return st.session_state.get("dry_run_mode", True)

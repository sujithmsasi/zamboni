"""
Zamboni — Streamlit Auth (Phase 1: Static Login)
Phase 2 will switch to ALB + Cognito — this module becomes a thin pass-through.
"""
import streamlit as st


def check_login() -> bool:
    """
    Block access until the user logs in.
    Returns True if logged in, False otherwise (and renders login form).
    """
    if "logged_in" not in st.session_state:
        st.session_state.logged_in = False
        st.session_state.username  = None

    if st.session_state.logged_in:
        return True

    _render_login_form()
    return False


def _render_login_form() -> None:
    # Centered logo + branding
    _, mid, _ = st.columns([1, 2, 1])
    with mid:
        import base64 as _b64
        from pathlib import Path as _P
        _logo = _P(__file__).parent.parent / "assets" / "zamboni_logo.png"
        if _logo.exists():
            _b = _b64.b64encode(_logo.read_bytes()).decode()
            st.markdown(
                f"<div style='text-align:center;margin-bottom:8px;'>"
                f"<img src='data:image/png;base64,{_b}' "
                f"style='width:180px;height:auto;' /></div>",
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                "<h1 style='text-align:center;'>🧊 Zamboni</h1>",
                unsafe_allow_html=True,
            )
        st.markdown(
            "<p style='text-align:center;color:#94a3b8;margin-top:-4px;'>"
            "Iceberg Table Governance · AAA Data Analytics</p>",
            unsafe_allow_html=True,
        )
    st.markdown("---")

    col1, col2, col3 = st.columns([1, 2, 1])

    with col2:
        st.markdown("### Sign in")

        username = st.text_input("Username", key="login_username")
        password = st.text_input("Password", type="password", key="login_password")

        if st.button("Login", type="primary", use_container_width=True):
            if _verify(username, password):
                st.session_state.logged_in = True
                st.session_state.username  = username
                st.rerun()
            else:
                st.error("Invalid credentials")

        st.caption("Phase 1 uses static credentials. Phase 2 will switch to corporate SSO via Cognito + ALB.")

    st.stop()


def _verify(username: str, password: str) -> bool:
    """Check username/password against Streamlit secrets."""
    if not username or not password:
        return False

    try:
        valid_password = st.secrets.get("auth", {}).get(username)
    except Exception:
        return False

    return valid_password == password


def logout_button() -> None:
    """Render a logout button in the sidebar."""
    if st.sidebar.button("🚪 Logout", use_container_width=True):
        st.session_state.logged_in = False
        st.session_state.username  = None
        st.rerun()


def current_user() -> str:
    """Return the logged-in username, or 'unknown'."""
    return st.session_state.get("username", "unknown")

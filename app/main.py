"""
Zamboni — Streamlit App Entry Point
Run with:
    streamlit run app/main.py --server.port 8501

The pages/ folder is auto-loaded by Streamlit as a multi-page app.
"""
import streamlit as st

from app.components.auth import check_login
from app.components.sidebar import render as render_sidebar


def main():
    # ── Page config ───────────────────────────────────────────────────────────
    st.set_page_config(
        page_title="Zamboni",
        page_icon="🧊",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    # ── Auth gate ─────────────────────────────────────────────────────────────
    if not check_login():
        return  # check_login() renders the form and st.stop()s

    # ── Sidebar ───────────────────────────────────────────────────────────────
    render_sidebar()

    # ── Default landing redirect ──────────────────────────────────────────────
    # Streamlit auto-routes to pages/0_🏠_Home.py when the user logs in
    # main.py just shows a brief welcome if pages aren't yet structured
    st.markdown("# 🧊 Welcome to Zamboni")
    st.markdown("Use the sidebar navigation to access pages.")
    st.info("Tip: Start with **🏠 Home** for the daily snapshot.")


if __name__ == "__main__":
    main()

"""
Zamboni — Streamlit App Entry Point
Run with:
    streamlit run app/main.py --server.port 8501

Streamlit auto-loads pages/ as a multi-page app.
This file sets global config and auth, then redirects to Home.
"""
import streamlit as st
from app.components.auth import check_login
from app.components.sidebar import render as render_sidebar

st.set_page_config(
    page_title="Zamboni",
    page_icon="🧊",
    layout="wide",
    initial_sidebar_state="expanded",
    menu_items={
        "Get Help":     None,
        "Report a bug": None,
        "About":        "**Zamboni** — Iceberg Table Governance Framework\nD&A Platform",
    },
)

# Auth gate — stops here and shows login if not authenticated
check_login()

# Sidebar
render_sidebar()

# Redirect to Home page (Streamlit multi-page auto-routes via pages/)
st.switch_page("pages/0_Home.py")

"""
Zamboni — Entry Point
Run with: streamlit run app/_main.py
Pages must be in app/pages/ (relative to this file).
The _ prefix hides this file from Streamlit's page navigation.
"""
import streamlit as st
from app.components.auth import check_login
from app.components.sidebar import render as render_sidebar
from app.components.header import render as render_header

st.set_page_config(
    page_title="Zamboni",
    page_icon="🧊",
    layout="wide",
    initial_sidebar_state="expanded",
    menu_items={
        "Get Help":     None,
        "Report a bug": None,
        "About":        "**Zamboni** — Iceberg Table Governance\nD&A Platform",
    },
)

check_login()
render_sidebar()
render_header()

# Relative to app/_main.py — pages/ folder is app/pages/
st.switch_page("pages/0_Home.py")

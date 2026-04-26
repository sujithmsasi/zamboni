"""
Zamboni — Top-level launcher
Run with: streamlit run zamboni_app.py
This file is outside app/ so it never appears in the page navigation.
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

st.switch_page("app/pages/0_Home.py")

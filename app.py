"""
STAM — Spatial Transformation Appraisal Mechanism
Streamlit entry point.

Gauteng Province Capital Budget Decision Support Platform
GT/GDeG/031/2025 | VITAL TERRA / Vastpoint POC
"""

import os
import sys

import streamlit as st

sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv

load_dotenv()

from services.db import Project, init_db, get_session

# Initialise database on first run
init_db()

# Auto-seed demo data if DB is empty (needed for Streamlit Cloud)
_session = get_session()
if _session.query(Project).count() == 0:
    _session.close()
    from data.seed import seed_database
    seed_database()
else:
    _session.close()

st.set_page_config(
    page_title="STAM — Gauteng Capital Budget Appraisal",
    page_icon="🗺️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.image("https://upload.wikimedia.org/wikipedia/commons/thumb/4/4b/Gauteng_province_in_South_Africa.svg/200px-Gauteng_province_in_South_Africa.svg.png",
             width=60)
    st.title("STAM")
    st.caption("Spatial Transformation Appraisal Mechanism")
    st.divider()

    pages = {
        "Dashboard":       "📊",
        "Import Data":     "📥",
        "Map View":        "🗺️",
        "GIS Viewer":      "🌐",
        "Projects":        "📋",
        "Scoring Engine":  "⚡",
        "Query Builder":   "🔍",
        "Reports":         "📄",
    }

    if "page" not in st.session_state:
        st.session_state.page = "Dashboard"

    selection = st.radio(
        "Navigate",
        list(pages.keys()),
        format_func=lambda p: f"{pages[p]}  {p}",
        index=list(pages.keys()).index(st.session_state.page),
        label_visibility="collapsed",
    )
    st.session_state.page = selection

    st.divider()

    # Role selector (demo: visual only)
    role = st.selectbox(
        "Active Role",
        ["Analyst", "Planner", "Administrator", "Executive Viewer"],
        key="user_role",
    )

    st.divider()
    st.caption("🏛️ Gauteng Province")
    st.caption("GT/GDeG/031/2025")
    st.caption("VITAL TERRA / Vastpoint")

# ── Page router ───────────────────────────────────────────────────────────────

if selection == "Dashboard":
    from pages import dashboard
    dashboard.render()

elif selection == "Import Data":
    from pages import data_import
    data_import.render()

elif selection == "Map View":
    from pages import gis_viewer
    gis_viewer.render()

elif selection == "GIS Viewer":
    st.title("🌐 GIS Viewer")
    st.caption("STAM Geoportal — powered by VITAL TERRA")
    st.components.v1.iframe(
        "https://tvapp.terra.group/geoportal/stam/public/",
        height=800,
        scrolling=True,
    )

elif selection == "Projects":
    from pages import projects
    projects.render()

elif selection == "Scoring Engine":
    from pages import scoring
    scoring.render()

elif selection == "Query Builder":
    from pages import queries
    queries.render()

elif selection == "Reports":
    from pages import reports
    reports.render()

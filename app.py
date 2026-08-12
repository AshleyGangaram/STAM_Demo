"""
STAM — Spatial Transformation Appraisal Mechanism
Streamlit entry point.

Gauteng Province Capital Budget Decision Support Platform
GT/GDeG/031/2025 | TERRA VITAL / Vastpoint POC
"""

import os
import sys
from pathlib import Path

import streamlit as st

_APP_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_APP_DIR))

from dotenv import load_dotenv

load_dotenv()

st.set_page_config(
    page_title="STAM — Gauteng Capital Budget Appraisal",
    page_icon="🗺️",
    layout="wide",
    initial_sidebar_state="expanded",
)

from services import auth


# ── Bootstrap ─────────────────────────────────────────────────────────────────
# Cached so it runs once per server process rather than on every rerun —
# create_all() plus the seed queries on each interaction made the app crawl.

@st.cache_resource(show_spinner=False)
def _bootstrap() -> bool:
    from services.db import Project, get_session, init_db

    init_db()

    # Auto-seed demo data if the DB is empty (needed for Streamlit Cloud)
    session = get_session()
    empty = session.query(Project).count() == 0
    session.close()
    if empty:
        from data.seed import seed_database
        seed_database()

    # Accounts and the IRM worklist must exist before anyone can sign in.
    from data.seed import seed_irm_worklist, seed_users
    seed_users()
    seed_irm_worklist()
    return True


_bootstrap()


@st.cache_resource(show_spinner=False)
def _warm_heavy_imports() -> bool:
    """
    Pull in the slow modules (folium, streamlit_folium, pandas) while the login
    form is already on screen.

    Importing pages.location_capture costs ~8 s cold. Paying that after the user
    clicks "Sign in" leaves them staring at an unchanged login page long enough
    to assume the app has hung — so it is paid up front instead, during the
    seconds they spend typing.
    """
    try:
        import pages.location_capture   # noqa: F401
    except Exception:
        pass                            # never let warming break the login
    return True


# ── Authentication gate ───────────────────────────────────────────────────────

if not auth.is_authenticated():
    from pages import login
    login.render()
    _warm_heavy_imports()
    st.stop()

user = auth.current_user()

# ── Sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.image(str(_APP_DIR / "assets" / "logo.png"), width=180)
    st.divider()

    # Analysis pages — the original STAM platform
    analysis_pages = {
        "Dashboard":       "📊",
        "Import Data":     "📥",
        "Map View":        "🗺️",
        "GIS Viewer":      "🌐",
        "Projects":        "📋",
        "Scoring Engine":  "⚡",
        "Query Builder":   "🔍",
        "Reports":         "📄",
        "Decision Support":"📊",
        "Audit Trail":     "📝",
    }
    capture_pages = {"Location Capture": "📍"}
    admin_pages = {"IRM Worklist": "📇", "User Admin": "👥"}

    if user.is_admin:
        pages = {**analysis_pages, **capture_pages, **admin_pages}
    elif user.role == "IRM Capturer":
        # Capturers are external users with one job — they see nothing else.
        pages = dict(capture_pages)
    else:
        pages = dict(analysis_pages)

    if st.session_state.get("page") not in pages:
        st.session_state.page = next(iter(pages))

    if len(pages) == 1:
        # A single-purpose account — a nav control with one option is just noise.
        selection = next(iter(pages))
        st.markdown(f"### {pages[selection]}  {selection}")
    else:
        selection = st.radio(
            "Navigate",
            list(pages.keys()),
            format_func=lambda p: f"{pages[p]}  {p}",
            index=list(pages.keys()).index(st.session_state.page),
            label_visibility="collapsed",
        )
    st.session_state.page = selection

    st.divider()

    # Signed-in identity (drives user_role used by every audit log entry)
    st.markdown(f"**{user.full_name}**")
    st.caption(f"{user.role}" + (f" · {user.organisation}" if user.organisation else ""))
    if st.button("Sign out", use_container_width=True):
        auth.sign_out()
        st.rerun()

    st.divider()
    st.caption("🏛️ Gauteng Province")
    st.caption("GT/GDeG/031/2025")
    st.caption("TERRA VITAL / Vastpoint")

# ── Page router ───────────────────────────────────────────────────────────────
# The spinner matters: the first visit to a map-heavy page imports folium and
# streamlit_folium, which takes seconds. Without visible feedback that reads as
# a frozen app.

with st.spinner(f"Loading {selection}…"):
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
        st.caption("STAM Geoportal — powered by TERRA VITAL")
        st.components.v1.iframe(
            "https://tvapp.terra.group/geoportal/stam/public/",
            height=800,
            scrolling=False,
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
        from pages import reports_v2
        reports_v2.render()

    elif selection == "Decision Support":
        from pages import reports
        reports.render()

    elif selection == "Audit Trail":
        from pages import audit_trail
        audit_trail.render()

    elif selection == "Location Capture":
        from pages import location_capture
        location_capture.render()

    elif selection == "IRM Worklist":
        from pages import irm_worklist
        irm_worklist.render()

    elif selection == "User Admin":
        from pages import user_admin
        user_admin.render()

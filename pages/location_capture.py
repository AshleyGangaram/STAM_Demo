"""
IRM Location Capture — correct a project's location before STAM ingestion.

Pick an IRM project, find the real place by address or landmark, digitise it as a
point, line, or polygon, and record verification status, lifecycle status, and
comments. Exports feed STAM's existing project importer.

Widget blocks live in pages/capture_components.py; this module wires them together.
"""

from __future__ import annotations

import json

import folium
import streamlit as st
from folium.plugins import Draw
from streamlit_folium import st_folium

from pages import capture_components as ui
from services import auth, capture
from services.spatial import (
    CAPTURE_COLOURS,
    CAPTURE_TOOLTIP_PREFIX,
    DRAFT_COLOUR,
    add_captures_layer,
    add_draft_layer,
    add_irm_original_marker,
    make_base_map,
)

MAP_HEIGHT = 620


def render() -> None:
    if not auth.require_role(auth.CAPTURE_ROLES):
        return

    user = auth.current_user()
    user_role = st.session_state.get("user_role", user.role)
    ui.init_state()

    st.title("📍 IRM Location Capture")
    st.caption(
        "Verify and correct National Treasury IRM project locations before they are "
        "ingested by STAM. Search for the site, draw its true extent, and record what "
        "you found."
    )
    ui.show_flash()

    worklist = capture.list_irm_projects()
    irm_project = ui.render_project_selector(worklist)

    project_code = st.session_state[ui.K_PROJECT_CODE]
    project_name = st.session_state[ui.K_PROJECT_NAME]

    st.divider()
    map_column, form_column = st.columns([2, 1], gap="large")

    with map_column:
        ui.render_search_panel()
        map_data = _render_map(project_code, irm_project)

    # Folded in before the form renders, so the drawing lands in the same run.
    # Deliberately no st.rerun() here — rerunning on a map event is what makes
    # the page loop.
    ui.consume_map_events(map_data, CAPTURE_TOOLTIP_PREFIX)

    with form_column:
        editing = (
            capture.get_capture(st.session_state[ui.K_EDITING_ID])
            if st.session_state[ui.K_EDITING_ID] else None
        )
        geometry = _active_geometry(editing)

        ui.render_attribute_form(
            geometry=geometry,
            editing=editing,
            project_code=project_code,
            project_name=project_name,
            irm_project_id=st.session_state[ui.K_IRM_ID],
            username=user.username,
            user_role=user_role,
        )

    _render_tables(project_code, user, user_role)


# ── Map ───────────────────────────────────────────────────────────────────────

def _render_map(project_code: str, irm_project) -> dict:
    """Draw the capture map and return streamlit-folium's interaction payload."""
    existing = capture.captures_for_project(project_code) if project_code else []

    folium_map = make_base_map(
        center=st.session_state[ui.K_VIEW_CENTER],
        zoom=st.session_state[ui.K_VIEW_ZOOM],
        satellite_first=True,
    )

    Draw(
        export=False,
        position="topleft",
        draw_options={
            "marker": True,
            "polyline": {"shapeOptions": {"color": "#1F4E79", "weight": 5}},
            "polygon": {"allowIntersection": False,
                        "shapeOptions": {"color": "#1F4E79", "weight": 3}},
            "circle": False,
            "rectangle": False,
            "circlemarker": False,
        },
        edit_options={"edit": True, "remove": False},
    ).add_to(folium_map)

    if existing:
        add_captures_layer(folium_map, existing)

    if (irm_project is not None
            and irm_project.irm_latitude is not None
            and irm_project.irm_longitude is not None):
        add_irm_original_marker(
            folium_map,
            irm_project.irm_latitude,
            irm_project.irm_longitude,
            label=irm_project.project_name,
        )

    # Repaint the in-progress shape — Leaflet.Draw loses it on every rerun.
    add_draft_layer(folium_map, _draft_geometry(),
                    label=st.session_state[ui.K_PROJECT_CODE] or "New shape")

    _add_legend(folium_map)

    # Base-layer switcher: Satellite / CartoDB Light / OpenStreetMap, plus the
    # overlay toggles. Without this the map is stuck on whichever base opens.
    folium.LayerControl(collapsed=False, position="topright").add_to(folium_map)

    # Only draw and click events are returned. Requesting "center"/"zoom" makes
    # the map post its own position on every settle, which reruns the script,
    # which rebuilds the map, which settles again — an endless refresh.
    return st_folium(
        folium_map,
        key="cap_map",
        use_container_width=True,
        height=MAP_HEIGHT,
        returned_objects=[
            "last_active_drawing",
            "last_object_clicked_tooltip",
        ],
    ) or {}


def _draft_geometry() -> dict | None:
    """The unsaved shape, if one is in progress."""
    return st.session_state.get(ui.K_PENDING_GEOM)


def _add_legend(folium_map) -> None:
    rows = "".join(
        f"<div style='display:flex;align-items:center;margin:3px 0'>"
        f"<span style='background:{colour};display:inline-block;flex-shrink:0;"
        f"width:12px;height:12px;margin-right:6px;border-radius:50%'></span>"
        f"<span style='color:#1a1a1a'>{status}</span></div>"
        for status, colour in CAPTURE_COLOURS.items()
    )
    legend = (
        "<div style='position:fixed;bottom:30px;left:30px;z-index:1000;"
        "background:white;padding:10px;border-radius:8px;border:1px solid #ccc;"
        "font-size:12px;min-width:170px;color:#1a1a1a;"
        "box-shadow:0 2px 6px rgba(0,0,0,0.2)'>"
        "<b style='color:#1a1a1a'>Verification status</b><br>"
        f"{rows}"
        "<div style='display:flex;align-items:center;margin:6px 0 3px;"
        "border-top:1px solid #ddd;padding-top:6px'>"
        f"<span style='background:{DRAFT_COLOUR};display:inline-block;flex-shrink:0;"
        "width:12px;height:12px;margin-right:6px;border-radius:50%'></span>"
        "<span style='color:#1a1a1a'>Unsaved shape</span></div>"
        "<div style='color:#555'>⚑ = original IRM location</div>"
        "</div>"
    )
    folium_map.get_root().html.add_child(folium.Element(legend))


def _active_geometry(editing) -> dict | None:
    """The shape the form is currently working on: a new drawing, or the edited one."""
    pending = st.session_state[ui.K_PENDING_GEOM]
    if pending:
        return pending
    if editing is not None:
        try:
            return json.loads(editing.geometry_geojson or "{}") or None
        except (ValueError, TypeError):
            return None
    return None


# ── Tables ────────────────────────────────────────────────────────────────────

def _render_tables(project_code: str, user, user_role: str) -> None:
    st.divider()

    project_tab, mine_tab, all_tab = st.tabs(
        ["This project", "My captures", "All captures"]
    )

    with project_tab:
        st.subheader(f"Captured locations — {project_code or 'no project selected'}")
        ui.render_project_captures(
            capture.captures_for_project(project_code) if project_code else [],
            user_role,
        )

    with mine_tab:
        mine = capture.list_captures(captured_by=user.username)
        st.subheader(f"Captured by {user.full_name} ({len(mine)})")
        if mine:
            st.dataframe(ui.captures_dataframe(mine),
                         use_container_width=True, hide_index=True)
        else:
            st.caption("You have not captured any locations yet.")
        st.divider()
        ui.render_export_panel(mine)

    with all_tab:
        if not user.is_admin:
            st.info("Only administrators can see and export every user's captures.")
            return
        everything = capture.list_captures()
        st.subheader(f"All captures ({len(everything)})")
        if everything:
            st.dataframe(ui.captures_dataframe(everything),
                         use_container_width=True, hide_index=True)
        else:
            st.caption("No locations have been captured yet.")
        st.divider()
        ui.render_export_panel(everything)
        st.caption(
            "⚠️ On Streamlit Cloud the database is rebuilt on every restart — "
            "export regularly so captured work is not lost."
        )

"""
STAM GIS Viewer — Use Case 5 (20 pts)
Full interactive map with layer control, buffer, heatmap, thematic scoring.
"""

from __future__ import annotations

import json
import os

import folium
import streamlit as st
from streamlit_folium import st_folium

from services.db import Facility, Project, get_session, log_action
from services.spatial import (
    GSDF_COLOURS,
    SCORE_COLOURS,
    add_facilities_layer,
    add_gsdf_layer,
    add_heatmap_layer,
    add_population_layer,
    add_project_buffer,
    add_projects_layer,
    facilities_within_buffer,
    make_base_map,
    nearest_facility,
)

_HERE = os.path.dirname(__file__)
DEMO_DIR = os.path.join(_HERE, "..", "data", "demo")


def render():
    st.title("🗺️ GIS Viewer")
    st.caption(
        "Spatial visualisation and analysis — layer control, buffer analysis, "
        "thematic scoring maps, and service gap heatmaps."
    )

    session = get_session()
    projects = session.query(Project).all()
    facilities = session.query(Facility).all()

    # ── Controls ──────────────────────────────────────────────────────────────
    ctrl_col, map_col = st.columns([1, 4])

    with ctrl_col:
        st.subheader("Layer Controls")

        show_gsdf = st.checkbox("GSDF 2030 Zones", value=True)
        show_pop = st.checkbox("Population Density", value=False)
        show_facilities = st.checkbox("Existing Facilities", value=True)
        show_projects = st.checkbox("Capital Projects", value=True)
        show_heatmap = st.checkbox("Service Gap Heatmap", value=False)

        st.divider()
        st.subheader("Buffer Analysis")
        project_names = {f"[{p.project_id}] {p.name}": p for p in projects}
        buffer_proj = st.selectbox("Buffer around project:", ["None"] + list(project_names.keys()))
        buffer_radius = st.slider("Buffer radius (km)", 1, 20, 5)

        st.divider()
        st.subheader("Map Style")
        map_theme = st.selectbox("Theme", ["CartoDB Light", "OpenStreetMap"])

        st.divider()
        st.subheader("Municipality Filter")
        municipalities = sorted({p.municipality for p in projects if p.municipality})
        selected_muni = st.multiselect("Filter by municipality", municipalities,
                                        default=municipalities)

        st.divider()
        st.subheader("Score Filter")
        min_score = st.slider("Minimum STAM score", 0, 100, 0)
        max_score = st.slider("Maximum STAM score", 0, 100, 100)

    with map_col:
        # Filter projects
        filtered = [
            p for p in projects
            if (not selected_muni or p.municipality in selected_muni)
            and (p.total_score or 0) >= min_score
            and (p.total_score or 0) <= max_score
        ]

        # Build map
        gsdf_path = os.path.join(DEMO_DIR, "gsdf_zones.geojson")
        pop_path = os.path.join(DEMO_DIR, "population.geojson")

        m = make_base_map()

        if show_gsdf:
            m = add_gsdf_layer(m, gsdf_path)
        if show_pop:
            m = add_population_layer(m, pop_path)
        if show_facilities:
            m = add_facilities_layer(m, facilities)
        if show_projects:
            m = add_projects_layer(m, filtered)
        if show_heatmap:
            m = add_heatmap_layer(m, filtered)

        # Buffer
        buffer_results = []
        if buffer_proj != "None":
            bp = project_names[buffer_proj]
            m = add_project_buffer(m, bp.latitude, bp.longitude,
                                    buffer_radius, bp.name)
            # Drop pin for selected project
            folium.Marker(
                location=[bp.latitude, bp.longitude],
                popup=f"<b>{bp.name}</b><br>Buffer: {buffer_radius} km",
                icon=folium.Icon(color="darkblue", icon="star", prefix="fa"),
            ).add_to(m)
            buffer_results = facilities_within_buffer(
                bp.latitude, bp.longitude, buffer_radius, facilities
            )
            log_action("BUFFER_ANALYSIS", "project", bp.project_id,
                       {"radius_km": buffer_radius, "facilities_found": len(buffer_results)},
                       user_role=st.session_state.get("user_role", "Analyst"))

        # GSDF legend
        legend_html = (
            "<div style='position:fixed;bottom:30px;left:30px;z-index:1000;"
            "background:white;padding:10px;border-radius:8px;border:1px solid #ccc;"
            "font-size:12px;min-width:180px;color:#1a1a1a;box-shadow:0 2px 6px rgba(0,0,0,0.2)'>"
            "<b style='color:#1a1a1a'>2030 Classification</b><br>"
        )
        for cls, colour in GSDF_COLOURS.items():
            legend_html += (
                f"<div style='display:flex;align-items:center;margin:3px 0'>"
                f"<span style='background:{colour};display:inline-block;flex-shrink:0;"
                f"width:14px;height:14px;margin-right:6px;border-radius:2px;border:1px solid #ccc'></span>"
                f"<span style='color:#1a1a1a'>{cls}</span></div>"
            )
        legend_html += "<div style='margin:6px 0 3px;border-top:1px solid #ddd;padding-top:4px'><b style='color:#1a1a1a'>Project Score</b></div>"
        for cls, colour in SCORE_COLOURS.items():
            legend_html += (
                f"<div style='display:flex;align-items:center;margin:3px 0'>"
                f"<span style='background:{colour};display:inline-block;flex-shrink:0;"
                f"width:12px;height:12px;margin-right:6px;border-radius:50%'></span>"
                f"<span style='color:#1a1a1a'>{cls}</span></div>"
            )
        legend_html += "</div>"
        m.get_root().html.add_child(folium.Element(legend_html))

        folium.LayerControl(collapsed=False).add_to(m)

        map_data = st_folium(m, use_container_width=True, height=620, returned_objects=["last_object_clicked"])

    # ── Buffer results ────────────────────────────────────────────────────────
    if buffer_proj != "None" and buffer_results:
        st.divider()
        bp = project_names[buffer_proj]
        st.subheader(f"Facilities within {buffer_radius} km of {bp.name}")
        import pandas as pd
        df = pd.DataFrame(buffer_results)
        df["occupancy_pct"] = (df["current_occupancy"] / df["capacity"] * 100).round(1)
        df["status"] = df.apply(
            lambda r: "⚠ OVER CAPACITY" if r["current_occupancy"] > r["capacity"] else "OK",
            axis=1,
        )
        st.dataframe(df[["name", "facility_type", "distance_km",
                          "capacity", "current_occupancy", "occupancy_pct", "status"]],
                     use_container_width=True, hide_index=True)

        population_in_buffer = sum(
            int(wrd.get("population", 0)) * min(buffer_radius / 5.0, 1.0)
            for wrd in _load_population_data()
            if _within_km(
                float(wrd.get("lat", -26)), float(wrd.get("lon", 28)),
                bp.latitude, bp.longitude, buffer_radius
            )
        )
        col1, col2, col3 = st.columns(3)
        col1.metric("Facilities Found", len(buffer_results))
        col2.metric("Over Capacity", sum(1 for r in buffer_results
                                          if r["current_occupancy"] > r["capacity"]))
        col3.metric("Est. Population Served", f"~{population_in_buffer:,}")

    # ── Filtered project table ────────────────────────────────────────────────
    st.divider()
    st.subheader(f"Projects in View ({len(filtered)})")
    if filtered:
        import pandas as pd
        rows = []
        for p in sorted(filtered, key=lambda x: -(x.total_score or 0)):
            rows.append({
                "ID": p.project_id,
                "Name": p.name,
                "Score": p.total_score or 0,
                "Classification": p.classification or "Pending",
                "GSDF Zone": p.gsdf_classification or "",
                "Municipality": p.municipality,
                "Budget (ZAR M)": round((p.budget_rands or 0) / 1e6, 1),
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    session.close()


def _load_population_data() -> list[dict]:
    """Load population ward centroids for estimate calculations."""
    pop_path = os.path.join(DEMO_DIR, "population.geojson")
    if not os.path.exists(pop_path):
        return []
    with open(pop_path) as f:
        data = json.load(f)
    result = []
    for feat in data["features"]:
        coords = feat["geometry"]["coordinates"][0]
        lats = [c[1] for c in coords]
        lons = [c[0] for c in coords]
        result.append({
            "lat": sum(lats) / len(lats),
            "lon": sum(lons) / len(lons),
            **feat["properties"],
        })
    return result


def _within_km(lat1, lon1, lat2, lon2, km) -> bool:
    import math
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(a)) <= km

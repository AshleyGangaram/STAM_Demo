"""
Spatial utilities for STAM:
  - Folium map factory with STAM layers
  - Buffer / proximity calculations
  - GeoJSON helpers
  - Distance calculations
"""

from __future__ import annotations

import json
import math
import os
from typing import Any

import folium
from folium.plugins import HeatMap, MeasureControl, MiniMap

# Colour scheme for GSDF classifications
GSDF_COLOURS: dict[str, str] = {
    "Priority":    "#1a7431",   # dark green
    "Accommodate": "#f5a623",   # amber
    "Discourage":  "#d0021b",   # red
    "Outside":     "#9b9b9b",   # grey
}

# Score-based project colours
SCORE_COLOURS: dict[str, str] = {
    "Priority Now":        "#1a7431",
    "Priority Next Cycle": "#4caf50",
    "Conditional":         "#f5a623",
    "Not Recommended":     "#d0021b",
}

# Facility type icons (Folium FontAwesome)
FACILITY_ICONS: dict[str, tuple[str, str]] = {
    "clinic":        ("plus-square", "red"),
    "school":        ("graduation-cap", "blue"),
    "library":       ("book", "purple"),
    "community_hall":("home", "orange"),
    "road":          ("road", "gray"),
}

# Project type icons
PROJECT_ICONS: dict[str, str] = {
    "clinic":     "plus-square",
    "school":     "graduation-cap",
    "library":    "book",
    "community":  "home",
    "housing":    "building",
    "road":       "road",
    "commercial": "briefcase",
    "industrial": "industry",
}

# IRM location-capture colours, keyed by verification status
CAPTURE_COLOURS: dict[str, str] = {
    "Verified":         "#1a7431",   # dark green
    "Corrected":        "#1F4E79",   # STAM blue
    "Approximate":      "#f5a623",   # amber
    "Unable to Locate": "#d0021b",   # red
    "Duplicate Record": "#7b1fa2",   # purple
}

GAUTENG_CENTER = [-26.05, 28.05]
GAUTENG_ZOOM = 9

# Tooltips on captured shapes carry this prefix so a map click can be resolved
# back to a LocationCapture row via st_folium's last_object_clicked_tooltip.
CAPTURE_TOOLTIP_PREFIX = "capture:"


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


def _satellite_layer() -> folium.TileLayer:
    return folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        attr="Esri",
        name="Satellite",
        overlay=False,
        control=True,
        max_zoom=21,
    )


def make_base_map(center: list | None = None, zoom: int = GAUTENG_ZOOM,
                  satellite_first: bool = False) -> folium.Map:
    """
    Return a Folium map centred on Gauteng with standard controls.

    Folium shows whichever base layer is added first, so `satellite_first=True`
    opens on imagery — which is what makes a site identifiable when digitising.
    """
    m = folium.Map(
        location=center or GAUTENG_CENTER,
        zoom_start=zoom,
        tiles=None,
        max_zoom=21,
    )
    # Base layers — order decides the default
    if satellite_first:
        _satellite_layer().add_to(m)
    folium.TileLayer("CartoDB positron", name="CartoDB Light", control=True).add_to(m)
    folium.TileLayer("OpenStreetMap", name="OpenStreetMap", control=True,
                     max_zoom=19).add_to(m)
    if not satellite_first:
        _satellite_layer().add_to(m)

    # Controls
    m.add_child(MeasureControl(position="topright"))
    m.add_child(MiniMap(toggle_display=True, position="bottomright"))
    return m


def add_gsdf_layer(m: folium.Map, gsdf_geojson_path: str) -> folium.Map:
    """Add GSDF 2030 zone polygons to the map."""
    if not os.path.exists(gsdf_geojson_path):
        return m
    with open(gsdf_geojson_path) as f:
        data = json.load(f)

    def style_fn(feature):
        cls = feature["properties"].get("classification", "Outside")
        colour = GSDF_COLOURS.get(cls, "#9b9b9b")
        return {"fillColor": colour, "color": colour, "weight": 1.5,
                "fillOpacity": 0.25, "opacity": 0.8}

    layer = folium.GeoJson(
        data,
        name="GSDF 2030 Zones",
        style_function=style_fn,
        tooltip=folium.GeoJsonTooltip(
            fields=["zone_name", "classification", "municipality"],
            aliases=["Zone:", "Classification:", "Municipality:"],
        ),
    )
    layer.add_to(m)
    return m


def add_population_layer(m: folium.Map, pop_geojson_path: str) -> folium.Map:
    """Add population density choropleth to the map."""
    if not os.path.exists(pop_geojson_path):
        return m
    with open(pop_geojson_path) as f:
        data = json.load(f)

    max_density = max(
        (f["properties"].get("density_per_km2", 0) for f in data["features"]), default=1
    )

    def style_fn(feature):
        d = feature["properties"].get("density_per_km2", 0)
        opacity = min(d / max_density * 0.6 + 0.05, 0.7)
        return {"fillColor": "#e63946", "color": "#e63946",
                "weight": 0.5, "fillOpacity": opacity, "opacity": 0.4}

    layer = folium.GeoJson(
        data,
        name="Population Density",
        style_function=style_fn,
        tooltip=folium.GeoJsonTooltip(
            fields=["ward_name", "population", "density_per_km2", "municipality"],
            aliases=["Ward:", "Population:", "Density (per km²):", "Municipality:"],
        ),
    )
    layer.add_to(m)
    return m


def add_facilities_layer(m: folium.Map, facilities: list[Any]) -> folium.Map:
    """Add existing facility markers to the map."""
    group = folium.FeatureGroup(name="Existing Facilities", show=True)
    for f in facilities:
        lat = getattr(f, "latitude", None)
        lon = getattr(f, "longitude", None)
        if lat is None or lon is None:
            continue
        ft = getattr(f, "facility_type", "clinic")
        icon_name, icon_colour = FACILITY_ICONS.get(ft, ("info-sign", "blue"))
        cap = getattr(f, "capacity", 0) or 0
        occ = getattr(f, "current_occupancy", 0) or 0
        occ_pct = round(occ / cap * 100, 1) if cap > 0 else 0
        status = "OVER CAPACITY" if occ > cap else f"{occ_pct}% full"
        popup_html = (
            f"<b>{getattr(f, 'name', '')}</b><br>"
            f"Type: {ft}<br>"
            f"Capacity: {cap}<br>"
            f"Occupancy: {occ} ({status})<br>"
            f"Municipality: {getattr(f, 'municipality', '')}"
        )
        folium.Marker(
            location=[lat, lon],
            popup=folium.Popup(popup_html, max_width=250),
            tooltip=getattr(f, "name", ""),
            icon=folium.Icon(icon=icon_name, prefix="fa", color=icon_colour),
        ).add_to(group)
    group.add_to(m)
    return m


def add_projects_layer(m: folium.Map, projects: list[Any],
                       highlight_ids: list[str] | None = None) -> folium.Map:
    """Add project markers coloured by STAM classification."""
    group = folium.FeatureGroup(name="Capital Projects", show=True)
    for p in projects:
        lat = getattr(p, "latitude", None)
        lon = getattr(p, "longitude", None)
        if lat is None or lon is None:
            continue
        cls = getattr(p, "classification", "Not Recommended")
        colour = SCORE_COLOURS.get(cls, "#9b9b9b")
        pid = getattr(p, "project_id", "")
        score = getattr(p, "total_score", 0)
        budget = getattr(p, "budget_rands", 0) or 0
        is_highlight = highlight_ids and pid in highlight_ids

        popup_html = (
            f"<b>[{pid}] {getattr(p, 'name', '')}</b><br>"
            f"Department: {getattr(p, 'department', '')}<br>"
            f"Type: {getattr(p, 'project_type', '')}<br>"
            f"STAM Score: <b>{score}/100</b><br>"
            f"Classification: <b style='color:{colour}'>{cls}</b><br>"
            f"Budget: R{budget:,.0f}<br>"
            f"GSDF Zone: {getattr(p, 'gsdf_classification', '')}<br>"
            f"Readiness: {getattr(p, 'readiness_status', '')}"
        )
        icon_name = PROJECT_ICONS.get(getattr(p, "project_type", ""), "map-marker")
        radius = 16 if is_highlight else 12

        folium.CircleMarker(
            location=[lat, lon],
            radius=radius,
            color=colour,
            fill=True,
            fill_color=colour,
            fill_opacity=0.8,
            weight=3 if is_highlight else 1.5,
            popup=folium.Popup(popup_html, max_width=300),
            tooltip=f"[{pid}] {getattr(p, 'name', '')} — {score}/100",
        ).add_to(group)

    group.add_to(m)
    return m


def add_project_buffer(m: folium.Map, lat: float, lon: float,
                       radius_km: float = 5.0, label: str = "") -> folium.Map:
    """Draw a buffer circle around a project site."""
    radius_m = radius_km * 1000
    folium.Circle(
        location=[lat, lon],
        radius=radius_m,
        color="#1F4E79",
        fill=True,
        fill_color="#1F4E79",
        fill_opacity=0.1,
        weight=2,
        dash_array="8 4",
        tooltip=f"{radius_km} km buffer{': ' + label if label else ''}",
    ).add_to(m)
    return m


def add_captures_layer(m: folium.Map, captures: list[Any],
                       group_name: str = "Captured Locations") -> folium.Map:
    """
    Draw saved IRM location captures, coloured by verification status.

    The tooltip is the capture id prefixed with CAPTURE_TOOLTIP_PREFIX so that a
    click can be resolved back to a record via st_folium's
    `last_object_clicked_tooltip`.
    """
    group = folium.FeatureGroup(name=group_name, show=True)

    for c in captures:
        raw = getattr(c, "geometry_geojson", "") or ""
        try:
            geometry = json.loads(raw)
        except (ValueError, TypeError):
            continue
        if not geometry.get("coordinates"):
            continue

        status = getattr(c, "verification_status", "") or ""
        colour = CAPTURE_COLOURS.get(status, "#1F4E79")
        capture_id = getattr(c, "id", "")
        code = getattr(c, "project_code", "")
        name = getattr(c, "name", None) or getattr(c, "project_name", "")
        primary = " ★ primary" if getattr(c, "is_primary", 0) else ""
        tooltip = f"{CAPTURE_TOOLTIP_PREFIX}{capture_id}"

        measure = ""
        if getattr(c, "length_m", None):
            measure = f"Length: {c.length_m:,.0f} m<br>"
        elif getattr(c, "area_m2", None):
            measure = f"Area: {c.area_m2:,.0f} m² ({c.area_m2 / 10_000:,.2f} ha)<br>"

        popup_html = (
            f"<b>[{code}] {name}</b>{primary}<br>"
            f"Verification: <b style='color:{colour}'>{status}</b><br>"
            f"Lifecycle: {getattr(c, 'lifecycle_status', '')}<br>"
            f"{measure}"
            f"Captured by: {getattr(c, 'captured_by', '')}<br>"
            f"<i>Click the shape to edit it.</i>"
        )
        popup = folium.Popup(popup_html, max_width=300)

        if geometry.get("type") == "Point":
            lon, lat = geometry["coordinates"][0], geometry["coordinates"][1]
            folium.CircleMarker(
                location=[lat, lon],
                radius=10 if primary else 8,
                color=colour,
                fill=True,
                fill_color=colour,
                fill_opacity=0.85,
                weight=3 if primary else 2,
                popup=popup,
                tooltip=tooltip,
            ).add_to(group)
        else:
            folium.GeoJson(
                {"type": "Feature", "geometry": geometry, "properties": {}},
                style_function=lambda _f, colour=colour, primary=primary: {
                    "color": colour,
                    "weight": 5 if primary else 3,
                    "fillColor": colour,
                    "fillOpacity": 0.25,
                    "opacity": 0.9,
                },
                popup=popup,
                tooltip=tooltip,
            ).add_to(group)

    group.add_to(m)
    return m


DRAFT_COLOUR = "#ff6f00"   # amber — an unsaved shape


def add_draft_layer(m: folium.Map, geometry: dict | None,
                    label: str = "Unsaved shape") -> folium.Map:
    """
    Redraw the shape the user is currently working on.

    Leaflet.Draw keeps a new shape only in the browser, so it vanishes the moment
    Streamlit reruns and rebuilds the map. Painting it back as its own layer is
    what makes a drawn point or polygon stay put until it is saved or cleared.
    """
    if not geometry or not geometry.get("coordinates"):
        return m

    group = folium.FeatureGroup(name="Unsaved shape", show=True)
    tooltip = f"{label} — not yet saved"

    if geometry.get("type") == "Point":
        lon, lat = geometry["coordinates"][0], geometry["coordinates"][1]
        folium.CircleMarker(
            location=[lat, lon],
            radius=11,
            color=DRAFT_COLOUR,
            fill=True,
            fill_color=DRAFT_COLOUR,
            fill_opacity=0.9,
            weight=3,
            tooltip=tooltip,
        ).add_to(group)
    else:
        folium.GeoJson(
            {"type": "Feature", "geometry": geometry, "properties": {}},
            style_function=lambda _f: {
                "color": DRAFT_COLOUR,
                "weight": 4,
                "fillColor": DRAFT_COLOUR,
                "fillOpacity": 0.20,
                "opacity": 0.95,
                "dashArray": "8 5",
            },
            tooltip=tooltip,
        ).add_to(group)

    group.add_to(m)
    return m


def add_irm_original_marker(m: folium.Map, lat: float, lon: float,
                            label: str = "") -> folium.Map:
    """Mark the original (uncorrected) IRM coordinate so the user sees the delta."""
    folium.Marker(
        location=[lat, lon],
        popup=folium.Popup(
            f"<b>Original IRM location</b><br>{label}<br>"
            f"{lat:.5f}, {lon:.5f}<br><i>This is the value being corrected.</i>",
            max_width=260,
        ),
        tooltip="Original IRM location",
        icon=folium.Icon(color="lightgray", icon="flag", prefix="fa"),
    ).add_to(m)
    return m


def add_heatmap_layer(m: folium.Map, projects: list[Any]) -> folium.Map:
    """Add a heatmap of underserved / high-gap areas based on low STAM scores."""
    # Invert score for heatmap: low score = high need signal
    heat_data = []
    for p in projects:
        lat = getattr(p, "latitude", None)
        lon = getattr(p, "longitude", None)
        score = getattr(p, "total_score", 50) or 50
        if lat and lon:
            # Weight = inverse score (lower score = brighter heat for gap areas)
            weight = max(100 - score, 5) / 100.0
            heat_data.append([lat, lon, weight])

    if heat_data:
        HeatMap(heat_data, name="Service Gap Heatmap",
                min_opacity=0.3, radius=35, blur=25).add_to(m)
    return m


def facilities_within_buffer(lat: float, lon: float, radius_km: float,
                              facilities: list[Any]) -> list[dict]:
    """Return facilities within radius_km of the given point."""
    results = []
    for f in facilities:
        flat = getattr(f, "latitude", None)
        flon = getattr(f, "longitude", None)
        if flat is None or flon is None:
            continue
        dist = _haversine_km(lat, lon, flat, flon)
        if dist <= radius_km:
            results.append({
                "name": getattr(f, "name", ""),
                "facility_type": getattr(f, "facility_type", ""),
                "distance_km": round(dist, 2),
                "capacity": getattr(f, "capacity", 0),
                "current_occupancy": getattr(f, "current_occupancy", 0),
            })
    results.sort(key=lambda x: x["distance_km"])
    return results


def nearest_facility(lat: float, lon: float, facility_type: str,
                     facilities: list[Any]) -> dict | None:
    """Return the nearest facility of a given type."""
    same = [f for f in facilities if getattr(f, "facility_type", "") == facility_type]
    if not same:
        return None
    nearest = min(same, key=lambda f: _haversine_km(
        lat, lon,
        getattr(f, "latitude", lat),
        getattr(f, "longitude", lon),
    ))
    return {
        "name": getattr(nearest, "name", ""),
        "distance_km": round(_haversine_km(
            lat, lon,
            getattr(nearest, "latitude", lat),
            getattr(nearest, "longitude", lon),
        ), 2),
        "capacity": getattr(nearest, "capacity", 0),
        "current_occupancy": getattr(nearest, "current_occupancy", 0),
    }


def export_map_to_image(m: folium.Map) -> bytes:
    """
    Export a folium map to PNG bytes for PDF embedding.
    Uses a temporary file approach with folium's export capabilities.
    """
    import tempfile
    try:
        # Try using folium's built-in PNG export if available
        # This requires selenium to be installed
        png_data = m._repr_png_()
        if png_data:
            return png_data
    except Exception:
        pass

    try:
        # Fallback: save as HTML and note that it's interactive
        import io
        html_str = m._repr_html_()
        if html_str:
            # For now, return a placeholder indicating map export
            # In production, use headless browser to screenshot
            return b"Map data available (interactive version in web app)"
    except Exception:
        return b"Map export not available"

"""
Map layers for the capture page.

The draft layer is what stops a freshly drawn shape from vanishing: Leaflet.Draw
holds a new shape only in the browser, so it is lost the moment Streamlit reruns
and rebuilds the map. These tests assert the shape is painted back into the map's
own HTML, which is what survives a rerun.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services import capture
from services.spatial import (
    CAPTURE_TOOLTIP_PREFIX,
    DRAFT_COLOUR,
    add_captures_layer,
    add_draft_layer,
    make_base_map,
)

POINT = {"type": "Point", "coordinates": [28.1919, -25.7406]}
LINE = {"type": "LineString", "coordinates": [[28.00, -26.00], [28.01, -26.00]]}
SQUARE = {"type": "Polygon", "coordinates": [[
    [28.00, -26.00], [28.01, -26.00], [28.01, -25.99], [28.00, -25.99], [28.00, -26.00],
]]}


def _html(folium_map) -> str:
    return folium_map.get_root().render()


# ── Draft layer ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("geometry,marker", [
    (POINT, "-25.7406"),
    (LINE, "28.01"),
    (SQUARE, "-25.99"),
])
def test_a_drawn_shape_is_painted_back_onto_the_map(geometry, marker):
    folium_map = make_base_map()
    add_draft_layer(folium_map, geometry)

    html = _html(folium_map)
    assert marker in html, "the drawn shape is missing from the rebuilt map"
    assert DRAFT_COLOUR in html, "the draft styling was not applied"


def test_the_draft_is_visibly_distinct_from_saved_shapes():
    """An unsaved shape must not look like one that is already captured."""
    from services.spatial import CAPTURE_COLOURS
    assert DRAFT_COLOUR not in CAPTURE_COLOURS.values()


def test_an_absent_draft_adds_nothing():
    baseline = _html(make_base_map())
    for empty in (None, {}, {"type": "Point", "coordinates": []}):
        folium_map = make_base_map()
        add_draft_layer(folium_map, empty)
        assert len(_html(folium_map)) == len(baseline)


def test_the_draft_is_labelled_as_unsaved():
    folium_map = make_base_map()
    add_draft_layer(folium_map, SQUARE, label="IRM-2026-0101")
    html = _html(folium_map)
    assert "not yet saved" in html
    assert "IRM-2026-0101" in html


# ── Saved captures layer ──────────────────────────────────────────────────────

def test_saved_shapes_carry_a_resolvable_tooltip(temp_db):
    """The tooltip is how a map click is traced back to a database row."""
    ok, message, capture_id = capture.save_capture(
        project_code="IRM-1", project_name="Test", geometry=SQUARE,
        verification_status="Corrected", lifecycle_status="Design",
        captured_by="tester",
    )
    assert ok, message

    folium_map = make_base_map()
    add_captures_layer(folium_map, capture.list_captures())
    assert f"{CAPTURE_TOOLTIP_PREFIX}{capture_id}" in _html(folium_map)


def test_capture_colour_follows_verification_status(temp_db):
    from services.spatial import CAPTURE_COLOURS
    capture.save_capture(
        project_code="IRM-1", project_name="Test", geometry=SQUARE,
        verification_status="Unable to Locate", lifecycle_status="On Hold",
        captured_by="tester",
    )
    folium_map = make_base_map()
    add_captures_layer(folium_map, capture.list_captures())
    assert CAPTURE_COLOURS["Unable to Locate"] in _html(folium_map)


def test_a_corrupt_geometry_does_not_break_the_map(temp_db):
    ok, _, capture_id = capture.save_capture(
        project_code="IRM-1", project_name="Test", geometry=POINT,
        verification_status="Verified", lifecycle_status="Design",
        captured_by="tester",
    )
    assert ok
    from services.db import LocationCapture, get_session
    session = get_session()
    session.query(LocationCapture).filter(
        LocationCapture.id == capture_id
    ).first().geometry_geojson = "{not json"
    session.commit()
    session.close()

    folium_map = make_base_map()
    add_captures_layer(folium_map, capture.list_captures())   # must not raise
    _html(folium_map)


# ── Base map ──────────────────────────────────────────────────────────────────

def test_capture_map_opens_on_satellite_imagery():
    """Imagery is what makes a site identifiable when digitising."""
    html = _html(make_base_map(satellite_first=True))
    satellite = html.index("World_Imagery")
    positron = html.index("cartocdn")
    assert satellite < positron, "satellite must be added first to be the default"


def test_other_pages_keep_their_original_default():
    html = _html(make_base_map())
    assert html.index("cartocdn") < html.index("World_Imagery")


def test_all_three_base_layers_are_offered():
    """
    Imagery for identifying a site, street maps for reading names.

    Layer names only reach the HTML via the LayerControl — which is precisely why
    a map without one gives the user no way to switch to or from imagery.
    """
    import folium
    folium_map = make_base_map(satellite_first=True)
    folium.LayerControl(collapsed=False).add_to(folium_map)

    html = _html(folium_map)
    for name in ("Satellite", "CartoDB Light", "OpenStreetMap"):
        assert name in html, f"base layer '{name}' is missing"


def test_imagery_zooms_deeper_than_the_street_basemap():
    """Digitising a building needs more zoom than OSM's tiles provide."""
    from services.spatial import _satellite_layer
    assert _satellite_layer().options["max_zoom"] >= 21


def test_the_capture_map_exposes_a_layer_switcher():
    """
    Regression: the capture map shipped without a LayerControl, so the base layer
    could not be changed and imagery could not be toggled off.
    """
    import folium
    folium_map = make_base_map(satellite_first=True)
    add_draft_layer(folium_map, SQUARE)
    folium.LayerControl(collapsed=False, position="topright").add_to(folium_map)

    html = _html(folium_map)
    assert "L.control.layers" in html, "no layer switcher on the map"
    assert "Unsaved shape" in html, "overlays should be toggleable too"

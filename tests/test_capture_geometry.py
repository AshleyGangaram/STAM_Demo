"""Unit tests for services/geometry.py — measurement and validation.

GeoJSON coordinates are [longitude, latitude] throughout.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services import capture, geometry

# Union Buildings, Pretoria
POINT = {"type": "Point", "coordinates": [28.1919, -25.7406]}

# A 0.01° east-west segment at latitude -26 ≈ 1 km
LINE = {"type": "LineString", "coordinates": [[28.00, -26.00], [28.01, -26.00]]}

# A 0.01° × 0.01° box at latitude -26 ≈ 1.0 km × 1.11 km
SQUARE = {"type": "Polygon", "coordinates": [[
    [28.00, -26.00], [28.01, -26.00], [28.01, -25.99], [28.00, -25.99], [28.00, -26.00],
]]}


# ── Centroids ─────────────────────────────────────────────────────────────────

def test_point_centroid_is_the_point():
    m = geometry.geometry_metrics(POINT)
    assert m["centroid_lat"] == pytest.approx(-25.7406)
    assert m["centroid_lon"] == pytest.approx(28.1919)
    assert m["geometry_type"] == "Point"
    assert m["length_m"] is None
    assert m["area_m2"] is None


def test_line_centroid_is_the_midpoint():
    m = geometry.geometry_metrics(LINE)
    assert m["centroid_lat"] == pytest.approx(-26.0)
    assert m["centroid_lon"] == pytest.approx(28.005)


def test_polygon_centroid_is_the_box_centre():
    m = geometry.geometry_metrics(SQUARE)
    assert m["centroid_lat"] == pytest.approx(-25.995, abs=1e-4)
    assert m["centroid_lon"] == pytest.approx(28.005, abs=1e-4)


# ── Measurements ──────────────────────────────────────────────────────────────

def test_line_length_matches_known_distance():
    """0.01° of longitude at latitude -26 is ~1000 m."""
    m = geometry.geometry_metrics(LINE)
    assert m["length_m"] == pytest.approx(1000, rel=0.02)
    assert m["area_m2"] is None


def test_multi_segment_line_sums_its_segments():
    two_km = {"type": "LineString", "coordinates": [
        [28.00, -26.00], [28.01, -26.00], [28.02, -26.00],
    ]}
    assert geometry.geometry_metrics(two_km)["length_m"] == pytest.approx(2000, rel=0.02)


def test_polygon_area_matches_known_box():
    """~1.0 km × ~1.11 km ⇒ ~1.11 million m²."""
    m = geometry.geometry_metrics(SQUARE)
    assert m["area_m2"] == pytest.approx(1_111_000, rel=0.05)
    assert m["length_m"] is None


def test_polygon_area_is_orientation_independent():
    clockwise = {"type": "Polygon", "coordinates": [
        list(reversed(SQUARE["coordinates"][0]))
    ]}
    assert geometry.geometry_metrics(clockwise)["area_m2"] == pytest.approx(
        geometry.geometry_metrics(SQUARE)["area_m2"], rel=1e-6
    )


def test_metrics_never_raise_on_junk():
    for junk in ({}, None, {"type": "Point"}, {"type": "Point", "coordinates": []}):
        assert geometry.geometry_metrics(junk) == geometry.EMPTY_METRICS


# ── Validation ────────────────────────────────────────────────────────────────

def test_valid_geometries_produce_no_errors():
    for geom in (POINT, LINE, SQUARE):
        assert geometry.validate_geometry(geom) == []


@pytest.mark.parametrize("geom,fragment", [
    ({"type": "Circle", "coordinates": [28.0, -26.0]},                  "not supported"),
    ({"type": "Point", "coordinates": [28.0]},                          "longitude and latitude"),
    ({"type": "LineString", "coordinates": [[28.0, -26.0]]},            "at least 2"),
    ({"type": "Polygon", "coordinates": [[[28.0, -26.0], [28.1, -26.0]]]}, "at least 4"),
    ({"type": "Point", "coordinates": [31.03, -17.82]},                 "South Africa"),
    ({"type": "LineString", "coordinates": [[28.0, -26.0], [31.03, -17.82]]}, "South Africa"),
    ({}, "No geometry"),
    (None, "No geometry"),
])
def test_invalid_geometries_are_rejected(geom, fragment):
    errors = geometry.validate_geometry(geom)
    assert errors, f"expected a rejection for {geom}"
    assert any(fragment.lower() in e.lower() for e in errors), errors


def test_unclosed_polygon_is_accepted_and_closed():
    """Leaflet.draw may emit an unclosed ring; we close it rather than reject."""
    unclosed = {"type": "Polygon", "coordinates": [[
        [28.00, -26.00], [28.01, -26.00], [28.01, -25.99], [28.00, -25.99],
    ]]}
    assert geometry.validate_geometry(unclosed) == []
    assert geometry.geometry_metrics(unclosed)["area_m2"] == pytest.approx(
        geometry.geometry_metrics(SQUARE)["area_m2"], rel=0.01
    )


# ── Feature unwrapping ────────────────────────────────────────────────────────

def test_geometry_extracted_from_a_drawn_feature():
    """streamlit-folium returns a Feature, not a bare geometry."""
    feature = {"type": "Feature", "properties": {}, "geometry": POINT}
    assert geometry.extract_geometry(feature) == POINT
    assert geometry.extract_geometry(POINT) == POINT
    assert geometry.extract_geometry(None) is None


# ── Status vocabularies ───────────────────────────────────────────────────────

def test_status_vocabularies_are_populated_and_ordered():
    assert capture.VERIFICATION_STATUSES[0] == "Verified"
    assert "Corrected" in capture.VERIFICATION_STATUSES
    assert "Unable to Locate" in capture.VERIFICATION_STATUSES
    assert capture.LIFECYCLE_STATUSES[0] == "Planning"
    assert "Under Construction" in capture.LIFECYCLE_STATUSES

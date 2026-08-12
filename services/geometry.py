"""
Measurement and validation for user-drawn GeoJSON geometries.

Pure functions with no database, Streamlit, or network dependency — everything a
drawn shape needs before it can be trusted and stored.

GeoJSON coordinates are [longitude, latitude] throughout.
"""

from __future__ import annotations

import math
from typing import Any

from services.geocoder import within_south_africa
from services.spatial import _haversine_km

GEOMETRY_TYPES: tuple[str, ...] = ("Point", "LineString", "Polygon")

EMPTY_METRICS: dict[str, Any] = {
    "geometry_type": "",
    "centroid_lat": None,
    "centroid_lon": None,
    "length_m": None,
    "area_m2": None,
}

_EARTH_RADIUS_M = 6_371_000.0


# ── Unwrapping ────────────────────────────────────────────────────────────────

def extract_geometry(drawing: dict | None) -> dict | None:
    """
    Unwrap a supported GeoJSON geometry from whatever the map handed back.

    Leaflet.Draw returns a Feature; a stored capture holds a bare geometry.
    Anything else — including a supported wrapper around an unsupported shape —
    yields None.
    """
    geometry = raw_geometry(drawing)
    if geometry is None:
        return None
    return geometry if geometry.get("type") in GEOMETRY_TYPES else None


def raw_geometry(drawing: dict | None) -> dict | None:
    """
    Unwrap without filtering on type, so validation can name an unsupported
    shape rather than reporting that nothing was drawn.
    """
    if not isinstance(drawing, dict):
        return None
    if drawing.get("type") == "Feature":
        geometry = drawing.get("geometry")
        return geometry if isinstance(geometry, dict) else None
    return drawing if drawing.get("type") else None


# ── Coordinate access ─────────────────────────────────────────────────────────

def _ring(geom: dict) -> list[list[float]]:
    """A polygon's outer ring, closed — Leaflet may emit it open."""
    coords = geom.get("coordinates") or []
    ring = list(coords[0]) if coords else []
    if len(ring) >= 3 and ring[0] != ring[-1]:
        ring = ring + [ring[0]]
    return ring


def _positions(geom: dict) -> list[list[float]]:
    """Flatten a geometry to a list of [lon, lat] positions."""
    gtype = geom.get("type")
    coords = geom.get("coordinates") or []
    if gtype == "Point":
        return [list(coords)] if coords else []
    if gtype == "LineString":
        return [list(c) for c in coords]
    if gtype == "Polygon":
        return [list(c) for c in _ring(geom)]
    return []


# ── Measurement ───────────────────────────────────────────────────────────────

def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres."""
    return _haversine_km(lat1, lon1, lat2, lon2) * 1000.0


def _line_length_m(positions: list[list[float]]) -> float:
    return sum(
        distance_m(lat1, lon1, lat2, lon2)
        for (lon1, lat1), (lon2, lat2) in zip(positions, positions[1:])
    )


def _polygon_area_m2(ring: list[list[float]]) -> float:
    """
    Shoelace area on an equirectangular projection about the ring's own latitude.

    Accurate to well under a percent at project scale (a few kilometres) and needs
    no projection library. The sign is discarded, so winding order is irrelevant.
    """
    if len(ring) < 4:
        return 0.0
    mean_lat = sum(p[1] for p in ring) / len(ring)
    lat_scale = math.radians(1.0) * _EARTH_RADIUS_M
    lon_scale = lat_scale * math.cos(math.radians(mean_lat))
    xs = [p[0] * lon_scale for p in ring]
    ys = [p[1] * lat_scale for p in ring]
    twice_area = sum(
        xs[i] * ys[i + 1] - xs[i + 1] * ys[i] for i in range(len(ring) - 1)
    )
    return abs(twice_area) / 2.0


def _polygon_centroid(ring: list[list[float]]) -> tuple[float, float]:
    """Area-weighted centroid of a closed ring, falling back to the mean vertex."""
    if len(ring) < 4:
        lats = [p[1] for p in ring] or [0.0]
        lons = [p[0] for p in ring] or [0.0]
        return sum(lats) / len(lats), sum(lons) / len(lons)

    cx = cy = signed_area = 0.0
    for (x0, y0), (x1, y1) in zip(ring, ring[1:]):
        cross = x0 * y1 - x1 * y0
        signed_area += cross
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross

    if abs(signed_area) < 1e-12:              # degenerate ring
        lats = [p[1] for p in ring]
        lons = [p[0] for p in ring]
        return sum(lats) / len(lats), sum(lons) / len(lons)

    signed_area /= 2.0
    return cy / (6.0 * signed_area), cx / (6.0 * signed_area)


def geometry_metrics(geom: dict | None) -> dict[str, Any]:
    """
    Measure a geometry: centroid, plus length (LineString) or area (Polygon).

    Returns EMPTY_METRICS for anything unusable rather than raising.
    """
    geom = extract_geometry(geom)
    if not geom:
        return dict(EMPTY_METRICS)

    gtype = geom.get("type")
    positions = _positions(geom)
    if not positions:
        return dict(EMPTY_METRICS)

    metrics = dict(EMPTY_METRICS)
    metrics["geometry_type"] = gtype

    if gtype == "Point":
        if len(positions[0]) < 2:
            return dict(EMPTY_METRICS)
        metrics["centroid_lon"] = float(positions[0][0])
        metrics["centroid_lat"] = float(positions[0][1])
    elif gtype == "LineString":
        metrics["length_m"] = round(_line_length_m(positions), 1)
        metrics["centroid_lat"] = sum(p[1] for p in positions) / len(positions)
        metrics["centroid_lon"] = sum(p[0] for p in positions) / len(positions)
    else:
        ring = _ring(geom)
        metrics["area_m2"] = round(_polygon_area_m2(ring), 1)
        metrics["centroid_lat"], metrics["centroid_lon"] = _polygon_centroid(ring)

    if metrics["centroid_lat"] is not None:
        metrics["centroid_lat"] = round(float(metrics["centroid_lat"]), 6)
        metrics["centroid_lon"] = round(float(metrics["centroid_lon"]), 6)
    return metrics


# ── Validation ────────────────────────────────────────────────────────────────

def validate_geometry(geom: dict | None) -> list[str]:
    """
    Return human-readable problems with a drawn shape; empty means it is usable.

    Drawn geometry is untrusted input, so this runs before every save.
    """
    geom = raw_geometry(geom)
    if not geom:
        return ["No geometry was drawn. Add a point, line, or polygon to the map."]

    gtype = geom.get("type")
    if gtype not in GEOMETRY_TYPES:
        return [f"Geometry type '{gtype}' is not supported — "
                f"draw a point, line, or polygon."]

    errors: list[str] = []
    positions = _positions(geom)

    if gtype == "Point":
        if not positions or len(positions[0]) < 2:
            return ["A point needs both a longitude and latitude."]
    elif gtype == "LineString":
        if len(positions) < 2:
            errors.append("A line needs at least 2 points.")
    else:
        if len(_ring(geom)) < 4:
            errors.append("A polygon needs at least 4 points (3 corners plus closure).")

    outside = [
        p for p in positions
        if len(p) >= 2 and not within_south_africa(p[1], p[0])
    ]
    if outside:
        lon, lat = outside[0][0], outside[0][1]
        errors.append(
            f"{len(outside)} point(s) fall outside South Africa "
            f"(first at {lat:.5f}, {lon:.5f}). Check you are on the right map area."
        )
    return errors

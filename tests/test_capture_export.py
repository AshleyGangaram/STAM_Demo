"""Export shaping — the handoff back into STAM's project importer."""

from __future__ import annotations

import io
import json
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services import capture
from services.db import IRMProject, get_session
from services.importer import REQUIRED_PROJECT_COLS

POINT = {"type": "Point", "coordinates": [28.1919, -25.7406]}
LINE = {"type": "LineString", "coordinates": [[28.00, -26.00], [28.01, -26.00]]}
SQUARE = {"type": "Polygon", "coordinates": [[
    [28.00, -26.00], [28.01, -26.00], [28.01, -25.99], [28.00, -25.99], [28.00, -26.00],
]]}


def _save(code, geometry, is_primary=None, **kwargs):
    ok, message, capture_id = capture.save_capture(
        project_code=code,
        project_name=kwargs.pop("name", f"Project {code}"),
        geometry=geometry,
        verification_status=kwargs.pop("verification_status", "Corrected"),
        lifecycle_status=kwargs.pop("lifecycle_status", "Design"),
        captured_by="tester",
        is_primary=is_primary,
        **kwargs,
    )
    assert ok, message
    return capture_id


def _add_irm(code, lat=None, lon=None, **kwargs):
    session = get_session()
    session.add(IRMProject(
        project_code=code,
        project_name=kwargs.pop("project_name", f"Project {code}"),
        department=kwargs.pop("department", "Health"),
        municipality=kwargs.pop("municipality", "Tshwane"),
        irm_latitude=lat, irm_longitude=lon,
        budget_rands=kwargs.pop("budget_rands", 1_000_000.0),
        financial_year=kwargs.pop("financial_year", "2026/27"),
    ))
    session.commit()
    session.close()


# ── Primary selection ─────────────────────────────────────────────────────────

def test_one_row_per_project_even_with_many_shapes(temp_db):
    _save("IRM-A", POINT)
    _save("IRM-A", LINE, is_primary=False)
    _save("IRM-A", SQUARE, is_primary=False)
    _save("IRM-B", POINT)

    rows = capture.export_rows()
    assert len(rows) == 2
    assert {r["project_id"] for r in rows} == {"IRM-A", "IRM-B"}


def test_export_uses_the_flagged_primary_shape(temp_db):
    _save("IRM-A", POINT)
    _save("IRM-A", SQUARE, is_primary=True)     # takes over as primary

    row = capture.export_rows()[0]
    assert row["geometry_type"] == "Polygon"
    assert row["latitude"] == pytest.approx(-25.995, abs=1e-3)


def test_export_falls_back_to_the_earliest_shape_without_a_primary(temp_db):
    first = _save("IRM-A", POINT)
    _save("IRM-A", SQUARE, is_primary=False)
    capture.update_capture(first, is_primary=False)      # no primary at all now

    row = capture.export_rows()[0]
    assert row["geometry_type"] == "Point"               # earliest wins


# ── STAM compatibility ────────────────────────────────────────────────────────

def test_excel_columns_satisfy_the_stam_importer(temp_db):
    _save("IRM-A", POINT)
    frame = pd.read_excel(io.BytesIO(capture.export_captures_excel()), dtype=str)
    missing = REQUIRED_PROJECT_COLS - set(frame.columns)
    assert not missing, f"STAM importer would reject this file: {sorted(missing)}"


def test_excel_carries_the_corrected_coordinates(temp_db):
    _add_irm("IRM-A", lat=-25.50, lon=28.30, department="Education",
             municipality="Ekurhuleni", financial_year="2027/28")
    _save("IRM-A", POINT, lifecycle_status="Under Construction")

    frame = pd.read_excel(io.BytesIO(capture.export_captures_excel()))
    row = frame.iloc[0]
    assert row["project_id"] == "IRM-A"
    assert row["latitude"] == pytest.approx(-25.7406)
    assert row["longitude"] == pytest.approx(28.1919)
    assert row["department"] == "Education"          # inherited from the worklist
    assert row["municipality"] == "Ekurhuleni"
    assert row["budget_year"] == "2027/28"
    assert row["readiness_status"] == "Ready"        # mapped from lifecycle status
    assert row["irm_latitude"] == pytest.approx(-25.50)
    assert row["metres_moved"] > 0


def test_lifecycle_maps_onto_a_readiness_value_stam_accepts(temp_db):
    from services.importer import VALID_READINESS
    for lifecycle in capture.LIFECYCLE_STATUSES:
        assert capture._READINESS_FROM_LIFECYCLE[lifecycle] in VALID_READINESS


def test_metres_moved_is_none_without_original_irm_coordinates(temp_db):
    _add_irm("IRM-A", lat=None, lon=None)
    _save("IRM-A", POINT)
    assert capture.export_rows()[0]["metres_moved"] is None


# ── GeoJSON ───────────────────────────────────────────────────────────────────

def test_geojson_keeps_every_shape(temp_db):
    _save("IRM-A", POINT)
    _save("IRM-A", LINE, is_primary=False)
    _save("IRM-A", SQUARE, is_primary=False)

    collection = json.loads(capture.export_captures_geojson())
    assert collection["type"] == "FeatureCollection"
    assert len(collection["features"]) == 3
    assert {f["geometry"]["type"] for f in collection["features"]} == {
        "Point", "LineString", "Polygon"
    }


def test_geojson_properties_carry_the_correction_trail(temp_db):
    _add_irm("IRM-A", lat=-25.50, lon=28.30)
    _save("IRM-A", POINT, comments="Confirmed with the district office")

    feature = json.loads(capture.export_captures_geojson())["features"][0]
    props = feature["properties"]
    assert props["project_code"] == "IRM-A"
    assert props["verification_status"] == "Corrected"
    assert props["comments"] == "Confirmed with the district office"
    assert props["is_primary"] is True
    assert props["irm_latitude"] == pytest.approx(-25.50)
    assert props["metres_moved"] > 0
    assert props["captured_by"] == "tester"


def test_deleted_captures_never_export(temp_db):
    keep = _save("IRM-A", POINT)
    drop = _save("IRM-B", SQUARE)
    capture.soft_delete_capture(drop)

    collection = json.loads(capture.export_captures_geojson())
    assert len(collection["features"]) == 1
    assert collection["features"][0]["properties"]["capture_id"] == keep
    assert [r["project_id"] for r in capture.export_rows()] == ["IRM-A"]


def test_exports_handle_an_empty_database(temp_db):
    collection = json.loads(capture.export_captures_geojson())
    assert collection["features"] == []

    frame = pd.read_excel(io.BytesIO(capture.export_captures_excel()))
    assert frame.empty
    assert list(frame.columns) == capture.STAM_EXPORT_COLUMNS

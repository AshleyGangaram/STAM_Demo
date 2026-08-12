"""
End-to-end: capture a location, export it, and feed it back into STAM.

This is the handoff the whole page exists to serve — if STAM's own importer
rejects our export, the tool has failed regardless of how well it draws.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services import capture
from services.db import IRMProject, Project, get_session
from services.importer import import_projects_from_excel

CLINIC_SITE = {"type": "Polygon", "coordinates": [[
    [28.2200, -25.9800], [28.2215, -25.9800],
    [28.2215, -25.9790], [28.2200, -25.9790], [28.2200, -25.9800],
]]}
ROAD_ALIGNMENT = {"type": "LineString", "coordinates": [
    [28.1000, -25.8500], [28.1200, -25.8600], [28.1400, -25.8650],
]}


@pytest.fixture
def worklist(temp_db):
    """Two IRM projects: one with no coordinates, one placed in the wrong city."""
    session = get_session()
    session.add(IRMProject(
        project_code="IRM-2026-0101", project_name="Tembisa Clinic Upgrade",
        department="Health", municipality="Ekurhuleni",
        irm_latitude=None, irm_longitude=None,
        budget_rands=42_000_000.0, financial_year="2026/27",
    ))
    session.add(IRMProject(
        project_code="IRM-2026-0104", project_name="R55 Road Rehabilitation",
        department="Roads & Transport", municipality="Tshwane",
        irm_latitude=-29.8587, irm_longitude=31.0218,      # Durban — wrong
        budget_rands=310_000_000.0, financial_year="2026/27",
    ))
    session.commit()
    session.close()
    return temp_db


def test_capture_export_import_round_trip(worklist, tmp_path):
    # 1. A capturer locates the clinic and the road.
    ok, message, _ = capture.save_capture(
        project_code="IRM-2026-0101", project_name="Tembisa Clinic Upgrade",
        geometry=CLINIC_SITE,
        verification_status="Corrected", lifecycle_status="Design",
        comments="Site confirmed against 2025 imagery and the district plan.",
        captured_by="capturer", search_query="Tembisa Hospital",
    )
    assert ok, message

    ok, message, _ = capture.save_capture(
        project_code="IRM-2026-0104", project_name="R55 Road Rehabilitation",
        geometry=ROAD_ALIGNMENT,
        verification_status="Corrected", lifecycle_status="Under Construction",
        comments="IRM coordinate was in Durban; realigned to the R55 corridor.",
        captured_by="capturer",
    )
    assert ok, message

    # 2. The admin exports for STAM.
    export_path = tmp_path / "corrected.xlsx"
    export_path.write_bytes(capture.export_captures_excel())

    # 3. STAM's own importer consumes it, unmodified.
    result = import_projects_from_excel(str(export_path))
    assert result.imported == 2, [e.model_dump() for e in result.errors]
    assert result.errors == []

    # 4. The corrected coordinates are what landed in STAM.
    session = get_session()
    projects = {p.project_id: p for p in session.query(Project).all()}
    session.close()

    clinic = projects["IRM-2026-0101"]
    assert clinic.name == "Tembisa Clinic Upgrade"
    assert clinic.latitude == pytest.approx(-25.9795, abs=1e-3)
    assert clinic.longitude == pytest.approx(28.22075, abs=1e-3)
    assert clinic.municipality == "Ekurhuleni"
    assert clinic.budget_year == "2026/27"

    road = projects["IRM-2026-0104"]
    assert road.latitude == pytest.approx(-25.8583, abs=1e-2)
    assert road.longitude == pytest.approx(28.12, abs=1e-2)
    # The wrong Durban coordinate did not survive
    assert road.latitude > -27.0


def test_the_wrong_coordinate_is_measurably_corrected(worklist):
    capture.save_capture(
        project_code="IRM-2026-0104", project_name="R55 Road Rehabilitation",
        geometry=ROAD_ALIGNMENT,
        verification_status="Corrected", lifecycle_status="Design",
        captured_by="capturer",
    )
    row = capture.export_rows()[0]
    # Durban to the R55 corridor is roughly 450 km
    assert row["metres_moved"] == pytest.approx(450_000, rel=0.2)
    assert row["irm_latitude"] == pytest.approx(-29.8587)


def test_only_the_primary_shape_reaches_stam(worklist, tmp_path):
    """A project with several shapes must still produce exactly one STAM row."""
    capture.save_capture(
        project_code="IRM-2026-0101", project_name="Tembisa Clinic Upgrade",
        geometry={"type": "Point", "coordinates": [28.2207, -25.9795]},
        verification_status="Approximate", lifecycle_status="Planning",
        captured_by="capturer",
    )
    capture.save_capture(
        project_code="IRM-2026-0101", project_name="Tembisa Clinic Upgrade",
        geometry=CLINIC_SITE,
        verification_status="Corrected", lifecycle_status="Design",
        captured_by="capturer", is_primary=True,
    )

    export_path = tmp_path / "corrected.xlsx"
    export_path.write_bytes(capture.export_captures_excel())
    result = import_projects_from_excel(str(export_path))

    assert result.imported == 1
    session = get_session()
    assert session.query(Project).count() == 1
    session.close()

"""IRM worklist import — column handling, duplicates, and blank coordinates."""

from __future__ import annotations

import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services import capture


def _write_csv(tmp_path, rows, name="worklist.csv") -> str:
    path = tmp_path / name
    pd.DataFrame(rows).to_csv(path, index=False)
    return str(path)


def _write_xlsx(tmp_path, rows, name="worklist.xlsx") -> str:
    path = tmp_path / name
    pd.DataFrame(rows).to_excel(path, index=False)
    return str(path)


BASIC = [
    {"project_code": "IRM-1", "project_name": "Tembisa Clinic",
     "department": "Health", "municipality": "Ekurhuleni",
     "latitude": -26.00, "longitude": 28.22, "financial_year": "2026/27"},
    {"project_code": "IRM-2", "project_name": "Mamelodi Library",
     "department": "Culture", "municipality": "Tshwane",
     "latitude": "", "longitude": "", "financial_year": "2026/27"},
]


# ── Happy path ────────────────────────────────────────────────────────────────

def test_imports_rows_from_csv(temp_db, tmp_path):
    result = capture.import_irm_worklist(_write_csv(tmp_path, BASIC))
    assert result.imported == 2
    assert result.errors == []
    assert len(capture.list_irm_projects()) == 2


def test_imports_rows_from_excel(temp_db, tmp_path):
    result = capture.import_irm_worklist(_write_xlsx(tmp_path, BASIC))
    assert result.imported == 2


def test_blank_coordinates_are_accepted(temp_db, tmp_path):
    """Missing locations are the reason this tool exists — not an error."""
    capture.import_irm_worklist(_write_csv(tmp_path, BASIC))
    projects = {p.project_code: p for p in capture.list_irm_projects()}
    assert projects["IRM-2"].irm_latitude is None
    assert projects["IRM-2"].irm_longitude is None
    assert projects["IRM-1"].irm_latitude == pytest.approx(-26.00)


def test_original_irm_values_are_preserved(temp_db, tmp_path):
    capture.import_irm_worklist(_write_csv(tmp_path, BASIC))
    project = {p.project_code: p for p in capture.list_irm_projects()}["IRM-1"]
    assert project.department == "Health"
    assert project.municipality == "Ekurhuleni"
    assert project.financial_year == "2026/27"
    assert project.province == "Gauteng"          # sensible default


# ── Column handling ───────────────────────────────────────────────────────────

def test_column_aliases_are_mapped(temp_db, tmp_path):
    rows = [{"Project ID": "IRM-9", "Name": "Aliased Project",
             "Lat": -26.1, "Lng": 28.1, "Budget": "1,500,000",
             "Budget Year": "2026/27", "Location": "Soweto"}]
    result = capture.import_irm_worklist(_write_csv(tmp_path, rows))
    assert result.imported == 1, result.errors

    project = capture.list_irm_projects()[0]
    assert project.project_code == "IRM-9"
    assert project.project_name == "Aliased Project"
    assert project.irm_latitude == pytest.approx(-26.1)
    assert project.budget_rands == pytest.approx(1_500_000)
    assert project.irm_address == "Soweto"


def test_missing_required_columns_stop_the_import(temp_db, tmp_path):
    path = _write_csv(tmp_path, [{"description": "no code or name here"}])
    result = capture.import_irm_worklist(path)
    assert result.imported == 0
    assert len(result.errors) == 1
    assert "Missing required columns" in result.errors[0].message
    assert capture.list_irm_projects() == []


def test_unreadable_file_returns_an_error_not_an_exception(temp_db, tmp_path):
    result = capture.import_irm_worklist(str(tmp_path / "nope.xlsx"))
    assert result.imported == 0
    assert result.errors[0].field == "file"


# ── Row validation ────────────────────────────────────────────────────────────

def test_rows_without_a_code_or_name_are_reported(temp_db, tmp_path):
    rows = [
        {"project_code": "", "project_name": "Nameless code"},
        {"project_code": "IRM-3", "project_name": ""},
        {"project_code": "IRM-4", "project_name": "Fine"},
    ]
    result = capture.import_irm_worklist(_write_csv(tmp_path, rows))
    assert result.imported == 1
    assert len(result.errors) == 2
    assert {e.field for e in result.errors} == {"project_code", "project_name"}


def test_duplicate_codes_within_one_file_are_rejected_once(temp_db, tmp_path):
    rows = [
        {"project_code": "IRM-5", "project_name": "First"},
        {"project_code": "IRM-5", "project_name": "Second"},
    ]
    result = capture.import_irm_worklist(_write_csv(tmp_path, rows))
    assert result.imported == 1
    assert len(result.errors) == 1
    assert "Duplicate" in result.errors[0].message


def test_codes_already_in_the_worklist_are_skipped_with_a_warning(temp_db, tmp_path):
    path = _write_csv(tmp_path, BASIC)
    capture.import_irm_worklist(path)
    result = capture.import_irm_worklist(path)      # same file twice

    assert result.imported == 0
    assert len(result.warnings) == 2
    assert "already in the worklist" in result.warnings[0]
    assert len(capture.list_irm_projects()) == 2


def test_coordinates_outside_south_africa_warn_but_still_import(temp_db, tmp_path):
    """A wrong coordinate is exactly what needs correcting — keep it, flag it."""
    rows = [{"project_code": "IRM-6", "project_name": "Wrong province",
             "latitude": -29.8587, "longitude": 31.0218}]      # Durban, not Gauteng
    rows[0]["latitude"] = -17.8252                              # Harare — outside SA
    rows[0]["longitude"] = 31.0335

    result = capture.import_irm_worklist(_write_csv(tmp_path, rows))
    assert result.imported == 1
    assert any("outside South Africa" in w for w in result.warnings)
    assert capture.list_irm_projects()[0].irm_latitude == pytest.approx(-17.8252)


# ── Clearing ──────────────────────────────────────────────────────────────────

def test_clearing_the_worklist_leaves_captures_intact(temp_db, tmp_path):
    capture.import_irm_worklist(_write_csv(tmp_path, BASIC))
    capture.save_capture(
        project_code="IRM-1", project_name="Tembisa Clinic",
        geometry={"type": "Point", "coordinates": [28.22, -26.00]},
        verification_status="Verified", lifecycle_status="Design",
        captured_by="tester",
    )

    removed = capture.clear_irm_worklist()
    assert removed == 2
    assert capture.list_irm_projects() == []
    assert len(capture.list_captures()) == 1        # captures survive

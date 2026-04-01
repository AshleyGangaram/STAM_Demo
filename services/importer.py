"""
Data import service for STAM.

Handles:
  - Excel (.xlsx) import with column mapping and row-level validation
  - GeoJSON import for facility and spatial layers
  - Import log written to audit_log
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from models.schemas import ImportError, ImportResult
from services.db import AuditLog, Facility, Project, get_session, log_action

REQUIRED_PROJECT_COLS = {
    "project_id", "project_name", "department", "project_type",
    "latitude", "longitude", "budget_rands", "budget_year",
    "readiness_status", "municipality",
}

VALID_PROJECT_TYPES = {"clinic", "school", "library", "community", "housing",
                       "road", "commercial", "industrial"}
VALID_READINESS = {"Ready", "Design", "Planning", "Concept"}
VALID_GSDF = {"Priority", "Accommodate", "Discourage", "Outside"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def import_projects_from_excel(
    file_path: str,
    budget_year: str = "2026/27",
    user_role: str = "Analyst",
) -> ImportResult:
    """
    Parse an Excel file and import valid rows into the projects table.

    Returns ImportResult with counts and per-row errors.
    """
    errors: list[ImportError] = []
    warnings: list[str] = []
    imported = 0

    try:
        df = pd.read_excel(file_path, dtype=str)
    except Exception as exc:
        return ImportResult(
            total_rows=0,
            imported=0,
            errors=[ImportError(row=0, field="file", message=str(exc))],
        )

    # Normalise column names
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]

    # Check required columns
    missing_cols = REQUIRED_PROJECT_COLS - set(df.columns)
    if "project_name" in missing_cols and "name" in df.columns:
        df = df.rename(columns={"name": "project_name"})
        missing_cols.discard("project_name")
    if missing_cols:
        return ImportResult(
            total_rows=len(df),
            imported=0,
            errors=[ImportError(row=0, field="columns",
                                message=f"Missing required columns: {sorted(missing_cols)}")],
        )

    session = get_session()
    existing_ids = {r[0] for r in session.query(Project.project_id).all()}

    for idx, row in df.iterrows():
        row_num = int(idx) + 2  # 1-indexed, +1 for header

        # ── Validate project_id ──────────────────────────────────────────────
        pid = str(row.get("project_id", "")).strip()
        if not pid:
            errors.append(ImportError(row=row_num, field="project_id",
                                       message="project_id is required"))
            continue

        if pid in existing_ids:
            warnings.append(f"Row {row_num}: project_id '{pid}' already exists — skipped.")
            continue

        # ── Validate coordinates ──────────────────────────────────────────────
        try:
            lat = float(str(row.get("latitude", "")).strip())
            lon = float(str(row.get("longitude", "")).strip())
        except (ValueError, TypeError):
            errors.append(ImportError(row=row_num, field="latitude/longitude",
                                       message="Coordinates must be numeric",
                                       value=f"{row.get('latitude')}, {row.get('longitude')}"))
            continue

        if not (-35 <= lat <= -22):
            errors.append(ImportError(row=row_num, field="latitude",
                                       message="Latitude out of South Africa range",
                                       value=str(lat)))
            continue

        # ── Validate budget ───────────────────────────────────────────────────
        try:
            budget = float(str(row.get("budget_rands", "0")).replace(",", "").strip())
        except (ValueError, TypeError):
            errors.append(ImportError(row=row_num, field="budget_rands",
                                       message="Budget must be a numeric value (ZAR)",
                                       value=str(row.get("budget_rands"))))
            continue

        # ── Build project record ──────────────────────────────────────────────
        proj_type = str(row.get("project_type", "clinic")).strip().lower()
        if proj_type not in VALID_PROJECT_TYPES:
            warnings.append(f"Row {row_num}: project_type '{proj_type}' not in known types — saved as-is.")

        readiness = str(row.get("readiness_status", "Concept")).strip()
        if readiness not in VALID_READINESS:
            warnings.append(f"Row {row_num}: readiness_status '{readiness}' unknown — defaulted to 'Concept'.")
            readiness = "Concept"

        project = Project(
            project_id=pid,
            name=str(row.get("project_name", "")).strip(),
            department=str(row.get("department", "")).strip(),
            project_type=proj_type,
            latitude=lat,
            longitude=lon,
            budget_rands=budget,
            budget_year=str(row.get("budget_year", budget_year)).strip(),
            readiness_status=readiness,
            municipality=str(row.get("municipality", "")).strip(),
            ward=str(row.get("ward", "")).strip(),
            gsdf_classification="",
            total_score=0,
            classification="Pending",
            source_file=os.path.basename(file_path),
        )
        session.add(project)
        existing_ids.add(pid)
        imported += 1

    session.commit()
    session.close()

    log_action(
        action="IMPORT_PROJECTS",
        entity_type="project",
        detail={"file": os.path.basename(file_path), "imported": imported,
                "errors": len(errors), "warnings": len(warnings)},
        user_role=user_role,
    )

    return ImportResult(
        total_rows=len(df),
        imported=imported,
        errors=errors,
        warnings=warnings,
    )


def import_facilities_from_geojson(file_path: str, user_role: str = "Analyst") -> ImportResult:
    """Import facility point features from a GeoJSON file."""
    errors: list[ImportError] = []
    imported = 0

    try:
        with open(file_path) as f:
            data = json.load(f)
    except Exception as exc:
        return ImportResult(total_rows=0, imported=0,
                            errors=[ImportError(row=0, field="file", message=str(exc))])

    features = data.get("features", [])
    session = get_session()

    for idx, feat in enumerate(features):
        props = feat.get("properties", {})
        geom = feat.get("geometry", {})
        coords = geom.get("coordinates", [])

        if not coords or len(coords) < 2:
            errors.append(ImportError(row=idx + 1, field="geometry",
                                       message="Missing or invalid coordinates"))
            continue

        lon, lat = float(coords[0]), float(coords[1])
        facility = Facility(
            name=props.get("name", f"Facility {idx + 1}"),
            facility_type=props.get("facility_type", "clinic"),
            latitude=lat,
            longitude=lon,
            capacity=int(props.get("capacity", 0)),
            current_occupancy=int(props.get("current_occupancy", 0)),
            municipality=props.get("municipality", ""),
            ward=props.get("ward", ""),
        )
        session.add(facility)
        imported += 1

    session.commit()
    session.close()

    log_action(
        action="IMPORT_FACILITIES",
        entity_type="facility",
        detail={"file": os.path.basename(file_path), "imported": imported},
        user_role=user_role,
    )

    return ImportResult(total_rows=len(features), imported=imported, errors=errors)

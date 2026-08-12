"""
IRM location capture service.

Responsibilities:
  - CRUD over LocationCapture rows (soft delete, primary-feature rules)
  - IRM worklist import from Excel/CSV
  - Export to GeoJSON (all shapes) and STAM-shaped Excel (primary shape only)

Geometry measurement and validation live in services/geometry.py.
No Streamlit imports — this module is callable from scripts and tests.
"""

from __future__ import annotations

import io
import json
import os
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from models.schemas import ImportError, ImportResult
from services.db import IRMProject, LocationCapture, get_session, log_action
from services.geocoder import within_south_africa
from services.geometry import (
    distance_m,
    extract_geometry,
    geometry_metrics,
    validate_geometry,
)

# ── Vocabularies ──────────────────────────────────────────────────────────────

VERIFICATION_STATUSES: tuple[str, ...] = (
    "Verified",           # IRM location was already correct
    "Corrected",          # moved to the true location
    "Approximate",        # best estimate, needs a field check
    "Unable to Locate",   # insufficient information
    "Duplicate Record",   # same site as another IRM entry
)

LIFECYCLE_STATUSES: tuple[str, ...] = (
    "Planning",
    "Design",
    "Tender",
    "Under Construction",
    "Practically Complete",
    "On Hold",
    "Cancelled",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def metres_moved(capture_row: LocationCapture, irm: IRMProject | None) -> float | None:
    """Distance from the original IRM coordinate to the captured centroid."""
    if irm is None or irm.irm_latitude is None or irm.irm_longitude is None:
        return None
    if capture_row.centroid_lat is None or capture_row.centroid_lon is None:
        return None
    return round(distance_m(
        irm.irm_latitude, irm.irm_longitude,
        capture_row.centroid_lat, capture_row.centroid_lon,
    ), 1)


# ── Capture CRUD ──────────────────────────────────────────────────────────────

def captures_for_project(project_code: str) -> list[LocationCapture]:
    session = get_session()
    try:
        return (session.query(LocationCapture)
                .filter(LocationCapture.project_code == project_code,
                        LocationCapture.deleted == 0)
                .order_by(LocationCapture.created_at)
                .all())
    finally:
        session.close()


def list_captures(captured_by: str | None = None) -> list[LocationCapture]:
    session = get_session()
    try:
        query = session.query(LocationCapture).filter(LocationCapture.deleted == 0)
        if captured_by:
            query = query.filter(LocationCapture.captured_by == captured_by)
        return query.order_by(LocationCapture.updated_at.desc()).all()
    finally:
        session.close()


def get_capture(capture_id: str) -> LocationCapture | None:
    session = get_session()
    try:
        return session.query(LocationCapture).filter(
            LocationCapture.id == capture_id
        ).first()
    finally:
        session.close()


def _clear_other_primaries(session, project_code: str, keep_id: str | None) -> None:
    others = (session.query(LocationCapture)
              .filter(LocationCapture.project_code == project_code,
                      LocationCapture.is_primary == 1)
              .all())
    for row in others:
        if row.id != keep_id:
            row.is_primary = 0


def save_capture(
    *,
    project_code: str,
    project_name: str,
    geometry: dict,
    verification_status: str,
    lifecycle_status: str,
    comments: str = "",
    captured_by: str = "",
    irm_project_id: str | None = None,
    search_query: str = "",
    is_primary: bool | None = None,
    user_role: str = "IRM Capturer",
) -> tuple[bool, str, str | None]:
    """
    Persist a new capture. Returns (success, message, capture_id).

    When `is_primary` is None the first capture for a project code becomes the
    primary one automatically.
    """
    project_code = (project_code or "").strip()
    if not project_code:
        return False, "A project code is required.", None
    if not (project_name or "").strip():
        return False, "A project name is required.", None

    geometry = extract_geometry(geometry)
    errors = validate_geometry(geometry)
    if errors:
        return False, " ".join(errors), None
    if verification_status not in VERIFICATION_STATUSES:
        return False, f"Unknown verification status '{verification_status}'.", None
    if lifecycle_status not in LIFECYCLE_STATUSES:
        return False, f"Unknown lifecycle status '{lifecycle_status}'.", None

    metrics = geometry_metrics(geometry)
    session = get_session()
    try:
        existing = (session.query(LocationCapture)
                    .filter(LocationCapture.project_code == project_code,
                            LocationCapture.deleted == 0)
                    .count())
        primary = existing == 0 if is_primary is None else bool(is_primary)

        row = LocationCapture(
            project_code=project_code,
            project_name=project_name.strip(),
            irm_project_id=irm_project_id,
            geometry_type=metrics["geometry_type"],
            geometry_geojson=json.dumps(geometry),
            centroid_lat=metrics["centroid_lat"],
            centroid_lon=metrics["centroid_lon"],
            length_m=metrics["length_m"],
            area_m2=metrics["area_m2"],
            is_primary=1 if primary else 0,
            verification_status=verification_status,
            lifecycle_status=lifecycle_status,
            comments=(comments or "").strip(),
            search_query=(search_query or "").strip(),
            captured_by=captured_by,
            deleted=0,
        )
        session.add(row)
        session.flush()                       # assign row.id before clearing others
        if primary:
            _clear_other_primaries(session, project_code, row.id)
        capture_id = row.id
        session.commit()
    finally:
        session.close()

    log_action("CAPTURE_SAVED", "location_capture", capture_id, {
        "project_code": project_code,
        "geometry_type": metrics["geometry_type"],
        "verification_status": verification_status,
        "lifecycle_status": lifecycle_status,
        "primary": primary,
    }, user_role=user_role)
    return True, f"Location captured for {project_code}.", capture_id


def update_capture(
    capture_id: str,
    *,
    project_name: str | None = None,
    geometry: dict | None = None,
    verification_status: str | None = None,
    lifecycle_status: str | None = None,
    comments: str | None = None,
    is_primary: bool | None = None,
    user_role: str = "IRM Capturer",
) -> tuple[bool, str]:
    """Update an existing capture. Only the supplied fields change."""
    if geometry is not None:
        geometry = extract_geometry(geometry)
        errors = validate_geometry(geometry)
        if errors:
            return False, " ".join(errors)
    if verification_status is not None and verification_status not in VERIFICATION_STATUSES:
        return False, f"Unknown verification status '{verification_status}'."
    if lifecycle_status is not None and lifecycle_status not in LIFECYCLE_STATUSES:
        return False, f"Unknown lifecycle status '{lifecycle_status}'."

    session = get_session()
    try:
        row = session.query(LocationCapture).filter(
            LocationCapture.id == capture_id
        ).first()
        if row is None or row.deleted:
            return False, "That capture no longer exists."

        if project_name is not None and project_name.strip():
            row.project_name = project_name.strip()
        if geometry is not None:
            metrics = geometry_metrics(geometry)
            row.geometry_type = metrics["geometry_type"]
            row.geometry_geojson = json.dumps(geometry)
            row.centroid_lat = metrics["centroid_lat"]
            row.centroid_lon = metrics["centroid_lon"]
            row.length_m = metrics["length_m"]
            row.area_m2 = metrics["area_m2"]
        if verification_status is not None:
            row.verification_status = verification_status
        if lifecycle_status is not None:
            row.lifecycle_status = lifecycle_status
        if comments is not None:
            row.comments = comments.strip()
        if is_primary is not None:
            row.is_primary = 1 if is_primary else 0
            if is_primary:
                _clear_other_primaries(session, row.project_code, row.id)

        project_code = row.project_code
        session.commit()
    finally:
        session.close()

    log_action("CAPTURE_UPDATED", "location_capture", capture_id,
               {"project_code": project_code}, user_role=user_role)
    return True, "Capture updated."


def soft_delete_capture(capture_id: str,
                        user_role: str = "IRM Capturer") -> tuple[bool, str]:
    """
    Mark a capture deleted without removing the row, preserving the trail.

    If it was the primary feature, the oldest remaining capture is promoted so a
    project never silently loses its exported coordinate.
    """
    session = get_session()
    try:
        row = session.query(LocationCapture).filter(
            LocationCapture.id == capture_id
        ).first()
        if row is None or row.deleted:
            return False, "That capture no longer exists."

        was_primary = bool(row.is_primary)
        project_code = row.project_code
        row.deleted = 1
        row.is_primary = 0

        if was_primary:
            replacement = (session.query(LocationCapture)
                           .filter(LocationCapture.project_code == project_code,
                                   LocationCapture.deleted == 0,
                                   LocationCapture.id != capture_id)
                           .order_by(LocationCapture.created_at)
                           .first())
            if replacement is not None:
                replacement.is_primary = 1
        session.commit()
    finally:
        session.close()

    log_action("CAPTURE_DELETED", "location_capture", capture_id,
               {"project_code": project_code}, user_role=user_role)
    return True, "Capture removed."


def set_primary(capture_id: str, user_role: str = "IRM Capturer") -> tuple[bool, str]:
    session = get_session()
    try:
        row = session.query(LocationCapture).filter(
            LocationCapture.id == capture_id
        ).first()
        if row is None or row.deleted:
            return False, "That capture no longer exists."
        _clear_other_primaries(session, row.project_code, row.id)
        row.is_primary = 1
        project_code = row.project_code
        session.commit()
    finally:
        session.close()

    log_action("CAPTURE_SET_PRIMARY", "location_capture", capture_id,
               {"project_code": project_code}, user_role=user_role)
    return True, "Set as the primary location for this project."


# ── IRM worklist ──────────────────────────────────────────────────────────────

REQUIRED_WORKLIST_COLS = {"project_code", "project_name"}

_WORKLIST_ALIASES = {
    "project_id": "project_code",
    "code": "project_code",
    "irm_code": "project_code",
    "name": "project_name",
    "irm_project_name": "project_name",
    "lat": "latitude",
    "lon": "longitude",
    "lng": "longitude",
    "long": "longitude",
    "irm_latitude": "latitude",
    "irm_longitude": "longitude",
    "location": "address",
    "irm_address": "address",
    "budget": "budget_rands",
    "fy": "financial_year",
    "budget_year": "financial_year",
}


def _clean(value: Any) -> str:
    text = str(value).strip()
    return "" if text.lower() in ("nan", "none", "nat") else text


def _to_float(value: Any) -> float | None:
    text = _clean(value).replace(",", "").replace("R", "").replace(" ", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def list_irm_projects() -> list[IRMProject]:
    session = get_session()
    try:
        return session.query(IRMProject).order_by(IRMProject.project_code).all()
    finally:
        session.close()


def import_irm_worklist(file_path: str, username: str = "",
                        user_role: str = "Administrator") -> ImportResult:
    """
    Load an IRM extract (Excel or CSV) into the worklist.

    Only project_code and project_name are required — blank coordinates are the
    normal case here, not an error, since missing locations are the reason this
    tool exists. Codes already in the worklist are skipped with a warning.
    """
    errors: list[ImportError] = []
    warnings: list[str] = []
    imported = 0

    try:
        if file_path.lower().endswith(".csv"):
            frame = pd.read_csv(file_path, dtype=str)
        else:
            frame = pd.read_excel(file_path, dtype=str)
    except Exception as exc:
        return ImportResult(total_rows=0, imported=0,
                            errors=[ImportError(row=0, field="file", message=str(exc))])

    frame.columns = [str(c).strip().lower().replace(" ", "_") for c in frame.columns]
    frame = frame.rename(columns={k: v for k, v in _WORKLIST_ALIASES.items()
                                  if k in frame.columns and v not in frame.columns})

    missing = REQUIRED_WORKLIST_COLS - set(frame.columns)
    if missing:
        return ImportResult(
            total_rows=len(frame), imported=0,
            errors=[ImportError(
                row=0, field="columns",
                message=f"Missing required columns: {sorted(missing)}. "
                        f"Found: {sorted(frame.columns)}")],
        )

    session = get_session()
    try:
        existing = {r[0] for r in session.query(IRMProject.project_code).all()}
        seen: set[str] = set()

        for idx, row in frame.iterrows():
            row_num = int(idx) + 2      # 1-indexed plus header

            code = _clean(row.get("project_code"))
            if not code:
                errors.append(ImportError(row=row_num, field="project_code",
                                          message="project_code is required"))
                continue
            # `seen` is checked first so a duplicate inside the uploaded file is
            # reported as such, not as "already in the worklist".
            if code in seen:
                errors.append(ImportError(row=row_num, field="project_code",
                                          message="Duplicate project_code in file",
                                          value=code))
                continue
            if code in existing:
                warnings.append(f"Row {row_num}: '{code}' is already in the worklist — skipped.")
                continue

            name = _clean(row.get("project_name"))
            if not name:
                errors.append(ImportError(row=row_num, field="project_name",
                                          message="project_name is required",
                                          value=code))
                continue
            seen.add(code)

            lat = _to_float(row.get("latitude"))
            lon = _to_float(row.get("longitude"))
            if (lat is not None or lon is not None) and not within_south_africa(lat, lon):
                warnings.append(
                    f"Row {row_num}: '{code}' has coordinates outside South Africa "
                    f"({lat}, {lon}) — kept as the original IRM value for correction."
                )

            session.add(IRMProject(
                project_code=code,
                project_name=name,
                department=_clean(row.get("department")),
                municipality=_clean(row.get("municipality")),
                province=_clean(row.get("province")) or "Gauteng",
                irm_latitude=lat,
                irm_longitude=lon,
                irm_address=_clean(row.get("address")),
                budget_rands=_to_float(row.get("budget_rands")),
                financial_year=_clean(row.get("financial_year")),
                source_file=os.path.basename(file_path),
                imported_by=username,
            ))
            existing.add(code)
            imported += 1

        session.commit()
    finally:
        session.close()

    log_action("IMPORT_IRM_WORKLIST", "irm_project", "", {
        "file": os.path.basename(file_path),
        "imported": imported,
        "errors": len(errors),
        "warnings": len(warnings),
    }, user_role=user_role)

    return ImportResult(total_rows=len(frame), imported=imported,
                        errors=errors, warnings=warnings)


def clear_irm_worklist(user_role: str = "Administrator") -> int:
    session = get_session()
    try:
        removed = session.query(IRMProject).delete()
        session.commit()
    finally:
        session.close()
    log_action("CLEAR_IRM_WORKLIST", "irm_project", "", {"removed": removed},
               user_role=user_role)
    return removed


# ── Export ────────────────────────────────────────────────────────────────────

def _irm_lookup() -> dict[str, IRMProject]:
    return {p.project_code: p for p in list_irm_projects()}


def capture_to_feature(row: LocationCapture,
                       irm: IRMProject | None = None) -> dict:
    """Convert a capture row into a GeoJSON Feature with full provenance."""
    try:
        geometry = json.loads(row.geometry_geojson or "{}")
    except (ValueError, TypeError):
        geometry = {}

    return {
        "type": "Feature",
        "geometry": geometry,
        "properties": {
            "capture_id": row.id,
            "project_code": row.project_code,
            "project_name": row.project_name,
            "geometry_type": row.geometry_type,
            "is_primary": bool(row.is_primary),
            "verification_status": row.verification_status,
            "lifecycle_status": row.lifecycle_status,
            "comments": row.comments or "",
            "centroid_lat": row.centroid_lat,
            "centroid_lon": row.centroid_lon,
            "length_m": row.length_m,
            "area_m2": row.area_m2,
            "captured_by": row.captured_by,
            "captured_at": row.created_at,
            "updated_at": row.updated_at,
            "search_query": row.search_query or "",
            "irm_latitude": getattr(irm, "irm_latitude", None),
            "irm_longitude": getattr(irm, "irm_longitude", None),
            "irm_address": getattr(irm, "irm_address", "") or "",
            "metres_moved": metres_moved(row, irm),
            "department": getattr(irm, "department", "") or "",
            "municipality": getattr(irm, "municipality", "") or "",
        },
    }


def export_captures_geojson(captures: list[LocationCapture] | None = None) -> bytes:
    """Every capture as a GeoJSON FeatureCollection."""
    rows = list_captures() if captures is None else captures
    irm = _irm_lookup()
    collection = {
        "type": "FeatureCollection",
        "name": "STAM_IRM_Location_Captures",
        "generated_at": _now(),
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}},
        "features": [capture_to_feature(r, irm.get(r.project_code)) for r in rows],
    }
    return json.dumps(collection, indent=2).encode("utf-8")


def primary_captures(captures: list[LocationCapture] | None = None
                     ) -> list[LocationCapture]:
    """
    One capture per project code — the flagged primary, else the earliest.

    This is what resolves many-geometries-per-project down to STAM's single
    latitude/longitude Project model.
    """
    rows = list_captures() if captures is None else captures
    chosen: dict[str, LocationCapture] = {}
    for row in sorted(rows, key=lambda r: (r.created_at or "")):
        current = chosen.get(row.project_code)
        if current is None or (row.is_primary and not current.is_primary):
            chosen[row.project_code] = row
    return [chosen[code] for code in sorted(chosen)]


# Column order matches importer.REQUIRED_PROJECT_COLS so the export can be fed
# straight back into STAM's existing Excel import.
STAM_EXPORT_COLUMNS = [
    "project_id", "project_name", "department", "project_type",
    "latitude", "longitude", "budget_rands", "budget_year",
    "readiness_status", "municipality", "ward",
    "geometry_type", "verification_status", "lifecycle_status",
    "comments", "captured_by", "captured_at",
    "irm_latitude", "irm_longitude", "metres_moved",
]

# STAM requires a readiness value from its own vocabulary; map ours onto it.
_READINESS_FROM_LIFECYCLE = {
    "Planning": "Planning",
    "Design": "Design",
    "Tender": "Ready",
    "Under Construction": "Ready",
    "Practically Complete": "Ready",
    "On Hold": "Concept",
    "Cancelled": "Concept",
}


def export_rows(captures: list[LocationCapture] | None = None) -> list[dict]:
    """One dict per project code, shaped for STAM's project importer."""
    irm = _irm_lookup()
    rows = []
    for row in primary_captures(captures):
        source = irm.get(row.project_code)
        rows.append({
            "project_id": row.project_code,
            "project_name": row.project_name,
            "department": getattr(source, "department", "") or "",
            "project_type": "",
            "latitude": row.centroid_lat,
            "longitude": row.centroid_lon,
            "budget_rands": getattr(source, "budget_rands", None),
            "budget_year": getattr(source, "financial_year", "") or "",
            "readiness_status": _READINESS_FROM_LIFECYCLE.get(
                row.lifecycle_status, "Concept"),
            "municipality": getattr(source, "municipality", "") or "",
            "ward": "",
            "geometry_type": row.geometry_type,
            "verification_status": row.verification_status,
            "lifecycle_status": row.lifecycle_status,
            "comments": row.comments or "",
            "captured_by": row.captured_by or "",
            "captured_at": row.created_at,
            "irm_latitude": getattr(source, "irm_latitude", None),
            "irm_longitude": getattr(source, "irm_longitude", None),
            "metres_moved": metres_moved(row, source),
        })
    return rows


def export_captures_excel(captures: list[LocationCapture] | None = None) -> bytes:
    """Corrected locations as an Excel workbook STAM's importer can read."""
    frame = pd.DataFrame(export_rows(captures), columns=STAM_EXPORT_COLUMNS)
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        frame.to_excel(writer, index=False, sheet_name="Corrected Locations")
    return buffer.getvalue()

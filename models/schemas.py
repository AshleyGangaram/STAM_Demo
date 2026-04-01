"""
Pydantic models for STAM — used for AI structured outputs and API validation.
"""

from __future__ import annotations
from typing import Optional
from pydantic import BaseModel, Field


# ── Scoring ───────────────────────────────────────────────────────────────────

class ScoreCriterion(BaseModel):
    name: str
    score: float
    max_score: float
    explanation: str = ""


class ScoreResult(BaseModel):
    project_id: str
    project_name: str
    total_score: float
    max_score: float = 100.0
    classification: str        # Priority Now | Priority Next Cycle | Conditional | Not Recommended
    criteria: list[ScoreCriterion] = Field(default_factory=list)
    overall_explanation: str = ""
    recommendation: str = ""


# ── Spatial analysis ──────────────────────────────────────────────────────────

class NearestFacility(BaseModel):
    name: str
    facility_type: str
    distance_km: float
    capacity: Optional[int] = None
    occupancy_pct: Optional[float] = None


class SpatialContext(BaseModel):
    project_id: str
    gsdf_zone: str = ""
    msdf_alignment: str = ""
    nearest_same_type_km: float = 0.0
    nearest_clinic_km: float = 0.0
    nearest_school_km: float = 0.0
    population_density: float = 0.0    # persons/km²
    transport_access_score: float = 0.0
    urban_area: bool = True
    nearby_facilities: list[NearestFacility] = Field(default_factory=list)


# ── Query ─────────────────────────────────────────────────────────────────────

class QueryCriteria(BaseModel):
    query_name: str = ""
    facility_type: Optional[str] = None
    min_distance_km: Optional[float] = None
    min_population_density: Optional[float] = None
    gsdf_classification: Optional[list[str]] = None
    budget_year: Optional[str] = None
    readiness_status: Optional[list[str]] = None
    municipality: Optional[str] = None
    max_total_score: Optional[int] = None
    min_total_score: Optional[int] = None


class QueryResult(BaseModel):
    query: QueryCriteria
    total_results: int
    summary: str = ""
    features: list[dict] = Field(default_factory=list)   # GeoJSON features


# ── AI Report ─────────────────────────────────────────────────────────────────

class ReportSection(BaseModel):
    heading: str
    content: str


class AnalysisReport(BaseModel):
    title: str
    municipality: str
    sector: str
    generated_at: str = ""
    executive_summary: str = ""
    sections: list[ReportSection] = Field(default_factory=list)
    recommendation: str = ""
    risk_notes: str = ""


# ── Import validation ─────────────────────────────────────────────────────────

class ImportError(BaseModel):
    row: int
    field: str
    message: str
    value: str = ""


class ImportResult(BaseModel):
    total_rows: int
    imported: int
    errors: list[ImportError] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

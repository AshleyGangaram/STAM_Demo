"""
Claude AI integration for STAM.

Functions:
  - generate_spatial_analysis_narrative(project, context) → str
  - generate_analysis_report(municipality, sector, projects, facilities) → AnalysisReport
  - generate_query_summary(query_criteria, results) → str
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import anthropic
from dotenv import load_dotenv

from models.schemas import AnalysisReport, ReportSection

load_dotenv()

_PROMPTS_DIR = os.path.join(os.path.dirname(__file__), "..", "prompts")

ANALYSIS_MODEL = "claude-sonnet-4-6"
PARSING_MODEL = "claude-haiku-4-5-20251001"


def _get_api_key() -> str:
    """Resolve API key from Streamlit secrets (cloud) or env var (local)."""
    try:
        import streamlit as st
        key = st.secrets.get("ANTHROPIC_API_KEY", "")
        if key:
            return key
    except Exception:
        pass
    return os.environ.get("ANTHROPIC_API_KEY", "")


def _client() -> anthropic.Anthropic:
    key = _get_api_key()
    if not key:
        raise ValueError("ANTHROPIC_API_KEY not set. Add it to Streamlit secrets or .env file.")
    return anthropic.Anthropic(api_key=key)


def _load_prompt(filename: str) -> str:
    path = os.path.join(_PROMPTS_DIR, filename)
    if os.path.exists(path):
        with open(path) as f:
            return f.read().strip()
    return ""


def generate_spatial_narrative(
    project_name: str,
    project_type: str,
    municipality: str,
    score: int,
    classification: str,
    score_breakdown: dict,
    nearest_facility_km: float = 0.0,
) -> str:
    """
    Generate a short AI narrative explaining a project's STAM score.
    Used in the Scoring page "Explain Score" panel.
    Uses Haiku for speed and cost.
    """
    system_prompt = _load_prompt("spatial_analysis.txt") or (
        "You are a spatial planning analyst for Gauteng Province, South Africa. "
        "Write a concise (150-200 word) professional narrative explaining a STAM "
        "appraisal score for a capital project. Focus on spatial transformation "
        "rationale, SPLUMA alignment, and service delivery impact. "
        "Be factual and use planning terminology appropriate for a budget committee."
    )

    user_msg = f"""
Project: {project_name}
Type: {project_type}
Municipality: {municipality}
STAM Total Score: {score}/100
Classification: {classification}
Score Breakdown: {json.dumps(score_breakdown, indent=2)}
Distance to nearest similar facility: {nearest_facility_km:.1f} km

Write a professional spatial analysis narrative for this project.
"""

    try:
        client = _client()
        response = client.messages.create(
            model=PARSING_MODEL,
            max_tokens=512,
            system=system_prompt,
            messages=[{"role": "user", "content": user_msg}],
        )
        return response.content[0].text.strip()
    except Exception as exc:
        return (
            f"[AI narrative unavailable: {exc}]\n\n"
            f"This project scored {score}/100 and is classified as {classification}. "
            f"Manual review recommended."
        )


def generate_analysis_report(
    municipality: str,
    sector: str,
    projects: list,
    facilities: list,
    population_data: dict | None = None,
) -> AnalysisReport:
    """
    Generate a full STAM decision support report using Claude Sonnet.
    Used in the Reports page for the 8-section analysis report.
    """
    system_prompt = _load_prompt("report_generation.txt") or (
        "You are a senior spatial planning and infrastructure analyst for Gauteng Province, "
        "South Africa. Generate a professional STAM (Spatial Transformation Appraisal "
        "Mechanism) decision support report. The report must:\n"
        "- Follow the 8-section structure provided\n"
        "- Use evidence-based language aligned with SPLUMA, GSDF 2030, NDP\n"
        "- Include a clear recommendation sentence\n"
        "- Be suitable for presentation to a Provincial Budget Committee\n"
        "- Quantify service gaps using the data provided\n"
        "Output as JSON matching the schema provided."
    )

    # Prepare project summaries
    project_summaries = []
    for p in projects:
        project_summaries.append({
            "project_id": getattr(p, "project_id", ""),
            "name": getattr(p, "name", ""),
            "type": getattr(p, "project_type", ""),
            "budget_rands": getattr(p, "budget_rands", 0),
            "stam_score": getattr(p, "total_score", 0),
            "classification": getattr(p, "classification", ""),
            "gsdf_zone": getattr(p, "gsdf_classification", ""),
            "readiness": getattr(p, "readiness_status", ""),
            "municipality": getattr(p, "municipality", ""),
        })

    facility_summaries = []
    for f in facilities:
        cap = getattr(f, "capacity", 0) or 0
        occ = getattr(f, "current_occupancy", 0) or 0
        facility_summaries.append({
            "name": getattr(f, "name", ""),
            "type": getattr(f, "facility_type", ""),
            "capacity": cap,
            "occupancy": occ,
            "overcapacity": occ > cap,
            "municipality": getattr(f, "municipality", ""),
        })

    user_msg = f"""
Generate a STAM Analysis Report in JSON format.

Municipality: {municipality}
Sector: {sector}
Report Date: {datetime.now(timezone.utc).strftime('%d %B %Y')}

Capital Projects under assessment:
{json.dumps(project_summaries, indent=2)}

Existing {sector} facilities:
{json.dumps(facility_summaries, indent=2)}

Output JSON with this exact schema:
{{
  "executive_summary": "string (2-3 sentences)",
  "sections": [
    {{"heading": "1. Area Profile", "content": "..."}},
    {{"heading": "2. Existing Facilities and Capacity", "content": "..."}},
    {{"heading": "3. Service Gap Analysis", "content": "..."}},
    {{"heading": "4. Candidate Project Options", "content": "..."}},
    {{"heading": "5. STAM Scorecards", "content": "..."}},
    {{"heading": "6. Indicative Budget Estimates", "content": "..."}},
    {{"heading": "7. Recommendation and Rationale", "content": "..."}},
    {{"heading": "8. Risk and Readiness Notes", "content": "..."}}
  ],
  "recommendation": "One clear recommendation sentence starting with 'Option...' or 'The STAM appraisal recommends...'",
  "risk_notes": "string"
}}
"""

    try:
        client = _client()
        response = client.messages.create(
            model=ANALYSIS_MODEL,
            max_tokens=4096,
            system=system_prompt,
            messages=[{"role": "user", "content": user_msg}],
        )
        raw = response.content[0].text.strip()
        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw)

        sections = [ReportSection(**s) for s in data.get("sections", [])]
        return AnalysisReport(
            title=f"STAM {sector.title()} Capacity Assessment — {municipality}",
            municipality=municipality,
            sector=sector,
            generated_at=datetime.now(timezone.utc).strftime("%d %B %Y %H:%M UTC"),
            executive_summary=data.get("executive_summary", ""),
            sections=sections,
            recommendation=data.get("recommendation", ""),
            risk_notes=data.get("risk_notes", ""),
        )
    except Exception as exc:
        return AnalysisReport(
            title=f"STAM {sector.title()} Assessment — {municipality}",
            municipality=municipality,
            sector=sector,
            generated_at=datetime.now(timezone.utc).strftime("%d %B %Y %H:%M UTC"),
            executive_summary=f"Report generation encountered an error: {exc}. Please check your API key and retry.",
            sections=[],
            recommendation="Unable to generate recommendation — AI service unavailable.",
            risk_notes="",
        )


def generate_query_summary(query_name: str, criteria: dict, result_count: int,
                           sample_results: list[dict]) -> str:
    """Generate a 2-3 sentence plain-English summary of a query result set."""
    user_msg = (
        f"Query: '{query_name}'\n"
        f"Criteria: {json.dumps(criteria)}\n"
        f"Results: {result_count} projects/areas matched\n"
        f"Sample: {json.dumps(sample_results[:3])}\n\n"
        "Write a 2-3 sentence plain-English summary of what this query found, "
        "suitable for a Gauteng planning committee briefing."
    )
    try:
        client = _client()
        response = client.messages.create(
            model=PARSING_MODEL,
            max_tokens=200,
            system="You are a spatial planning analyst. Summarise query results concisely.",
            messages=[{"role": "user", "content": user_msg}],
        )
        return response.content[0].text.strip()
    except Exception:
        return (
            f"Query '{query_name}' returned {result_count} results matching the specified criteria. "
            "Review the results table and map for detailed analysis."
        )

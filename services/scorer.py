"""
STAM Scoring Engine — 7-criteria weighted appraisal model.

Criteria and default weights (total = 100):
  1. GSDF 2030 overlap          20 pts
  2. Municipal SDF priority     15 pts
  3. Service gap / access       20 pts
  4. Public transport access    10 pts
  5. Brownfield / densification 10 pts
  6. Due diligence readiness    15 pts
  7. Cost efficiency            10 pts

Classifications:
  80-100  Priority Now
  65-79   Priority Next Cycle
  50-64   Conditional / Needs Review
  0-49    Not Recommended
"""

from __future__ import annotations

import json
import math
from typing import Any

from models.schemas import ScoreCriterion, ScoreResult

# ── Default weights ───────────────────────────────────────────────────────────

DEFAULT_WEIGHTS: dict[str, int] = {
    "gsdf_overlap":     20,
    "msdf_priority":    15,
    "service_gap":      20,
    "transport_access": 10,
    "brownfield":       10,
    "readiness":        15,
    "cost_efficiency":  10,
}

# Budget benchmarks per project type (ZAR per unit or per m²)
COST_BENCHMARKS: dict[str, float] = {
    "clinic":     15_000_000,
    "school":     20_000_000,
    "library":    8_000_000,
    "community":  8_000_000,
    "housing":    5_500_000,   # per 100 units
    "road":       45_000_000,
    "commercial": 100_000_000,
    "industrial": 80_000_000,
}

# Readiness scores
READINESS_SCORES: dict[str, int] = {
    "Ready":    15,
    "Design":   10,
    "Planning":  6,
    "Concept":   2,
}

# GSDF classification multipliers (fraction of max)
GSDF_MULTIPLIERS: dict[str, float] = {
    "Priority":    1.0,
    "Accommodate": 0.5,
    "Discourage":  0.1,
    "Outside":     0.0,
}

# MSDF alignment derived from GSDF (simplified for demo)
MSDF_MULTIPLIERS: dict[str, float] = {
    "Priority":    1.0,
    "Accommodate": 0.65,
    "Discourage":  0.2,
    "Outside":     0.3,
}


def classify(score: float) -> str:
    if score >= 80:
        return "Priority Now"
    if score >= 65:
        return "Priority Next Cycle"
    if score >= 50:
        return "Conditional"
    return "Not Recommended"


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


def score_project(
    project: Any,
    facilities: list[Any],
    weights: dict[str, int] | None = None,
) -> ScoreResult:
    """
    Score a single project using the 7-criteria STAM model.

    Args:
        project: ORM Project row or dict-like with project fields.
        facilities: List of ORM Facility rows from the database.
        weights: Optional weight override dict. Uses DEFAULT_WEIGHTS if None.

    Returns:
        ScoreResult with score, breakdown and explanation.
    """
    w = weights or DEFAULT_WEIGHTS
    gsdf = getattr(project, "gsdf_classification", None) or "Outside"
    readiness = getattr(project, "readiness_status", None) or "Concept"
    proj_type = getattr(project, "project_type", None) or "clinic"
    budget = getattr(project, "budget_rands", None) or 0
    lat = getattr(project, "latitude", None) or -26.0
    lon = getattr(project, "longitude", None) or 28.0

    # ── 1. GSDF overlap ───────────────────────────────────────────────────────
    gsdf_score = round(w["gsdf_overlap"] * GSDF_MULTIPLIERS.get(gsdf, 0.0))
    gsdf_expl = {
        "Priority":    f"Project falls within a GSDF 2030 Priority zone — full {w['gsdf_overlap']} points awarded.",
        "Accommodate": f"Project falls within a GSDF 2030 Accommodate zone — 50% of {w['gsdf_overlap']} points.",
        "Discourage":  f"Project is in a GSDF 2030 Discourage zone — only 10% of {w['gsdf_overlap']} points.",
        "Outside":     "Project location is outside all GSDF 2030 focus areas — 0 points.",
    }.get(gsdf, "GSDF classification unknown.")

    # ── 2. Municipal SDF priority ─────────────────────────────────────────────
    msdf_score = round(w["msdf_priority"] * MSDF_MULTIPLIERS.get(gsdf, 0.3))
    msdf_expl = (
        f"Municipal SDF alignment derived from provincial GSDF zone ({gsdf}). "
        f"Score: {msdf_score}/{w['msdf_priority']}."
    )

    # ── 3. Service gap ────────────────────────────────────────────────────────
    same_type = [f for f in facilities if getattr(f, "facility_type", "") == proj_type]
    if same_type:
        min_dist = min(
            _haversine_km(lat, lon, getattr(f, "latitude", lat), getattr(f, "longitude", lon))
            for f in same_type
        )
        # Overcapacity bonus: if nearest facility is over capacity, gap is higher
        nearest = min(same_type, key=lambda f: _haversine_km(
            lat, lon, getattr(f, "latitude", lat), getattr(f, "longitude", lon)
        ))
        cap = getattr(nearest, "capacity", 1) or 1
        occ = getattr(nearest, "current_occupancy", 0) or 0
        overcapacity_bonus = 1.3 if occ > cap else 1.0

        # Score: 5+ km away gets near-full score, <1km gets very low
        dist_factor = min(min_dist / 5.0, 1.0) * overcapacity_bonus
        gap_score = round(min(w["service_gap"] * dist_factor, w["service_gap"]))
        gap_expl = (
            f"Nearest existing {proj_type} is {min_dist:.1f} km away "
            f"(capacity: {cap}, occupancy: {occ} = "
            f"{'over' if occ > cap else 'under'} capacity). "
            f"Service gap score: {gap_score}/{w['service_gap']}."
        )
    else:
        gap_score = round(w["service_gap"] * 0.9)
        gap_expl = f"No existing {proj_type} facilities in database — high service gap assumed."

    # ── 4. Transport access ───────────────────────────────────────────────────
    # Simplified: projects in Priority zones assumed to have better transport
    transport_map = {"Priority": 0.8, "Accommodate": 0.65, "Discourage": 0.4, "Outside": 0.5}
    transport_score = round(w["transport_access"] * transport_map.get(gsdf, 0.5))
    transport_expl = (
        f"Transport accessibility estimated from zone classification ({gsdf}). "
        f"Score: {transport_score}/{w['transport_access']}."
    )

    # ── 5. Brownfield / densification ─────────────────────────────────────────
    # Urban townships score higher; commercial/industrial in discourage score low
    brownfield_map = {
        "clinic":     {"Priority": 0.8, "Accommodate": 0.7, "Discourage": 0.4, "Outside": 0.5},
        "school":     {"Priority": 0.7, "Accommodate": 0.65, "Discourage": 0.4, "Outside": 0.5},
        "library":    {"Priority": 0.7, "Accommodate": 0.65, "Discourage": 0.4, "Outside": 0.5},
        "community":  {"Priority": 0.9, "Accommodate": 0.7, "Discourage": 0.5, "Outside": 0.5},
        "housing":    {"Priority": 0.7, "Accommodate": 0.6, "Discourage": 0.3, "Outside": 0.4},
        "road":       {"Priority": 0.7, "Accommodate": 0.6, "Discourage": 0.3, "Outside": 0.4},
        "commercial": {"Priority": 0.5, "Accommodate": 0.4, "Discourage": 0.2, "Outside": 0.3},
        "industrial": {"Priority": 0.4, "Accommodate": 0.3, "Discourage": 0.1, "Outside": 0.2},
    }
    bf_factor = brownfield_map.get(proj_type, {}).get(gsdf, 0.5)
    brownfield_score = round(w["brownfield"] * bf_factor)
    brownfield_expl = (
        f"Brownfield/densification benefit for {proj_type} in {gsdf} zone. "
        f"Score: {brownfield_score}/{w['brownfield']}."
    )

    # ── 6. Readiness ──────────────────────────────────────────────────────────
    readiness_score = READINESS_SCORES.get(readiness, 2)
    # Scale to configured weight
    readiness_score = round(readiness_score * w["readiness"] / 15)
    readiness_expl = {
        "Ready":    f"Project is fully ready for implementation — full {w['readiness']} points.",
        "Design":   f"Project is in detailed design — {readiness_score}/{w['readiness']} points.",
        "Planning": f"Project is in planning phase — {readiness_score}/{w['readiness']} points.",
        "Concept":  f"Project is at concept stage only — {readiness_score}/{w['readiness']} points.",
    }.get(readiness, f"Readiness status unknown — {readiness_score}/{w['readiness']}.")

    # ── 7. Cost efficiency ────────────────────────────────────────────────────
    benchmark = COST_BENCHMARKS.get(proj_type, 20_000_000)
    if budget > 0:
        ratio = benchmark / budget
        cost_factor = min(max(ratio, 0.1), 1.0)
    else:
        cost_factor = 0.5
    cost_score = round(w["cost_efficiency"] * cost_factor)
    cost_expl = (
        f"Benchmark for {proj_type}: R{benchmark:,.0f}. "
        f"Proposed budget: R{budget:,.0f}. "
        f"Cost efficiency score: {cost_score}/{w['cost_efficiency']}."
    )

    # ── Total ─────────────────────────────────────────────────────────────────
    total = gsdf_score + msdf_score + gap_score + transport_score + brownfield_score + readiness_score + cost_score
    total = min(total, 100)
    label = classify(total)

    # Overall narrative
    if total >= 80:
        overall = (
            f"This project scores {total}/100 and is classified as '{label}'. "
            f"It is strongly aligned with the Gauteng Spatial Development Framework, "
            f"addresses a measurable service gap, and is ready for implementation. "
            f"STAM recommends prioritisation in the current MTEF cycle."
        )
    elif total >= 65:
        overall = (
            f"This project scores {total}/100 and is classified as '{label}'. "
            f"It shows good spatial alignment but may require improved readiness or "
            f"further due diligence before inclusion in the current budget cycle. "
            f"Consider for the next MTEF year."
        )
    elif total >= 50:
        overall = (
            f"This project scores {total}/100 and is classified as '{label}'. "
            f"Spatial alignment is partial and further review is recommended before "
            f"committing budget. Address readiness gaps and confirm MSDF alignment."
        )
    else:
        overall = (
            f"This project scores {total}/100 and is classified as '{label}'. "
            f"The project location is not aligned with GSDF/MSDF spatial priorities "
            f"and does not address a demonstrable service gap. "
            f"Budget commitment is not recommended at this stage."
        )

    return ScoreResult(
        project_id=getattr(project, "project_id", ""),
        project_name=getattr(project, "name", ""),
        total_score=total,
        classification=label,
        criteria=[
            ScoreCriterion(name="GSDF 2030 Priority Overlap",       score=gsdf_score,       max_score=w["gsdf_overlap"],     explanation=gsdf_expl),
            ScoreCriterion(name="Municipal SDF Priority",            score=msdf_score,       max_score=w["msdf_priority"],    explanation=msdf_expl),
            ScoreCriterion(name="Service Gap / Access",              score=gap_score,        max_score=w["service_gap"],      explanation=gap_expl),
            ScoreCriterion(name="Public Transport Accessibility",    score=transport_score,  max_score=w["transport_access"], explanation=transport_expl),
            ScoreCriterion(name="Brownfield / Densification Benefit",score=brownfield_score, max_score=w["brownfield"],       explanation=brownfield_expl),
            ScoreCriterion(name="Due Diligence Readiness",           score=readiness_score,  max_score=w["readiness"],        explanation=readiness_expl),
            ScoreCriterion(name="Cost Efficiency",                   score=cost_score,       max_score=w["cost_efficiency"],  explanation=cost_expl),
        ],
        overall_explanation=overall,
        recommendation=(
            "Prioritise in current MTEF cycle." if total >= 80
            else "Consider for next MTEF cycle." if total >= 65
            else "Review before committing budget." if total >= 50
            else "Do not prioritise — spatial alignment insufficient."
        ),
    )

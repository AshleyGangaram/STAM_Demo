"""
STAM Reports — V2-style report generation with card selection,
municipality filter, embedded map, area profile, facilities table,
and candidate project listing.
"""

from __future__ import annotations

import io
import os
from typing import Any

import folium
import pandas as pd
import streamlit as st
from streamlit_folium import st_folium

from services.db import Facility, Project, get_session, log_action
from services.spatial import SCORE_COLOURS

def _util(f: Facility) -> int:
    """Compute utilisation % from occupancy / capacity."""
    if f.capacity and f.capacity > 0 and f.current_occupancy is not None:
        return round(f.current_occupancy / f.capacity * 100)
    return 0


_HERE = os.path.dirname(__file__)
DEMO_DIR = os.path.join(_HERE, "..", "data", "demo")

# ── Report type definitions ──────────────────────────────────────────────────

_REPORT_TYPES: list[dict[str, Any]] = [
    {
        "key": "education",
        "icon": "🏫",
        "title": "Education Capacity Assessment",
        "subtitle": "School gap analysis by municipality",
        "colour": "#0097A7",
        "project_types": ["school"],
        "facility_types": ["school"],
    },
    {
        "key": "health",
        "icon": "🏥",
        "title": "Health Facility Gap Analysis",
        "subtitle": "Clinic coverage and demand",
        "colour": "#00897B",
        "project_types": ["clinic"],
        "facility_types": ["clinic"],
    },
    {
        "key": "portfolio",
        "icon": "📋",
        "title": "Portfolio Appraisal Summary",
        "subtitle": "All projects, scores, and classifications",
        "colour": "#5C6BC0",
        "project_types": [],
        "facility_types": [],
    },
    {
        "key": "single",
        "icon": "📄",
        "title": "Single Project Decision Report",
        "subtitle": "Full scorecard and recommendation",
        "colour": "#78909C",
        "project_types": [],
        "facility_types": [],
    },
]


def _card_html(rt: dict[str, Any], selected: bool) -> str:
    border = f"3px solid {rt['colour']}" if selected else "1px solid #ddd"
    bg = "#f0faf8" if selected else "#ffffff"
    return (
        f"<div style='border:{border};border-radius:8px;padding:14px 16px;"
        f"background:{bg};cursor:pointer;min-height:80px'>"
        f"<div style='font-size:1.5em'>{rt['icon']}</div>"
        f"<div style='font-weight:600;margin-top:4px;color:#1a1a1a'>{rt['title']}</div>"
        f"<div style='font-size:0.82em;color:#666'>{rt['subtitle']}</div>"
        f"</div>"
    )


def _build_map(projects: list[Project], facilities: list[Facility]) -> folium.Map:
    """Small Folium map showing candidate project locations."""
    m = folium.Map(location=[-26.1, 28.0], zoom_start=9, tiles=None)
    folium.TileLayer("CartoDB positron", name="CartoDB Light", control=True).add_to(m)
    folium.TileLayer("OpenStreetMap", name="OpenStreetMap", control=True).add_to(m)
    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        attr="Esri",
        name="Satellite",
        overlay=False,
        control=True,
    ).add_to(m)
    folium.LayerControl(collapsed=False).add_to(m)
    for p in projects:
        if p.latitude and p.longitude:
            colour = SCORE_COLOURS.get(p.classification, "#9b9b9b")
            folium.CircleMarker(
                location=[p.latitude, p.longitude],
                radius=8,
                color=colour,
                fill=True,
                fill_color=colour,
                fill_opacity=0.85,
                popup=f"<b>{p.name}</b><br>Score: {p.total_score or '–'}",
            ).add_to(m)
    return m


def render() -> None:
    st.title("📄 Reports")
    st.caption("Generate structured decision-support reports with spatial evidence.")

    session = get_session()
    all_projects = session.query(Project).all()
    all_facilities = session.query(Facility).all()

    # ── Report type cards ────────────────────────────────────────────────────
    if "report_type" not in st.session_state:
        st.session_state.report_type = "education"

    cols = st.columns(len(_REPORT_TYPES))
    for i, rt in enumerate(_REPORT_TYPES):
        with cols[i]:
            is_selected = st.session_state.report_type == rt["key"]
            st.markdown(_card_html(rt, is_selected), unsafe_allow_html=True)
            if st.button("Select", key=f"sel_{rt['key']}", use_container_width=True):
                st.session_state.report_type = rt["key"]
                st.rerun()

    st.divider()

    active = next(r for r in _REPORT_TYPES if r["key"] == st.session_state.report_type)

    # ── Filters ──────────────────────────────────────────────────────────────
    municipalities = sorted({p.municipality for p in all_projects if p.municipality})
    filter_col, btn_col = st.columns([2, 1])

    with filter_col:
        if active["key"] == "single":
            project_labels = {f"[{p.project_id}] {p.name}": p for p in all_projects}
            selected_label = st.selectbox("Select project", list(project_labels.keys()))
            selected_muni = None
        else:
            selected_muni = st.selectbox(
                "Municipality (optional)",
                ["All municipalities"] + municipalities,
            )
            selected_label = None

    with btn_col:
        st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
        generate = st.button("🟢 Generate Report", type="primary", use_container_width=True)

    if not generate:
        session.close()
        return

    # ── Filter data ──────────────────────────────────────────────────────────
    if active["key"] == "single" and selected_label:
        proj = project_labels[selected_label]
        projects = [proj]
        facilities = [
            f for f in all_facilities
            if f.municipality == proj.municipality
        ]
    else:
        muni = None if selected_muni == "All municipalities" else selected_muni
        ptypes = active["project_types"]
        projects = [
            p for p in all_projects
            if (muni is None or p.municipality == muni)
            and (not ptypes or p.project_type in ptypes)
        ]
        facilities = [
            f for f in all_facilities
            if (muni is None or f.municipality == muni)
            and (not active["facility_types"] or f.facility_type in active["facility_types"])
        ]

    if not projects:
        st.warning("No projects match the selected criteria.")
        session.close()
        return

    log_action(
        "GENERATE_REPORT", "report", active["key"],
        {"type": active["title"], "projects": len(projects)},
        user_role=st.session_state.get("user_role", "Analyst"),
    )

    # ── Export buttons ────────────────────────────────────────────────────────
    dl1, dl2, _ = st.columns([1, 1, 3])
    with dl1:
        try:
            from services.report_gen import generate_report_docx
            from services.ai_analyzer import generate_analysis_report

            with st.spinner("Generating AI report..."):
                report_obj = generate_analysis_report(
                    municipality=projects[0].municipality or "Gauteng",
                    sector=active["key"],
                    projects=projects,
                    facilities=facilities,
                )
                docx_bytes = generate_report_docx(report_obj, projects)
            st.download_button(
                "📥 Download Word Document",
                data=docx_bytes,
                file_name=f"STAM_{active['key']}_report.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        except Exception as exc:
            st.error(f"❌ Report generation failed: {str(exc)[:100]}")
            st.download_button("📥 Download Word Document", data=b"", file_name="report.docx", disabled=True)

    with dl2:
        rows_export = []
        for p in projects:
            rows_export.append({
                "Project ID": p.project_id,
                "Name": p.name,
                "Municipality": p.municipality,
                "Score": p.total_score or 0,
                "Classification": p.classification or "Pending",
                "Budget (ZAR M)": round((p.budget_rands or 0) / 1e6, 1),
            })
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            pd.DataFrame(rows_export).to_excel(writer, index=False, sheet_name="Report")
        st.download_button(
            "📥 Download Excel",
            data=buf.getvalue(),
            file_name=f"STAM_{active['key']}_report.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    st.divider()

    # ── Rendered report ──────────────────────────────────────────────────────
    muni_label = projects[0].municipality or "Gauteng Province"
    st.markdown(f"### {active['title']}")
    st.caption(f"STAM | Gauteng COGTA | GT/GDeG/031/2025")

    # Map
    st.markdown("#### Candidate Project Locations")
    m = _build_map(projects, facilities)
    st_folium(m, use_container_width=True, height=500, returned_objects=[])

    # Area profile
    st.markdown("#### Area Profile")
    total_capacity = sum(f.capacity or 0 for f in facilities)
    avg_utilisation = (
        sum(_util(f) or 0 for f in facilities) / len(facilities)
        if facilities else 0
    )
    over_capacity = sum(1 for f in facilities if (_util(f) or 0) > 100)
    st.write(
        f"There are currently **{len(facilities)}** existing facilities of this type, "
        f"with a total capacity of **{total_capacity:,}** and an average utilisation of "
        f"**{avg_utilisation:.0f}%**. **{over_capacity}** facilities are operating above capacity."
    )

    # Facilities table
    if facilities:
        st.markdown("#### Existing Facilities")
        fac_rows = []
        for f in facilities:
            util = _util(f) or 0
            colour = "#d32f2f" if util > 100 else "#1a1a1a"
            fac_rows.append({
                "Facility": f.name,
                "Municipality": f.municipality,
                "Capacity": f.capacity or 0,
                "Utilisation": f"{util}%",
            })

        # Build styled HTML table
        header = (
            "<tr style='border-bottom:2px solid #ddd;text-align:left'>"
            "<th style='padding:6px 10px;font-weight:600'>Facility</th>"
            "<th style='padding:6px 10px;font-weight:600'>Municipality</th>"
            "<th style='padding:6px 10px;font-weight:600;text-align:right'>Capacity</th>"
            "<th style='padding:6px 10px;font-weight:600;text-align:right'>Utilisation</th>"
            "</tr>"
        )
        body_rows = []
        for f in facilities:
            util = _util(f) or 0
            colour = "#d32f2f" if util > 100 else "#1a1a1a"
            body_rows.append(
                f"<tr style='border-bottom:1px solid #eee'>"
                f"<td style='padding:6px 10px'>{f.name}</td>"
                f"<td style='padding:6px 10px'>{f.municipality}</td>"
                f"<td style='padding:6px 10px;text-align:right'>{f.capacity or 0:,}</td>"
                f"<td style='padding:6px 10px;text-align:right;color:{colour};"
                f"font-weight:600'>{util}%</td>"
                f"</tr>"
            )
        st.markdown(
            f"<table style='width:100%;border-collapse:collapse;font-size:0.9em'>"
            f"<thead>{header}</thead><tbody>{''.join(body_rows)}</tbody></table>",
            unsafe_allow_html=True,
        )

    # Candidate projects
    st.markdown("#### Candidate Projects")
    proj_rows = []
    for p in sorted(projects, key=lambda x: -(x.total_score or 0)):
        proj_rows.append({
            "Project": p.name,
            "Municipality": p.municipality,
            "Budget": f"R{(p.budget_rands or 0) / 1e6:.1f}M",
            "Score": p.total_score or 0,
            "Classification": p.classification or "Pending",
        })
    st.dataframe(pd.DataFrame(proj_rows), use_container_width=True, hide_index=True)

    session.close()

"""
STAM Query Builder — Use Case 3 (20 pts)
Spatial and attribute filtering with decision-grade output.
"""

from __future__ import annotations

import io
import json
import os

import folium
import pandas as pd
import streamlit as st
from streamlit_folium import st_folium

from services.db import Facility, Project, SavedQuery, get_session, log_action
from services.spatial import (
    SCORE_COLOURS,
    add_facilities_layer,
    add_projects_layer,
    make_base_map,
    nearest_facility,
)

_HERE = os.path.dirname(__file__)
DEMO_DIR = os.path.join(_HERE, "..", "data", "demo")

# Pre-built demo queries
PRESET_QUERIES = {
    "Query A — Underserved Health Access": {
        "description": "Settlements with population density above threshold and farther than 5 km from an existing clinic.",
        "facility_type": "clinic",
        "min_distance_km": 5.0,
        "gsdf_classification": ["Priority", "Accommodate"],
    },
    "Query B — School Capacity Shortfall": {
        "description": "High-growth priority areas where current school capacity is at or over capacity.",
        "facility_type": "school",
        "min_distance_km": 3.0,
        "gsdf_classification": ["Priority"],
    },
    "Query C — Non-Compliant Budget Projects": {
        "description": "Projects budgeted for 2026/27 that fall outside GSDF/MSDF priority areas.",
        "budget_year": "2026/27",
        "gsdf_classification": ["Discourage", "Outside"],
        "readiness_status": ["Ready", "Design", "Planning", "Concept"],
    },
    "Query D — Ready but Misaligned": {
        "description": "Projects in non-priority areas with advanced readiness status — potential budget risk.",
        "readiness_status": ["Ready", "Design"],
        "gsdf_classification": ["Discourage", "Outside", "Accommodate"],
    },
}


def _apply_query(projects: list, facilities: list, criteria: dict) -> list:
    """Filter projects based on query criteria."""
    results = []
    for p in projects:
        # GSDF filter
        if "gsdf_classification" in criteria:
            if p.gsdf_classification not in criteria["gsdf_classification"]:
                continue

        # Budget year filter
        if "budget_year" in criteria:
            if p.budget_year != criteria["budget_year"]:
                continue

        # Readiness filter
        if "readiness_status" in criteria:
            if p.readiness_status not in criteria["readiness_status"]:
                continue

        # Municipality filter
        if criteria.get("municipality"):
            if p.municipality != criteria["municipality"]:
                continue

        # Score range filter
        if "max_total_score" in criteria:
            if (p.total_score or 0) > criteria["max_total_score"]:
                continue
        if "min_total_score" in criteria:
            if (p.total_score or 0) < criteria["min_total_score"]:
                continue

        # Distance filter (nearest facility of given type)
        if "facility_type" in criteria and "min_distance_km" in criteria:
            nf = nearest_facility(
                p.latitude or -26.0, p.longitude or 28.0,
                criteria["facility_type"], facilities
            )
            if nf and nf["distance_km"] < criteria["min_distance_km"]:
                continue   # close enough already — not a gap

        results.append(p)
    return results


def render():
    st.title("🔍 Query Builder")
    st.caption(
        "Combine spatial, demographic, facility and budget criteria in one query "
        "to support evidence-based investment prioritisation."
    )

    session = get_session()
    projects = session.query(Project).all()
    facilities = session.query(Facility).all()

    tab_preset, tab_custom = st.tabs(["Pre-Built Queries", "Custom Query"])

    # ── Pre-built queries ─────────────────────────────────────────────────────
    with tab_preset:
        st.subheader("Pre-Built Demo Queries")
        selected_query = st.selectbox("Select a query", list(PRESET_QUERIES.keys()))
        qdef = PRESET_QUERIES[selected_query]
        st.info(qdef["description"])

        if st.button("▶ Run Query", type="primary", key="run_preset"):
            _run_and_display(selected_query, qdef, projects, facilities, session)

    # ── Custom query ──────────────────────────────────────────────────────────
    with tab_custom:
        st.subheader("Build a Custom Spatial Query")
        st.caption("Combine any of the criteria below and click Run.")

        municipalities = sorted({p.municipality for p in projects if p.municipality})
        facility_types = ["clinic", "school", "library", "community_hall"]

        c1, c2, c3 = st.columns(3)
        with c1:
            ftype = st.selectbox("Facility type (gap analysis)", ["Any"] + facility_types)
            min_dist = st.number_input("Minimum distance to facility (km)", 0.0, 50.0, 0.0, 0.5)
        with c2:
            gsdf_filter = st.multiselect(
                "GSDF Classification",
                ["Priority", "Accommodate", "Discourage", "Outside"],
                default=[],
            )
            readiness_filter = st.multiselect(
                "Readiness Status",
                ["Ready", "Design", "Planning", "Concept"],
                default=[],
            )
        with c3:
            muni_filter = st.selectbox("Municipality", ["All"] + municipalities)
            budget_year_filter = st.text_input("Budget Year", placeholder="e.g. 2026/27")
            score_min = st.slider("Min STAM Score", 0, 100, 0)
            score_max = st.slider("Max STAM Score", 0, 100, 100)

        query_name = st.text_input("Query name (for saving)", "My Custom Query")

        if st.button("▶ Run Custom Query", type="primary", key="run_custom"):
            criteria: dict = {}
            if ftype != "Any":
                criteria["facility_type"] = ftype
                if min_dist > 0:
                    criteria["min_distance_km"] = min_dist
            if gsdf_filter:
                criteria["gsdf_classification"] = gsdf_filter
            if readiness_filter:
                criteria["readiness_status"] = readiness_filter
            if muni_filter != "All":
                criteria["municipality"] = muni_filter
            if budget_year_filter:
                criteria["budget_year"] = budget_year_filter
            if score_min > 0:
                criteria["min_total_score"] = score_min
            if score_max < 100:
                criteria["max_total_score"] = score_max

            _run_and_display(query_name, criteria, projects, facilities, session)

    session.close()


def _run_and_display(query_name: str, criteria: dict, projects: list,
                      facilities: list, session) -> None:
    results = _apply_query(projects, facilities, criteria)

    log_action(
        "RUN_QUERY", "query", "",
        {"query_name": query_name, "criteria": criteria, "results": len(results)},
        user_role=st.session_state.get("user_role", "Analyst"),
    )

    # Save to DB
    saved = SavedQuery(
        query_name=query_name,
        criteria=json.dumps(criteria),
        results_count=len(results),
        created_by=st.session_state.get("user_role", "Analyst"),
    )
    session.add(saved)
    session.commit()

    st.divider()

    # Summary chips
    chip_col = st.columns(4)
    chip_col[0].metric("Results", len(results))
    chip_col[1].metric("Priority Now", sum(1 for p in results if p.classification == "Priority Now"))
    chip_col[2].metric("Not Recommended", sum(1 for p in results if p.classification == "Not Recommended"))
    chip_col[3].metric("Total Budget", f"R{sum((p.budget_rands or 0) for p in results) / 1e6:.1f}M")

    if not results:
        st.info("No projects matched the query criteria.")
        return

    # AI summary
    with st.spinner("Generating AI query summary..."):
        try:
            from services.ai_analyzer import generate_query_summary
            sample = [{"id": p.project_id, "name": p.name,
                       "score": p.total_score, "class": p.classification}
                      for p in results[:5]]
            summary = generate_query_summary(query_name, criteria, len(results), sample)
            st.info(f"🤖 {summary}")
        except Exception:
            pass

    map_col, table_col = st.columns([3, 2])

    with map_col:
        m = make_base_map()
        m = add_facilities_layer(m, facilities)
        m = add_projects_layer(m, results, highlight_ids=[p.project_id for p in results])
        folium.LayerControl().add_to(m)
        st_folium(m, use_container_width=True, height=500, returned_objects=[])

    with table_col:
        rows = []
        for p in sorted(results, key=lambda x: -(x.total_score or 0)):
            nf = nearest_facility(p.latitude or -26.0, p.longitude or 28.0,
                                   p.project_type or "clinic", [
                                       f for f in facilities
                                       if getattr(f, "facility_type", "") == p.project_type
                                   ])
            rows.append({
                "ID": p.project_id,
                "Name": p.name[:30],
                "Score": p.total_score or 0,
                "Classification": p.classification or "",
                "GSDF": p.gsdf_classification or "",
                "Nearest (km)": nf["distance_km"] if nf else "—",
                "Budget (M)": round((p.budget_rands or 0) / 1e6, 1),
            })
        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True, hide_index=True)

        # Export
        ex_col1, ex_col2 = st.columns(2)

        with ex_col1:
            buf = io.BytesIO()
            with pd.ExcelWriter(buf, engine="openpyxl") as writer:
                df.to_excel(writer, index=False, sheet_name="STAM Query Results")
            st.download_button(
                "⬇ Export to Excel",
                data=buf.getvalue(),
                file_name=f"STAM_Query_{query_name.replace(' ', '_')[:30]}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )

        with ex_col2:
            # PDF export
            try:
                from reportlab.lib.pagesizes import letter, landscape
                from reportlab.lib.styles import getSampleStyleSheet
                from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
                from reportlab.lib import colors
                from reportlab.lib.units import inch
                from reportlab.lib.enums import TA_CENTER

                pdf_buf = io.BytesIO()
                pdf_doc = SimpleDocTemplate(pdf_buf, pagesize=landscape(letter), topMargin=0.5*inch, bottomMargin=0.5*inch)

                styles = getSampleStyleSheet()
                story = []

                # Title
                title_style = styles['Heading2']
                title_style.textColor = colors.HexColor("#1F4E79")
                story.append(Paragraph(f"STAM Query Results: {query_name}", title_style))
                story.append(Spacer(1, 0.2*inch))

                # Table
                table_data = [list(df.columns)] + df.values.tolist()
                table = Table(table_data, colWidths=[0.8*inch]*len(df.columns))
                table.setStyle(TableStyle([
                    ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#1F4E79")),
                    ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                    ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                    ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                    ('FONTSIZE', (0, 0), (-1, 0), 9),
                    ('BOTTOMPADDING', (0, 0), (-1, 0), 6),
                    ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
                    ('GRID', (0, 0), (-1, -1), 1, colors.grey),
                    ('FONTSIZE', (0, 1), (-1, -1), 8),
                ]))
                story.append(table)

                pdf_doc.build(story)
                st.download_button(
                    "⬇ Export to PDF",
                    data=pdf_buf.getvalue(),
                    file_name=f"STAM_Query_{query_name.replace(' ', '_')[:30]}.pdf",
                    mime="application/pdf",
                )
            except Exception as exc:
                st.error(f"PDF export unavailable: {str(exc)[:50]}")

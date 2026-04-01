"""
STAM Reports — Use Case 4 (20 pts)
AI-powered analysis report generation with DOCX export.
"""

from __future__ import annotations

import io
import os
from datetime import datetime, timezone

import pandas as pd
import streamlit as st

from services.db import Facility, Project, get_session, log_action

_SECTOR_SCENARIOS = {
    "Education Capacity Assessment — Ekurhuleni": {
        "municipality": "Ekurhuleni",
        "sector": "education",
        "project_types": ["school"],
        "description": "Assesses school capacity vs demand in Ekurhuleni and evaluates candidate education projects.",
    },
    "Health Services Gap — City of Johannesburg": {
        "municipality": "City of Johannesburg",
        "sector": "health",
        "project_types": ["clinic"],
        "description": "Identifies underserved areas for primary health care in Johannesburg.",
    },
    "Social Facilities Assessment — Sedibeng": {
        "municipality": "Sedibeng",
        "sector": "social development",
        "project_types": ["community", "library"],
        "description": "Evaluates community hall and library gaps in Sedibeng district.",
    },
    "Full Portfolio Review — All Municipalities": {
        "municipality": "All",
        "sector": "capital portfolio",
        "project_types": [],
        "description": "Comprehensive STAM appraisal of all capital projects across the province.",
    },
}


def render():
    st.title("📄 Analysis Reports")
    st.caption(
        "STAM converts geospatial and portfolio evidence into structured decision support "
        "reports for budget committees. Powered by Claude AI."
    )

    session = get_session()
    all_projects = session.query(Project).all()
    all_facilities = session.query(Facility).all()

    tab_generate, tab_scorecard = st.tabs(["Generate Report", "Scorecard Export"])

    with tab_generate:
        st.subheader("Generate STAM Analysis Report")
        st.caption("Select a scenario below or define a custom report scope.")

        scenario_name = st.selectbox("Select scenario", list(_SECTOR_SCENARIOS.keys()))
        scenario = _SECTOR_SCENARIOS[scenario_name]
        st.info(scenario["description"])

        with st.expander("Report options"):
            include_map = st.checkbox("Include project map description", value=True)
            include_budget = st.checkbox("Include budget estimates", value=True)
            include_risk = st.checkbox("Include risk notes", value=True)

        if st.button("🤖 Generate Report", type="primary"):
            municipality = scenario["municipality"]
            sector = scenario["sector"]
            ptypes = scenario["project_types"]

            # Filter relevant projects
            relevant_projects = [
                p for p in all_projects
                if (municipality == "All" or p.municipality == municipality)
                and (not ptypes or p.project_type in ptypes)
            ]
            relevant_facilities = [
                f for f in all_facilities
                if (municipality == "All" or f.municipality == municipality)
            ]

            if not relevant_projects:
                st.warning(
                    f"No projects found for {municipality} / {sector}. "
                    "Try 'Full Portfolio Review' to see all projects."
                )
                session.close()
                return

            with st.spinner("Generating AI-powered STAM report... this may take 15–30 seconds."):
                try:
                    from services.ai_analyzer import generate_analysis_report
                    report = generate_analysis_report(
                        municipality=municipality,
                        sector=sector,
                        projects=relevant_projects,
                        facilities=relevant_facilities,
                    )

                    log_action(
                        "GENERATE_REPORT", "report", "",
                        {"scenario": scenario_name, "municipality": municipality,
                         "sector": sector, "projects": len(relevant_projects)},
                        user_role=st.session_state.get("user_role", "Analyst"),
                    )

                    st.success("Report generated successfully.")
                    st.divider()

                    # Display report
                    st.markdown(f"# {report.title}")
                    st.caption(f"Generated: {report.generated_at}")
                    st.divider()

                    # Recommendation callout
                    if report.recommendation:
                        st.markdown(
                            f"<div style='background:#e8f5e9;border-left:5px solid #1a7431;"
                            f"padding:12px;border-radius:4px;margin:12px 0;color:#1a1a1a'>"
                            f"<b style='color:#1a7431'>STAM Recommendation:</b><br>{report.recommendation}"
                            f"</div>",
                            unsafe_allow_html=True,
                        )

                    st.markdown("## Executive Summary")
                    st.write(report.executive_summary)
                    st.divider()

                    for section in report.sections:
                        st.markdown(f"## {section.heading}")
                        st.write(section.content)

                    if report.risk_notes and include_risk:
                        st.markdown("## Risk and Readiness Notes")
                        st.write(report.risk_notes)

                    # STAM Scorecard table
                    st.divider()
                    st.markdown("## STAM Scorecard Summary")
                    rows = []
                    for p in sorted(relevant_projects, key=lambda x: -(x.total_score or 0)):
                        rows.append({
                            "Project ID": p.project_id,
                            "Name": p.name,
                            "Score": p.total_score or 0,
                            "Classification": p.classification or "Pending",
                            "GSDF Zone": p.gsdf_classification or "",
                            "Readiness": p.readiness_status,
                            "Budget (ZAR M)": round((p.budget_rands or 0) / 1e6, 1),
                        })
                    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

                    # Export buttons
                    st.divider()
                    st.subheader("Export Report")
                    dl_col1, dl_col2 = st.columns(2)

                    with dl_col1:
                        from services.report_gen import generate_report_docx
                        docx_bytes = generate_report_docx(report, relevant_projects)
                        st.download_button(
                            "⬇ Download DOCX Report",
                            data=docx_bytes,
                            file_name=f"STAM_Report_{scenario_name[:40].replace(' ', '_')}.docx",
                            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        )

                    with dl_col2:
                        buf = io.BytesIO()
                        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
                            pd.DataFrame(rows).to_excel(
                                writer, index=False, sheet_name="STAM Scorecard"
                            )
                        st.download_button(
                            "⬇ Download Excel Scorecard",
                            data=buf.getvalue(),
                            file_name=f"STAM_Scorecard_{municipality}.xlsx",
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        )

                except Exception as exc:
                    st.error(
                        f"Report generation failed: {exc}\n\n"
                        "Check that ANTHROPIC_API_KEY is set in your .env file."
                    )

    with tab_scorecard:
        st.subheader("Quick Scorecard Export")
        st.caption("Export the full STAM scorecard for all projects without generating a narrative report.")

        rows = []
        for p in sorted(all_projects, key=lambda x: -(x.total_score or 0)):
            rows.append({
                "Project ID": p.project_id,
                "Name": p.name,
                "Department": p.department,
                "Type": p.project_type,
                "Score": p.total_score or 0,
                "Classification": p.classification or "Pending",
                "GSDF Zone": p.gsdf_classification or "",
                "Readiness": p.readiness_status,
                "Municipality": p.municipality,
                "Budget Year": p.budget_year,
                "Budget (ZAR)": p.budget_rands or 0,
            })

        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True, hide_index=True)

        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="STAM Full Scorecard")
        st.download_button(
            "⬇ Download Full Scorecard (Excel)",
            data=buf.getvalue(),
            file_name="STAM_Full_Scorecard.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    session.close()

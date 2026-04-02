"""
STAM Scoring Engine — Use Case 2 (10 pts)
Demonstrates the 7-criteria automated appraisal model.
"""

from __future__ import annotations

import json

import streamlit as st

from services.db import Facility, Project, ScoreTemplate, get_session, log_action
from services.scorer import DEFAULT_WEIGHTS, classify, score_project
from services.spatial import SCORE_COLOURS, nearest_facility


def render():
    st.title("⚡ STAM Scoring Engine")
    st.caption(
        "Automates the manual prioritisation approach into a configurable rules engine "
        "that produces an explainable score and classification for each project."
    )

    session = get_session()
    projects = session.query(Project).order_by(Project.project_id).all()
    facilities = session.query(Facility).all()
    templates = session.query(ScoreTemplate).all()

    if not projects:
        st.warning("No projects found. Run `python data/seed.py` first.")
        session.close()
        return

    # ── Weight configuration ──────────────────────────────────────────────────
    with st.expander("⚙️ Configure Scoring Weights", expanded=False):
        st.caption("Adjust weights below (total should equal 100). Changes apply immediately.")
        active_template = next((t for t in templates if t.active), None)
        base_weights = json.loads(active_template.weights) if active_template else DEFAULT_WEIGHTS

        cols = st.columns(7)
        labels = {
            "gsdf_overlap":     "GSDF Overlap",
            "msdf_priority":    "MSDF Priority",
            "service_gap":      "Service Gap",
            "transport_access": "Transport",
            "brownfield":       "Brownfield",
            "readiness":        "Readiness",
            "cost_efficiency":  "Cost Efficiency",
        }
        custom_weights = {}
        for i, (key, label) in enumerate(labels.items()):
            custom_weights[key] = cols[i].number_input(
                label, min_value=0, max_value=50,
                value=int(base_weights.get(key, DEFAULT_WEIGHTS[key])),
                step=1, key=f"w_{key}",
            )
        total_w = sum(custom_weights.values())
        if total_w != 100:
            st.warning(f"⚠ Weights sum to {total_w} (should be 100). Scores will be calculated as-is.")
        else:
            st.success("✓ Weights sum to 100")

    st.divider()

    # ── Project selector ──────────────────────────────────────────────────────
    project_options = {f"[{p.project_id}] {p.name}": p for p in projects}

    col_left, col_right = st.columns(2)

    with col_left:
        st.subheader("Project A")
        sel_a = st.selectbox("Select Project A", list(project_options.keys()),
                              index=0, key="proj_a")
        proj_a = project_options[sel_a]

    with col_right:
        st.subheader("Project B (comparison)")
        default_b = min(len(project_options) - 1, 14)  # P015 by default
        sel_b = st.selectbox("Select Project B", list(project_options.keys()),
                              index=default_b, key="proj_b")
        proj_b = project_options[sel_b]

    run_btn = st.button("⚡ Run STAM Appraisal", type="primary", use_container_width=True)

    # Store results in session state to persist across reruns
    if run_btn:
        result_a = score_project(proj_a, facilities, custom_weights)
        result_b = score_project(proj_b, facilities, custom_weights)

        # Store in session state for persistence
        st.session_state.last_result_a = result_a
        st.session_state.last_result_b = result_b
        st.session_state.last_proj_a = proj_a
        st.session_state.last_proj_b = proj_b

        log_action("RUN_STAM_APPRAISAL", "project",
                   f"{proj_a.project_id},{proj_b.project_id}",
                   {"scores": {proj_a.project_id: result_a.total_score,
                               proj_b.project_id: result_b.total_score}},
                   user_role=st.session_state.get("user_role", "Analyst"))

    # Display results if they exist in session state
    if st.session_state.get("last_result_a") and st.session_state.get("last_result_b"):
        result_a = st.session_state.last_result_a
        result_b = st.session_state.last_result_b
        proj_a = st.session_state.last_proj_a
        proj_b = st.session_state.last_proj_b

        st.divider()
        st.subheader("Appraisal Results")

        col_a, col_b = st.columns(2)

        for col, proj, result in [(col_a, proj_a, result_a), (col_b, proj_b, result_b)]:
            with col:
                colour = SCORE_COLOURS.get(result.classification, "#9b9b9b")
                st.markdown(
                    f"<h3 style='color:{colour}'>{result.total_score}/100 — {result.classification}</h3>",
                    unsafe_allow_html=True,
                )
                st.markdown(f"**{proj.name}**")
                st.caption(f"Municipality: {proj.municipality} | GSDF: {proj.gsdf_classification} | Readiness: {proj.readiness_status}")

                # Score bars
                for criterion in result.criteria:
                    pct = criterion.score / criterion.max_score * 100 if criterion.max_score else 0
                    bar_colour = "#1a7431" if pct >= 70 else "#f5a623" if pct >= 40 else "#d0021b"
                    st.markdown(
                        f"<div style='margin-bottom:6px'>"
                        f"<div style='display:flex;justify-content:space-between;color:#1a1a1a'>"
                        f"<small><b>{criterion.name}</b></small>"
                        f"<small>{criterion.score:.0f}/{criterion.max_score:.0f}</small></div>"
                        f"<div style='background:#e0e0e0;border-radius:4px;height:10px'>"
                        f"<div style='background:{bar_colour};width:{pct:.0f}%;height:10px;"
                        f"border-radius:4px'></div></div></div>",
                        unsafe_allow_html=True,
                    )

        # ── Explain Score panels ──────────────────────────────────────────────
        st.divider()
        st.subheader("🔍 Explain Score")
        exp_a, exp_b = st.columns(2)

        for col, proj, result in [(exp_a, proj_a, result_a), (exp_b, proj_b, result_b)]:
            with col:
                st.markdown(f"**[{proj.project_id}] Criteria Detail**")
                for criterion in result.criteria:
                    with st.expander(f"{criterion.name}: {criterion.score:.0f}/{criterion.max_score:.0f}"):
                        st.write(criterion.explanation)

                st.markdown("---")
                st.markdown("**Overall Assessment**")
                st.info(result.overall_explanation)
                st.markdown(f"**Recommendation:** {result.recommendation}")

        # ── AI Narrative (optional) ────────────────────────────────────────────
        st.divider()
        st.subheader("🤖 AI-Generated Spatial Narrative")
        ai_col_a, ai_col_b = st.columns(2)

        for col, proj, result in [(ai_col_a, proj_a, result_a), (ai_col_b, proj_b, result_b)]:
            with col:
                if st.button(f"Generate AI narrative for {proj.project_id}", key=f"ai_{proj.project_id}"):
                    with st.spinner("Generating narrative..."):
                        try:
                            from services.ai_analyzer import generate_spatial_narrative
                            nf = nearest_facility(
                                proj.latitude, proj.longitude,
                                proj.project_type, facilities,
                            )
                            dist_km = nf["distance_km"] if nf else 0.0
                            breakdown = json.loads(proj.score_breakdown or "{}")
                            narrative = generate_spatial_narrative(
                                project_name=proj.name,
                                project_type=proj.project_type or "",
                                municipality=proj.municipality or "",
                                score=result.total_score,
                                classification=result.classification,
                                score_breakdown=breakdown,
                                nearest_facility_km=dist_km,
                            )
                            st.write(narrative)
                        except Exception as exc:
                            st.warning(f"AI unavailable: {exc}")

        # ── Weight sensitivity ────────────────────────────────────────────────
        st.divider()
        st.subheader("⚖️ Weight Sensitivity")
        st.caption("Adjust the weights panel above and re-run to see how scores change.")

        sens_data = []
        for p in projects[:8]:
            r = score_project(p, facilities, custom_weights)
            sens_data.append({
                "Project": f"[{p.project_id}] {p.name[:30]}",
                "Score": r.total_score,
                "Classification": r.classification,
            })

        import pandas as pd
        df = pd.DataFrame(sens_data).sort_values("Score", ascending=False)
        st.dataframe(df, use_container_width=True, hide_index=True)

    session.close()

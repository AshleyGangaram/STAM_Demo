"""
STAM Project Registry — project list, detail view, and budget calculator.
"""

from __future__ import annotations

import json

import folium
import pandas as pd
import streamlit as st
from streamlit_folium import st_folium

from services.db import Facility, Project, get_session, log_action
from services.scorer import COST_BENCHMARKS, score_project
from services.spatial import (
    SCORE_COLOURS,
    add_facilities_layer,
    add_project_buffer,
    make_base_map,
    nearest_facility,
)


def render():
    st.title("📋 Project Registry")
    st.caption(
        "Full capital project inventory with spatial context, STAM scores, "
        "and indicative budget estimates."
    )

    session = get_session()
    projects = session.query(Project).order_by(Project.project_id).all()
    facilities = session.query(Facility).all()

    if not projects:
        st.warning("No projects found. Run `python data/seed.py` to load demo data.")
        session.close()
        return

    tab_list, tab_detail, tab_budget = st.tabs(["Project List", "Project Detail", "Budget Calculator"])

    # ── Project list ──────────────────────────────────────────────────────────
    with tab_list:
        st.subheader("All Capital Projects")

        municipalities = ["All"] + sorted({p.municipality for p in projects if p.municipality})
        ptypes = ["All"] + sorted({p.project_type for p in projects if p.project_type})
        cls_options = ["All"] + list(SCORE_COLOURS.keys()) + ["Pending"]

        f1, f2, f3 = st.columns(3)
        muni_f = f1.selectbox("Municipality", municipalities)
        type_f = f2.selectbox("Project Type", ptypes)
        cls_f = f3.selectbox("Classification", cls_options)

        filtered = projects
        if muni_f != "All":
            filtered = [p for p in filtered if p.municipality == muni_f]
        if type_f != "All":
            filtered = [p for p in filtered if p.project_type == type_f]
        if cls_f != "All":
            filtered = [p for p in filtered if (p.classification or "Pending") == cls_f]

        rows = []
        for p in sorted(filtered, key=lambda x: -(x.total_score or 0)):
            colour = SCORE_COLOURS.get(p.classification or "", "#9b9b9b")
            rows.append({
                "ID": p.project_id,
                "Name": p.name,
                "Dept": p.department,
                "Type": p.project_type,
                "Score": p.total_score or 0,
                "Classification": p.classification or "Pending",
                "GSDF Zone": p.gsdf_classification or "",
                "Readiness": p.readiness_status,
                "Budget (ZAR M)": round((p.budget_rands or 0) / 1e6, 1),
                "Municipality": p.municipality,
                "Ward": p.ward,
                "Budget Year": p.budget_year,
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        st.caption(f"Showing {len(rows)} of {len(projects)} projects.")

    # ── Project detail ────────────────────────────────────────────────────────
    with tab_detail:
        project_options = {f"[{p.project_id}] {p.name}": p for p in projects}
        selected = st.selectbox("Select project", list(project_options.keys()))
        p = project_options[selected]

        colour = SCORE_COLOURS.get(p.classification or "", "#9b9b9b")

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("STAM Score", f"{p.total_score or 0}/100")
        c2.metric("Classification", p.classification or "Pending")
        c3.metric("Budget", f"R{(p.budget_rands or 0) / 1e6:.1f}M")
        c4.metric("Readiness", p.readiness_status or "—")

        st.divider()

        info_col, map_col = st.columns([1, 2])
        with info_col:
            st.markdown(f"**Project ID:** {p.project_id}")
            st.markdown(f"**Name:** {p.name}")
            st.markdown(f"**Department:** {p.department}")
            st.markdown(f"**Type:** {p.project_type}")
            st.markdown(f"**Municipality:** {p.municipality}")
            st.markdown(f"**Ward:** {p.ward}")
            st.markdown(f"**Budget Year:** {p.budget_year}")
            st.markdown(f"**GSDF Zone:** {p.gsdf_classification}")
            st.markdown(f"**Source File:** {p.source_file or 'Manual'}")

            # Nearest facilities
            st.divider()
            st.markdown("**Nearest Facilities**")
            for ftype in ["clinic", "school", "library"]:
                nf = nearest_facility(
                    p.latitude or -26.0, p.longitude or 28.0, ftype, facilities
                )
                if nf:
                    cap = nf.get("capacity", 0) or 0
                    occ = nf.get("current_occupancy", 0) or 0
                    over = " ⚠ OVER CAPACITY" if occ > cap else ""
                    st.markdown(
                        f"- **{ftype.title()}:** {nf['name']} "
                        f"({nf['distance_km']} km){over}"
                    )

        with map_col:
            if p.latitude and p.longitude:
                m = folium.Map(location=[p.latitude, p.longitude], zoom_start=12,
                                tiles="CartoDB positron")
                m = add_project_buffer(m, p.latitude, p.longitude, 5.0, p.name)
                folium.Marker(
                    location=[p.latitude, p.longitude],
                    popup=f"<b>{p.name}</b><br>Score: {p.total_score}/100",
                    icon=folium.Icon(color="darkblue", icon="star", prefix="fa"),
                ).add_to(m)
                m = add_facilities_layer(m, facilities)
                st_folium(m, use_container_width=True, height=500, returned_objects=[])
            else:
                st.warning("No coordinates for this project.")

        # Score breakdown
        if p.score_breakdown:
            st.divider()
            st.subheader("Score Breakdown")
            breakdown = json.loads(p.score_breakdown)
            labels = {
                "gsdf_overlap": "GSDF Overlap (20)", "msdf_priority": "MSDF Priority (15)",
                "service_gap": "Service Gap (20)", "transport_access": "Transport (10)",
                "brownfield": "Brownfield (10)", "readiness": "Readiness (15)",
                "cost_efficiency": "Cost Efficiency (10)",
            }
            cols = st.columns(7)
            for i, (key, label) in enumerate(labels.items()):
                score = breakdown.get(key, 0)
                max_pts = int(label.split("(")[1].rstrip(")"))
                pct = score / max_pts * 100 if max_pts else 0
                colour_bar = "#1a7431" if pct >= 70 else "#f5a623" if pct >= 40 else "#d0021b"
                cols[i].markdown(
                    f"<div style='text-align:center'>"
                    f"<div style='font-size:11px;color:#333333'>{label.split('(')[0].strip()}</div>"
                    f"<div style='font-size:22px;font-weight:bold;color:{colour_bar}'>{score}</div>"
                    f"<div style='font-size:11px;color:#333333'>/{max_pts}</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )

    # ── Budget calculator ─────────────────────────────────────────────────────
    with tab_budget:
        st.subheader("Indicative Budget Calculator")
        st.caption(
            "STAM provides indicative cost estimates based on benchmark data for each "
            "facility type. These estimates support budget planning conversations."
        )

        bc1, bc2 = st.columns(2)
        with bc1:
            calc_type = st.selectbox(
                "Facility Type",
                list(COST_BENCHMARKS.keys()),
                format_func=lambda x: x.title(),
            )
            quantity = st.number_input("Number of facilities / units", min_value=1, value=1)
            contingency = st.slider("Contingency (%)", 0, 30, 15)

        with bc2:
            benchmark = COST_BENCHMARKS.get(calc_type, 15_000_000)
            base_cost = benchmark * quantity
            contingency_cost = base_cost * contingency / 100
            total_cost = base_cost + contingency_cost

            st.metric("Benchmark per unit", f"R{benchmark:,.0f}")
            st.metric("Base Cost", f"R{base_cost:,.0f}")
            st.metric("Contingency", f"R{contingency_cost:,.0f}")
            st.metric("Total Indicative CAPEX", f"R{total_cost:,.0f}",
                       delta=f"R{total_cost / 1e6:.1f}M")

        st.caption(
            "Note: These are indicative benchmarks only. Detailed cost estimates require "
            "site assessment, EIA, design fees, and inflation adjustments."
        )

        # Compare against project budgets
        st.divider()
        st.subheader("Compare Against Projects")
        same_type = [p for p in projects if p.project_type == calc_type]
        if same_type:
            comp_rows = []
            for p in same_type:
                budget = p.budget_rands or 0
                ratio = budget / benchmark if benchmark > 0 else 0
                comp_rows.append({
                    "ID": p.project_id,
                    "Name": p.name,
                    "Proposed Budget": f"R{budget:,.0f}",
                    "Benchmark": f"R{benchmark:,.0f}",
                    "Ratio": f"{ratio:.2f}x",
                    "Assessment": "✓ Within range" if 0.5 <= ratio <= 2.0 else "⚠ Review budget",
                })
            st.dataframe(pd.DataFrame(comp_rows), use_container_width=True, hide_index=True)

    session.close()

"""
STAM Dashboard — overview KPIs, overview map, and early warning alerts.
"""

from __future__ import annotations

import json
import os

import streamlit as st
from streamlit_folium import st_folium

from services.db import AuditLog, Facility, Project, ScoreTemplate, get_session
from services.spatial import (
    SCORE_COLOURS,
    add_facilities_layer,
    add_gsdf_layer,
    add_heatmap_layer,
    add_population_layer,
    add_projects_layer,
    make_base_map,
)

_HERE = os.path.dirname(__file__)
DEMO_DIR = os.path.join(_HERE, "..", "data", "demo")


def render():
    st.title("📊 STAM Dashboard")
    st.caption(
        "Spatial Transformation Appraisal Mechanism — Gauteng Province Capital Budget Platform"
    )

    session = get_session()
    projects = session.query(Project).all()
    facilities = session.query(Facility).all()
    audit_entries = session.query(AuditLog).order_by(AuditLog.timestamp.desc()).limit(8).all()

    scored = [p for p in projects if p.total_score and p.total_score > 0]
    priority_now = [p for p in scored if p.classification == "Priority Now"]
    not_recommended = [p for p in scored if p.classification == "Not Recommended"]
    alerts = [p for p in projects if p.readiness_status in ("Ready", "Design")
              and p.classification in ("Not Recommended", "Conditional")]

    # ── KPI Row ───────────────────────────────────────────────────────────────
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Total Projects", len(projects))
    c2.metric("Scored Projects", len(scored))
    c3.metric("Priority Now", len(priority_now), delta=f"+{len(priority_now)} this cycle")
    c4.metric("Not Recommended", len(not_recommended))
    c5.metric("Budget Alerts", len(alerts), delta_color="inverse" if alerts else "normal",
              delta=f"⚠ {len(alerts)} reviews needed" if alerts else "All clear")

    # ── Budget summary ────────────────────────────────────────────────────────
    total_budget = sum((p.budget_rands or 0) for p in projects)
    priority_budget = sum((p.budget_rands or 0) for p in priority_now)
    not_rec_budget = sum((p.budget_rands or 0) for p in not_recommended)

    st.divider()
    bc1, bc2, bc3 = st.columns(3)
    bc1.metric("Total Portfolio Budget", f"R{total_budget / 1e6:.1f}M")
    bc2.metric("Priority Now Budget", f"R{priority_budget / 1e6:.1f}M",
               delta=f"{priority_budget / total_budget * 100:.0f}% of total" if total_budget else "")
    bc3.metric("Budget in Non-Priority Areas",
               f"R{not_rec_budget / 1e6:.1f}M",
               delta=f"⚠ {not_rec_budget / total_budget * 100:.0f}% misaligned" if total_budget else "",
               delta_color="inverse")

    st.divider()

    # ── Map + Alerts ──────────────────────────────────────────────────────────
    map_col, alert_col = st.columns([2, 1])

    with map_col:
        st.subheader("Province Overview Map")
        st.caption("Projects coloured by STAM classification. Toggle layers using the ☰ control.")

        m = make_base_map()
        gsdf_path = os.path.join(DEMO_DIR, "gsdf_zones.geojson")
        pop_path = os.path.join(DEMO_DIR, "population.geojson")

        m = add_gsdf_layer(m, gsdf_path)
        m = add_population_layer(m, pop_path)
        m = add_facilities_layer(m, facilities)
        m = add_projects_layer(m, projects)
        m = add_heatmap_layer(m, projects)

        import folium
        folium.LayerControl(collapsed=False).add_to(m)

        st_folium(m, width=700, height=480, returned_objects=[])

    with alert_col:
        st.subheader("⚠ Budget & Alignment Alerts")
        if alerts:
            for p in alerts[:6]:
                colour = SCORE_COLOURS.get(p.classification, "#9b9b9b")
                st.markdown(
                    f"<div style='border-left:4px solid {colour}; padding:8px; margin-bottom:8px; "
                    f"background:#f8f8f8; border-radius:4px; color:#1a1a1a'>"
                    f"<b>[{p.project_id}]</b> {p.name}<br>"
                    f"<small style='color:#444444'>Score: {p.total_score}/100 | {p.classification}<br>"
                    f"R{(p.budget_rands or 0) / 1e6:.1f}M | Readiness: {p.readiness_status}</small>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
        else:
            st.success("No budget alignment alerts.")

        st.divider()
        st.subheader("Classification Breakdown")
        for label, colour in SCORE_COLOURS.items():
            count = sum(1 for p in scored if p.classification == label)
            budget = sum((p.budget_rands or 0) for p in scored if p.classification == label)
            st.markdown(
                f"<div style='display:flex;align-items:center;gap:8px;margin-bottom:4px'>"
                f"<span style='width:12px;height:12px;background:{colour};display:inline-block;"
                f"border-radius:50%'></span>"
                f"<b>{label}</b>: {count} projects (R{budget / 1e6:.1f}M)"
                f"</div>",
                unsafe_allow_html=True,
            )

    # ── Recent activity ───────────────────────────────────────────────────────
    st.divider()
    st.subheader("Recent Activity")
    if audit_entries:
        rows = []
        for e in audit_entries:
            ts = e.timestamp[:19].replace("T", " ") if e.timestamp else ""
            rows.append({
                "Timestamp": ts,
                "User Role": e.user_role,
                "Action": e.action,
                "Entity": e.entity_type,
                "Detail": e.detail[:80] if e.detail else "",
            })
        import pandas as pd
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.info("No activity recorded yet. Run data/seed.py to load demo data.")

    # ── Project table ─────────────────────────────────────────────────────────
    st.divider()
    st.subheader("All Projects")
    if projects:
        import pandas as pd
        rows = []
        for p in sorted(projects, key=lambda x: -(x.total_score or 0)):
            colour = SCORE_COLOURS.get(p.classification, "#9b9b9b")
            rows.append({
                "ID": p.project_id,
                "Project Name": p.name,
                "Dept": p.department,
                "Type": p.project_type,
                "Score": p.total_score or 0,
                "Classification": p.classification or "Pending",
                "GSDF Zone": p.gsdf_classification or "",
                "Budget (ZAR M)": round((p.budget_rands or 0) / 1e6, 1),
                "Readiness": p.readiness_status,
                "Municipality": p.municipality,
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    session.close()

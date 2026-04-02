"""
STAM Audit Trail — records user roles and actions.
"""

from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from services.db import AuditLog, get_session


def render() -> None:
    st.title("📝 Audit Trail")
    st.caption("Complete log of user actions, appraisals, and system events.")

    session = get_session()
    entries = (
        session.query(AuditLog)
        .order_by(AuditLog.timestamp.desc())
        .all()
    )

    # ── Filter controls ──────────────────────────────────────────────────────
    col1, col2, col3 = st.columns([1, 1, 1])

    actions = sorted({e.action for e in entries if e.action})
    with col1:
        selected_action = st.selectbox(
            "Filter by action",
            ["All Actions"] + actions,
        )

    roles = sorted({e.user_role for e in entries if e.user_role})
    with col2:
        selected_role = st.selectbox(
            "Filter by role",
            ["All Roles"] + roles,
        )

    entity_types = sorted({e.entity_type for e in entries if e.entity_type})
    with col3:
        selected_entity = st.selectbox(
            "Filter by entity",
            ["All Entities"] + entity_types,
        )

    # Apply filters (immutable — build new list)
    filtered = [
        e for e in entries
        if (selected_action == "All Actions" or e.action == selected_action)
        and (selected_role == "All Roles" or e.user_role == selected_role)
        and (selected_entity == "All Entities" or e.entity_type == selected_entity)
    ]

    st.markdown(f"**{len(filtered)}** records")
    st.divider()

    # ── Build table ──────────────────────────────────────────────────────────
    if not filtered:
        st.info("No audit records match the selected filters.")
        session.close()
        return

    _ACTION_COLOURS = {
        "login":     "#2196F3",
        "appraisal": "#9C27B0",
        "import":    "#4CAF50",
        "export":    "#FF9800",
        "seed":      "#607D8B",
        "create":    "#009688",
        "update":    "#FF5722",
        "delete":    "#F44336",
        "query":     "#3F51B5",
        "report":    "#795548",
    }

    rows_html: list[str] = []
    for e in filtered:
        ts = (e.timestamp or "")[:19].replace("T", " ")
        action_colour = _ACTION_COLOURS.get(e.action, "#9E9E9E")

        # Parse detail — show plain text if it's JSON with a message
        detail_text = ""
        if e.detail:
            try:
                d = json.loads(e.detail)
                if isinstance(d, dict):
                    detail_text = d.get("message", d.get("detail", json.dumps(d)))
                else:
                    detail_text = str(d)
            except (json.JSONDecodeError, TypeError):
                detail_text = str(e.detail)

        entity_label = e.entity_type or ""
        if e.entity_id:
            entity_label += f" ({e.entity_id})" if entity_label else e.entity_id

        rows_html.append(
            f"<tr>"
            f"<td style='white-space:nowrap'>{ts}</td>"
            f"<td>{e.user_role or ''}</td>"
            f"<td><span style='background:{action_colour};color:#fff;padding:2px 8px;"
            f"border-radius:4px;font-size:0.8em'>{e.action}</span></td>"
            f"<td>{entity_label}</td>"
            f"<td style='max-width:400px;overflow:hidden;text-overflow:ellipsis'>"
            f"{detail_text}</td>"
            f"</tr>"
        )

    table_html = (
        "<div style='overflow-x:auto'>"
        "<table style='width:100%;border-collapse:collapse;font-size:0.9em'>"
        "<thead><tr style='border-bottom:2px solid #ddd;text-align:left'>"
        "<th style='padding:8px'>Timestamp</th>"
        "<th style='padding:8px'>User</th>"
        "<th style='padding:8px'>Action</th>"
        "<th style='padding:8px'>Entity</th>"
        "<th style='padding:8px'>Detail</th>"
        "</tr></thead><tbody>"
        + "".join(
            f"<tr style='border-bottom:1px solid #eee'>"
            f"{row[4:]}"  # strip outer <tr>
            for row in rows_html
        )
        + "</tbody></table></div>"
    )

    st.markdown(table_html, unsafe_allow_html=True)

    session.close()

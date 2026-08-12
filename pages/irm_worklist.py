"""
IRM Worklist — administrator view.

Load a National Treasury IRM extract (Excel or CSV) so capturers can pick projects
from a list instead of typing codes by hand, and track location-capture progress
against it.
"""

from __future__ import annotations

import os
import tempfile

import pandas as pd
import streamlit as st

from services import auth, capture

TEMPLATE_COLUMNS = [
    "project_code", "project_name", "department", "municipality",
    "latitude", "longitude", "address", "budget_rands", "financial_year",
]


def render() -> None:
    if not auth.require_role(auth.ADMIN_ROLES):
        return

    user = auth.current_user()
    user_role = st.session_state.get("user_role", user.role)

    st.title("📇 IRM Worklist")
    st.caption(
        "Projects awaiting location verification. Only **project_code** and "
        "**project_name** are required — missing coordinates are expected, and are "
        "exactly what this tool exists to fix."
    )

    upload_tab, manage_tab = st.tabs(["Upload extract", "Current worklist"])

    with upload_tab:
        _render_upload(user.username, user_role)

    with manage_tab:
        _render_worklist(user_role)


# ── Upload ────────────────────────────────────────────────────────────────────

def _render_upload(username: str, user_role: str) -> None:
    st.subheader("Import an IRM extract")

    with st.expander("Expected columns"):
        st.markdown(
            "**Required:** `project_code`, `project_name`\n\n"
            "**Optional:** `department`, `municipality`, `province`, `latitude`, "
            "`longitude`, `address`, `budget_rands`, `financial_year`\n\n"
            "Common aliases are mapped automatically — `project_id`, `code`, `name`, "
            "`lat`, `lon`, `lng`, `budget`, `budget_year`, `location`."
        )
        st.download_button(
            "⬇️ Download a blank template",
            data=pd.DataFrame(columns=TEMPLATE_COLUMNS).to_csv(index=False).encode(),
            file_name="irm_worklist_template.csv",
            mime="text/csv",
        )

    uploaded = st.file_uploader(
        "IRM extract (.xlsx or .csv)", type=["xlsx", "xls", "csv"],
        key="irm_worklist_upload",
    )
    if uploaded is None:
        return

    suffix = os.path.splitext(uploaded.name)[1].lower()
    try:
        preview = (pd.read_csv(uploaded, dtype=str) if suffix == ".csv"
                   else pd.read_excel(uploaded, dtype=str))
    except Exception as exc:
        st.error(f"Could not read that file: {exc}")
        return

    st.subheader("Preview")
    st.caption(f"{len(preview)} row(s) · columns: {', '.join(map(str, preview.columns))}")
    st.dataframe(preview.head(15), use_container_width=True, hide_index=True)

    if not st.button("📥 Import into the worklist", type="primary"):
        return

    uploaded.seek(0)
    path = _write_temp(uploaded, suffix)
    try:
        result = capture.import_irm_worklist(path, username=username,
                                             user_role=user_role)
    finally:
        _remove_quietly(path)

    _render_import_result(result)


def _write_temp(uploaded, suffix: str) -> str:
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as handle:
        handle.write(uploaded.getbuffer())
        return handle.name


def _remove_quietly(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def _render_import_result(result) -> None:
    columns = st.columns(4)
    columns[0].metric("Rows read", result.total_rows)
    columns[1].metric("Imported", result.imported)
    columns[2].metric("Errors", len(result.errors))
    columns[3].metric("Warnings", len(result.warnings))

    if result.imported:
        st.success(f"{result.imported} project(s) added to the worklist.")
    elif not result.errors:
        st.info("Nothing new to import — every code in the file is already present.")

    if result.errors:
        st.subheader("Validation errors")
        st.dataframe(
            pd.DataFrame([{
                "Row": e.row, "Field": e.field,
                "Problem": e.message, "Value": e.value,
            } for e in result.errors]),
            use_container_width=True, hide_index=True,
        )

    if result.warnings:
        st.subheader("Warnings")
        for warning in result.warnings[:50]:
            st.warning(warning)
        if len(result.warnings) > 50:
            st.caption(f"…and {len(result.warnings) - 50} more.")


# ── Manage ────────────────────────────────────────────────────────────────────

def _render_worklist(user_role: str) -> None:
    worklist = capture.list_irm_projects()
    if not worklist:
        st.info("The worklist is empty. Import an IRM extract to get started.")
        return

    captures = capture.list_captures()
    counts: dict[str, int] = {}
    for row in captures:
        counts[row.project_code] = counts.get(row.project_code, 0) + 1

    located = sum(1 for p in worklist if counts.get(p.project_code))
    missing_coords = sum(
        1 for p in worklist if p.irm_latitude is None or p.irm_longitude is None
    )

    columns = st.columns(4)
    columns[0].metric("Projects", len(worklist))
    columns[1].metric("Located", located)
    columns[2].metric("Outstanding", len(worklist) - located)
    columns[3].metric("No IRM coordinates", missing_coords)

    st.progress(located / len(worklist),
                text=f"{located} of {len(worklist)} projects have a captured location")

    st.dataframe(
        pd.DataFrame([{
            "Status": "✅ Located" if counts.get(p.project_code) else "⬜ Outstanding",
            "Shapes": counts.get(p.project_code, 0),
            "Project code": p.project_code,
            "Project name": p.project_name,
            "Department": p.department or "",
            "Municipality": p.municipality or "",
            "IRM latitude": p.irm_latitude,
            "IRM longitude": p.irm_longitude,
            "IRM address": p.irm_address or "",
            "FY": p.financial_year or "",
            "Budget (ZAR)": p.budget_rands,
            "Source file": p.source_file or "",
        } for p in worklist]),
        use_container_width=True, hide_index=True,
    )

    st.divider()
    with st.expander("⚠️ Clear the worklist"):
        st.caption(
            "Removes every worklist project. Captured locations are **not** deleted — "
            "they keep their project codes and still export."
        )
        confirm = st.text_input("Type CLEAR to confirm", key="irm_clear_confirm")
        if st.button("Clear worklist", disabled=confirm != "CLEAR"):
            removed = capture.clear_irm_worklist(user_role=user_role)
            st.success(f"Removed {removed} project(s) from the worklist.")
            st.rerun()

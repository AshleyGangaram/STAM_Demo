"""
Widget blocks for the Location Capture page.

Imported by pages/location_capture.py — not routed directly by app.py. Kept
separate so the page module stays orchestration only.

Session-state keys used here are all prefixed `cap_` and are cleared on sign-out.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pandas as pd
import streamlit as st

from services import capture, geocoder
from services.db import IRMProject
from services.geometry import extract_geometry, geometry_metrics, validate_geometry

# ── Session-state keys ────────────────────────────────────────────────────────

K_PENDING_GEOM = "cap_pending_geom"
K_EDITING_ID = "cap_editing_id"
K_PROJECT_CODE = "cap_project_code"
K_PROJECT_NAME = "cap_project_name"
K_IRM_ID = "cap_irm_id"
K_VIEW_CENTER = "cap_view_center"
K_VIEW_ZOOM = "cap_view_zoom"
K_CENTRED_ON = "cap_centred_on"
K_SEARCH_HITS = "cap_search_hits"
K_SEARCH_QUERY = "cap_search_query"
K_SEARCH_MESSAGE = "cap_search_message"
K_LAST_TOOLTIP = "cap_last_tooltip"
K_LAST_DRAW_SIG = "cap_last_draw_sig"
K_FORM_NONCE = "cap_form_nonce"
K_FLASH = "cap_flash"

DEFAULT_CENTER = [-26.05, 28.05]
DEFAULT_ZOOM = 9
SEARCH_ZOOM = 17


def init_state() -> None:
    """Seed every session key this page relies on."""
    defaults = {
        K_PENDING_GEOM: None,
        K_EDITING_ID: None,
        K_PROJECT_CODE: "",
        K_PROJECT_NAME: "",
        K_IRM_ID: None,
        K_VIEW_CENTER: list(DEFAULT_CENTER),
        K_VIEW_ZOOM: DEFAULT_ZOOM,
        K_CENTRED_ON: "",
        K_SEARCH_HITS: [],
        K_SEARCH_QUERY: "",
        K_SEARCH_MESSAGE: "",
        K_LAST_TOOLTIP: "",
        K_LAST_DRAW_SIG: "",
        K_FORM_NONCE: 0,
        K_FLASH: None,
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def set_view(lat: float, lon: float, zoom: int = SEARCH_ZOOM, token: str = "") -> None:
    """
    Move the map.

    The view is only ever changed by an explicit action — a search result, a
    coordinate jump, or a project switch. It is deliberately NOT driven by the
    map's own reported centre: feeding that back in makes the map re-render,
    settle fractionally differently, report again, and refresh forever.
    """
    st.session_state[K_VIEW_CENTER] = [float(lat), float(lon)]
    st.session_state[K_VIEW_ZOOM] = int(zoom)
    st.session_state[K_CENTRED_ON] = token


def clear_draft() -> None:
    """Drop the in-progress geometry and reset the form widgets."""
    st.session_state[K_PENDING_GEOM] = None
    st.session_state[K_EDITING_ID] = None
    st.session_state[K_LAST_DRAW_SIG] = ""
    st.session_state[K_LAST_TOOLTIP] = ""
    st.session_state[K_FORM_NONCE] += 1


def flash(kind: str, message: str) -> None:
    """Queue a message to show after the next rerun."""
    st.session_state[K_FLASH] = (kind, message)


def show_flash() -> None:
    payload = st.session_state.pop(K_FLASH, None)
    if not payload:
        return
    kind, message = payload
    {"success": st.success, "error": st.error, "warning": st.warning}.get(
        kind, st.info
    )(message)


# ── Project selector ──────────────────────────────────────────────────────────

def render_project_selector(worklist: list[IRMProject]) -> IRMProject | None:
    """
    Choose the project being located, from the IRM worklist or by free entry.

    Returns the matching IRMProject when one was picked, else None (free entry).
    Writes the chosen code/name into session state.
    """
    captured_codes = _codes_with_captures()

    # No ➕ here on purpose: that symbol means "start a new capture" everywhere
    # else on this page, and two different plus controls read as the same action.
    free_entry = st.toggle(
        "This project is not on the IRM worklist",
        value=False,
        key="cap_free_entry",
        help="Switch on to type a project code and name that has not been "
             "imported into the worklist yet.",
    )

    if free_entry or not worklist:
        if not worklist and not free_entry:
            st.info(
                "The IRM worklist is empty. An administrator can load an IRM extract "
                "from **IRM Worklist**, or you can capture ad-hoc projects here."
            )
        left, right = st.columns([1, 2])
        code = left.text_input("Project code *", key="cap_manual_code",
                               placeholder="e.g. IRM-2026-0417").strip()
        name = right.text_input("Project name *", key="cap_manual_name",
                                placeholder="e.g. Tembisa Clinic Upgrade").strip()
        _set_project(code, name, None)
        return None

    options: dict[str, IRMProject] = {}
    for project in worklist:
        tick = "✅ " if project.project_code in captured_codes else "⬜ "
        muni = f" — {project.municipality}" if project.municipality else ""
        options[f"{tick}[{project.project_code}] {project.project_name}{muni}"] = project

    labels = list(options)
    current_code = st.session_state.get(K_PROJECT_CODE)
    index = next(
        (i for i, label in enumerate(labels)
         if options[label].project_code == current_code),
        0,
    )

    chosen_label = st.selectbox(
        f"IRM project ({len(worklist)} in worklist · "
        f"{len(captured_codes)} located)",
        labels,
        index=index,
        help="✅ marks projects that already have at least one captured location.",
    )
    project = options[chosen_label]
    _set_project(project.project_code, project.project_name, project.id)
    _render_irm_context(project)

    # Selecting a project that already has an IRM coordinate takes the map there,
    # so the user starts looking at what they are correcting.
    if (project.irm_latitude is not None and project.irm_longitude is not None
            and geocoder.within_south_africa(project.irm_latitude,
                                             project.irm_longitude)):
        token = f"irm:{project.project_code}"
        if token != st.session_state.get(K_CENTRED_ON):
            set_view(project.irm_latitude, project.irm_longitude, 15, token)

    return project


def _set_project(code: str, name: str, irm_id: str | None) -> None:
    """Switching project clears any half-finished draft for the previous one."""
    if code != st.session_state.get(K_PROJECT_CODE):
        st.session_state[K_PENDING_GEOM] = None
        st.session_state[K_EDITING_ID] = None
        st.session_state[K_LAST_DRAW_SIG] = ""
        st.session_state[K_LAST_TOOLTIP] = ""
        st.session_state[K_FORM_NONCE] += 1
    st.session_state[K_PROJECT_CODE] = code
    st.session_state[K_PROJECT_NAME] = name
    st.session_state[K_IRM_ID] = irm_id


def _codes_with_captures() -> set[str]:
    return {row.project_code for row in capture.list_captures()}


def _render_irm_context(project: IRMProject) -> None:
    bits = []
    if project.department:
        bits.append(f"**Department:** {project.department}")
    if project.municipality:
        bits.append(f"**Municipality:** {project.municipality}")
    if project.financial_year:
        bits.append(f"**FY:** {project.financial_year}")
    if project.budget_rands:
        bits.append(f"**Budget:** R{project.budget_rands:,.0f}")
    if bits:
        st.caption(" · ".join(bits))

    if project.irm_latitude is not None and project.irm_longitude is not None:
        inside = geocoder.within_south_africa(project.irm_latitude, project.irm_longitude)
        marker = "⚑" if inside else "⚠️"
        note = "" if inside else " — **outside South Africa**"
        st.caption(
            f"{marker} IRM currently records "
            f"`{project.irm_latitude:.5f}, {project.irm_longitude:.5f}`{note}"
        )
    else:
        st.caption("⚑ IRM holds **no coordinates** for this project.")
    if project.irm_address:
        st.caption(f"📍 IRM address on file: _{project.irm_address}_")


# ── Search panel ──────────────────────────────────────────────────────────────

def render_search_panel() -> None:
    """Address / landmark search that recentres the map on the chosen result."""
    provider = geocoder.active_provider()
    label = "Google Places" if provider == "google" else "OpenStreetMap"

    with st.form("cap_search_form", clear_on_submit=False):
        columns = st.columns([4, 1])
        query = columns[0].text_input(
            "Search for an address or landmark",
            value=st.session_state.get(K_SEARCH_QUERY, ""),
            placeholder="e.g. Tembisa Hospital, or 12 Church Street, Pretoria",
            label_visibility="collapsed",
        )
        searched = columns[1].form_submit_button("🔍 Search", use_container_width=True)

    if searched:
        outcome = geocoder.search(query)
        st.session_state[K_SEARCH_QUERY] = query
        st.session_state[K_SEARCH_HITS] = list(outcome.hits)
        st.session_state[K_SEARCH_MESSAGE] = outcome.message
        st.session_state.pop("cap_search_choice", None)   # reset to the best match

    message = st.session_state.get(K_SEARCH_MESSAGE)
    if message:
        st.warning(message)

    hits = st.session_state.get(K_SEARCH_HITS) or []
    if hits:
        chosen = st.radio(
            f"{len(hits)} result(s) from {label} — the map follows your selection",
            list(range(len(hits))),
            format_func=lambda i: hits[i].label,
            key="cap_search_choice",
        )
        # Centre and zoom automatically. This runs before the map is built lower
        # down the page, so the move needs no rerun.
        hit = hits[chosen]
        token = f"hit:{hit.lat:.6f},{hit.lon:.6f}"
        if token != st.session_state.get(K_CENTRED_ON):
            set_view(hit.lat, hit.lon, SEARCH_ZOOM, token)
        st.caption(f"🎯 Centred on **{hit.name}** — {hit.lat:.5f}, {hit.lon:.5f}")
    else:
        st.caption(
            f"Search powered by {label}. You can also pan the map manually, or use "
            "**Jump to coordinates** below."
        )

    with st.expander("Jump to coordinates"):
        columns = st.columns([2, 2, 1])
        lat = columns[0].number_input("Latitude", value=-26.05, format="%.6f",
                                      key="cap_manual_lat")
        lon = columns[1].number_input("Longitude", value=28.05, format="%.6f",
                                      key="cap_manual_lon")
        columns[2].markdown("&nbsp;")
        if columns[2].button("Go", use_container_width=True):
            if geocoder.within_south_africa(lat, lon):
                set_view(lat, lon, SEARCH_ZOOM, f"jump:{lat},{lon}")
            else:
                st.error("Those coordinates fall outside South Africa.")


# ── Geometry summary ──────────────────────────────────────────────────────────

def render_geometry_summary(geometry: dict | None) -> None:
    if not geometry:
        st.info(
            "**No shape selected.**\n\n"
            "Use the draw tools on the left of the map:\n"
            "- 📍 **Marker** for a single site\n"
            "- ➖ **Line** for a road or pipeline\n"
            "- ⬛ **Polygon** for a site boundary\n\n"
            "Or click an existing shape to edit it."
        )
        return

    metrics = geometry_metrics(geometry)
    st.markdown(f"**Shape:** `{metrics['geometry_type']}`")
    if metrics["centroid_lat"] is not None:
        st.markdown(
            f"**Centroid:** `{metrics['centroid_lat']:.6f}, "
            f"{metrics['centroid_lon']:.6f}`"
        )
    if metrics["length_m"]:
        st.markdown(f"**Length:** {metrics['length_m']:,.0f} m "
                    f"({metrics['length_m'] / 1000:,.2f} km)")
    if metrics["area_m2"]:
        st.markdown(f"**Area:** {metrics['area_m2']:,.0f} m² "
                    f"({metrics['area_m2'] / 10_000:,.2f} ha)")


# ── Attribute form ────────────────────────────────────────────────────────────

def render_attribute_form(*, geometry: dict | None, editing: object | None,
                          project_code: str, project_name: str,
                          irm_project_id: str | None, username: str,
                          user_role: str) -> None:
    """
    The capture form. Saves a new record or updates the one being edited.

    Reads the geometry from session state (never straight off the map widget), so
    a rerun triggered by any other control cannot lose the user's drawing.
    """
    nonce = st.session_state[K_FORM_NONCE]
    is_edit = editing is not None

    st.subheader("✏️ Edit capture" if is_edit else "➕ New capture")

    if not project_code or not project_name:
        st.warning("Choose a project (or enter a code and name) before capturing.")
        return

    st.text_input("Project code", value=project_code, disabled=True,
                  key=f"cap_form_code_{nonce}")
    st.text_input("Project name", value=project_name, disabled=True,
                  key=f"cap_form_name_{nonce}")

    render_geometry_summary(geometry)
    st.divider()

    verification_index = (
        capture.VERIFICATION_STATUSES.index(editing.verification_status)
        if is_edit and editing.verification_status in capture.VERIFICATION_STATUSES
        else 0
    )
    lifecycle_index = (
        capture.LIFECYCLE_STATUSES.index(editing.lifecycle_status)
        if is_edit and editing.lifecycle_status in capture.LIFECYCLE_STATUSES
        else 0
    )

    with st.form(f"cap_attributes_{nonce}"):
        verification = st.selectbox(
            "Location verification status *",
            capture.VERIFICATION_STATUSES,
            index=verification_index,
            help="How confident are you that this geometry is the true location?",
        )
        lifecycle = st.selectbox(
            "Project lifecycle status *",
            capture.LIFECYCLE_STATUSES,
            index=lifecycle_index,
        )
        comments = st.text_area(
            "Comments",
            value=(editing.comments if is_edit else "") or "",
            placeholder="What did you verify, and against what source? "
                        "Note anything the next reviewer needs to know.",
            height=120,
        )
        primary_default = bool(editing.is_primary) if is_edit else True
        is_primary = st.checkbox(
            "Use this shape as the project's primary location",
            value=primary_default,
            help="The primary shape supplies the latitude and longitude exported "
                 "to STAM. Only one per project.",
        )

        submitted = st.form_submit_button(
            "💾 Update capture" if is_edit else "💾 Save capture",
            type="primary", use_container_width=True,
        )

    if submitted:
        _handle_submit(
            geometry=geometry, editing=editing, project_code=project_code,
            project_name=project_name, irm_project_id=irm_project_id,
            verification=verification, lifecycle=lifecycle, comments=comments,
            is_primary=is_primary, username=username, user_role=user_role,
        )

    columns = st.columns(2)
    # Editing is a temporary detour — this is the way back to the default state.
    if columns[0].button(
        "➕ New capture" if is_edit else "↩️ Clear shape",
        use_container_width=True,
        help="Return to a blank capture form." if is_edit
             else "Discard the shape you are drawing.",
    ):
        clear_draft()
        st.rerun()

    if is_edit and columns[1].button("🗑️ Delete", use_container_width=True):
        ok, message = capture.soft_delete_capture(editing.id, user_role=user_role)
        flash("success" if ok else "error", message)
        clear_draft()
        st.rerun()


def _handle_submit(*, geometry, editing, project_code, project_name,
                   irm_project_id, verification, lifecycle, comments,
                   is_primary, username, user_role) -> None:
    if editing is not None:
        ok, message = capture.update_capture(
            editing.id,
            geometry=geometry,
            verification_status=verification,
            lifecycle_status=lifecycle,
            comments=comments,
            is_primary=is_primary,
            user_role=user_role,
        )
    else:
        errors = validate_geometry(geometry)
        if errors:
            st.error(" ".join(errors))
            return
        ok, message, _ = capture.save_capture(
            project_code=project_code,
            project_name=project_name,
            geometry=geometry,
            verification_status=verification,
            lifecycle_status=lifecycle,
            comments=comments,
            captured_by=username,
            irm_project_id=irm_project_id,
            search_query=st.session_state.get(K_SEARCH_QUERY, ""),
            is_primary=is_primary,
            user_role=user_role,
        )

    flash("success" if ok else "error", message)
    if ok:
        clear_draft()
        st.rerun()


# ── Capture tables and exports ────────────────────────────────────────────────

def captures_dataframe(rows: list) -> pd.DataFrame:
    return pd.DataFrame([{
        "Primary": "★" if row.is_primary else "",
        "Project code": row.project_code,
        "Project name": row.project_name,
        "Shape": row.geometry_type,
        "Verification": row.verification_status,
        "Lifecycle": row.lifecycle_status,
        "Latitude": round(row.centroid_lat, 6) if row.centroid_lat is not None else None,
        "Longitude": round(row.centroid_lon, 6) if row.centroid_lon is not None else None,
        "Length (m)": round(row.length_m) if row.length_m else None,
        "Area (ha)": round(row.area_m2 / 10_000, 2) if row.area_m2 else None,
        "Comments": row.comments or "",
        "Captured by": row.captured_by or "",
        "Captured at": (row.created_at or "")[:16].replace("T", " "),
    } for row in rows])


def render_project_captures(rows: list, user_role: str) -> None:
    """Captures for the currently selected project, with per-row actions."""
    if not rows:
        st.caption("No locations captured for this project yet.")
        return

    st.dataframe(captures_dataframe(rows), use_container_width=True, hide_index=True)

    options = {
        f"{'★ ' if row.is_primary else ''}{row.geometry_type} · "
        f"{row.verification_status} · {(row.comments or '—')[:40]}": row
        for row in rows
    }
    columns = st.columns([3, 1, 1])
    label = columns[0].selectbox("Select a shape", list(options),
                                 key="cap_row_choice")
    chosen = options[label]

    columns[1].markdown("&nbsp;")
    if columns[1].button("✏️ Edit", use_container_width=True,
                         help="Open this shape in the form above."):
        open_for_editing(chosen.id)
        st.rerun()

    columns[2].markdown("&nbsp;")
    if columns[2].button("★ Set primary", use_container_width=True,
                         disabled=bool(chosen.is_primary),
                         help="Use this shape's coordinates in the STAM export."):
        ok, message = capture.set_primary(chosen.id, user_role=user_role)
        flash("success" if ok else "error", message)
        st.rerun()


def render_export_panel(rows: list) -> None:
    """Download buttons for the STAM handoff."""
    if not rows:
        st.caption("Nothing to export yet.")
        return

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
    projects = len({row.project_code for row in rows})

    st.caption(
        f"**{len(rows)}** shape(s) across **{projects}** project(s). "
        "GeoJSON carries every shape; Excel carries one row per project, taken "
        "from the primary shape, in the column layout STAM's importer expects."
    )

    columns = st.columns(2)
    columns[0].download_button(
        "⬇️ GeoJSON (all shapes)",
        data=capture.export_captures_geojson(rows),
        file_name=f"irm_location_captures_{stamp}.geojson",
        mime="application/geo+json",
        use_container_width=True,
    )
    try:
        excel_bytes = capture.export_captures_excel(rows)
    except Exception as exc:                       # openpyxl / pandas failure
        columns[1].error(f"Excel export unavailable: {exc}")
        return
    columns[1].download_button(
        "⬇️ Excel (for STAM import)",
        data=excel_bytes,
        file_name=f"irm_corrected_locations_{stamp}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )


# ── Map event handling ────────────────────────────────────────────────────────

def _signature(geometry: dict | None) -> str:
    return json.dumps(geometry, sort_keys=True) if geometry else ""


def open_for_editing(capture_id: str) -> None:
    st.session_state[K_EDITING_ID] = capture_id
    st.session_state[K_PENDING_GEOM] = None
    st.session_state[K_FORM_NONCE] += 1


def consume_map_events(map_data: dict, tooltip_prefix: str) -> bool:
    """
    Fold map interactions into session state. Returns True when state changed.

    Called before the form renders, so a change is picked up within the same run —
    no rerun, and therefore no chance of a refresh loop.

    streamlit-folium reports the *last* interaction and keeps reporting it on
    every subsequent rerun, and a click on a saved shape populates both
    `last_object_clicked_tooltip` and `last_active_drawing`. So both values are
    banked together and acted on only when one of them actually changes —
    otherwise the click that opened a shape for editing would, one rerun later,
    look like a freshly drawn shape and silently become a duplicate capture.
    """
    if not map_data:
        return False

    geometry = extract_geometry(map_data.get("last_active_drawing"))
    signature = _signature(geometry)
    tooltip = map_data.get("last_object_clicked_tooltip") or ""

    tooltip_changed = tooltip != st.session_state.get(K_LAST_TOOLTIP)
    draw_changed = signature != st.session_state.get(K_LAST_DRAW_SIG)
    if not (tooltip_changed or draw_changed):
        return False

    st.session_state[K_LAST_TOOLTIP] = tooltip
    st.session_state[K_LAST_DRAW_SIG] = signature

    clicked = _clicked_capture(tooltip, tooltip_prefix)

    # A newly clicked saved shape — including a marker, which reports a tooltip
    # but no drawing at all.
    if tooltip_changed and clicked is not None:
        open_for_editing(clicked.id)
        return True

    if draw_changed and geometry:
        # Re-clicking a shape already named by the current tooltip arrives here
        # as a "new" drawing; it is the saved shape, so reopen rather than copy.
        if clicked is not None and signature == _signature(_stored_geometry(clicked)):
            open_for_editing(clicked.id)
            return True

        st.session_state[K_PENDING_GEOM] = geometry
        st.session_state[K_EDITING_ID] = None
        st.session_state[K_FORM_NONCE] += 1
        return True

    return False


def _clicked_capture(tooltip: str, prefix: str):
    if not tooltip.startswith(prefix):
        return None
    row = capture.get_capture(tooltip[len(prefix):])
    return None if row is None or row.deleted else row


def _stored_geometry(row) -> dict | None:
    try:
        return json.loads(row.geometry_geojson or "{}") or None
    except (ValueError, TypeError):
        return None

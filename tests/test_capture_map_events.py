"""
The map-event state machine in pages/capture_components.py.

streamlit-folium reports the *last* interaction and keeps reporting it on every
subsequent rerun, so these tests replay realistic sequences of payloads to prove
a single click cannot be processed twice, and that clicking a saved shape opens
it for editing instead of duplicating it.
"""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services import capture
from services.spatial import CAPTURE_TOOLTIP_PREFIX

POINT = {"type": "Point", "coordinates": [28.1919, -25.7406]}
SQUARE = {"type": "Polygon", "coordinates": [[
    [28.00, -26.00], [28.01, -26.00], [28.01, -25.99], [28.00, -25.99], [28.00, -26.00],
]]}
NEW_LINE = {"type": "LineString", "coordinates": [[28.10, -26.10], [28.11, -26.10]]}


@pytest.fixture
def ui(monkeypatch):
    """
    capture_components with a plain dict standing in for st.session_state.

    Importing the module pulls in Streamlit, which has no script context under
    pytest; swapping in a dict lets the pure state logic be exercised directly.
    """
    from pages import capture_components as module

    class FakeState(dict):
        def setdefault(self, key, value):
            return super().setdefault(key, value)

    class FakeStreamlit:
        def __init__(self):
            self.session_state = FakeState()

    fake = FakeStreamlit()
    monkeypatch.setattr(module, "st", fake)
    module.init_state()
    return module


def _payload(drawing=None, tooltip="", center=None, zoom=None) -> dict:
    return {
        "last_active_drawing": ({"type": "Feature", "properties": {},
                                 "geometry": drawing} if drawing else None),
        "last_object_clicked_tooltip": tooltip,
        "center": center,
        "zoom": zoom,
    }


def _consume(ui, payload) -> bool:
    return ui.consume_map_events(payload, CAPTURE_TOOLTIP_PREFIX)


def _save(code="IRM-1", geometry=None, **kwargs):
    ok, message, capture_id = capture.save_capture(
        project_code=code, project_name="Test Project",
        geometry=geometry or POINT,
        verification_status="Corrected", lifecycle_status="Planning",
        captured_by="tester", **kwargs,
    )
    assert ok, message
    return capture_id


# ── Drawing ───────────────────────────────────────────────────────────────────

def test_a_new_drawing_becomes_the_pending_geometry(ui):
    assert _consume(ui, _payload(drawing=SQUARE)) is True
    assert ui.st.session_state[ui.K_PENDING_GEOM] == SQUARE
    assert ui.st.session_state[ui.K_EDITING_ID] is None


def test_the_same_drawing_is_not_processed_twice(ui):
    payload = _payload(drawing=SQUARE)
    assert _consume(ui, payload) is True
    # Every later rerun replays the identical payload — it must be inert
    assert _consume(ui, payload) is False
    assert _consume(ui, payload) is False


def test_a_second_drawing_replaces_the_first(ui):
    _consume(ui, _payload(drawing=SQUARE))
    assert _consume(ui, _payload(drawing=NEW_LINE)) is True
    assert ui.st.session_state[ui.K_PENDING_GEOM] == NEW_LINE


def test_an_empty_payload_changes_nothing(ui):
    assert _consume(ui, {}) is False
    assert ui.st.session_state[ui.K_PENDING_GEOM] is None


# ── Clicking a saved shape ────────────────────────────────────────────────────

def test_clicking_a_saved_shape_opens_it_for_editing(ui, temp_db):
    capture_id = _save(geometry=SQUARE)
    tooltip = f"{CAPTURE_TOOLTIP_PREFIX}{capture_id}"

    assert _consume(ui, _payload(drawing=SQUARE, tooltip=tooltip)) is True
    assert ui.st.session_state[ui.K_EDITING_ID] == capture_id
    assert ui.st.session_state[ui.K_PENDING_GEOM] is None


def test_a_click_is_never_replayed_as_a_new_capture(ui, temp_db):
    """
    Regression: a click populates BOTH the tooltip and last_active_drawing, so a
    naive handler treats the following rerun as a fresh drawing and turns an edit
    into a duplicate insert.
    """
    capture_id = _save(geometry=SQUARE)
    payload = _payload(drawing=SQUARE, tooltip=f"{CAPTURE_TOOLTIP_PREFIX}{capture_id}")

    assert _consume(ui, payload) is True
    assert _consume(ui, payload) is False          # the replay must be inert

    assert ui.st.session_state[ui.K_EDITING_ID] == capture_id
    assert ui.st.session_state[ui.K_PENDING_GEOM] is None


def test_clicking_a_marker_that_reports_no_drawing_still_edits(ui, temp_db):
    """Point captures are CircleMarkers — a click gives a tooltip and nothing else."""
    capture_id = _save(geometry=POINT)
    payload = _payload(drawing=None, tooltip=f"{CAPTURE_TOOLTIP_PREFIX}{capture_id}")

    assert _consume(ui, payload) is True
    assert ui.st.session_state[ui.K_EDITING_ID] == capture_id


def test_drawing_after_a_click_starts_a_new_capture(ui, temp_db):
    capture_id = _save(geometry=SQUARE)
    tooltip = f"{CAPTURE_TOOLTIP_PREFIX}{capture_id}"
    _consume(ui, _payload(drawing=SQUARE, tooltip=tooltip))

    # The tooltip lingers, but the drawing is genuinely new
    assert _consume(ui, _payload(drawing=NEW_LINE, tooltip=tooltip)) is True
    assert ui.st.session_state[ui.K_EDITING_ID] is None
    assert ui.st.session_state[ui.K_PENDING_GEOM] == NEW_LINE


def test_reclicking_the_same_shape_reopens_it(ui, temp_db):
    """After drawing something else, clicking the original shape must edit it."""
    capture_id = _save(geometry=SQUARE)
    tooltip = f"{CAPTURE_TOOLTIP_PREFIX}{capture_id}"

    _consume(ui, _payload(drawing=SQUARE, tooltip=tooltip))
    _consume(ui, _payload(drawing=NEW_LINE, tooltip=tooltip))
    assert ui.st.session_state[ui.K_EDITING_ID] is None

    # Tooltip is unchanged, but the drawing swings back to the saved geometry
    assert _consume(ui, _payload(drawing=SQUARE, tooltip=tooltip)) is True
    assert ui.st.session_state[ui.K_EDITING_ID] == capture_id
    assert ui.st.session_state[ui.K_PENDING_GEOM] is None


def test_a_deleted_capture_cannot_be_opened(ui, temp_db):
    capture_id = _save(geometry=SQUARE)
    capture.soft_delete_capture(capture_id)
    payload = _payload(drawing=SQUARE,
                       tooltip=f"{CAPTURE_TOOLTIP_PREFIX}{capture_id}")

    _consume(ui, payload)
    assert ui.st.session_state[ui.K_EDITING_ID] is None


def test_an_unknown_tooltip_is_ignored(ui, temp_db):
    payload = _payload(drawing=SQUARE, tooltip="Existing Facilities")
    assert _consume(ui, payload) is True            # the drawing still registers
    assert ui.st.session_state[ui.K_EDITING_ID] is None
    assert ui.st.session_state[ui.K_PENDING_GEOM] == SQUARE


# ── View control ──────────────────────────────────────────────────────────────

def test_map_events_never_move_the_view(ui):
    """
    Regression: the page refreshed endlessly because the map's reported centre
    was fed back into the map's own construction — it re-rendered, settled
    fractionally differently, reported again, and looped. Map events must not
    touch the view at all.
    """
    ui.set_view(-25.7406, 28.1919, 17, token="search")
    before_center = list(ui.st.session_state[ui.K_VIEW_CENTER])
    before_zoom = ui.st.session_state[ui.K_VIEW_ZOOM]

    _consume(ui, _payload(drawing=SQUARE, center={"lat": -30.0, "lng": 25.0}, zoom=5))

    assert ui.st.session_state[ui.K_VIEW_CENTER] == before_center
    assert ui.st.session_state[ui.K_VIEW_ZOOM] == before_zoom


def test_the_view_moves_only_on_an_explicit_action(ui):
    assert ui.st.session_state[ui.K_VIEW_CENTER] == ui.DEFAULT_CENTER
    assert ui.st.session_state[ui.K_VIEW_ZOOM] == ui.DEFAULT_ZOOM

    ui.set_view(-25.7406, 28.1919, ui.SEARCH_ZOOM, token="hit:union-buildings")
    assert ui.st.session_state[ui.K_VIEW_CENTER] == [-25.7406, 28.1919]
    assert ui.st.session_state[ui.K_VIEW_ZOOM] == ui.SEARCH_ZOOM
    assert ui.st.session_state[ui.K_CENTRED_ON] == "hit:union-buildings"


def test_the_search_zoom_is_close_enough_to_identify_a_building(ui):
    assert ui.SEARCH_ZOOM >= 16


# ── Draft lifecycle ───────────────────────────────────────────────────────────

def test_clearing_a_draft_resets_the_form(ui):
    _consume(ui, _payload(drawing=SQUARE))
    nonce_before = ui.st.session_state[ui.K_FORM_NONCE]

    ui.clear_draft()
    assert ui.st.session_state[ui.K_PENDING_GEOM] is None
    assert ui.st.session_state[ui.K_EDITING_ID] is None
    assert ui.st.session_state[ui.K_FORM_NONCE] > nonce_before

    # The same shape can then be redrawn and picked up again
    assert _consume(ui, _payload(drawing=SQUARE)) is True
    assert ui.st.session_state[ui.K_PENDING_GEOM] == SQUARE

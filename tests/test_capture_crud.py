"""Capture CRUD: primary-feature rules, soft delete, and update semantics."""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services import capture

POINT = {"type": "Point", "coordinates": [28.1919, -25.7406]}
OTHER_POINT = {"type": "Point", "coordinates": [28.20, -25.75]}
SQUARE = {"type": "Polygon", "coordinates": [[
    [28.00, -26.00], [28.01, -26.00], [28.01, -25.99], [28.00, -25.99], [28.00, -26.00],
]]}


def _save(code="IRM-1", name="Test Project", geometry=None, **kwargs):
    return capture.save_capture(
        project_code=code,
        project_name=name,
        geometry=geometry or POINT,
        verification_status=kwargs.pop("verification_status", "Corrected"),
        lifecycle_status=kwargs.pop("lifecycle_status", "Planning"),
        captured_by=kwargs.pop("captured_by", "tester"),
        **kwargs,
    )


# ── Save ──────────────────────────────────────────────────────────────────────

def test_save_persists_geometry_and_metrics(temp_db):
    ok, message, capture_id = _save(comments="Verified against site photo")
    assert ok, message
    row = capture.get_capture(capture_id)
    assert row.project_code == "IRM-1"
    assert row.geometry_type == "Point"
    assert row.centroid_lat == pytest.approx(-25.7406)
    assert row.comments == "Verified against site photo"
    assert row.captured_by == "tester"
    assert row.deleted == 0


def test_first_capture_for_a_project_becomes_primary(temp_db):
    _, _, first = _save()
    assert capture.get_capture(first).is_primary == 1


def test_second_capture_does_not_steal_primary_by_default(temp_db):
    _, _, first = _save()
    _, _, second = _save(geometry=SQUARE, is_primary=False)
    assert capture.get_capture(first).is_primary == 1
    assert capture.get_capture(second).is_primary == 0


def test_only_one_primary_survives_per_project(temp_db):
    _, _, first = _save()
    _, _, second = _save(geometry=SQUARE, is_primary=True)
    assert capture.get_capture(first).is_primary == 0
    assert capture.get_capture(second).is_primary == 1


def test_primary_is_scoped_to_its_own_project(temp_db):
    _, _, a = _save(code="IRM-A")
    _, _, b = _save(code="IRM-B")
    assert capture.get_capture(a).is_primary == 1
    assert capture.get_capture(b).is_primary == 1


@pytest.mark.parametrize("kwargs,fragment", [
    ({"code": ""}, "project code is required"),
    ({"name": ""}, "project name is required"),
    ({"geometry": {"type": "Point", "coordinates": [31.03, -17.82]}}, "South Africa"),
    ({"verification_status": "Made Up"}, "verification status"),
    ({"lifecycle_status": "Made Up"}, "lifecycle status"),
])
def test_invalid_saves_are_rejected(temp_db, kwargs, fragment):
    ok, message, capture_id = _save(**kwargs)
    assert ok is False
    assert capture_id is None
    assert fragment.lower() in message.lower()
    assert capture.list_captures() == []


# ── Update ────────────────────────────────────────────────────────────────────

def test_update_changes_only_the_supplied_fields(temp_db):
    _, _, capture_id = _save(comments="original")
    ok, message = capture.update_capture(capture_id, comments="revised")
    assert ok, message
    row = capture.get_capture(capture_id)
    assert row.comments == "revised"
    assert row.verification_status == "Corrected"     # untouched
    assert row.lifecycle_status == "Planning"


def test_update_recomputes_metrics_when_geometry_changes(temp_db):
    _, _, capture_id = _save()
    assert capture.get_capture(capture_id).area_m2 is None

    ok, _ = capture.update_capture(capture_id, geometry=SQUARE)
    assert ok
    row = capture.get_capture(capture_id)
    assert row.geometry_type == "Polygon"
    assert row.area_m2 == pytest.approx(1_111_000, rel=0.05)
    assert row.centroid_lat == pytest.approx(-25.995, abs=1e-3)


def test_update_rejects_a_bad_geometry_without_touching_the_row(temp_db):
    _, _, capture_id = _save()
    ok, message = capture.update_capture(
        capture_id, geometry={"type": "Point", "coordinates": [31.03, -17.82]}
    )
    assert ok is False
    assert "South Africa" in message
    assert capture.get_capture(capture_id).centroid_lat == pytest.approx(-25.7406)


def test_update_of_a_missing_capture_fails_gracefully(temp_db):
    ok, message = capture.update_capture("does-not-exist", comments="x")
    assert ok is False
    assert "no longer exists" in message


# ── Delete ────────────────────────────────────────────────────────────────────

def test_soft_delete_hides_the_row_but_keeps_it(temp_db):
    _, _, capture_id = _save()
    ok, _ = capture.soft_delete_capture(capture_id)
    assert ok
    assert capture.list_captures() == []
    assert capture.get_capture(capture_id).deleted == 1   # still on disk


def test_deleting_the_primary_promotes_the_next_oldest(temp_db):
    _, _, first = _save()
    _, _, second = _save(geometry=SQUARE, is_primary=False)
    capture.soft_delete_capture(first)
    assert capture.get_capture(second).is_primary == 1


def test_deleting_a_non_primary_leaves_the_primary_alone(temp_db):
    _, _, first = _save()
    _, _, second = _save(geometry=SQUARE, is_primary=False)
    capture.soft_delete_capture(second)
    assert capture.get_capture(first).is_primary == 1


def test_double_delete_is_reported_not_repeated(temp_db):
    _, _, capture_id = _save()
    capture.soft_delete_capture(capture_id)
    ok, message = capture.soft_delete_capture(capture_id)
    assert ok is False
    assert "no longer exists" in message


# ── Promote ───────────────────────────────────────────────────────────────────

def test_set_primary_moves_the_flag(temp_db):
    _, _, first = _save()
    _, _, second = _save(geometry=OTHER_POINT, is_primary=False)
    ok, _ = capture.set_primary(second)
    assert ok
    assert capture.get_capture(first).is_primary == 0
    assert capture.get_capture(second).is_primary == 1


# ── Listing ───────────────────────────────────────────────────────────────────

def test_listing_filters_by_project_and_by_user(temp_db):
    _save(code="IRM-A", captured_by="nomsa")
    _save(code="IRM-B", captured_by="nomsa")
    _save(code="IRM-B", captured_by="thabo", is_primary=False)

    assert len(capture.captures_for_project("IRM-B")) == 2
    assert len(capture.list_captures(captured_by="nomsa")) == 2
    assert len(capture.list_captures(captured_by="thabo")) == 1
    assert len(capture.list_captures()) == 3

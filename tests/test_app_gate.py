"""
Smoke tests for the auth gate and role-filtered navigation, driven through
Streamlit's own AppTest harness so session state behaves as it does in the browser.
"""

from __future__ import annotations

import os
import sys

import pytest
from streamlit.testing.v1 import AppTest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services import auth

APP_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "app.py")
)

CAPTURE_PAGE = "📍  Location Capture"
ADMIN_PAGES = {"👥  User Admin", "📇  IRM Worklist"}


def _run(session_state: dict | None = None) -> AppTest:
    harness = AppTest.from_file(APP_PATH, default_timeout=60)
    for key, value in (session_state or {}).items():
        harness.session_state[key] = value
    return harness.run()


def _nav_options(harness: AppTest) -> list[str]:
    radios = [r for r in harness.radio if r.label == "Navigate"]
    return [str(o) for o in radios[0].options] if radios else []


def _signed_in(role: str) -> dict:
    user = auth.AuthUser(
        id="test-id", username=f"{role.lower().replace(' ', '')}-user",
        full_name=f"Test {role}", role=role, organisation="Test Org",
    )
    return {"auth_user": user, "user_role": role}


# ── The gate ──────────────────────────────────────────────────────────────────

def test_anonymous_visitors_see_the_login_screen_only():
    harness = _run()
    assert not harness.exception

    headings = [h.value for h in harness.title]
    assert "Sign in to STAM" in headings

    # The application itself has not rendered
    assert _nav_options(harness) == []
    assert any("Password" == i.label for i in harness.text_input)


def test_bad_credentials_are_rejected():
    harness = _run()
    inputs = {i.label: i for i in harness.text_input}
    inputs["Username"].set_value("admin")
    inputs["Password"].set_value("definitely-not-the-password")
    harness.button[0].click().run()

    assert not harness.exception
    assert any("Incorrect username or password" in e.value for e in harness.error)
    assert "auth_user" not in harness.session_state
    assert harness.session_state["auth_failures"] == 1   # throttle is counting
    assert _nav_options(harness) == []                   # still gated


# ── Role-filtered navigation ──────────────────────────────────────────────────

def test_analyst_cannot_see_the_capture_page():
    harness = _run(_signed_in("Analyst"))
    assert not harness.exception

    options = _nav_options(harness)
    assert options, "the app should have rendered its navigation"
    assert CAPTURE_PAGE not in options
    assert not ADMIN_PAGES & set(options)


def test_capturer_sees_only_the_capture_page():
    """Capturers are external users with one job — no STAM analysis pages."""
    harness = _run(_signed_in("IRM Capturer"))
    assert not harness.exception

    assert harness.session_state["page"] == "Location Capture"
    assert any("IRM Location Capture" in t.value for t in harness.title)

    # A single-option nav is suppressed entirely, so nothing else is reachable
    assert _nav_options(harness) == []
    body = " ".join(m.value for m in harness.markdown)
    for hidden in ("Dashboard", "Scoring Engine", "User Admin", "IRM Worklist"):
        assert hidden not in body, f"capturer should not see '{hidden}'"


def test_administrator_sees_everything():
    harness = _run(_signed_in("Administrator"))
    assert not harness.exception

    options = _nav_options(harness)
    assert CAPTURE_PAGE in options
    assert ADMIN_PAGES <= set(options)


@pytest.mark.parametrize("role", sorted(auth.ALL_ROLES))
def test_every_role_can_load_the_dashboard(role):
    """No role should be able to crash the app just by signing in."""
    harness = _run(_signed_in(role))
    assert not harness.exception, f"{role} broke the app: {harness.exception}"


# ── Page-level guards ─────────────────────────────────────────────────────────

def test_capture_page_refuses_a_role_that_lacks_permission():
    """Even if the page is reached directly, the guard must hold."""
    state = _signed_in("Analyst")
    state["page"] = "Location Capture"
    harness = _run(state)

    assert not harness.exception
    # 'Location Capture' is not in this role's nav, so the router falls back
    assert harness.session_state["page"] == "Dashboard"


def test_admin_can_open_the_capture_page():
    state = _signed_in("Administrator")
    state["page"] = "Location Capture"
    harness = _run(state)

    assert not harness.exception
    assert any("IRM Location Capture" in t.value for t in harness.title)


def test_a_fresh_login_lands_on_a_blank_new_capture():
    """
    Signing in must drop the user straight into a new capture — no half-finished
    draft and nothing open for editing, whatever the previous session was doing.
    """
    harness = _run(_signed_in("IRM Capturer"))
    assert not harness.exception

    assert harness.session_state["page"] == "Location Capture"
    assert harness.session_state["cap_editing_id"] is None
    assert harness.session_state["cap_pending_geom"] is None
    assert harness.session_state["cap_free_entry"] is False   # worklist, not free entry

    headings = [s.value for s in harness.subheader]
    assert any("New capture" in h for h in headings), headings
    assert not any("Edit capture" in h for h in headings), headings


def test_only_one_control_on_the_page_offers_a_plus():
    """
    '➕' must mean exactly one thing — start a new capture. A second ➕ on the
    worklist toggle read as the same action and confused users.
    """
    harness = _run(_signed_in("IRM Capturer"))
    assert not harness.exception

    plus_labels = [
        element.label
        for group in (harness.toggle, harness.checkbox, harness.button)
        for element in group
        if "➕" in (element.label or "")
    ]
    assert plus_labels == [], f"competing ➕ controls: {plus_labels}"

    toggles = [t.label for t in harness.toggle]
    assert any("not on the IRM worklist" in label for label in toggles), toggles


def test_signing_out_discards_any_capture_in_progress():
    """The next user must not inherit the previous one's draft."""
    state = _signed_in("IRM Capturer")
    state["cap_pending_geom"] = {"type": "Point", "coordinates": [28.19, -25.74]}
    state["cap_editing_id"] = "some-capture-id"
    harness = _run(state)

    sign_out = [b for b in harness.button if "Sign out" in b.label]
    assert sign_out, "the sidebar should offer a sign-out button"
    sign_out[0].click().run()

    assert not harness.exception
    assert "auth_user" not in harness.session_state
    assert "cap_pending_geom" not in harness.session_state
    assert "cap_editing_id" not in harness.session_state
    assert any("Sign in to STAM" in t.value for t in harness.title)


@pytest.mark.parametrize("page,heading", [
    ("Location Capture", "IRM Location Capture"),
    ("IRM Worklist", "IRM Worklist"),
    ("User Admin", "User Admin"),
])
def test_every_new_page_renders_for_an_administrator(page, heading):
    """
    Catches detached-instance errors: these pages read ORM rows after the session
    that loaded them has been closed.
    """
    state = _signed_in("Administrator")
    state["page"] = page
    harness = _run(state)

    assert not harness.exception, f"{page} raised: {harness.exception}"
    assert any(heading in t.value for t in harness.title)

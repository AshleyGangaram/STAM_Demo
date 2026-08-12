"""
STAM sign-in screen.

Rendered by app.py in place of the whole application whenever no user is
authenticated. Holds a short lockout after repeated failures.
"""

from __future__ import annotations

import time
from pathlib import Path

import streamlit as st

from services import auth

_ASSETS = Path(__file__).resolve().parent.parent / "assets"

_FAILURES_KEY = "auth_failures"
_LOCKED_UNTIL_KEY = "auth_locked_until"


def _seconds_remaining() -> int:
    locked_until = st.session_state.get(_LOCKED_UNTIL_KEY, 0)
    return max(0, int(locked_until - time.time()))


def _register_failure() -> None:
    failures = st.session_state.get(_FAILURES_KEY, 0) + 1
    st.session_state[_FAILURES_KEY] = failures
    if failures >= auth.MAX_FAILED_ATTEMPTS:
        st.session_state[_LOCKED_UNTIL_KEY] = time.time() + auth.LOCKOUT_SECONDS
        st.session_state[_FAILURES_KEY] = 0


def render() -> None:
    _left, middle, _right = st.columns([1, 1.6, 1])

    with middle:
        logo = _ASSETS / "logo.png"
        if logo.exists():
            st.image(str(logo), width=220)

        st.title("Sign in to STAM")
        st.caption(
            "Spatial Transformation Appraisal Mechanism — "
            "Gauteng Province Capital Budget Decision Support"
        )
        st.divider()

        locked_for = _seconds_remaining()
        if locked_for:
            st.error(
                f"Too many failed attempts. Try again in {locked_for} second"
                f"{'s' if locked_for != 1 else ''}."
            )

        with st.form("stam_login", clear_on_submit=False):
            username = st.text_input("Username", autocomplete="username")
            password = st.text_input("Password", type="password",
                                     autocomplete="current-password")
            submitted = st.form_submit_button(
                "Sign in", type="primary", use_container_width=True,
                disabled=bool(locked_for),
            )

        if submitted and not locked_for:
            with st.spinner("Signing in…"):
                user, message = auth.authenticate(username, password)
            if user is None:
                _register_failure()
                remaining = auth.MAX_FAILED_ATTEMPTS - st.session_state.get(_FAILURES_KEY, 0)
                st.error(message)
                if 0 < remaining <= 2:
                    st.caption(f"{remaining} attempt{'s' if remaining != 1 else ''} "
                               "left before a temporary lockout.")
            else:
                auth.sign_in(user)
                st.session_state.pop(_LOCKED_UNTIL_KEY, None)
                st.rerun()

        if auth.using_default_admin_password():
            st.divider()
            st.warning(
                "**Demo deployment** — the administrator account is still using its "
                "default password. Sign in as `admin` / `ChangeMe!2026` and change it "
                "from **User Admin**, or set `STAM_ADMIN_PASSWORD` in `.env`."
            )

        st.divider()
        st.caption("🏛️ Gauteng Province · GT/GDeG/031/2025")
        st.caption("TERRA VITAL / Vastpoint")

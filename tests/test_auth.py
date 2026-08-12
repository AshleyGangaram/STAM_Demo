"""Unit tests for services/auth.py — password hashing and role constants."""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services import auth


# ── Hashing ───────────────────────────────────────────────────────────────────

def test_hash_verify_round_trip():
    stored = auth.hash_password("Correct-Horse-42")
    assert auth.verify_password("Correct-Horse-42", stored) is True


def test_verify_rejects_wrong_password():
    stored = auth.hash_password("Correct-Horse-42")
    assert auth.verify_password("correct-horse-42", stored) is False
    assert auth.verify_password("", stored) is False
    assert auth.verify_password("Correct-Horse-43", stored) is False


def test_hash_format_is_pbkdf2_with_four_parts():
    stored = auth.hash_password("whatever")
    parts = stored.split("$")
    assert len(parts) == 4
    assert parts[0] == "pbkdf2_sha256"
    assert int(parts[1]) >= 100_000        # iteration count not silently weakened
    assert len(parts[2]) == 32             # 16-byte salt as hex
    assert len(parts[3]) == 64             # sha256 digest as hex


def test_salt_is_unique_per_hash():
    a = auth.hash_password("same-password")
    b = auth.hash_password("same-password")
    assert a != b                          # different salt ⇒ different stored value
    assert auth.verify_password("same-password", a)
    assert auth.verify_password("same-password", b)


@pytest.mark.parametrize(
    "bad",
    ["", "not-a-hash", "pbkdf2_sha256$abc", "pbkdf2_sha256$x$y$z", "md5$1$aa$bb", None],
)
def test_verify_returns_false_on_malformed_hash(bad):
    """A corrupt stored hash must deny access, never raise."""
    assert auth.verify_password("anything", bad) is False


def test_empty_password_cannot_be_hashed():
    with pytest.raises(ValueError):
        auth.hash_password("")


# ── Roles ─────────────────────────────────────────────────────────────────────

def test_role_constants_are_consistent():
    assert auth.CAPTURE_ROLES <= auth.ALL_ROLES
    assert auth.ADMIN_ROLES <= auth.ALL_ROLES
    assert auth.ADMIN_ROLES <= auth.CAPTURE_ROLES   # admins can always capture
    assert "Administrator" in auth.ADMIN_ROLES
    assert "IRM Capturer" in auth.CAPTURE_ROLES
    assert "Analyst" not in auth.CAPTURE_ROLES      # plain analysts cannot capture


def test_existing_stam_roles_are_preserved():
    """The four roles STAM already uses in audit logs must remain valid."""
    for role in ("Analyst", "Planner", "Administrator", "Executive Viewer"):
        assert role in auth.ALL_ROLES


# ── Accounts (against a throwaway database) ───────────────────────────────────

def test_create_and_authenticate(temp_db):
    ok, message = auth.create_user("Nomsa", "Capture!2026", full_name="Nomsa K",
                                   role="IRM Capturer", organisation="Treasury")
    assert ok, message

    user, message = auth.authenticate("nomsa", "Capture!2026")
    assert user is not None, message
    assert user.username == "nomsa"          # normalised to lowercase
    assert user.role == "IRM Capturer"
    assert user.can_capture is True
    assert user.is_admin is False


def test_usernames_are_case_insensitive(temp_db):
    auth.create_user("nomsa", "Capture!2026")
    assert auth.authenticate("NOMSA", "Capture!2026")[0] is not None
    assert auth.authenticate("  Nomsa  ", "Capture!2026")[0] is not None


@pytest.mark.parametrize("username,password", [
    ("nomsa", "wrong-password"),
    ("ghost", "Capture!2026"),
    ("", "Capture!2026"),
    ("nomsa", ""),
])
def test_bad_credentials_share_one_message(temp_db, username, password):
    """The message must not reveal whether the account exists."""
    auth.create_user("nomsa", "Capture!2026")
    user, message = auth.authenticate(username, password)
    assert user is None
    assert message == "Incorrect username or password."


def test_disabled_accounts_cannot_sign_in(temp_db):
    auth.create_user("nomsa", "Capture!2026")
    auth.set_active("nomsa", False)

    user, message = auth.authenticate("nomsa", "Capture!2026")
    assert user is None
    assert "disabled" in message.lower()


def test_duplicate_usernames_are_refused(temp_db):
    assert auth.create_user("nomsa", "Capture!2026")[0] is True
    ok, message = auth.create_user("NOMSA", "Another!2026")
    assert ok is False
    assert "already exists" in message


@pytest.mark.parametrize("password,fragment", [
    ("short1", "at least 8"),
    ("", "required"),
])
def test_weak_passwords_are_refused(temp_db, password, fragment):
    ok, message = auth.create_user("nomsa", password)
    assert ok is False
    assert fragment in message


def test_unknown_roles_are_refused(temp_db):
    ok, message = auth.create_user("nomsa", "Capture!2026", role="Supreme Leader")
    assert ok is False
    assert "Unknown role" in message


def test_password_change_invalidates_the_old_password(temp_db):
    auth.create_user("nomsa", "Capture!2026")
    auth.set_password("nomsa", "Brand!New2026")

    assert auth.authenticate("nomsa", "Capture!2026")[0] is None
    assert auth.authenticate("nomsa", "Brand!New2026")[0] is not None


def test_role_change_takes_effect(temp_db):
    auth.create_user("nomsa", "Capture!2026", role="Analyst")
    assert auth.authenticate("nomsa", "Capture!2026")[0].can_capture is False

    auth.set_role("nomsa", "Administrator")
    user = auth.authenticate("nomsa", "Capture!2026")[0]
    assert user.is_admin is True
    assert user.can_capture is True


def test_bootstrap_admin_is_created_once(temp_db):
    auth.ensure_default_admin()
    admin = auth.get_user(auth.DEFAULT_ADMIN_USERNAME)
    assert admin is not None
    assert admin.role == "Administrator"

    auth.set_password(auth.DEFAULT_ADMIN_USERNAME, "Rotated!2026")
    auth.ensure_default_admin()                       # must not reset the password
    assert auth.authenticate("admin", "Rotated!2026")[0] is not None


def test_default_admin_password_warning_clears_after_a_change(temp_db, monkeypatch):
    monkeypatch.setattr(auth, "_get_secret", lambda name, default="": "")
    auth.ensure_default_admin()
    assert auth.using_default_admin_password() is True

    auth.set_password(auth.DEFAULT_ADMIN_USERNAME, "Rotated!2026")
    assert auth.using_default_admin_password() is False


def test_password_hash_is_never_exposed_on_the_session_user(temp_db):
    auth.create_user("nomsa", "Capture!2026")
    user = auth.authenticate("nomsa", "Capture!2026")[0]
    assert not hasattr(user, "password_hash")
    assert "Capture!2026" not in repr(user)

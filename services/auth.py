"""
Authentication and user management for STAM.

Password hashing uses stdlib PBKDF2-HMAC-SHA256 rather than bcrypt, which keeps
the Streamlit Cloud deployment free of native wheels. Stored format:

    pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>

Session state (Streamlit) holds only the authenticated user's id, username, name
and role — never the password or its hash.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass

from services.db import User, get_session, log_action

# ── Roles ─────────────────────────────────────────────────────────────────────
# The four original STAM roles are preserved so every existing
# log_action(..., user_role=...) call keeps its meaning, plus one new role.

ALL_ROLES: frozenset[str] = frozenset({
    "Administrator",
    "Analyst",
    "Planner",
    "Executive Viewer",
    "IRM Capturer",
})

ADMIN_ROLES: frozenset[str] = frozenset({"Administrator"})
CAPTURE_ROLES: frozenset[str] = frozenset({"Administrator", "IRM Capturer"})

DEFAULT_ROLE = "Analyst"

# ── Hashing parameters ────────────────────────────────────────────────────────

_ALGORITHM = "pbkdf2_sha256"
_ITERATIONS = 240_000
_SALT_BYTES = 16

# ── Login throttle ────────────────────────────────────────────────────────────

MAX_FAILED_ATTEMPTS = 5
LOCKOUT_SECONDS = 60

# ── Default admin bootstrap ───────────────────────────────────────────────────

DEFAULT_ADMIN_USERNAME = "admin"
DEFAULT_ADMIN_PASSWORD = "ChangeMe!2026"


@dataclass(frozen=True)
class AuthUser:
    """Immutable snapshot of the signed-in user, safe to hold in session state."""
    id: str
    username: str
    full_name: str
    role: str
    organisation: str = ""
    email: str = ""

    @property
    def can_capture(self) -> bool:
        return self.role in CAPTURE_ROLES

    @property
    def is_admin(self) -> bool:
        return self.role in ADMIN_ROLES


# ── Password hashing ──────────────────────────────────────────────────────────

def hash_password(password: str, *, iterations: int = _ITERATIONS) -> str:
    """Return a self-describing PBKDF2 hash string for `password`."""
    if not password:
        raise ValueError("Password must not be empty.")
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"{_ALGORITHM}${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    """
    Constant-time check of `password` against a stored hash.

    Any malformed or unrecognised stored value denies access rather than raising —
    a corrupt row must never become an exception on the login path.
    """
    if not password or not stored:
        return False
    try:
        algorithm, iterations, salt_hex, hash_hex = stored.split("$")
        if algorithm != _ALGORITHM:
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations)
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest.hex(), hash_hex)


# ── Configuration helpers ─────────────────────────────────────────────────────

def _get_secret(name: str, default: str = "") -> str:
    """Resolve a secret from Streamlit secrets (cloud) then env var (local)."""
    try:
        import streamlit as st
        value = st.secrets.get(name, "")
        if value:
            return str(value)
    except Exception:
        pass
    return os.environ.get(name, default)


# ── User CRUD ─────────────────────────────────────────────────────────────────

def _to_auth_user(row: User) -> AuthUser:
    return AuthUser(
        id=row.id,
        username=row.username,
        full_name=row.full_name or row.username,
        role=row.role or DEFAULT_ROLE,
        organisation=row.organisation or "",
        email=row.email or "",
    )


def create_user(username: str, password: str, full_name: str = "",
                role: str = DEFAULT_ROLE, email: str = "",
                organisation: str = "") -> tuple[bool, str]:
    """Create a user. Returns (success, message) — never raises on duplicates."""
    username = (username or "").strip().lower()
    if not username:
        return False, "Username is required."
    if not password:
        return False, "Password is required."
    if len(password) < 8:
        return False, "Password must be at least 8 characters."
    if role not in ALL_ROLES:
        return False, f"Unknown role '{role}'."

    session = get_session()
    try:
        if session.query(User).filter(User.username == username).first():
            return False, f"Username '{username}' already exists."
        session.add(User(
            username=username,
            full_name=(full_name or "").strip() or username,
            email=(email or "").strip(),
            organisation=(organisation or "").strip(),
            password_hash=hash_password(password),
            role=role,
            active=1,
        ))
        session.commit()
        return True, f"User '{username}' created."
    finally:
        session.close()


def list_users() -> list[User]:
    session = get_session()
    try:
        return session.query(User).order_by(User.username).all()
    finally:
        session.close()


def get_user(username: str) -> User | None:
    session = get_session()
    try:
        return session.query(User).filter(
            User.username == (username or "").strip().lower()
        ).first()
    finally:
        session.close()


def set_password(username: str, new_password: str) -> tuple[bool, str]:
    if not new_password or len(new_password) < 8:
        return False, "Password must be at least 8 characters."
    session = get_session()
    try:
        row = session.query(User).filter(User.username == username).first()
        if not row:
            return False, f"User '{username}' not found."
        row.password_hash = hash_password(new_password)
        session.commit()
        return True, f"Password updated for '{username}'."
    finally:
        session.close()


def set_active(username: str, active: bool) -> tuple[bool, str]:
    session = get_session()
    try:
        row = session.query(User).filter(User.username == username).first()
        if not row:
            return False, f"User '{username}' not found."
        row.active = 1 if active else 0
        session.commit()
        return True, f"'{username}' {'enabled' if active else 'disabled'}."
    finally:
        session.close()


def set_role(username: str, role: str) -> tuple[bool, str]:
    if role not in ALL_ROLES:
        return False, f"Unknown role '{role}'."
    session = get_session()
    try:
        row = session.query(User).filter(User.username == username).first()
        if not row:
            return False, f"User '{username}' not found."
        row.role = role
        session.commit()
        return True, f"'{username}' is now {role}."
    finally:
        session.close()


# ── Authentication ────────────────────────────────────────────────────────────

def authenticate(username: str, password: str) -> tuple[AuthUser | None, str]:
    """
    Validate credentials. Returns (user, message); user is None on failure.

    The failure message is deliberately identical for unknown-user and
    wrong-password so it cannot be used to enumerate accounts.
    """
    generic = "Incorrect username or password."
    username = (username or "").strip().lower()
    if not username or not password:
        return None, generic

    session = get_session()
    try:
        row = session.query(User).filter(User.username == username).first()
        if row is None or not verify_password(password, row.password_hash):
            return None, generic
        if not row.active:
            return None, "This account has been disabled. Contact an administrator."

        from services.db import _now
        row.last_login_at = _now()
        session.commit()
        user = _to_auth_user(row)
    finally:
        session.close()

    log_action("LOGIN", "user", user.username,
               {"role": user.role}, user_role=user.role)
    return user, f"Welcome, {user.full_name}."


def ensure_default_admin() -> None:
    """
    Create the bootstrap administrator when the users table is empty.

    Password comes from STAM_ADMIN_PASSWORD (Streamlit secrets or env). When that
    is unset the documented default is used and `using_default_admin_password()`
    stays true so the UI can keep warning about it.
    """
    session = get_session()
    try:
        if session.query(User).count() > 0:
            return
    finally:
        session.close()

    password = _get_secret("STAM_ADMIN_PASSWORD") or DEFAULT_ADMIN_PASSWORD
    create_user(
        username=DEFAULT_ADMIN_USERNAME,
        password=password,
        full_name="STAM Administrator",
        role="Administrator",
        organisation="TERRA VITAL / Vastpoint",
    )


def using_default_admin_password() -> bool:
    """True while the bootstrap admin still holds the documented default password."""
    if _get_secret("STAM_ADMIN_PASSWORD"):
        return False
    row = get_user(DEFAULT_ADMIN_USERNAME)
    return bool(row and verify_password(DEFAULT_ADMIN_PASSWORD, row.password_hash))


# ── Streamlit session helpers ─────────────────────────────────────────────────

_SESSION_KEY = "auth_user"


def sign_in(user: AuthUser) -> None:
    import streamlit as st
    st.session_state[_SESSION_KEY] = user
    # Keep the legacy key populated so every existing log_action(...) call works.
    st.session_state["user_role"] = user.role
    st.session_state["auth_failures"] = 0


def sign_out() -> None:
    import streamlit as st
    user = current_user()
    if user:
        log_action("LOGOUT", "user", user.username, {}, user_role=user.role)
    for key in (_SESSION_KEY, "user_role"):
        st.session_state.pop(key, None)
    # Drop capture-page state so the next user starts clean.
    for key in [k for k in st.session_state if k.startswith("cap_")]:
        st.session_state.pop(key, None)


def current_user() -> AuthUser | None:
    import streamlit as st
    return st.session_state.get(_SESSION_KEY)


def is_authenticated() -> bool:
    return current_user() is not None


def has_role(*roles: str) -> bool:
    """True when the signed-in user's role is in `roles` (a set may be passed)."""
    user = current_user()
    if user is None:
        return False
    allowed: set[str] = set()
    for role in roles:
        allowed |= set(role) if isinstance(role, (set, frozenset)) else {role}
    return user.role in allowed


def require_role(*roles: str) -> bool:
    """
    Guard for page render() functions. Shows a message and returns False when the
    signed-in user may not view the page.
    """
    import streamlit as st
    if not is_authenticated():
        st.error("You must sign in to view this page.")
        return False
    if not has_role(*roles):
        st.error("🔒 You do not have permission to view this page.")
        st.caption("Contact a STAM administrator if you believe this is a mistake.")
        return False
    return True

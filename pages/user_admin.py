"""
User Admin — create and manage STAM accounts.

Administrators only. Roles decide what appears in the sidebar:
  Administrator      everything, including this page
  IRM Capturer       Location Capture plus the standard STAM pages
  Analyst / Planner / Executive Viewer   the standard STAM pages only
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from services import auth

ROLE_DESCRIPTIONS = {
    "Administrator":    "Full access, user management, worklist upload, all exports.",
    "IRM Capturer":     "Can capture and correct project locations.",
    "Analyst":          "Standard STAM analysis pages.",
    "Planner":          "Standard STAM analysis pages.",
    "Executive Viewer": "Standard STAM analysis pages.",
}

ROLE_ORDER = ["Administrator", "IRM Capturer", "Analyst", "Planner", "Executive Viewer"]


def render() -> None:
    if not auth.require_role(auth.ADMIN_ROLES):
        return

    current = auth.current_user()

    st.title("👥 User Admin")
    st.caption("Accounts, roles, and passwords for the STAM platform.")

    if auth.using_default_admin_password():
        st.warning(
            "**The `admin` account is still using its default password.** "
            "Change it below, or set `STAM_ADMIN_PASSWORD` in `.env` / Streamlit "
            "secrets before this is shown to anyone outside the team."
        )

    users_tab, add_tab, manage_tab = st.tabs(["Users", "Add a user", "Manage a user"])

    with users_tab:
        _render_users()

    with add_tab:
        _render_add_user()

    with manage_tab:
        _render_manage_user(current)


def _render_users() -> None:
    users = auth.list_users()
    if not users:
        st.info("No users yet.")
        return

    columns = st.columns(3)
    columns[0].metric("Users", len(users))
    columns[1].metric("Active", sum(1 for u in users if u.active))
    columns[2].metric("Can capture",
                      sum(1 for u in users if u.role in auth.CAPTURE_ROLES))

    st.dataframe(
        pd.DataFrame([{
            "Status": "✅ Active" if u.active else "🚫 Disabled",
            "Username": u.username,
            "Full name": u.full_name or "",
            "Role": u.role,
            "Organisation": u.organisation or "",
            "Email": u.email or "",
            "Last sign-in": (u.last_login_at or "—")[:16].replace("T", " "),
            "Created": (u.created_at or "")[:10],
        } for u in users]),
        use_container_width=True, hide_index=True,
    )

    with st.expander("What each role can do"):
        for role in ROLE_ORDER:
            st.markdown(f"**{role}** — {ROLE_DESCRIPTIONS[role]}")


def _render_add_user() -> None:
    st.subheader("Create an account")

    with st.form("add_user", clear_on_submit=True):
        columns = st.columns(2)
        username = columns[0].text_input("Username *", placeholder="jdlamini")
        full_name = columns[1].text_input("Full name", placeholder="Jabu Dlamini")

        columns = st.columns(2)
        email = columns[0].text_input("Email", placeholder="jabu@treasury.gov.za")
        organisation = columns[1].text_input("Organisation",
                                             placeholder="National Treasury")

        role = st.selectbox("Role *", ROLE_ORDER, index=ROLE_ORDER.index("IRM Capturer"))
        st.caption(ROLE_DESCRIPTIONS[role])

        columns = st.columns(2)
        password = columns[0].text_input("Password *", type="password")
        confirm = columns[1].text_input("Confirm password *", type="password")

        submitted = st.form_submit_button("Create user", type="primary")

    if not submitted:
        return
    if password != confirm:
        st.error("The two passwords do not match.")
        return

    ok, message = auth.create_user(
        username=username, password=password, full_name=full_name,
        role=role, email=email, organisation=organisation,
    )
    if ok:
        st.success(message)
        st.rerun()
    else:
        st.error(message)


def _render_manage_user(current) -> None:
    users = auth.list_users()
    if not users:
        st.info("No users to manage.")
        return

    options = {f"{u.username} — {u.full_name or ''} ({u.role})": u for u in users}
    chosen = st.selectbox("User", list(options))
    user = options[chosen]
    is_self = user.username == current.username

    st.divider()
    left, right = st.columns(2)

    with left:
        st.subheader("Role")
        index = ROLE_ORDER.index(user.role) if user.role in ROLE_ORDER else 0
        new_role = st.selectbox("Assign role", ROLE_ORDER, index=index,
                                key=f"role_{user.username}")
        st.caption(ROLE_DESCRIPTIONS[new_role])
        if st.button("Update role", disabled=new_role == user.role):
            if is_self and new_role not in auth.ADMIN_ROLES:
                st.error("You cannot remove your own administrator role.")
            else:
                ok, message = auth.set_role(user.username, new_role)
                st.success(message) if ok else st.error(message)
                st.rerun()

        st.subheader("Access")
        if user.active:
            if st.button("🚫 Disable account", disabled=is_self,
                         help="You cannot disable your own account." if is_self else None):
                ok, message = auth.set_active(user.username, False)
                st.success(message) if ok else st.error(message)
                st.rerun()
        else:
            if st.button("✅ Enable account"):
                ok, message = auth.set_active(user.username, True)
                st.success(message) if ok else st.error(message)
                st.rerun()

    with right:
        st.subheader("Password")
        with st.form(f"reset_{user.username}", clear_on_submit=True):
            password = st.text_input("New password", type="password")
            confirm = st.text_input("Confirm new password", type="password")
            submitted = st.form_submit_button("Set password")

        if submitted:
            if password != confirm:
                st.error("The two passwords do not match.")
            else:
                ok, message = auth.set_password(user.username, password)
                if ok:
                    st.success(message)
                else:
                    st.error(message)

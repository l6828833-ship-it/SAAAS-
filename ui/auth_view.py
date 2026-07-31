"""Login and signup screen."""

from __future__ import annotations

import streamlit as st

from artisan_forge.saas import auth

from . import theme


def _store(user: dict) -> None:
    st.session_state["user"] = {
        "id": user["id"],
        "email": user["email"],
        "display_name": user.get("display_name") or user["email"].split("@")[0],
        "role": user.get("role", "member"),
        "plan": user.get("plan", "free"),
    }


def current_user() -> dict | None:
    return st.session_state.get("user")


def logout() -> None:
    for key in ("user", "nav", "last_run"):
        st.session_state.pop(key, None)


def gate() -> dict | None:
    """Render the auth screen. Returns the signed-in user or None."""
    user = current_user()
    if user:
        return user

    left, middle, right = st.columns([1, 1.15, 1])
    with middle:
        theme.brand(sidebar=False)
        theme.hero(
            "Forge digital",
            "products in minutes",
            "Sign in to generate print-ready calendars, ChatGPT-written bundles, "
            "listing images and Etsy copy.",
        )
        st.write("")

        tab_login, tab_signup = st.tabs(["Sign in", "Create account"])

        with tab_login:
            with st.form("login_form"):
                email = st.text_input("Email", placeholder="you@example.com")
                password = st.text_input("Password", type="password")
                submitted = st.form_submit_button("Sign in", type="primary", use_container_width=True)
            if submitted:
                try:
                    _store(auth.login(email, password))
                    st.rerun()
                except auth.AuthError as exc:
                    st.error(str(exc))

        with tab_signup:
            needs_code = auth.signup_requires_code()
            with st.form("signup_form"):
                name = st.text_input("Name", placeholder="Your name")
                email = st.text_input("Email", key="signup_email", placeholder="you@example.com")
                password = st.text_input(
                    "Password", type="password", key="signup_password",
                    help=f"At least {auth.MIN_PASSWORD} characters",
                )
                code = st.text_input("Invite code", type="password") if needs_code else ""
                submitted = st.form_submit_button("Create account", use_container_width=True)
            if submitted:
                try:
                    _store(auth.signup(email, password, name, code))
                    st.rerun()
                except auth.AuthError as exc:
                    st.error(str(exc))
            if needs_code:
                st.caption("Signups are invite-only on this deployment.")
            else:
                theme.note(
                    "The first account created becomes the admin. Set AF_SIGNUP_CODE to close "
                    "signups afterwards.",
                    "info",
                )
    return None

"""Account and engine settings."""

from __future__ import annotations

import os

import streamlit as st

from artisan_forge.ai.text_client import model_chain
from artisan_forge.config import get_settings
from artisan_forge.products.crochet import short_model
from artisan_forge.saas import auth, db

from . import api_keys, canva_panel, etsy_panel, theme


def render(user: dict) -> None:
    settings = get_settings()
    plan = auth.current_plan(user)

    theme.hero("Account", "& engine", "Your plan, credentials and deployment status.",
               kicker="settings")
    st.write("")

    theme.stats_row(
        [
            ("Plan", plan["plan"], "unlimited builds" if not plan["limit"] else f"{plan['limit']}/month"),
            ("Builds this month", plan["used_this_month"], "across all studios"),
            ("Role", user.get("role", "member"), user["email"]),
            (
                "Image model",
                short_model(settings.image_model) if settings.ai_available else "procedural",
                f"{settings.key_env_var} set" if settings.ai_available else "no API key",
            ),
        ]
    )

    api_keys.render(user)

    theme.section("Engine status", "what the app is using right now")
    rows = [
        (f"{settings.provider_label} gateway", settings.ai_available, settings.base_url),
        ("Pattern writer", settings.ai_available,
         ", ".join(short_model(m) for m in model_chain(settings)[:2])),
        ("Vision model (reads photos)", settings.ai_available,
         short_model(settings.vision_model) or "same as the writer"),
        ("Image model", settings.ai_available, short_model(settings.image_model)),
        ("Etsy app", settings.etsy_configured, settings.etsy_redirect_uri),
        ("Canva Connect", settings.canva_available, "editable designs"),
        ("Signup invite code", bool(os.getenv("AF_SIGNUP_CODE", "").strip()), "closes open signups"),
    ]
    for label, ok, detail in rows:
        icon = "\u2705" if ok else "\u2b1c"
        colour = "#6EE7B7" if ok else "#9A9AAE"
        st.markdown(
            f"<div style='display:flex;gap:.6rem;align-items:baseline;margin:.3rem 0'>"
            f"<span>{icon}</span><span style='color:#ECECF1;font-size:.92rem;min-width:190px'>"
            f"{theme.esc(label)}</span>"
            f"<span style='color:{colour};font-size:.85rem'>{theme.esc(detail)}</span></div>",
            unsafe_allow_html=True,
        )

    if not settings.ai_available:
        theme.note(
            f"Add {settings.key_env_var} above to switch artwork from procedural painting to "
            f"{short_model(settings.image_model)} and let "
            f"{short_model(settings.text_model)} write the content. Everything works "
            "without it, on templates and locally painted art.",
            "info",
        )

    etsy_panel.connect_panel(user)

    theme.section("Canva Connect", "push artwork as editable designs")
    canva_panel.render_connect_button()

    theme.section("Change password")
    with st.form("password_form"):
        current = st.text_input("Current password", type="password")
        new = st.text_input("New password", type="password")
        confirm = st.text_input("Confirm new password", type="password")
        submitted = st.form_submit_button("Update password")
    if submitted:
        if new != confirm:
            st.error("The new passwords do not match")
        else:
            try:
                auth.change_password(user["id"], current, new)
                st.success("Password updated")
            except auth.AuthError as exc:
                st.error(str(exc))

    theme.section("Storage")
    stats = db.user_stats(user["id"])
    st.caption(
        f"Database: `{db.db_path()}` \u00b7 output folder: `{settings.resolved_output_dir()}` \u00b7 "
        f"{stats['builds']} builds recorded."
    )
    theme.note(
        "On Railway the filesystem is ephemeral: the database and generated files are wiped on "
        "each redeploy. Attach a volume mounted at /data and set AF_DATA_DIR=/data and "
        "AF_OUTPUT_DIR=/data/output to keep them.",
        "warn",
    )

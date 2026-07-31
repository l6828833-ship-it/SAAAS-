"""Dashboard: overview, studio launcher, recent work."""

from __future__ import annotations

import streamlit as st

from artisan_forge import products
from artisan_forge.config import get_settings
from artisan_forge.saas import db

from . import theme
from .results import load_manifest

NAV_FOR = {
    "calendar": "Calendar Studio",
    "bundle": "Bundle Studio",
    "planner": "Planner Studio",
    "wall_art": "Wall Art Studio",
    "journal": "Journal Studio",
    "social": "Social Kit Studio",
}


def goto(page: str) -> None:
    st.session_state["nav"] = page
    st.rerun()


def render(user: dict) -> None:
    settings = get_settings()
    stats = db.user_stats(user["id"])

    theme.hero(
        f"Welcome back,",
        user["display_name"],
        "Describe a product, press one button, and get print-ready files, listing images "
        "and Etsy copy in a single run.",
        kicker="dashboard",
    )
    st.write("")

    theme.stats_row(
        [
            ("Products forged", stats["builds"], f"{stats['this_month']} this month"),
            ("Pages generated", stats["pages"], "vector PDF"),
            ("Listing images", stats["images"], "2000 x 2000 px"),
            (
                "AI engine",
                "connected" if settings.ai_available else "offline",
                settings.image_model if settings.ai_available else "procedural art + templates",
            ),
        ]
    )

    theme.section("Studios", "pick a product type and forge it")
    live = products.live()
    for row_start in range(0, len(live), 2):
        for column, product in zip(st.columns(2), live[row_start : row_start + 2]):
            with column:
                theme.product_card(
                    product.icon, product.label, product.tagline, product.outputs, "live"
                )
                if st.button(
                    f"Open {product.label}", key=f"open_{product.key}", use_container_width=True
                ):
                    goto(NAV_FOR[product.key])

    theme.section("Coming soon", "already scaffolded in the product catalog")
    soon = products.coming_soon()
    for row_start in range(0, len(soon), 2):
        for column, product in zip(st.columns(2), soon[row_start : row_start + 2]):
            with column:
                theme.product_card(
                    product.icon, product.label, product.tagline, product.outputs, "soon", product.eta
                )

    recent = db.list_builds(user["id"], limit=3)
    if recent:
        theme.section("Recent work", "your latest runs")
        for build in recent:
            columns = st.columns([1, 4, 1.4])
            thumbnail = build.get("thumbnail")
            with columns[0]:
                if thumbnail and load_manifest(build["run_dir"]):
                    try:
                        st.image(thumbnail, use_container_width=True)
                    except Exception:
                        st.empty()
            with columns[1]:
                st.markdown(
                    f"**{build['title']}**  \n"
                    f"<span style='color:#9A9AAE;font-size:.84rem'>"
                    f"{build['product_type']} \u00b7 {build['pages']} pages \u00b7 "
                    f"{build['images']} images \u00b7 {build['created_at'][:16].replace('T', ' ')}"
                    f"</span>",
                    unsafe_allow_html=True,
                )
            with columns[2]:
                if st.button("Open", key=f"recent_{build['id']}", use_container_width=True):
                    st.session_state["library_pick"] = build["run_dir"]
                    goto("Library")
    else:
        theme.section("Get started", "")
        theme.note(
            "Nothing forged yet. Open Calendar Studio for a 14-page printable calendar, or "
            "Bundle Studio to let ChatGPT write a product for you.",
            "info",
        )

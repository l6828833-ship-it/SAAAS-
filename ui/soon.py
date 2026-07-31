"""Teaser page for product types that are not built yet."""

from __future__ import annotations

import streamlit as st

from artisan_forge import products

from . import theme

DETAIL = {
    "planner": [
        "Dated weekly spreads driven by the same verified date engine as the calendars",
        "Undated daily pages, habit trackers, meal planners and goal sheets",
        "Pick your modules, get one bound-ready PDF",
    ],
    "wall_art": [
        "Quote posters and abstract prints from the same theme system",
        "One file per Etsy ratio: 2:3, 3:4, 4:5 and ISO A-sizes",
        "Frame and gallery-wall mockups included",
    ],
    "journal": [
        "Low-content interiors: lined, dot grid, graph, half-lined",
        "KDP trim sizes with correct gutter margins and page counts",
        "Separate interior and cover files",
    ],
    "social": [
        "Pinterest pins and Instagram stories that match the product theme",
        "Auto-filled with the listing title and key selling points",
        "Exported at platform-native sizes",
    ],
}


def render(product_key: str) -> None:
    product = products.get(product_key)
    if product is None:
        st.error("Unknown product type")
        return

    theme.hero(
        product.label.replace(" Studio", ""),
        "Studio",
        product.tagline,
        kicker=f"{product.icon} coming soon",
    )
    st.write("")
    theme.note(
        f"This studio is not built yet ({product.eta or 'planned'}). The shared engine it needs "
        "- page blocks, artwork, mockups, listing copy - is already in place.",
        "info",
    )

    theme.section("What it will do")
    for line in DETAIL.get(product_key, list(product.outputs)):
        st.markdown(
            f"<div style='display:flex;gap:.6rem;margin:.35rem 0'>"
            f"<span style='color:#7C5CFF'>\u25aa</span>"
            f"<span style='color:#B9B9C9;font-size:.92rem'>{theme.esc(line)}</span></div>",
            unsafe_allow_html=True,
        )

    theme.section("Available now instead")
    columns = st.columns(2)
    for column, live in zip(columns, products.live()):
        with column:
            theme.product_card(live.icon, live.label, live.tagline, live.outputs, "live")

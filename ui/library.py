"""Library: every product this account has forged."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from artisan_forge.saas import db

from . import theme
from .results import load_manifest, show


def render(user: dict) -> None:
    theme.hero("Your", "library", "Every product you have forged, with its files and listing copy.",
               kicker="library")
    st.write("")

    builds = db.list_builds(user["id"], limit=100)
    if not builds:
        theme.note("No builds yet. Forge something in Calendar Studio or Bundle Studio.", "info")
        return

    stats = db.user_stats(user["id"])
    theme.stats_row(
        [
            ("Products", stats["builds"], f"{stats['this_month']} this month"),
            ("Pages", stats["pages"], "total"),
            ("Images", stats["images"], "total"),
            ("Types", len(stats["by_type"]), ", ".join(stats["by_type"]) or "-"),
        ]
    )

    kinds = sorted({build["product_type"] for build in builds})
    col_a, col_b = st.columns([1, 2])
    kind = col_a.selectbox("Product type", ["all", *kinds])
    visible = [b for b in builds if kind == "all" or b["product_type"] == kind]

    labels = {
        f"{b['title']}  \u00b7  {b['product_type']}  \u00b7  {b['created_at'][:16].replace('T', ' ')}": b
        for b in visible
    }
    preselect = st.session_state.pop("library_pick", None)
    keys = list(labels)
    index = 0
    if preselect:
        for position, build in enumerate(labels.values()):
            if build["run_dir"] == preselect:
                index = position
                break
    chosen_label = col_b.selectbox("Build", keys, index=index if keys else 0)
    build = labels.get(chosen_label)
    if not build:
        return

    run_dir = Path(build["run_dir"])
    manifest = load_manifest(run_dir)
    st.divider()
    if not manifest:
        theme.note(
            f"The files for this build are gone from disk ({run_dir}). On Railway the filesystem "
            "is wiped on every redeploy - download products right after building.",
            "warn",
        )
        if st.button("Remove from library"):
            db.delete_build(user["id"], build["id"])
            st.rerun()
        return

    theme.section(manifest.get("title", build["title"]), build["product_type"])
    show(manifest, run_dir, compact=True)

    with st.expander("Danger zone"):
        if st.button("Remove this build from my library"):
            db.delete_build(user["id"], build["id"])
            st.rerun()

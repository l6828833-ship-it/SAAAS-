"""Library: every product this account has forged, as a feed of posts.

Each build is a card: preview image, title, the numbers that matter, and a
download button on the card itself. Getting the buyer ZIP should not require
opening anything - that was the main complaint with the old selectbox view, where
you had to pick a build out of a dropdown before you could see or download it.

Opening a post swaps the feed for the full report (files, listing copy, Etsy
publish, verification) via the shared viewer in `results.show`.
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from artisan_forge.saas import db

from . import theme
from .results import load_manifest, show

# Cards per page. The feed reads the bytes of every ZIP it renders a download
# button for, so the page size is also a memory ceiling.
PAGE_SIZE = 9
COLUMNS = 3

SORTS = {
    "newest": "Newest first",
    "oldest": "Oldest first",
    "title": "Title A-Z",
    "pages": "Most pages",
}

TYPE_ICONS = {"crochet": "\U0001f9f6", "calendar": "\U0001f4c5", "bundle": "\U0001f4e6"}


def _meta_line(build: dict) -> str:
    """The one-line summary under a post title."""
    created = str(build.get("created_at") or "")[:16].replace("T", " ")
    bits = [TYPE_ICONS.get(build["product_type"], "") + " " + build["product_type"]]
    if build.get("pages"):
        bits.append(f"{build['pages']} pages")
    if build.get("images"):
        bits.append(f"{build['images']} images")
    if created:
        bits.append(created)
    return "  \u00b7  ".join(part.strip() for part in bits if part.strip())


def _download_slot(build: dict, manifest: dict | None, run_dir: Path, key: str) -> None:
    """Download buttons that work straight off the card.

    The ZIP is what a buyer receives, so it is the primary action. The PDF is
    offered alongside it because that is what most people actually want to look
    at before they list the product.
    """
    zip_path = build.get("zip_path") or (manifest or {}).get("files", {}).get("zip")
    pdfs = list(((manifest or {}).get("files", {}).get("pdfs") or {}).values())

    columns = st.columns(2)
    if zip_path and Path(zip_path).exists():
        columns[0].download_button(
            "\u2b07 ZIP",
            Path(zip_path).read_bytes(),
            file_name=Path(zip_path).name,
            mime="application/zip",
            key=f"zip_{key}",
            use_container_width=True,
        )
    else:
        columns[0].button("ZIP gone", disabled=True, key=f"nozip_{key}",
                          use_container_width=True)

    pdf = next((Path(p) for p in pdfs if Path(p).exists()), None)
    if pdf:
        columns[1].download_button(
            "\u2b07 PDF",
            pdf.read_bytes(),
            file_name=pdf.name,
            mime="application/pdf",
            key=f"pdf_{key}",
            use_container_width=True,
        )
    else:
        columns[1].button("No PDF", disabled=True, key=f"nopdf_{key}",
                          use_container_width=True)


def _post(build: dict, key: str) -> None:
    """One product post."""
    run_dir = Path(build["run_dir"])
    manifest = load_manifest(run_dir)

    with st.container(border=True):
        thumbnail = build.get("thumbnail")
        if thumbnail and Path(thumbnail).exists():
            st.image(str(thumbnail), use_container_width=True)
        elif manifest is None:
            theme.thumb_missing("Files were wiped from disk")
        else:
            theme.thumb_placeholder("No listing image")

        title = (manifest or {}).get("title") or build.get("title") or run_dir.name
        variant = (manifest or {}).get("variant") or {}
        meta = _meta_line(build)
        if variant.get("total", 1) > 1:
            meta = f"{meta}  \u00b7  {variant['index']} of {variant['total']} in a batch"
        theme.post_head(title, meta, (build.get("brief") or "")[:150])

        if manifest is None:
            st.caption(
                "On Railway the filesystem is wiped on every redeploy, so download "
                "products soon after building."
            )
            if st.button("Remove from library", key=f"drop_{key}", use_container_width=True):
                db.delete_build(st.session_state["user"]["id"], build["id"])
                st.rerun()
            return

        _download_slot(build, manifest, run_dir, key)
        if st.button("Open post", key=f"open_{key}", use_container_width=True):
            st.session_state["library_open"] = str(run_dir)
            st.rerun()


def _detail(user: dict, run_dir: str) -> None:
    """The full report for one build, with a way back to the feed."""
    path = Path(run_dir)
    manifest = load_manifest(path)

    if st.button("\u2190 Back to all products"):
        st.session_state.pop("library_open", None)
        st.rerun()

    if not manifest:
        theme.note(
            f"The files for this build are gone from disk ({path}). On Railway the "
            "filesystem is wiped on every redeploy - download products right after "
            "building.",
            "warn",
        )
        return

    build = next(
        (b for b in db.list_builds(user["id"], limit=500) if b["run_dir"] == str(path)),
        None,
    )
    theme.section(manifest.get("title", path.name), manifest.get("mode_label", ""))

    variant = manifest.get("variant") or {}
    if variant.get("total", 1) > 1:
        theme.note(
            f"Pattern {variant['index']} of {variant['total']} from one batch. "
            f"Design direction: {variant.get('direction', '')}",
            "info",
        )
    design = manifest.get("design") or {}
    if design.get("summary"):
        st.caption(f"**Design:** {design['summary']}")

    show(manifest, path)

    if build:
        with st.expander("Danger zone"):
            if st.button("Remove this build from my library"):
                db.delete_build(user["id"], build["id"])
                st.session_state.pop("library_open", None)
                st.rerun()


def render(user: dict) -> None:
    theme.hero(
        "Your", "library",
        "Every product you have forged. Download the files straight from a card, or open "
        "a post for the full report and listing copy.",
        kicker="library",
    )
    st.write("")

    # Keep the user handy for the delete button inside a card.
    st.session_state["user"] = user

    opened = st.session_state.get("library_open")
    if opened:
        _detail(user, opened)
        return

    builds = db.list_builds(user["id"], limit=200)
    if not builds:
        theme.note(
            "No builds yet. Forge something in Crochet Studio, Calendar Studio or "
            "Bundle Studio.",
            "info",
        )
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
    st.write("")

    # ---- filters ----------------------------------------------------------
    kinds = sorted({build["product_type"] for build in builds})
    col_a, col_b, col_c = st.columns([1, 1, 2])
    kind = col_a.selectbox("Product type", ["all", *kinds], key="library_kind")
    sort = col_b.selectbox(
        "Sort", list(SORTS), format_func=lambda k: SORTS[k], key="library_sort"
    )
    query = col_c.text_input(
        "Search", key="library_search", placeholder="Search titles and briefs"
    ).strip().lower()

    visible = [b for b in builds if kind == "all" or b["product_type"] == kind]
    if query:
        visible = [
            b for b in visible
            if query in str(b.get("title", "")).lower()
            or query in str(b.get("brief", "")).lower()
        ]

    if sort == "oldest":
        visible = list(reversed(visible))
    elif sort == "title":
        visible.sort(key=lambda b: str(b.get("title", "")).lower())
    elif sort == "pages":
        visible.sort(key=lambda b: int(b.get("pages") or 0), reverse=True)

    if not visible:
        theme.note("Nothing matches those filters.", "info")
        return

    # ---- pagination -------------------------------------------------------
    pages = (len(visible) + PAGE_SIZE - 1) // PAGE_SIZE
    page = 1
    if pages > 1:
        page = st.select_slider(
            f"Page (showing {PAGE_SIZE} of {len(visible)} products)",
            options=list(range(1, pages + 1)),
            key="library_page",
        )
    window = visible[(page - 1) * PAGE_SIZE : page * PAGE_SIZE]

    # ---- the feed ---------------------------------------------------------
    for row_start in range(0, len(window), COLUMNS):
        row = window[row_start : row_start + COLUMNS]
        for column, build in zip(st.columns(COLUMNS), row):
            with column:
                _post(build, key=str(build["id"]))
        st.write("")

"""Shared result viewer: one manifest in, full product report out."""

from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

from . import theme


def load_manifest(run_dir: str | Path) -> dict | None:
    path = Path(run_dir) / "manifest.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def show(manifest: dict, run_dir: str | Path, compact: bool = False) -> None:
    run_dir = Path(run_dir)
    files = manifest.get("files", {})
    verification = manifest.get("verification", {})
    images = [Path(p) for p in files.get("listing_images", [])]
    images = [p for p in images if p.exists()]

    theme.stats_row(
        [
            ("Pages", manifest.get("pages", "-"), manifest.get("product_type", "")),
            ("Listing images", len(images), "2000 x 2000 px"),
            (
                "Dates verified" if manifest.get("product_type") == "calendar" else "Checks",
                "yes" if verification.get("ok") else "check",
                f"{verification.get('checks', 0)} checks",
            ),
            ("Build time", f"{manifest.get('duration_seconds', 0):.1f}s", manifest.get("art_source", "")),
        ]
    )

    for warning in manifest.get("warnings", []):
        theme.note(warning, "warn")

    tabs = st.tabs(["Mockups", "Files", "Etsy listing", "Publish to Etsy", "Report"])

    with tabs[0]:
        if images:
            for row_start in range(0, len(images), 3):
                for column, path in zip(st.columns(3), images[row_start : row_start + 3]):
                    column.image(str(path), caption=path.stem, use_container_width=True)
        elif manifest.get("mockups_enabled") is False:
            theme.note(
                "Listing images were turned off for this run. Set the listing images "
                "slider above 0 to generate them.",
                "info",
            )
        else:
            theme.note("No mockups were produced for this run.", "warn")

    with tabs[1]:
        columns = st.columns(3)
        slot = 0
        for key, path in (files.get("pdfs") or {}).items():
            file_path = Path(path)
            if file_path.exists():
                columns[slot % 3].download_button(
                    f"PDF \u00b7 {key}",
                    file_path.read_bytes(),
                    file_name=file_path.name,
                    mime="application/pdf",
                    use_container_width=True,
                )
                slot += 1
        zip_path = files.get("zip")
        if zip_path and Path(zip_path).exists():
            columns[slot % 3].download_button(
                "Buyer ZIP",
                Path(zip_path).read_bytes(),
                file_name=Path(zip_path).name,
                mime="application/zip",
                type="primary",
                use_container_width=True,
            )
        st.caption(f"Run folder: {run_dir}")
        if not compact:
            listing = [p.name for p in sorted(run_dir.rglob("*")) if p.is_file()]
            st.code("\n".join(listing), language="text")

    with tabs[2]:
        listing = manifest.get("listing", {})
        st.text_input("Title", listing.get("title", ""), key=f"title_{run_dir.name}")
        st.text_area("Tags", ", ".join(listing.get("tags", [])), height=68, key=f"tags_{run_dir.name}")
        st.text_area(
            "Description", listing.get("description", ""), height=340, key=f"desc_{run_dir.name}"
        )
        st.caption(f"Suggested price: ${listing.get('suggested_price_usd', 0):.2f}")

    with tabs[3]:
        user = st.session_state.get("user")
        if user:
            from . import etsy_panel

            etsy_panel.publish_panel(user, manifest, run_dir)
        else:
            st.info("Sign in to publish.")

    with tabs[4]:
        st.write(
            f"Artwork: `{manifest.get('art_source')}` \u00b7 "
            f"content: `{manifest.get('content_source', 'n/a')}`"
        )
        st.json({"spec": manifest.get("spec", {}), "verification": verification}, expanded=False)
        with st.expander("Generation prompts / plan"):
            st.json(manifest.get("art_prompts") or manifest.get("plan") or {}, expanded=False)


def run_with_progress(builder, label: str = "Forging your product\u2026"):
    """Run a build callable(progress) with a progress bar and status line."""
    progress = st.progress(0.0, text="Starting\u2026")
    status = st.empty()

    def report(message: str, fraction: float) -> None:
        progress.progress(min(max(fraction, 0.0), 1.0), text=message)
        status.caption(message)

    with st.spinner(label):
        try:
            result = builder(report)
        except Exception as exc:  # noqa: BLE001 - surface build errors in the UI
            progress.empty()
            status.empty()
            st.error(f"Build failed \u2014 {type(exc).__name__}: {exc}")
            return None
    progress.progress(1.0, text="Done")
    status.empty()
    return result


def remember(user: dict, result) -> None:
    """Record a finished build in the user's library."""
    from artisan_forge.saas import db

    manifest = load_manifest(result.run_dir) or {}
    thumbnail = result.listing_images[0] if result.listing_images else None
    db.record_build(
        user_id=user["id"],
        product_type=result.product_type,
        title=manifest.get("title") or Path(result.run_dir).name,
        run_dir=result.run_dir,
        brief=manifest.get("brief"),
        thumbnail=thumbnail,
        pages=int(manifest.get("pages") or 0),
        images=len(result.listing_images),
        zip_path=result.zip_path,
        art_source=result.art_source,
    )
    st.session_state["last_run"] = str(result.run_dir)

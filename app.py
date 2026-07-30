"""Artisan Forge - Streamlit dashboard.

    streamlit run app.py
"""

from __future__ import annotations

import hmac
import json
import os
from pathlib import Path

import streamlit as st

from artisan_forge import __version__
from artisan_forge.brief import parse_brief
from artisan_forge.config import get_settings
from artisan_forge.models import PAPER_SIZES
from artisan_forge.pipeline import build_product
from artisan_forge.themes import theme_labels

EXAMPLE = "2026 minimalist calendar with watercolor floral theme, 8.5x11, Sunday start"

st.set_page_config(page_title="Artisan Forge", page_icon="\U0001f528", layout="wide")


def password_gate() -> bool:
    """Optional shared-password gate.

    Set AF_APP_PASSWORD when the app is reachable from the internet - builds
    can spend OpenAI credits, so a public deployment should not be open.
    """
    expected = os.getenv("AF_APP_PASSWORD")
    if not expected:
        return True
    if st.session_state.get("_authenticated"):
        return True
    st.title("\U0001f528 Artisan Forge")
    with st.form("login"):
        entered = st.text_input("Password", type="password")
        if st.form_submit_button("Enter"):
            if hmac.compare_digest(entered, expected):
                st.session_state["_authenticated"] = True
                st.rerun()
            else:
                st.error("Incorrect password")
    return False


if not password_gate():
    st.stop()

settings = get_settings()
themes = theme_labels()


# --------------------------------------------------------------------- sidebar
with st.sidebar:
    st.title("\U0001f528 Artisan Forge")
    st.caption(f"v{__version__} \u00b7 automation engine for digital product creators")

    st.subheader("Engine status")
    if settings.ai_available:
        st.success(f"AI art: {settings.image_model}")
    else:
        reason = "AF_OFFLINE=1" if settings.force_offline else "no OPENAI_API_KEY"
        st.info(f"AI art off ({reason}) \u2014 using procedural art")
    st.write("Canva:", "connected" if settings.canva_available else "not configured")
    if not os.getenv("AF_APP_PASSWORD"):
        st.warning("No AF_APP_PASSWORD set - anyone who can reach this URL can run builds.")

    st.subheader("Overrides")
    theme_key = st.selectbox(
        "Theme", ["(from brief)"] + list(themes), format_func=lambda k: themes.get(k, k)
    )
    col_a, col_b = st.columns(2)
    year = col_a.number_input("Year", min_value=1900, max_value=2200, value=2026, step=1)
    paper = col_b.selectbox("Paper", ["(from brief)"] + sorted(PAPER_SIZES))
    orientation = col_a.selectbox("Orientation", ["(from brief)", "portrait", "landscape"])
    start_day = col_b.selectbox("Week starts", ["(from brief)", "Sunday", "Monday"])
    holidays = col_a.selectbox("Holidays", ["(from brief)", "US", "UK", "none"])
    month_art = col_b.selectbox("Interior art", ["seasonal (4 images)", "unique (12)", "single (1)"])

    notes = st.checkbox("Notes column")
    moon = st.checkbox("Moon phases")
    no_art = st.checkbox("No artwork panels")
    canva = st.checkbox("Create editable Canva design", value=False)
    images = st.slider("Listing images", 1, 10, 10)


# ------------------------------------------------------------------ main form
st.header("Describe the product")
brief = st.text_input("Product brief", value=EXAMPLE, placeholder=EXAMPLE)
run = st.button("Forge it", type="primary", use_container_width=True)

overrides: dict = {
    "year": int(year),
    "listing_image_count": int(images),
    "canva_export": bool(canva),
    "month_art_mode": month_art.split(" ")[0],
}
if theme_key != "(from brief)":
    overrides["theme"] = theme_key
if paper != "(from brief)":
    overrides["paper"] = paper
if orientation != "(from brief)":
    overrides["orientation"] = orientation
if start_day != "(from brief)":
    overrides["start_day"] = start_day
if holidays != "(from brief)":
    overrides["holidays"] = None if holidays == "none" else holidays
if notes:
    overrides["include_notes_column"] = True
if moon:
    overrides["include_moon_phases"] = True
if no_art:
    overrides["include_month_art"] = False

with st.expander("Parsed spec (before build)"):
    try:
        st.json(parse_brief(brief or EXAMPLE, **overrides).to_dict())
    except Exception as exc:  # noqa: BLE001
        st.error(f"{type(exc).__name__}: {exc}")

if run:
    spec = parse_brief(brief or EXAMPLE, **overrides)
    progress = st.progress(0.0, text="Starting\u2026")

    def report(message: str, fraction: float) -> None:
        progress.progress(min(max(fraction, 0.0), 1.0), text=message)

    with st.spinner("Forging your product\u2026"):
        try:
            result = build_product(spec, progress=report, settings=settings)
            st.session_state["result_dir"] = str(result.run_dir)
        except Exception as exc:  # noqa: BLE001
            st.error(f"Build failed - {type(exc).__name__}: {exc}")
            st.stop()
    progress.progress(1.0, text="Done")


# -------------------------------------------------------------------- results
def show_run(run_dir: Path) -> None:
    manifest_path = run_dir / "manifest.json"
    if not manifest_path.exists():
        st.warning(f"No manifest in {run_dir}")
        return
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    spec = manifest["spec"]
    verification = manifest.get("verification", {})
    cols = st.columns(4)
    cols[0].metric("Pages", manifest.get("pages"))
    cols[1].metric("Listing images", len(manifest["files"]["listing_images"]))
    cols[2].metric("Dates verified", "yes" if verification.get("ok") else "check")
    cols[3].metric("Build time", f"{manifest.get('duration_seconds', 0):.1f}s")

    for warning in manifest.get("warnings", []):
        st.warning(warning)

    tab_preview, tab_files, tab_copy, tab_report = st.tabs(
        ["Mockups", "Files", "Etsy listing", "Report"]
    )

    with tab_preview:
        images = [Path(p) for p in manifest["files"]["listing_images"]]
        images = [p for p in images if p.exists()]
        if images:
            for row_start in range(0, len(images), 3):
                for column, path in zip(st.columns(3), images[row_start : row_start + 3]):
                    column.image(str(path), caption=path.stem, use_container_width=True)
        else:
            st.info("No mockups were produced for this run.")

    with tab_files:
        for key, path in manifest["files"]["pdfs"].items():
            file_path = Path(path)
            if file_path.exists():
                st.download_button(
                    f"Download PDF ({key})",
                    file_path.read_bytes(),
                    file_name=file_path.name,
                    mime="application/pdf",
                )
        zip_path = manifest["files"].get("zip")
        if zip_path and Path(zip_path).exists():
            st.download_button(
                "Download buyer ZIP",
                Path(zip_path).read_bytes(),
                file_name=Path(zip_path).name,
                mime="application/zip",
            )
        st.caption(f"Run folder: {run_dir}")
        st.code("\n".join(sorted(p.name for p in run_dir.rglob("*") if p.is_file())), language="text")

    with tab_copy:
        listing = manifest.get("listing", {})
        st.text_input("Title", listing.get("title", ""))
        st.text_area("Tags (13)", ", ".join(listing.get("tags", [])), height=70)
        st.text_area("Description", listing.get("description", ""), height=380)
        st.caption(f"Suggested price: ${listing.get('suggested_price_usd', 0):.2f}")

    with tab_report:
        st.write(f"Artwork source: `{manifest.get('art_source')}`")
        st.json({"spec": spec, "verification": verification, "canva": manifest.get("canva")})
        with st.expander("Art prompts"):
            st.json(manifest.get("art_prompts", {}))


st.divider()
current = st.session_state.get("result_dir")
runs = sorted(
    (p for p in settings.resolved_output_dir().iterdir() if p.is_dir() and (p / "manifest.json").exists()),
    reverse=True,
)
if runs:
    labels = [p.name for p in runs]
    default = labels.index(Path(current).name) if current and Path(current).name in labels else 0
    chosen = st.selectbox("Run", labels, index=default)
    show_run(settings.resolved_output_dir() / chosen)
else:
    st.info("No builds yet. Describe a product above and press **Forge it**.")

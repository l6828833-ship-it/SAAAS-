"""Calendar Studio: one brief, one button, the whole product."""

from __future__ import annotations

import streamlit as st

from artisan_forge.brief import parse_brief
from artisan_forge.config import get_settings
from artisan_forge.models import PAPER_SIZES
from artisan_forge.pipeline import build_product
from artisan_forge.saas import auth
from artisan_forge.themes import theme_labels

from . import theme
from .results import load_manifest, remember, run_with_progress, show

EXAMPLES = [
    "2026 minimalist calendar with watercolor floral theme, 8.5x11, Sunday start",
    "2027 boho terracotta calendar, 11x14 landscape, monday start, with notes",
    "dark luxe 12x12 square calendar for 2027 with moon phases",
    "botanical A4 calendar 2026, monday start, no holidays",
]


def render(user: dict) -> None:
    settings = get_settings()
    themes = theme_labels()

    theme.hero(
        "Calendar",
        "Studio",
        "One line in, a complete product out: verified 12-month PDF in two paper sizes, "
        "ten listing images, Etsy copy and a buyer ZIP.",
        kicker="\U0001f4c5 live",
    )
    st.write("")

    brief = st.text_input(
        "Describe the calendar",
        value=st.session_state.get("cal_brief", EXAMPLES[0]),
        key="cal_brief_input",
        placeholder=EXAMPLES[0],
    )

    columns = st.columns(len(EXAMPLES))
    for column, example in zip(columns, EXAMPLES):
        short = example.split(",")[0][:26]
        if column.button(short, key=f"ex_{short}", use_container_width=True):
            st.session_state["cal_brief"] = example
            st.rerun()

    with st.expander("Advanced options"):
        col_a, col_b, col_c = st.columns(3)
        theme_key = col_a.selectbox(
            "Theme", ["(from brief)"] + list(themes), format_func=lambda k: themes.get(k, k)
        )
        paper = col_b.selectbox("Paper", ["(from brief)"] + sorted(PAPER_SIZES))
        orientation = col_c.selectbox("Orientation", ["(from brief)", "portrait", "landscape"])
        start_day = col_a.selectbox("Week starts", ["(from brief)", "Sunday", "Monday"])
        holidays = col_b.selectbox("Holidays", ["(from brief)", "US", "UK", "none"])
        art_mode = col_c.selectbox(
            "Interior art", ["seasonal (4 images)", "unique (12 images)", "single (1 image)"]
        )
        col_d, col_e, col_f = st.columns(3)
        notes = col_d.checkbox("Notes column")
        moon = col_d.checkbox("Moon phases")
        no_art = col_e.checkbox("No artwork panels")
        canva = col_e.checkbox("Editable Canva design")
        images = col_f.slider("Listing images", 1, 10, 10)
        bleed = col_f.select_slider("Bleed (in)", options=[0.0, 0.125, 0.25], value=0.0)

    overrides: dict = {
        "listing_image_count": int(images),
        "canva_export": bool(canva),
        "month_art_mode": art_mode.split(" ")[0],
        "bleed_in": float(bleed),
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

    try:
        spec = parse_brief(brief or EXAMPLES[0], **overrides)
        parse_error = None
    except Exception as exc:  # noqa: BLE001
        spec, parse_error = None, exc

    if parse_error:
        st.error(f"{type(parse_error).__name__}: {parse_error}")
        return

    st.markdown(
        f"<div style='color:#9A9AAE;font-size:.88rem;margin:.4rem 0 .8rem'>"
        f"Will build <b style='color:#ECECF1'>{spec.year} {themes.get(spec.theme, spec.theme)}</b> "
        f"\u00b7 {spec.size_label} {spec.orientation} \u00b7 {spec.start_day} start \u00b7 "
        f"{'US/UK holidays' if spec.holidays else 'no holidays'} \u00b7 "
        f"{int(images)} listing images</div>",
        unsafe_allow_html=True,
    )

    allowed, reason = auth.can_build(user)
    if not allowed:
        theme.note(reason, "warn")
        return

    if not settings.ai_available:
        theme.note(
            f"No {settings.key_env_var} configured - artwork will be painted locally "
            "(procedural mode).",
            "info",
        )

    if st.button("\u26a1 Generate everything", type="primary", use_container_width=True):
        result = run_with_progress(
            lambda report: build_product(spec, progress=report, settings=settings)
        )
        if result:
            remember(user, result)
            st.success(
                f"Done in {load_manifest(result.run_dir).get('duration_seconds', 0):.1f}s \u2014 "
                f"{len(result.listing_images)} listing images, "
                f"{len(result.pdf_paths)} PDF file(s)."
            )

    run_dir = st.session_state.get("last_run")
    if run_dir:
        manifest = load_manifest(run_dir)
        if manifest and manifest.get("product_type") == "calendar":
            st.divider()
            theme.section("Latest build", manifest.get("title", ""))
            show(manifest, run_dir)

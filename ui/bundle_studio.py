"""Bundle Studio: ChatGPT writes the pages, the engine lays them out."""

from __future__ import annotations

import streamlit as st

from artisan_forge.ai.text_client import model_chain
from artisan_forge.config import get_settings
from artisan_forge.models import PAPER_SIZES
from artisan_forge.products.bundle import MODULES, BundleSpec, build_bundle
from artisan_forge.saas import auth
from artisan_forge.themes import theme_labels

from . import theme
from .results import load_manifest, remember, run_with_progress, show

IDEAS = [
    ("self-care for new mums", "new mothers in the first year"),
    ("ADHD-friendly meal planning", "busy adults with ADHD"),
    ("first 90 days of a small business", "new solo founders"),
    ("gentle morning routines", "anyone rebuilding a routine"),
]


def render(user: dict) -> None:
    settings = get_settings()
    themes = theme_labels()

    theme.hero(
        "Bundle",
        "Studio",
        "Give it a topic. ChatGPT writes the prompts, checklists, trackers and affirmations, "
        "then the engine lays them out as a printable multi-page bundle.",
        kicker="\u2728 live",
    )
    st.write("")

    if settings.ai_available:
        theme.note(
            f"{settings.provider_label} content is on. Model chain: "
            f"{', '.join(model_chain(settings)[:3])} (first that answers wins).",
            "ok",
        )
    else:
        theme.note(
            f"No {settings.key_env_var} configured - content comes from built-in templates and "
            "artwork is painted locally. Add a key to have the model write the pages.",
            "info",
        )

    topic = st.text_input(
        "What is the bundle about?",
        value=st.session_state.get("bundle_topic", IDEAS[0][0]),
        placeholder="e.g. self-care for new mums",
    )
    columns = st.columns(len(IDEAS))
    for column, (idea, audience) in zip(columns, IDEAS):
        if column.button(idea[:24], key=f"idea_{idea[:12]}", use_container_width=True):
            st.session_state["bundle_topic"] = idea
            st.session_state["bundle_audience"] = audience
            st.rerun()

    col_a, col_b = st.columns(2)
    audience = col_a.text_input(
        "Who is it for?", value=st.session_state.get("bundle_audience", IDEAS[0][1])
    )
    tone = col_b.text_input("Tone", value="warm, practical, encouraging")

    modules = st.multiselect(
        "Sections to include",
        options=list(MODULES),
        default=["prompts", "checklist", "tracker", "affirmations", "notes"],
        format_func=lambda key: MODULES[key],
    )

    with st.expander("Advanced options"):
        col_c, col_d, col_e = st.columns(3)
        theme_key = col_c.selectbox(
            "Theme", list(themes), index=list(themes).index("minimalist"),
            format_func=lambda k: themes.get(k, k),
        )
        paper = col_d.selectbox("Paper", sorted(PAPER_SIZES), index=sorted(PAPER_SIZES).index("letter"))
        orientation = col_e.selectbox("Orientation", ["portrait", "landscape"])
        pages_per_module = col_c.slider("Pages per section", 1, 6, 2)
        images = col_d.slider("Listing images", 1, 8, 8)
        title = col_e.text_input("Custom title (optional)")

    allowed, reason = auth.can_build(user)
    if not allowed:
        theme.note(reason, "warn")
        return

    spec = BundleSpec(
        topic=topic,
        audience=audience or "anyone starting out",
        tone=tone or "warm, practical",
        theme=theme_key,
        paper=paper,
        orientation=orientation,
        modules=modules or ["prompts", "checklist", "notes"],
        pages_per_module=int(pages_per_module),
        title=title or None,
        listing_image_count=int(images),
    )

    estimate = 2 + len(spec.modules) * spec.pages_per_module
    st.markdown(
        f"<div style='color:#9A9AAE;font-size:.88rem;margin:.4rem 0 .8rem'>"
        f"About <b style='color:#ECECF1'>{estimate}\u2013{estimate + 4} pages</b> \u00b7 "
        f"{spec.size_label} {spec.orientation} \u00b7 {len(spec.modules)} sections \u00b7 "
        f"{int(images)} listing images</div>",
        unsafe_allow_html=True,
    )

    disabled = len(topic.strip()) < 3
    if st.button(
        "\u2728 Generate bundle", type="primary", use_container_width=True, disabled=disabled
    ):
        result = run_with_progress(
            lambda report: build_bundle(spec, progress=report, settings=settings),
            "Writing and laying out your bundle\u2026",
        )
        if result:
            remember(user, result)
            manifest = load_manifest(result.run_dir) or {}
            st.success(
                f"Built {manifest.get('pages', 0)} pages from "
                f"`{manifest.get('content_source', 'template')}` content in "
                f"{manifest.get('duration_seconds', 0):.1f}s."
            )
    if disabled:
        st.caption("Enter a topic of at least 3 characters to enable the button.")

    run_dir = st.session_state.get("last_run")
    if run_dir:
        manifest = load_manifest(run_dir)
        if manifest and manifest.get("product_type") == "bundle":
            st.divider()
            theme.section("Latest bundle", manifest.get("title", ""))
            show(manifest, run_dir)

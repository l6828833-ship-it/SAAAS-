"""Crochet Studio: five ways into one professional pattern PDF."""

from __future__ import annotations

import hashlib
import tempfile
import uuid
from pathlib import Path

import streamlit as st

from artisan_forge.ai.text_client import model_chain
from artisan_forge.config import get_settings
from artisan_forge.crochet import batch as batch_planner
from artisan_forge.crochet import diagrams, etsy_data, market
from artisan_forge.crochet.batch import ALLOCATION_STRATEGIES, DEFAULT_STRATEGY
from artisan_forge.crochet.brand import BrandKit
from artisan_forge.models import PAPER_SIZES
from artisan_forge.products.crochet import (
    COST_MODES,
    DEFAULT_COST_MODE,
    MAX_PATTERNS_PER_RUN,
    MAX_PHOTOS,
    MAX_SOURCE_FILES,
    MAX_PLATES,
    MODE_ORDER,
    MODES,
    PDF_MAX_PAGES,
    PDF_MIN_PAGES,
    CrochetSpec,
    build_crochet_batch,
    short_model,
)
from artisan_forge.saas import auth
from artisan_forge.themes import theme_labels

from . import theme
from .results import load_manifest, remember, run_with_progress, show

PATTERN_TYPES = ["", "cardigan", "sweater", "top", "blanket", "hat", "bag", "scarf",
                 "amigurumi", "socks", "mittens", "dress", "vest", "coaster"]
SIZE_PRESETS = {
    "Adult XS-2XL": ["XS", "S", "M", "L", "XL", "2XL"],
    "Adult XS-5XL": ["XS", "S", "M", "L", "XL", "2XL", "3XL", "4XL", "5XL"],
    "Adult S-XL": ["S", "M", "L", "XL"],
    "One size": [],
}
EXAMPLE_BRIEFS = [
    ("oversized ribbed cardigan in worsted cotton", "cardigan"),
    ("chunky waffle stitch throw blanket", "blanket"),
    ("summer mesh crop top in dk cotton", "top"),
    ("ribbed beanie with a folded brim", "hat"),
]


def _upload_dir() -> Path:
    """A stable per-session folder for uploaded files."""
    if "crochet_upload_id" not in st.session_state:
        st.session_state["crochet_upload_id"] = uuid.uuid4().hex[:12]
    path = Path(tempfile.gettempdir()) / "artisan-forge-uploads" / st.session_state["crochet_upload_id"]
    path.mkdir(parents=True, exist_ok=True)
    return path


def _save_uploads(files, subdir: str) -> list[str]:
    """Persist Streamlit uploads to disk and return their paths.

    Files are written under a content hash so a rerun does not duplicate them
    and an unchanged upload is not rewritten on every interaction.
    """
    if not files:
        return []
    target = _upload_dir() / subdir
    target.mkdir(parents=True, exist_ok=True)
    saved: list[str] = []
    for item in files:
        try:
            data = item.getvalue()
        except Exception:  # noqa: BLE001 - skip anything unreadable
            continue
        digest = hashlib.sha1(data).hexdigest()[:10]
        suffix = Path(item.name).suffix.lower() or ".bin"
        path = target / f"{digest}-{Path(item.name).stem[:40]}{suffix}"
        if not path.exists():
            path.write_bytes(data)
        saved.append(str(path))
    return saved


def _brand_inputs() -> BrandKit:
    """Shop branding, applied to every page of the pattern."""
    with st.expander("Your brand \u2014 shown on the cover, footer and thank-you page", expanded=False):
        col_a, col_b = st.columns(2)
        store = col_a.text_input("Shop name", key="crochet_store", placeholder="Loop & Thread Co")
        designer = col_b.text_input("Designer name", key="crochet_designer", placeholder="Marie Dupont")
        email = col_a.text_input("Support email", key="crochet_email", placeholder="hello@yourshop.com")
        website = col_b.text_input("Website", key="crochet_site", placeholder="yourshop.com")
        instagram = col_a.text_input("Instagram handle", key="crochet_ig", placeholder="@yourshop")
        ravelry = col_b.text_input("Ravelry name", key="crochet_ravelry", placeholder="yourshop")
        tagline = st.text_input(
            "Tagline", key="crochet_tagline",
            placeholder="Slow, wearable crochet for everyday.",
        )
        support = st.text_area(
            "Support note", key="crochet_support", height=68,
            placeholder="Stuck on a row? Email me and I will answer within two days.",
        )
        col_c, col_d, col_e = st.columns([1, 1, 2])
        accent = col_c.color_picker("Accent colour", value="#7C5CFF", key="crochet_accent")
        licence = col_d.selectbox(
            "Licence", ["personal", "small_business"],
            format_func=lambda k: "Personal use only" if k == "personal" else "Small business (may sell finished items)",
            key="crochet_licence",
        )
        logo_file = col_e.file_uploader(
            "Logo (optional)", type=["png", "jpg", "jpeg"], key="crochet_logo",
            accept_multiple_files=False,
        )
        logo_paths = _save_uploads([logo_file] if logo_file else [], "logo")

    return BrandKit(
        store_name=store,
        designer_name=designer,
        email=email,
        website=website,
        instagram=instagram,
        ravelry=ravelry,
        tagline=tagline,
        support_note=support,
        accent_hex=accent,
        licence=licence,
        logo_path=logo_paths[0] if logo_paths else None,
    )


def _mode_inputs(mode: str) -> dict:
    """Render the inputs for one mode and return the spec fragment."""
    fragment: dict = {}

    if mode == "from_pdfs":
        theme.note(
            f"Upload up to {MAX_SOURCE_FILES} crochet pattern PDFs you already own. Every row, "
            "stitch count, gauge and abbreviation is parsed out of them, then Qwen writes "
            "complete, graded patterns from what they contain. Ask for more patterns than you "
            "upload and each one is given its own design direction.",
            "info",
        )
        uploads = st.file_uploader(
            f"Pattern files (PDF or TXT, up to {MAX_SOURCE_FILES})",
            type=["pdf", "txt"], accept_multiple_files=True, key="crochet_pdfs",
        )
        paths = _save_uploads(uploads, "patterns")[:MAX_SOURCE_FILES]
        fragment["source_files"] = paths
        if uploads and len(uploads) > MAX_SOURCE_FILES:
            theme.note(f"Only the first {MAX_SOURCE_FILES} files will be used.", "warn")
        if paths:
            _preview_sources(paths)
        fragment["brief"] = st.text_input(
            "Anything to add? (optional)",
            placeholder="Make it oversized with a deeper armhole",
            key="crochet_pdf_note",
        )
        fragment.update(_batch_controls(paths, "pdfs", mode))

    elif mode == "from_etsy_data":
        theme.note(
            "Paste everything you have about your Etsy products \u2014 a shop CSV export, a JSON "
            "dump from the Etsy API, or just the listing text. Then type the number of the "
            "product you want a pattern for.",
            "info",
        )
        data_text = st.text_area(
            "Etsy product data (CSV, JSON, or one product per block)",
            height=180, key="crochet_etsy_text",
            placeholder=(
                "Title,Description,Price,Tags,Materials,SKU\n"
                "Chunky Cropped Cardigan,Oversized cardigan in worsted cotton,42.00,"
                '"cardigan,crochet","cotton",CARD-01'
            ),
        )
        col_a, col_b = st.columns(2)
        data_files = col_a.file_uploader(
            "or upload a CSV / JSON export", type=["csv", "json", "tsv", "txt"],
            accept_multiple_files=True, key="crochet_etsy_files",
        )
        photo_files = col_b.file_uploader(
            "Product photos (optional \u2014 the vision model reads them)",
            type=["png", "jpg", "jpeg", "webp"], accept_multiple_files=True,
            key="crochet_etsy_photos",
        )
        data_paths = _save_uploads(data_files, "etsy-data")
        photo_paths = _save_uploads(photo_files, "etsy-photos")

        fragment["etsy_data_text"] = data_text
        fragment["etsy_data_files"] = data_paths
        fragment["image_dir"] = str(_upload_dir() / "etsy-photos") if photo_paths else None

        products, warnings = etsy_data.load_products(data_text, data_paths)
        for warning in warnings:
            theme.note(warning, "warn")

        if products:
            st.caption(f"Found **{len(products)}** products. Pick one by number:")
            st.dataframe(
                [
                    {
                        "#": p.number,
                        "Title": p.title[:60],
                        "Price": f"{p.currency or ''}{p.price}".strip() or "-",
                        "Type": p.garment() or "-",
                        "Yarn": p.yarn_weight() or "-",
                        "Photos": len(p.local_images()),
                    }
                    for p in products[:60]
                ],
                use_container_width=True, hide_index=True, height=min(280, 40 + 35 * len(products)),
            )
            number = st.number_input(
                "Product number to build a pattern for",
                min_value=1, max_value=len(products), value=1, step=1,
                key="crochet_product_number",
            )
            chosen = products[int(number) - 1]
            st.markdown(
                f"<div style='color:#9A9AAE;font-size:.88rem;margin:.2rem 0 .6rem'>"
                f"Building from <b style='color:#ECECF1'>#{chosen.number} {theme.esc(chosen.title)}</b>"
                f"</div>",
                unsafe_allow_html=True,
            )
            fragment["product_number"] = int(number)
        else:
            fragment["product_number"] = 1
            if data_text.strip() or data_paths:
                theme.note(
                    "No products could be read from that data yet. A CSV needs a header row; "
                    "free text needs one product per block with 'Label: value' lines.",
                    "warn",
                )
        fragment.update(_batch_controls([], "etsy", mode))

    elif mode == "from_brief":
        brief = st.text_input(
            "What do you want to make?",
            value=st.session_state.get("crochet_brief", EXAMPLE_BRIEFS[0][0]),
            key="crochet_brief_input",
            placeholder="e.g. oversized ribbed cardigan in worsted cotton",
        )
        columns = st.columns(len(EXAMPLE_BRIEFS))
        for column, (text, garment) in zip(columns, EXAMPLE_BRIEFS):
            if column.button(text[:26], key=f"crochet_ex_{garment}", use_container_width=True):
                st.session_state["crochet_brief"] = text
                st.session_state["crochet_garment"] = garment
                st.rerun()
        fragment["brief"] = brief
        fragment.update(_batch_controls([], "brief", mode))

    elif mode == "from_photos":
        theme.note(
            f"Upload up to {MAX_PHOTOS} photos of the finished piece. Qwen's vision model reads "
            "the stitch pattern, the construction and the approximate gauge off the images, then "
            "writes the pattern that reproduces it. This mode needs an API key.",
            "info",
        )
        uploads = st.file_uploader(
            f"Photos of the finished piece (up to {MAX_PHOTOS})",
            type=["png", "jpg", "jpeg", "webp"], accept_multiple_files=True,
            key="crochet_photos",
        )
        paths = _save_uploads(uploads, "photos")[:MAX_PHOTOS]
        fragment["source_files"] = paths
        if paths:
            for row_start in range(0, len(paths), 4):
                for column, path in zip(st.columns(4), paths[row_start : row_start + 4]):
                    column.image(path, use_container_width=True)
        fragment["brief"] = st.text_input(
            "Anything the photos do not show? (optional)",
            placeholder="Worked top down, seamless, 5 mm hook",
            key="crochet_photo_note",
        )
        fragment.update(_batch_controls(paths, "photos", mode))

    else:  # tech_pack
        theme.note(
            "Diagrams only, and no API calls at all: schematic, stitch chart, foundation row, "
            "seam diagrams, gauge swatch, body measurements and a yardage chart, wrapped in a "
            "branded tech pack. Free to run.",
            "ok",
        )
        fragment["brief"] = st.text_input(
            "What is the tech pack for?",
            value=st.session_state.get("crochet_brief", "chunky waffle stitch throw"),
            key="crochet_tech_brief",
        )
        fragment.update(_batch_controls([], "tech", mode))

    return fragment


@st.cache_data(show_spinner=False)
def _score_uploads(paths: tuple[str, ...]) -> list[dict]:
    """Score uploads for the allocation preview, cached so reruns are cheap.

    Parsing ten PDFs on every widget interaction would make the form crawl, and
    the same files always score the same.
    """
    from artisan_forge.crochet.extract import extract_many

    try:
        return [score.to_dict() for score in batch_planner.score_sources(extract_many(list(paths)))]
    except Exception:  # noqa: BLE001 - the preview is a nicety, not the build
        return []


def _batch_controls(paths: list[str], key: str, mode: str = "") -> dict:
    """How many patterns to build, and how the sources are shared between them.

    Shown in every mode. Without uploads there is nothing to allocate, so only
    the count is asked for - each pattern still gets its own design direction,
    which is what makes five patterns from one brief five different products.
    """
    st.markdown("**How many patterns should this run produce?**")
    col_a, col_b = st.columns([1, 2])
    count = col_a.number_input(
        "Patterns to create",
        min_value=1, max_value=MAX_PATTERNS_PER_RUN,
        value=1, step=1, key=f"crochet_count_{key}",
        help="Each pattern gets its own PDF, mockups, listing copy and ZIP.",
    )

    strategy = DEFAULT_STRATEGY
    per = 0
    if paths:
        options = list(ALLOCATION_STRATEGIES)
        strategy = col_b.selectbox(
            "How should your uploads be used?",
            options,
            index=options.index(DEFAULT_STRATEGY),
            format_func=lambda k: ALLOCATION_STRATEGIES[k],
            key=f"crochet_alloc_{key}",
            help=(
                "Auto reads your uploads and gives the richer ones more patterns. "
                "Rebuild-each hands every pattern all of your files."
            ),
        )
        if strategy == "fixed":
            per = st.number_input(
                "Source files per pattern",
                min_value=1, max_value=len(paths), value=1, step=1,
                key=f"crochet_per_{key}",
            )
    else:
        col_b.caption(
            "No uploads in this mode, so every pattern starts from the same brief - "
            "each one is given a different design direction so they come out as "
            "separate products."
        )

    scores = [
        batch_planner.SourceScore(**row) for row in _score_uploads(tuple(paths))
    ] if paths and strategy == "auto" else []

    plans = batch_planner.fallback_plan(paths, int(count), strategy, scores, int(per))

    if len(plans) > 1 or paths:
        rows = []
        for plan in plans:
            rows.append({
                "#": plan.index,
                "Built from": plan.source_note
                or (", ".join(Path(p).name[:24] for p in plan.sources) or "your brief"),
                "Design direction": plan.direction[:88],
            })
        st.dataframe(
            rows, use_container_width=True, hide_index=True,
            height=min(320, 40 + 35 * len(rows)),
        )

    if scores and len(scores) > 1 and strategy == "auto":
        best = max(scores, key=lambda s: s.score)
        st.caption(
            f"Auto ranked **{best.name}** highest ({best.reason}), so it earns more of the "
            "patterns. With a key, the model reviews this allocation and can change it."
        )
    if int(count) > 1:
        extra = ""
        if mode == "from_etsy_data":
            extra = " Etsy mode walks forward through consecutive product numbers."
        st.caption(
            f"This run will build **{int(count)} separate patterns**, each with its own PDF, "
            f"listing images and Etsy copy.{extra}"
        )
    return {
        "pattern_count": int(count),
        "sources_per_pattern": int(per),
        "allocation": strategy,
    }


def _market_inputs(key: str) -> dict:
    """Upload competitor research so the listing is written from real demand."""
    with st.expander(
        "\U0001f4c8 Market research \u2014 write the title, tags, description and price "
        "from real Etsy data",
        expanded=False,
    ):
        st.caption(
            "Upload or paste an Etsy competitor scrape and the listing is built from it: "
            "tags ranked by real search volume, pricing from what the best sellers charge, "
            "and a title that follows the conventions buyers already click. "
            "Accepts **JSON**, **JSONL**, **CSV/TSV** and **Excel (.xlsx)**."
        )
        files = st.file_uploader(
            "Research files",
            type=["json", "jsonl", "csv", "tsv", "txt", "xlsx", "xlsm"],
            accept_multiple_files=True, key=f"crochet_market_files_{key}",
        )
        text = st.text_area(
            "or paste the data here",
            height=140, key=f"crochet_market_text_{key}",
            placeholder='[{"title": "...", "price": 8.5, "tags": ["crochet cardigan"], '
                        '"tagVolumes": {"crochet cardigan": 9200000}, '
                        '"ehuntEstimatedSales": 1420, "favoritesCount": 1840}]',
        )
        paths = _save_uploads(files, "market")

        with st.expander("What fields does it read?"):
            st.markdown(
                "Everything is optional \u2014 the more you supply, the better the listing.\n\n"
                "| Field | Used for |\n|---|---|\n"
                "| `title` | title conventions in your niche |\n"
                "| `tags` | tag candidates |\n"
                "| `tagVolumes` | ranking tags by monthly search volume |\n"
                "| `price`, `originalPrice`, `ehuntDiscountPercent` | pricing strategy |\n"
                "| `ehuntEstimatedSales`, `favoritesCount`, `reviewCount` | which competitors are winning |\n"
                "| `demandScore`, `opportunityScore` | niche health |\n"
                "| `imageCount` | how many listing images to make |\n\n"
                "Alternative field names (`estimatedSales`, `numFavorers`, `shopName`, "
                "`keywords`\u2026) are matched automatically."
            )

        if text.strip() or paths:
            listings, warnings = market.load_market_data(text, paths)
            for warning in warnings:
                theme.note(warning, "warn")
            if listings:
                report = market.analyse(listings, relevance="")
                cols = st.columns(4)
                cols[0].metric("Listings read", report.listings)
                cols[1].metric(
                    "Median price",
                    f"{report.price_median:g}" if report.price_median else "-",
                    help=f"Currency: {report.currency or 'unknown'}",
                )
                cols[2].metric(
                    "Suggested price",
                    f"{report.suggested_price:g}" if report.suggested_price else "-",
                    help="What the best-performing competitors charge",
                )
                cols[3].metric("Tags found", len([t for t in report.tags if t.etsy_safe]))

                top = [t for t in report.tags if t.etsy_safe][:13]
                if top:
                    st.dataframe(
                        [
                            {
                                "Tag": t.tag,
                                "Search volume": f"{t.volume:,}" if t.volume else "-",
                                "Competitors": t.used_by,
                            }
                            for t in top
                        ],
                        use_container_width=True, hide_index=True,
                        height=min(260, 40 + 34 * len(top)),
                    )
                    st.caption(
                        "Final ranking is re-scored against your actual item type when you "
                        "build, so off-niche tags drop away."
                    )
        return {"market_text": text, "market_files": paths}


def _preview_sources(paths: list[str]) -> None:
    """Show what the parser actually found in the uploads."""
    from artisan_forge.crochet.extract import extract_many, merge_sources

    with st.spinner("Reading your patterns\u2026"):
        sources = extract_many(paths)
    corpus = merge_sources(sources)

    rows = []
    for source in sources:
        rows.append({
            "File": source.name[:36],
            "Pages": source.pages,
            "Type": source.garment or "-",
            "Rows found": len(source.rows),
            "Abbrevs": len(source.abbreviations),
            "Hook": ", ".join(f"{h:g}mm" for h in source.hooks_mm) or "-",
            "Status": "ok" if source.ok else (source.error[:40] or "unreadable"),
        })
    st.dataframe(rows, use_container_width=True, hide_index=True)

    gauge = corpus.get("gauge") or {}
    bits = [f"**{corpus['sources']}** readable file(s)", f"**{corpus['total_rows']}** rows parsed"]
    if gauge.get("stitches"):
        bits.append(f"gauge **{gauge['stitches']:g} sts \u00d7 {gauge.get('rows', 0):g} rows**")
    if corpus.get("yarn_weight"):
        bits.append(f"**{corpus['yarn_weight']}** weight")
    if corpus.get("sizes"):
        bits.append(f"sizes **{', '.join(corpus['sizes'])}**")
    st.caption(" \u00b7 ".join(bits))


def render(user: dict) -> None:
    settings = get_settings()
    themes = theme_labels()

    theme.hero(
        "Crochet",
        "Studio",
        "Upload the patterns you own or your Etsy product data, and get a complete graded "
        "crochet pattern: written instructions with stitch counts, technical diagrams, sizing "
        "tables, and the Etsy listing to sell it with.",
        kicker="\U0001f9f6 live",
    )
    st.write("")

    if settings.ai_available:
        theme.note(
            f"{settings.provider_label} is on. "
            f"{short_model(settings.text_model)} writes the pattern, "
            f"{short_model(settings.image_model)} renders the photography. "
            f"Fallback chain: {', '.join(short_model(m) for m in model_chain(settings)[:3])}.",
            "ok",
        )
    else:
        theme.note(
            f"No {settings.key_env_var} configured \u2014 patterns come from built-in templates "
            "and artwork is painted locally. Everything still builds; add a key to have "
            f"{short_model(settings.text_model)} write the pattern and read your photos.",
            "info",
        )
    if not diagrams.available():
        theme.note(
            "matplotlib is not installed, so the technical diagram pages will be skipped. "
            "Install it with: pip install matplotlib",
            "warn",
        )

    # ---- the five features -------------------------------------------------
    mode = st.selectbox(
        "What do you want to do?",
        MODE_ORDER,
        format_func=lambda key: MODES[key],
        key="crochet_mode",
    )
    st.write("")
    fragment = _mode_inputs(mode)

    # ---- shared pattern options -------------------------------------------
    col_a, col_b, col_c = st.columns(3)
    garment = col_a.selectbox(
        "Item type",
        PATTERN_TYPES,
        index=(
            PATTERN_TYPES.index(st.session_state["crochet_garment"])
            if st.session_state.get("crochet_garment") in PATTERN_TYPES
            else 0
        ),
        format_func=lambda k: k.title() if k else "Detect automatically",
        key="crochet_garment_select",
    )
    size_preset = col_b.selectbox("Sizes to grade", list(SIZE_PRESETS), key="crochet_sizes")
    cost_mode = col_c.selectbox(
        "Cost", list(COST_MODES),
        index=list(COST_MODES).index(DEFAULT_COST_MODE),
        format_func=lambda k: COST_MODES[k],
        key="crochet_cost",
        disabled=mode == "tech_pack",
        help="Images are almost the whole cost of a run. Diagrams are always free.",
    )

    market_fragment = _market_inputs(mode)
    brand = _brand_inputs()

    with st.expander("Advanced options"):
        col_d, col_e, col_f = st.columns(3)
        theme_key = col_d.selectbox(
            "Theme", list(themes), index=list(themes).index("minimalist"),
            format_func=lambda k: themes.get(k, k), key="crochet_theme",
        )
        paper = col_e.selectbox(
            "Paper", sorted(PAPER_SIZES), index=sorted(PAPER_SIZES).index("letter"),
            key="crochet_paper",
        )
        orientation = col_f.selectbox("Orientation", ["portrait", "landscape"], key="crochet_orient")
        title = col_d.text_input("Custom title (optional)", key="crochet_title")
        audience = col_e.text_input(
            "Who is it for?", value="confident hobby crocheters", key="crochet_audience"
        )
        # In custom mode you say how many photographs the document gets, so the
        # listing-gallery slider is replaced rather than shown alongside it -
        # two image counts on one screen is how you end up paying for plates you
        # did not want.
        if cost_mode == "custom":
            custom_images = col_f.number_input(
                "Photographs in the PDF", min_value=0, max_value=MAX_PLATES, value=4, step=1,
                key="crochet_custom_images",
                help=f"AI photographs rendered for this pattern, up to {MAX_PLATES}. "
                     "Technical diagrams are drawn locally and are always free.",
            )
            custom_cover = col_f.checkbox(
                "First one is the cover", value=True, key="crochet_custom_cover",
                help="Off spends the whole budget on interior and gallery shots and "
                     "leaves the cover to the locally painted art.",
            )
            want_gallery = col_d.checkbox(
                "Also build the Etsy listing gallery", value=False,
                key="crochet_custom_gallery",
                help="Composited mockups for the listing. Free, but the slowest part "
                     "of a build.",
            )
            images = 8 if want_gallery else 0
            col_f.caption(
                f"{int(custom_images)} AI photograph(s)"
                + (" including a cover" if custom_cover else ", no AI cover")
                + (" \u00b7 listing gallery on" if images else " \u00b7 listing gallery off")
            )
        else:
            custom_images, custom_cover = 4, True
            images = col_f.slider(
                "Listing images", 0, 10, 8, key="crochet_images",
                help="Etsy mockups composited from the PDF pages and the AI plates. "
                     "0 turns them off. They cost nothing but are the slowest part of a build.",
            )
            if int(images) == 0:
                col_f.caption("Mockups off \u2014 PDF, diagrams and listing copy only.")
        include_chart = col_d.checkbox("Include stitch chart page", value=True, key="crochet_chart")
        include_gallery = col_e.checkbox("Include gallery page", value=True, key="crochet_gallery")
        bleed = col_f.number_input(
            "Bleed (inches)", min_value=0.0, max_value=0.25, value=0.0, step=0.125,
            key="crochet_bleed",
        )
        max_pages = col_d.slider(
            "Page limit", PDF_MIN_PAGES, 60, PDF_MAX_PAGES, key="crochet_max_pages",
            help="A ceiling, not a target. The pattern is as long as the design "
                 "needs - a coaster comes out short, a graded cardigan long. If a "
                 "plan runs over, the least useful pages (gallery, spare "
                 "troubleshooting) are dropped; the instructions never are.",
        )

        st.write("")
        st.markdown(
            "<div style='color:#9A9AAE;font-size:.84rem'>Canva has no text-to-image API, so the "
            "flow is: Qwen writes the photo prompt \u2192 Gemini renders it \u2192 the render is "
            "pushed to Canva as an editable design.</div>",
            unsafe_allow_html=True,
        )
        col_g, col_h = st.columns(2)
        use_canva = col_g.checkbox(
            "Push artwork to Canva as editable designs",
            value=False,
            key="crochet_canva",
            disabled=not settings.canva_available or mode == "tech_pack",
            help=None if settings.canva_available else "Set CANVA_ACCESS_TOKEN in .env first",
        )
        canva_pull_back = col_h.checkbox(
            "and place the Canva version back in the PDF",
            value=False, key="crochet_canva_pull", disabled=not use_canva,
        )
        if not settings.canva_available:
            st.caption("Canva is not connected. Add CANVA_ACCESS_TOKEN to .env to enable it.")

    # ---- build -------------------------------------------------------------
    spec = CrochetSpec(
        mode=mode,
        garment=garment,
        sizes=SIZE_PRESETS[size_preset],
        audience=audience or "confident hobby crocheters",
        title=title or None,
        brand=brand,
        theme=theme_key,
        paper=paper,
        orientation=orientation,
        bleed_in=float(bleed),
        include_chart=include_chart,
        include_gallery=include_gallery,
        max_pages=int(max_pages),
        cost_mode=cost_mode,
        custom_image_count=int(custom_images),
        custom_cover=bool(custom_cover),
        use_canva=bool(use_canva),
        canva_pull_back=bool(canva_pull_back),
        listing_image_count=int(images),
        **market_fragment,
        **fragment,
    )

    ready, reason = _readiness(spec)
    profile = spec.profile
    estimate = spec.estimated_cost_usd() if settings.ai_available else 0.0
    batch_note = (
        f"<b style='color:#ECECF1'>{spec.pattern_count} patterns</b> \u00b7 "
        if spec.pattern_count > 1 else ""
    )
    st.markdown(
        f"<div style='color:#9A9AAE;font-size:.88rem;margin:.4rem 0 .8rem'>"
        f"{batch_note}"
        f"<b style='color:#ECECF1'>{MODES[mode]}</b> \u00b7 {spec.size_label} {spec.orientation} "
        f"\u00b7 {len(spec.sizes) or 1} size(s) \u00b7 "
        + (f"{int(images)} listing images each" if int(images) else "no listing images")
        + "</div>",
        unsafe_allow_html=True,
    )

    if not settings.ai_available:
        st.caption(
            f"No {settings.key_env_var} configured, so this run is free "
            "(templates + local art)."
        )
    elif estimate <= 0:
        st.caption("This run makes no API calls, so it is free.")
    else:
        tone = "ok" if estimate < 0.10 else ("info" if estimate < 0.40 else "warn")
        detail = (
            f"{profile.plates} AI image(s) per pattern on "
            f"`{short_model(settings.image_model_for_tier(profile.image_tier))}`"
            if profile.plates else "no AI images"
        )
        theme.note(
            f"Estimated cost: about ${estimate:.2f} for this run \u2014 {detail}. "
            "Repeat prompts are served from the image cache for free, and every "
            "technical diagram is drawn locally at no cost.",
            tone,
        )

    allowed, quota_reason = auth.can_build(user)
    if not allowed:
        theme.note(quota_reason, "warn")
        return

    plural = "patterns" if spec.pattern_count > 1 else "pattern"
    if st.button(
        f"\U0001f9f6 Generate {spec.pattern_count} {plural}" if spec.pattern_count > 1
        else "\U0001f9f6 Generate pattern",
        type="primary", use_container_width=True, disabled=not ready,
    ):
        results = run_with_progress(
            lambda report: build_crochet_batch(spec, progress=report, settings=settings),
            f"Building your crochet {plural}\u2026",
        )
        if results:
            for result in results:
                remember(user, result)
            # Every pattern in the batch is shown, not just the last one. This is
            # the whole batch as posts; asking for five patterns and seeing one
            # result looked like four of them had silently failed.
            st.session_state["crochet_batch"] = [str(r.run_dir) for r in results]
            st.session_state.pop("crochet_open", None)
            if len(results) == 1:
                manifest = load_manifest(results[0].run_dir) or {}
                st.success(
                    f"Built a {manifest.get('pages', 0)}-page pattern with "
                    f"{manifest.get('steps', 0)} steps from "
                    f"`{manifest.get('content_source', 'template')}` content in "
                    f"{manifest.get('duration_seconds', 0):.1f}s."
                )
            else:
                st.success(
                    f"Built {len(results)} separate patterns \u2014 all of them are "
                    "below and in your Library."
                )
    if not ready:
        st.caption(reason)

    _results_feed()


def _batch_run_dirs() -> list[str]:
    """Which run folders to show: this session's batch, else the last build."""
    batch = [d for d in st.session_state.get("crochet_batch") or [] if Path(d).exists()]
    if batch:
        return batch
    last = st.session_state.get("last_run")
    return [str(last)] if last and Path(str(last)).exists() else []


def _results_feed() -> None:
    """The patterns from this session, as post cards, or one opened in full."""
    run_dirs = _batch_run_dirs()
    if not run_dirs:
        return

    opened = st.session_state.get("crochet_open")
    if opened and opened in run_dirs:
        manifest = load_manifest(opened)
        st.divider()
        if len(run_dirs) > 1 and st.button("\u2190 Back to all patterns"):
            st.session_state.pop("crochet_open", None)
            st.rerun()
        if manifest:
            theme.section(manifest.get("title", ""), manifest.get("mode_label", ""))
            _extras(manifest)
            show(manifest, Path(opened))
        return

    posts = []
    for run_dir in run_dirs:
        manifest = load_manifest(run_dir)
        if manifest and manifest.get("product_type") == "crochet":
            posts.append((run_dir, manifest))
    if not posts:
        return

    st.divider()
    if len(posts) == 1:
        run_dir, manifest = posts[0]
        theme.section("Latest pattern", manifest.get("title", ""))
        _extras(manifest)
        show(manifest, Path(run_dir))
        return

    theme.section(f"Your {len(posts)} new patterns", "Download straight from a card, or open one for the full report")
    for row_start in range(0, len(posts), 3):
        row = posts[row_start : row_start + 3]
        for column, (run_dir, manifest) in zip(st.columns(3), row):
            with column:
                _result_post(run_dir, manifest, key=f"r{row_start}_{run_dir[-12:]}")
        st.write("")


def _result_post(run_dir: str, manifest: dict, key: str) -> None:
    """One pattern as a card: preview, numbers, downloads, open.

    Reuses the Library's download slot so a card behaves the same in both
    places - the ZIP and the PDF come off the card without opening anything.
    """
    from .library import _download_slot  # local import: avoids a cycle at import time

    files = manifest.get("files") or {}
    images = files.get("listing_images") or []
    preview = next((p for p in images if Path(p).exists()), None)
    variant = manifest.get("variant") or {}

    with st.container(border=True):
        if preview:
            st.image(str(preview), use_container_width=True)
        else:
            theme.thumb_placeholder("No listing image")

        meta_bits = [f"{manifest.get('pages', 0)} pages",
                     f"{manifest.get('steps', 0)} steps",
                     f"{len(images)} images"]
        if variant.get("total", 1) > 1:
            meta_bits.append(f"{variant['index']} of {variant['total']}")
        theme.post_head(
            manifest.get("title", "Pattern"),
            "  \u00b7  ".join(meta_bits),
            str(variant.get("direction") or manifest.get("brief") or "")[:150],
        )

        build = {"zip_path": files.get("zip")}
        _download_slot(build, manifest, Path(run_dir), key)
        if st.button("Open pattern", key=f"open_{key}", use_container_width=True):
            st.session_state["crochet_open"] = run_dir
            st.rerun()


def _readiness(spec: CrochetSpec) -> tuple[bool, str]:
    """Is the form complete enough to build? Mirrors products.crochet.validate."""
    if spec.mode == "from_pdfs" and not spec.source_files:
        return False, "Upload at least one pattern PDF to enable the button."
    if spec.mode == "from_photos" and not spec.source_files:
        return False, "Upload at least one photo to enable the button."
    if spec.mode == "from_etsy_data" and not (spec.etsy_data_text.strip() or spec.etsy_data_files):
        return False, "Paste or upload your Etsy product data to enable the button."
    if spec.mode in ("from_brief", "tech_pack") and len(spec.brief.strip()) < 3:
        return False, "Describe what you want to make to enable the button."
    return True, ""


def _extras(manifest: dict) -> None:
    """Crochet-specific detail above the shared result viewer."""
    theme.stats_row(
        [
            ("Steps", manifest.get("steps", "-"), "with stitch counts"),
            ("Sizes", len(manifest.get("sizes") or []) or 1, ", ".join(manifest.get("sizes") or [])[:28]),
            ("Diagrams", len(manifest.get("diagrams") or {}), "drawn for this pattern"),
            ("Skill", str(manifest.get("skill_level") or "-").title(), manifest.get("mode_label", "")),
        ]
    )

    diagram_paths = [Path(p) for p in (manifest.get("diagrams") or {}).values()]
    diagram_paths = [p for p in diagram_paths if p.exists()]
    if diagram_paths:
        with st.expander(f"Technical diagrams ({len(diagram_paths)})"):
            for row_start in range(0, len(diagram_paths), 2):
                for column, path in zip(st.columns(2), diagram_paths[row_start : row_start + 2]):
                    column.image(str(path), caption=path.stem.replace("-", " "),
                                 use_container_width=True)

    research = manifest.get("market")
    if research and research.get("listings"):
        listing = manifest.get("listing") or {}
        with st.expander(
            f"\U0001f4c8 Market research \u2014 {research['listings']} competitors analysed"
        ):
            cols = st.columns(4)
            cols[0].metric("Competitors", research["listings"])
            cols[1].metric(
                "Their median price",
                f"{research.get('price_median') or '-'}",
                help=f"Currency: {research.get('currency') or 'unknown'}",
            )
            cols[2].metric("Your price", f"{listing.get('suggested_price_usd', '-')}")
            if listing.get("list_price_usd"):
                cols[3].metric(
                    "List at", f"{listing['list_price_usd']}",
                    help="Then discount, matching the market's sale pattern",
                )
            elif research.get("opportunity_mean") is not None:
                cols[3].metric("Opportunity", research["opportunity_mean"])

            if listing.get("keyword_reasoning"):
                st.caption(f"Keyword strategy: {listing['keyword_reasoning']}")
            tags = research.get("tags") or []
            usable = [t for t in tags if t.get("tag") and len(t["tag"]) <= 20][:13]
            if usable:
                st.dataframe(
                    [
                        {
                            "Tag": t["tag"],
                            "Search volume": f"{t.get('volume', 0):,}" if t.get("volume") else "-",
                            "Competitors using it": t.get("used_by", 0),
                            "On your listing": "yes" if t["tag"] in (listing.get("tags") or []) else "",
                        }
                        for t in usable
                    ],
                    use_container_width=True, hide_index=True,
                )
            if research.get("title_structures"):
                st.caption("Title conventions: " + "; ".join(research["title_structures"][:6]))
            st.caption(f"Listing copy source: `{manifest.get('listing_source', 'template')}`")

    canva = manifest.get("canva") or {}
    urls = {slot: url for slot, url in (canva.get("edit_urls") or {}).items() if url}
    if urls:
        with st.expander(f"Editable Canva designs ({len(urls)})"):
            for slot, url in urls.items():
                st.markdown(f"- **{slot}** \u2014 [open in Canva]({url})")
    elif canva.get("status") not in (None, "disabled"):
        st.caption(f"Canva: {canva.get('status')} \u2014 {canva.get('reason', '')}")

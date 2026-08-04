"""Crochet Studio: five ways into one professional pattern PDF.

The five modes share a single pipeline. Only the *input* stage differs:

    from_pdfs      upload up to 10 existing patterns -> parse them -> ChatGPT
                   rebuilds one complete, graded pattern from what they contain
    from_etsy_data paste your Etsy product data, give a product number, and the
                   pattern plus the full listing (title, tags, description) is
                   written from that product's own data and photos
    from_brief     describe the piece in a sentence and get a pattern
    from_photos    upload photos of a finished piece and have ChatGPT read the
                   stitch pattern and construction back off them
    tech_pack      diagrams, schematic and tech pack only - no AI calls, no cost

Then every mode runs the same five stages:

    1. content extraction   -> crochet.extract
    2. content expansion    -> crochet.expand   (ChatGPT, template fallback)
    3. diagram generation   -> crochet.diagrams (matplotlib)
    4. image prompts + art  -> crochet.imagery  (ChatGPT prompt -> render -> Canva)
    5. layout and packaging -> crochet.pdf, mockups, Etsy copy, buyer ZIP

With no API keys at all the studio still produces a complete document: the
content comes from built-in templates and the artwork is painted locally.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..ai.text_client import CopyStudio
from ..config import Settings, get_settings
from ..crochet import diagrams as dgm
from ..crochet import etsy_data, expand, imagery
from ..crochet.brand import BrandKit
from ..crochet.extract import corpus_brief, extract_many, merge_sources
from ..crochet.pdf import CrochetPDF
from ..mockups.compose import build_listing_images
from ..mockups.context import MockupContext
from ..models import PAPER_SIZES, BuildResult
from ..packaging import MAX_TAG_LEN, MAX_TAGS, MAX_TITLE_LEN, build_zip, write_copy, write_product_docs
from ..pdf.drawkit import wrap_text
from ..pdf.verify import extract_page_texts
from ..themes import get_theme

Progress = Callable[[str, float], None]

MODES: dict[str, str] = {
    "from_pdfs": "Rebuild from uploaded patterns (up to 10 PDFs)",
    "from_etsy_data": "From my Etsy product data (pick a product number)",
    "from_brief": "From a written brief",
    "from_photos": "From photos of a finished piece",
    "tech_pack": "Diagrams and tech pack only (no AI, no cost)",
}
MODE_ORDER = list(MODES)

# Modes that make no chat/image API calls at all.
OFFLINE_MODES = {"tech_pack"}
MAX_SOURCE_FILES = 10
MAX_PHOTOS = 6

COST_MODES = {
    "lean": "Lean - cheap models, 2 images, one chat call",
    "standard": "Standard - default models, 5 images",
    "max": "Maximum quality - best models, 5 large images",
}
PLATES_FOR_COST = {"lean": 2, "standard": 5, "max": 5}


@dataclass
class CrochetSpec:
    """Everything the studio needs for one pattern build."""

    mode: str = "from_brief"
    brief: str = ""
    garment: str = ""
    sizes: list[str] = field(default_factory=list)
    audience: str = "confident hobby crocheters"
    tone: str = "clear, warm, precise"
    title: str | None = None

    # mode inputs
    source_files: list[str] = field(default_factory=list)   # PDFs, or photos
    etsy_data_text: str = ""
    etsy_data_files: list[str] = field(default_factory=list)
    product_number: int = 1
    image_dir: str | None = None

    # branding
    brand: BrandKit = field(default_factory=BrandKit)

    # layout
    theme: str = "minimalist"
    paper: str = "letter"
    orientation: str = "portrait"
    bleed_in: float = 0.0
    include_chart: bool = True
    include_gallery: bool = True

    # generation and cost
    cost_mode: str = "standard"
    generate_ai_copy: bool = True
    generate_ai_art: bool = True
    use_canva: bool = False
    canva_pull_back: bool = False
    listing_image_count: int = 8

    # ------------------------------------------------------------- geometry
    @property
    def trim_size_in(self) -> tuple[float, float]:
        w, h = PAPER_SIZES.get(self.paper, PAPER_SIZES["letter"])
        w, h = min(w, h), max(w, h)
        return (h, w) if self.orientation == "landscape" else (w, h)

    @property
    def size_label(self) -> str:
        w, h = self.trim_size_in
        return f'{w:g}" x {h:g}"'

    @property
    def has_a4_companion(self) -> bool:
        return self.paper in ("letter", "a4")

    @property
    def plate_limit(self) -> int:
        return PLATES_FOR_COST.get(self.cost_mode, 5)

    @property
    def offline_only(self) -> bool:
        return self.mode in OFFLINE_MODES

    def display_title(self) -> str:
        if self.title:
            return self.title
        if self.garment:
            return f"{self.garment.title()} Crochet Pattern"
        return "Crochet Pattern"

    def product_slug(self) -> str:
        base = self.title or self.garment or self.brief or "crochet"
        cleaned = "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in base.lower())
        words = [w for w in cleaned.split() if w not in {"the", "a", "an", "of", "for", "and"}][:5]
        # only append the qualifiers that are not already in the name, so a
        # title of "Crochet Pattern" does not become "crochet-pattern-crochet-pattern"
        for suffix in ("crochet", "pattern"):
            if suffix not in words:
                words.append(suffix)
        return "-".join(words)

    def to_dict(self) -> dict:
        data = dataclasses.asdict(self)
        data["trim_size_in"] = list(self.trim_size_in)
        data["size_label"] = self.size_label
        data["mode_label"] = MODES.get(self.mode, self.mode)
        return data


def validate(spec: CrochetSpec) -> CrochetSpec:
    """Check the spec and clamp it into range. Raises ValueError on real problems."""
    if spec.mode not in MODES:
        raise ValueError(f"Unknown mode '{spec.mode}'. Choose one of: {', '.join(MODE_ORDER)}")
    if spec.paper not in PAPER_SIZES:
        raise ValueError(f"Unknown paper '{spec.paper}'")
    if spec.orientation not in ("portrait", "landscape"):
        raise ValueError("orientation must be 'portrait' or 'landscape'")

    if spec.mode == "from_pdfs":
        spec.source_files = [f for f in spec.source_files if Path(f).exists()][:MAX_SOURCE_FILES]
        if not spec.source_files:
            raise ValueError("Upload at least one crochet pattern PDF to rebuild from")
    elif spec.mode == "from_photos":
        spec.source_files = [f for f in spec.source_files if Path(f).exists()][:MAX_PHOTOS]
        if not spec.source_files:
            raise ValueError("Upload at least one photo of the finished piece")
    elif spec.mode == "from_etsy_data":
        if not spec.etsy_data_text.strip() and not spec.etsy_data_files:
            raise ValueError("Paste your Etsy product data, or upload a CSV or JSON export")
        if spec.product_number < 1:
            raise ValueError("Product number must be 1 or higher")
    elif spec.mode in ("from_brief", "tech_pack"):
        if len(spec.brief.strip()) < 3:
            raise ValueError("Describe what you want to make, in at least 3 characters")

    spec.theme = get_theme(spec.theme).key
    spec.cost_mode = spec.cost_mode if spec.cost_mode in COST_MODES else "standard"
    spec.listing_image_count = max(1, min(10, spec.listing_image_count))
    spec.bleed_in = max(0.0, min(0.25, spec.bleed_in))
    spec.sizes = [str(s).strip().upper() for s in spec.sizes if str(s).strip()][:10]
    spec.brand = spec.brand.cleaned()
    if spec.offline_only:
        spec.generate_ai_copy = False
        spec.generate_ai_art = False
        spec.use_canva = False
    return spec


# ------------------------------------------------------------------- listing
def listing_from_pattern(spec: CrochetSpec, pattern: dict, product=None) -> dict:
    """Etsy copy: from the model when it supplied it, otherwise built locally."""
    raw = pattern.get("listing") or {}
    garment = spec.garment or "crochet"
    sizes = (pattern.get("sizes") or {}).get("labels") or []
    skill = str(pattern.get("skill_level") or "").title()
    yarn = (pattern.get("yarn_guide") or {}).get("weight") or ""

    title = str(raw.get("title") or "").strip()
    if not title:
        parts = [
            f"{pattern.get('title', spec.display_title())} Crochet Pattern",
            "PDF Instant Download",
            f"{len(sizes)} Sizes {sizes[0]}-{sizes[-1]}" if len(sizes) > 1 else "",
            f"{yarn.title()} Weight" if yarn else "",
            f"{skill} Level" if skill else "",
        ]
        title = " | ".join(p for p in parts if p)
        while len(title) > MAX_TITLE_LEN and title.count("|") > 1:
            title = title.rsplit("|", 1)[0].strip()

    tags: list[str] = []
    candidates = list(raw.get("tags") or []) + [
        f"{garment} pattern",
        "crochet pattern",
        "pdf pattern",
        "instant download",
        f"crochet {garment}",
        "digital pattern",
        f"{yarn} weight yarn" if yarn else "beginner crochet",
        "written pattern",
        "crochet chart",
        "handmade gift",
        "crochet tutorial",
        "size inclusive",
        "graded pattern",
    ]
    for tag in candidates:
        clean = " ".join(str(tag).lower().split())[:MAX_TAG_LEN].strip()
        if clean and clean not in tags:
            tags.append(clean)
        if len(tags) == MAX_TAGS:
            break

    description = str(raw.get("description") or "").strip()
    if not description:
        section_titles = [s.get("title", "") for s in pattern.get("sections") or []]
        includes = [
            f"- Complete written pattern, {expand.total_steps(pattern)} numbered steps",
            "- Stitch count at the end of every row",
            f"- Graded for {len(sizes)} sizes ({', '.join(sizes)})" if sizes else "- One size",
            "- Full sizing and finished measurements table",
            "- Gauge guide with a swatch diagram",
            "- Construction schematic with finished dimensions",
            "- Stitch chart with symbol legend",
            "- Seaming diagrams for every join",
            "- Blocking guide and care instructions",
            "- Troubleshooting section",
            "- Yarn substitution guide and yardage by size",
        ]
        description = "\n".join(
            [
                f"{pattern.get('title', spec.display_title())} - crochet pattern PDF, instant download",
                "",
                str(pattern.get("intro") or ""),
                "",
                "WHAT YOU GET",
                *includes,
                "",
                "SKILL LEVEL",
                f"{skill or 'Advanced beginner'}. You should be comfortable with: "
                + "; ".join(pattern.get("skill_requirements") or [])[:400],
                "",
                "SECTIONS",
                *[f"- {title_}" for title_ in section_titles],
                "",
                "HOW IT WORKS",
                "1. Buy and download instantly - nothing is shipped.",
                "2. Read it on any device, or print the pages you need.",
                "3. Come back to it any time - the file is yours to keep.",
                "",
                "TERMS",
                "This is a pattern, not a finished item. For personal use only; "
                "the pattern file may not be resold or redistributed.",
            ]
        )

    price = {"lean": 5.50, "standard": 7.50, "max": 9.50}.get(spec.cost_mode, 7.50)
    if product is not None and getattr(product, "price", ""):
        try:  # match the shop's own pricing when we know it
            price = round(float(str(product.price).replace(",", ".")) * 0.35, 2) or price
        except (TypeError, ValueError):
            pass

    return {
        "title": title[:MAX_TITLE_LEN],
        "tags": tags,
        "description": description,
        "materials": ["PDF", "Digital Download", "Crochet Pattern"],
        "who_made_it": "i_did",
        "is_digital": True,
        "suggested_price_usd": price,
        "sections": ["Crochet Patterns"],
    }


# ------------------------------------------------------------------ mockups
def mockup_context(spec: CrochetSpec, pattern: dict, pages: list[dict]) -> MockupContext:
    theme = get_theme(spec.theme)
    sizes = (pattern.get("sizes") or {}).get("labels") or []
    interior = [
        index for index, page in enumerate(pages)
        if page["kind"] not in ("cover", "credits", "contents", "thanks")
    ]
    w_in, h_in = spec.trim_size_in
    title = str(pattern.get("title") or spec.display_title())
    return MockupContext(
        theme_key=spec.theme,
        trim_size_in=spec.trim_size_in,
        size_label=spec.size_label,
        orientation=spec.orientation,
        eyebrow="crochet pattern pdf",
        title_lines=_wrap_words(title, 2),
        badges=[
            f"{len(pages)} pages",
            f"{len(sizes)} sizes" if sizes else "one size",
            "instant download",
        ],
        grid_eyebrow="inside the pattern",
        grid_headline=f"{len(pages)} Pages of Pattern",
        grid_caption=" \u00b7 ".join(
            s.get("title", "") for s in (pattern.get("sections") or [])[:3]
        ),
        grid_cols=3,
        grid_rows=2,
        included_headline="What's in the pattern",
        bullets=[
            "Full written instructions with stitch counts",
            f"Graded for {len(sizes)} sizes" if sizes else "Complete written pattern",
            "Schematic, stitch chart and seam diagrams",
            "Gauge, blocking, care and troubleshooting",
        ],
        captions={
            "desk_eyebrow": "download, read, make",
            "desk_caption": f"{title} \u00b7 {spec.size_label}",
            "detail_headline": "Clear, uncluttered pattern pages",
            "detail_caption": f"{theme.label} layout \u00b7 vector text \u00b7 print or read on screen",
            "stack_headline": "Every diagram drawn for this pattern",
            "gift_ribbon": "AN INSTANT DIGITAL PATTERN",
            "size_headline": "Prints on Letter and A4" if spec.has_a4_companion else "Print-ready",
        },
        size_notes=[
            f'Page size \u2014 {w_in:g}" x {h_in:g}" ({round(w_in * 25.4)} x {round(h_in * 25.4)} mm)',
            "Prints on US Letter and A4" if spec.has_a4_companion else "Scales to any paper size",
            "PDF \u00b7 vector text \u00b7 read on any device",
        ],
        a4_included=spec.has_a4_companion,
        cover_index=0,
        page_indexes=interior[:6],
        scenes=["hero", "bundle_grid", "included", "detail", "desk", "stack", "gift", "size_chart"],
    )


def _wrap_words(text: str, lines: int) -> list[str]:
    words = text.split()
    if len(words) <= 2 or lines <= 1:
        return [text]
    middle = len(words) // 2
    return [" ".join(words[:middle]), " ".join(words[middle:])]


# ------------------------------------------------------------- verification
def verify_pattern_pdf(pdf_path: Path, pattern: dict, pages: list[dict]) -> dict:
    """Re-read the rendered PDF and confirm the content actually landed.

    Cheap insurance against a layout bug silently dropping instructions: the
    row labels are what a buyer follows, so every one of them must be findable
    in the extracted text.
    """
    errors: list[str] = []
    checks = 0
    texts = extract_page_texts(pdf_path)
    if not texts:
        return {
            "ok": True,
            "checks": 0,
            "text_check": "skipped (pypdfium2 unavailable)",
            "rendered_pages": 0,
            "errors": [],
        }

    if len(texts) != len(pages):
        errors.append(f"PDF has {len(texts)} pages, expected {len(pages)}")
    checks += 1

    joined = "\n".join(texts)
    upper = joined.upper()

    for section in pattern.get("sections") or []:
        title = str(section.get("title") or "")
        if title and title.upper() not in upper:
            errors.append(f"Section '{title}' is missing from the PDF")
        checks += 1

    labels = [
        str(step.get("label") or "")
        for section in pattern.get("sections") or []
        for step in section.get("steps") or []
    ]
    missing = [label for label in labels if label and label not in joined]
    if missing:
        errors.append(f"{len(missing)} step label(s) missing, e.g. {missing[:5]}")
    checks += len(labels)

    for label, present in (
        ("gauge", "GAUGE" in upper),
        ("abbreviations", "ABBREVIATIONS" in upper),
        ("troubleshooting", "TROUBLESHOOTING" in upper or not pattern.get("troubleshooting")),
        ("care", "CARE" in upper or not pattern.get("care")),
    ):
        if not present:
            errors.append(f"The {label} section did not render")
        checks += 1

    return {
        "ok": not errors,
        "checks": checks,
        "text_check": "passed" if not errors else "failed",
        "rendered_pages": len(texts),
        "errors": errors,
    }


# -------------------------------------------------------------------- inputs
def _gather(spec: CrochetSpec, run_dir: Path, warnings: list[str]) -> dict:
    """Run the mode-specific input stage.

    Returns {"brief", "garment", "sizes", "corpus", "product", "products",
    "photos", "extra_instructions"}.
    """
    out: dict = {
        "brief": spec.brief.strip(),
        "garment": spec.garment.strip().lower(),
        "sizes": list(spec.sizes),
        "corpus": None,
        "product": None,
        "products": [],
        "photos": [],
        "extra_instructions": "",
        "sources": [],
        "default_title": "",
    }

    if spec.mode == "from_pdfs":
        sources = extract_many(spec.source_files)
        out["sources"] = [s.to_dict() for s in sources]
        for source in sources:
            if source.error:
                warnings.append(f"Could not read {source.name}: {source.error}")
        corpus = merge_sources(sources)
        if not corpus["sources"]:
            raise ValueError(
                "None of the uploaded files could be read as text. Scanned or image-only "
                "PDFs need OCR first."
            )
        out["corpus"] = corpus
        out["garment"] = out["garment"] or corpus.get("garment", "")
        out["sizes"] = out["sizes"] or corpus.get("sizes", [])
        brief = corpus_brief(corpus)
        out["brief"] = f"{spec.brief.strip()}\n\n{brief}".strip()
        out["extra_instructions"] = (
            "\nThe source material above was parsed from patterns the designer already owns. "
            "Use it for the stitch pattern, construction and gauge, but write a fresh, "
            "complete and internally consistent pattern in your own words. Do not copy "
            "sentences verbatim from the source."
        )

    elif spec.mode == "from_etsy_data":
        products, load_warnings = etsy_data.load_products(
            spec.etsy_data_text, spec.etsy_data_files, spec.image_dir
        )
        warnings.extend(load_warnings)
        if not products:
            raise ValueError(
                "No products could be read from that data. Paste a CSV, a JSON export, or "
                "one product per block as 'Label: value' lines."
            )
        product = etsy_data.pick(products, spec.product_number)
        out["products"] = [p.to_dict() for p in products]
        out["product"] = product
        out["garment"] = out["garment"] or product.garment()
        out["sizes"] = out["sizes"] or product.sizes()
        # offline, the product's own name beats a generic "Cardigan Pattern"
        out["default_title"] = product.title
        out["photos"] = [str(p) for p in product.local_images()][:MAX_PHOTOS]
        out["brief"] = "\n\n".join(
            part for part in [
                spec.brief.strip(),
                f"SELECTED PRODUCT (number {product.number} of {len(products)})",
                product.brief(),
                etsy_data.catalogue_brief(products),
            ] if part
        )
        out["extra_instructions"] = (
            "\nThis pattern is for the selected Etsy product above. Read every field, "
            "including the tags and the description, and any attached product photographs. "
            "Write the pattern that produces that exact item. Then write the Etsy listing "
            "for the PATTERN (not the finished item), using the shop's existing tags and "
            "titles as a guide to what sells in this shop."
        )

    elif spec.mode == "from_photos":
        out["photos"] = list(spec.source_files)[:MAX_PHOTOS]
        out["brief"] = spec.brief.strip() or "Reverse-engineer the pattern from the photographs."
        out["extra_instructions"] = expand.photo_prompt(out["garment"], out["sizes"])

    elif spec.mode == "tech_pack":
        out["extra_instructions"] = ""

    return out


# --------------------------------------------------------------------- build
def build_crochet(
    spec: CrochetSpec,
    out_dir: str | Path | None = None,
    progress: Progress | None = None,
    settings: Settings | None = None,
) -> BuildResult:
    """Run the whole pipeline and return the finished product."""
    started = time.perf_counter()
    settings = settings or get_settings()
    spec = validate(spec)
    if spec.cost_mode == "lean":
        settings = settings.lean()

    def report(message: str, fraction: float) -> None:
        if progress:
            progress(message, fraction)

    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = Path(out_dir) if out_dir else settings.resolved_output_dir() / f"{stamp}_{spec.product_slug()}"
    art_dir = run_dir / "art"
    diagram_dir = run_dir / "diagrams"
    print_dir = run_dir / "print"
    mock_dir = run_dir / "mockups"
    for folder in (run_dir, art_dir, diagram_dir, print_dir, mock_dir):
        folder.mkdir(parents=True, exist_ok=True)

    result = BuildResult(spec=spec, run_dir=run_dir, product_type="crochet")
    theme = get_theme(spec.theme)

    # 1. extraction / input
    report("Reading your source material", 0.04)
    inputs = _gather(spec, run_dir, result.warnings)
    garment = inputs["garment"] or "crochet piece"

    # 2. expansion
    report("Writing the pattern with ChatGPT", 0.14)
    fallback = expand.template_pattern(
        garment=garment,
        title=spec.title or inputs["default_title"] or "",
        corpus=inputs["corpus"],
        sizes=inputs["sizes"] or None,
        designer=spec.brand.credit,
    )
    writer = CopyStudio(settings, offline=None if spec.generate_ai_copy else True)
    pattern = fallback
    if spec.generate_ai_copy:
        prompt = expand.content_prompt(
            inputs["brief"],
            garment=garment,
            sizes=inputs["sizes"] or None,
            audience=spec.audience,
            tone=spec.tone,
            designer=spec.brand.credit,
            extra_instructions=inputs["extra_instructions"],
        )
        raw = writer.ask_json(prompt, images=inputs["photos"] or None)
        if raw:
            pattern = expand.normalise_pattern(raw, fallback)
        result.warnings.extend(writer.warnings)
    content_source = writer.source
    pattern = expand.ensure_stitch_counts(pattern)
    if spec.title:
        pattern["title"] = spec.title

    # 3. diagrams
    report("Drawing the technical diagrams", 0.34)
    diagram_plates, diagram_warnings = dgm.build_all(pattern, diagram_dir, theme)
    result.warnings.extend(diagram_warnings)

    # 4. imagery, then Canva
    report("Writing image prompts and rendering artwork", 0.44)
    art = imagery.build_imagery(
        pattern,
        garment,
        art_dir,
        theme_key=spec.theme,
        settings=settings,
        generate_art=spec.generate_ai_art,
        use_canva=spec.use_canva,
        canva_pull_back=spec.canva_pull_back,
        plate_limit=spec.plate_limit,
        writer=writer if spec.generate_ai_copy else None,
        brand_note=f"SHOP: {spec.brand.shop}. {spec.brand.tagline}".strip(),
        progress=None if progress is None else (
            lambda message, fraction: report(message, 0.44 + 0.16 * fraction)
        ),
    )
    result.warnings.extend(art["warnings"])
    result.art_paths = dict(art["plates"])
    result.art_source = art["source"]
    result.canva = art["canva"]

    # 5. layout
    report("Laying out the pattern", 0.62)
    slug = spec.product_slug()
    document = CrochetPDF(
        pattern,
        brand=spec.brand,
        theme=theme,
        trim_size_in=spec.trim_size_in,
        bleed_in=spec.bleed_in,
        diagrams=diagram_plates,
        plates=art["plates"],
        captions=art["captions"],
        include_chart=spec.include_chart,
        include_gallery=spec.include_gallery,
    )
    pdf_path, pages = document.render(print_dir / f"{slug}-{spec.paper}.pdf")
    result.pdf_path = pdf_path
    result.pdf_paths[spec.paper] = pdf_path

    report("Checking the rendered pattern", 0.7)
    result.verification = verify_pattern_pdf(pdf_path, pattern, pages)
    for error in result.verification.get("errors", []):
        result.warnings.append(f"Verification: {error}")

    # 6. mockups
    report("Compositing listing images", 0.74)
    try:
        result.listing_images = build_listing_images(
            mockup_context(spec, pattern, pages),
            pdf_path,
            mock_dir,
            count=spec.listing_image_count,
            progress=None if progress is None else (
                lambda message, fraction: report(message, 0.74 + 0.16 * fraction)
            ),
        )
    except Exception as exc:  # noqa: BLE001 - never lose the PDF over a mockup
        result.warnings.append(f"Mockups failed: {type(exc).__name__}: {exc}")

    # 7. packaging
    report("Writing listing copy and packaging files", 0.92)
    listing = listing_from_pattern(spec, pattern, inputs["product"])
    result.listing_copy = listing
    write_copy(listing, run_dir)
    docs = write_product_docs(
        str(pattern.get("title") or spec.display_title()),
        [f"- {pdf_path.name}  ({spec.size_label}, {len(pages)} pages)"],
        print_dir,
        printing=[
            "1. Open the PDF in Adobe Reader (free), your browser, or any tablet.",
            "2. Print the pages you need at Actual size / 100% scale, or read on screen.",
            "3. The instruction pages print well in greyscale to save ink.",
        ],
    )
    result.zip_path = build_zip(run_dir / f"{slug}-etsy-files.zip", [pdf_path, *docs])

    manifest = {
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "product_type": "crochet",
        "title": pattern.get("title") or spec.display_title(),
        "brief": (spec.brief or garment)[:400],
        "mode": spec.mode,
        "mode_label": MODES.get(spec.mode, spec.mode),
        "spec": spec.to_dict(),
        "pages": len(pages),
        "page_kinds": [page["kind"] for page in pages],
        "steps": expand.total_steps(pattern),
        "sizes": (pattern.get("sizes") or {}).get("labels") or [],
        "skill_level": pattern.get("skill_level"),
        "content_source": content_source,
        "art_source": result.art_source,
        "pattern": pattern,
        "sources": inputs["sources"],
        "etsy_products": len(inputs["products"]),
        "selected_product": (
            inputs["product"].to_dict() if inputs["product"] is not None else None
        ),
        "art_prompts": art["prompts"],
        "canva": result.canva,
        "diagrams": {key: str(path) for key, path in diagram_plates.items()},
        "files": {
            "pdfs": {key: str(path) for key, path in result.pdf_paths.items()},
            "art": {key: str(path) for key, path in art["plates"].items()},
            "diagrams": {key: str(path) for key, path in diagram_plates.items()},
            "listing_images": [str(path) for path in result.listing_images],
            "zip": str(result.zip_path) if result.zip_path else None,
        },
        "verification": result.verification,
        "listing": listing,
        "warnings": result.warnings,
        "duration_seconds": round(time.perf_counter() - started, 2),
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")

    report("Done", 1.0)
    return result

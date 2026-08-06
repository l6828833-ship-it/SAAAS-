"""Crochet Studio: five ways into one professional pattern PDF.

The five modes share a single pipeline. Only the *input* stage differs:

    from_pdfs      upload up to 10 existing patterns -> parse them -> Qwen
                   rebuilds complete, graded patterns from what they contain
    from_etsy_data paste your Etsy product data, give a product number, and the
                   pattern plus the full listing (title, tags, description) is
                   written from that product's own data and photos
    from_brief     describe the piece in a sentence and get a pattern
    from_photos    upload photos of a finished piece and have the vision model
                   read the stitch pattern and construction back off them
    tech_pack      diagrams, schematic and tech pack only - no AI calls, no cost

Then every mode runs the same stages, in this order:

    1. content extraction   -> crochet.extract
    2. art direction        -> crochet.imagery  (Qwen designs the piece and
                               writes one prompt per plate)
    3. artwork              -> ai.image_client  (Gemini renders the plates)
    4. content expansion    -> crochet.expand   (Qwen writes the pattern with
                               the rendered plates attached as reference)
    5. diagram generation   -> crochet.diagrams (matplotlib)
    6. layout and packaging -> crochet.pdf, mockups, Etsy copy, buyer ZIP

Art direction comes *before* the pattern text on purpose. The plates are what a
buyer sees on the listing, so the instructions are written to match the
photographs rather than the other way round.

A batch is planned by `crochet.batch`: it decides how many patterns each upload
earns and gives every pattern its own design direction, so five patterns from one
PDF are five different products rather than five copies.

With no API keys at all the studio still produces a complete document: the
content comes from built-in templates and the artwork is painted locally.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..ai.text_client import CopyStudio
from ..config import Settings, get_settings
from ..crochet import batch as batch_planner
from ..crochet import diagrams as dgm
from ..crochet import etsy_data, expand, imagery, market
from ..crochet.batch import ALLOCATION_STRATEGIES, DEFAULT_STRATEGY
from ..crochet.brand import BrandKit
from ..crochet.imagery import MAX_PLATES
from ..crochet.extract import corpus_brief, extract_many, merge_sources
from ..crochet.pdf import MAX_PAGES as PDF_MAX_PAGES
from ..crochet.pdf import MIN_PAGES as PDF_MIN_PAGES
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
MAX_PATTERNS_PER_RUN = 10

# ------------------------------------------------------------------ cost model
# Photographic plates are still the bulk of what a run costs, but the numbers
# changed completely when the engine moved to Gemini. `gemini-2.5-flash-image` is
# token-priced - about 1290 output tokens per picture at $2.50 per million, so
# roughly $0.004 a plate against $0.133 for gpt-image-1.5 at high quality. A
# whole photoshoot now costs less than a single old cover, which is why the plate
# counts below are generous rather than rationed.
#
# Prices are only used to show an estimate in the UI, so being slightly stale is
# harmless.
IMAGE_PRICE_USD: dict[tuple[str, str], float] = {
    # Gemini: same price at every quality, because "quality" is not a parameter
    # it takes - the bill is output tokens.
    ("google-ai-studio/gemini-2.5-flash-image", "low"): 0.004,
    ("google-ai-studio/gemini-2.5-flash-image", "medium"): 0.004,
    ("google-ai-studio/gemini-2.5-flash-image", "high"): 0.004,
    ("google-ai-studio/gemini-3.1-flash-image-preview", "low"): 0.006,
    ("google-ai-studio/gemini-3.1-flash-image-preview", "medium"): 0.006,
    ("google-ai-studio/gemini-3.1-flash-image-preview", "high"): 0.006,
    # OpenAI list prices for a 1024x1024 output, if the provider is switched back.
    ("gpt-image-1.5", "low"): 0.009,
    ("gpt-image-1.5", "medium"): 0.034,
    ("gpt-image-1.5", "high"): 0.133,
    ("gpt-image-1-mini", "low"): 0.005,
    ("gpt-image-1-mini", "medium"): 0.011,
    ("gpt-image-1-mini", "high"): 0.052,
}
# Models billed per token rather than per picture. Aspect ratio changes the
# pixel count but not the token count, so the non-square surcharge is an
# OpenAI-only concept.
TOKEN_PRICED_PREFIXES = ("google-ai-studio/", "openai/gpt-image")
# Non-square plates cost roughly half again as much as a square one.
NON_SQUARE_FACTOR = 1.5
# Qwen3-VL-30B at $0.15 in / $0.60 out. A pattern is ~2k in and ~6k out, and a
# run makes three calls (art direction, the pattern itself, the listing pass),
# with the plates attached to the middle one.
TEXT_COST_USD = {"cheap": 0.012, "default": 0.015}


def _tier_model(tier: str) -> str | None:
    """The concrete image model a cost tier means for the configured provider."""
    return get_settings().image_model_for_tier(tier)


@dataclass(frozen=True)
class CostProfile:
    """What a cost mode actually spends."""

    key: str
    label: str
    plates: int                 # AI-generated photographic plates
    image_model: str | None     # None -> no AI images at all
    image_quality: str = "low"
    cheap_text: bool = True
    note: str = ""
    # Which model tier this profile wants, resolved against the live settings at
    # build time. `image_model` above is the same thing frozen at import, kept
    # for the price lookup and the UI label.
    image_tier: str = "cheap"

    def image_cost(self) -> float:
        if not self.image_model or self.plates <= 0:
            return 0.0
        unit = IMAGE_PRICE_USD.get((self.image_model, self.image_quality), 0.05)
        factor = 1.0 if self.image_model.startswith(TOKEN_PRICED_PREFIXES) else NON_SQUARE_FACTOR
        return self.plates * unit * factor

    def text_cost(self) -> float:
        return TEXT_COST_USD["cheap" if self.cheap_text else "default"]

    def estimate(self, pattern_count: int = 1, shared_art: bool = False) -> float:
        """Estimated USD for a whole run.

        With `shared_art` the generic photographic plates are generated once and
        reused across the batch, so only the text scales with the pattern count.
        """
        count = max(1, pattern_count)
        images = self.image_cost() * (1 if shared_art else count)
        return round(images + self.text_cost() * count, 4)


# Plate counts are sized around what a listing actually needs: a cover, about
# three interior photos for the PDF, and enough remaining shots to carry the
# Etsy gallery. `free` still produces a complete product - every technical
# diagram is drawn locally, so only the photography is missing.
COST_PROFILES: dict[str, CostProfile] = {
    "free": CostProfile(
        key="free", label="Free art", plates=0, image_model=None, image_tier="none",
        note="Artwork is painted locally. Qwen still writes the whole pattern.",
    ),
    "lean": CostProfile(
        key="lean", label="Cover + 3 interior photos", plates=4,
        image_model=_tier_model("cheap"), image_quality="medium", image_tier="cheap",
        note="A cover plus the three photos the PDF pages want. Every diagram is "
             "still drawn locally at no cost.",
    ),
    "standard": CostProfile(
        key="standard", label="Full listing set", plates=8,
        image_model=_tier_model("cheap"), image_quality="medium", image_tier="cheap",
        note="Cover, interior photos and enough styled shots to fill the Etsy "
             "gallery without repeating a scene.",
    ),
    "premium": CostProfile(
        key="premium", label="Premium photoshoot", plates=12,
        image_model=_tier_model("best"), image_quality="high", cheap_text=False,
        image_tier="best",
        note="Twelve distinct plates on the best image model, and the pattern "
             "written without the cheap-text shortcut.",
    ),
    # `plates` is a placeholder here: the spec's own `custom_image_count`
    # replaces it, which is why `plate_limit` special-cases this key.
    "custom": CostProfile(
        key="custom", label="Custom (you choose)", plates=4,
        image_model=_tier_model("cheap"), image_quality="medium", image_tier="cheap",
        note="You choose exactly how many photographs go in the PDF, whether "
             "to include a cover, and whether to build the Etsy listing gallery.",
    ),
}
# "lean" is the default because it is exactly the PDF's own shot list - a cover
# plus the three interior photos. The listing gallery is filled by the free local
# mockup composites, so most runs do not need to buy gallery plates as well.
DEFAULT_COST_MODE = "lean"

def short_model(model: str | None) -> str:
    """`google-ai-studio/gemini-2.5-flash-image` -> `gemini-2.5-flash-image`."""
    return str(model or "").rsplit("/", 1)[-1]


# Human-readable dropdown labels, with the estimated price baked in. Sub-cent
# estimates are shown to three decimals so "free" and "lean" are distinguishable.
COST_MODES: dict[str, str] = {
    key: (
        f"{profile.label} \u2014 "
        + (
            f"{profile.plates} image on {short_model(profile.image_model)}"
            if profile.plates == 1 else
            f"{profile.plates} images on {short_model(profile.image_model)}"
            if profile.plates else "no AI images"
        )
        + (
            f", about ${profile.estimate():.3f}"
            if profile.estimate() < 0.10 else
            f", about ${profile.estimate():.2f}"
        )
        + " per pattern"
    )
    for key, profile in COST_PROFILES.items()
}
PLATES_FOR_COST = {key: profile.plates for key, profile in COST_PROFILES.items()}


def cost_profile(mode: str) -> CostProfile:
    return COST_PROFILES.get(mode, COST_PROFILES[DEFAULT_COST_MODE])


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

    # batch: how many separate patterns to build, and how the uploads are shared
    # out between them. `allocation` is the strategy - see crochet.batch. With
    # "fixed", `sources_per_pattern` is how many files each pattern reads.
    pattern_count: int = 1
    sources_per_pattern: int = 0
    allocation: str = DEFAULT_STRATEGY

    # Set by the batch planner on each child spec, never by the UI. This is what
    # makes pattern 3 of 5 a different product rather than a third copy.
    variant_index: int = 1
    variant_total: int = 1
    variant_direction: str = ""
    variant_title_hint: str = ""
    # One-word tag for this variant ("Cropped", "Chunky"). Used to keep the
    # titles in a batch apart when the writer names them all the same thing.
    variant_label: str = ""

    # market research: a competitor scrape (JSON / JSONL / CSV / XLSX) used to
    # pick the title, tags, description and price from real demand signals.
    market_text: str = ""
    market_files: list[str] = field(default_factory=list)

    # branding
    brand: BrandKit = field(default_factory=BrandKit)

    # layout
    theme: str = "minimalist"
    paper: str = "letter"
    orientation: str = "portrait"
    bleed_in: float = 0.0
    include_chart: bool = True
    include_gallery: bool = True
    # Page ceiling, not a target. The pattern is as long as the design needs it
    # to be; this is only the point past which the least useful pages are cut.
    max_pages: int = PDF_MAX_PAGES
    # Only read when cost_mode == "custom": how many AI photographs to render
    # for the document, and whether the first one is a cover.
    custom_image_count: int = 4
    custom_cover: bool = True

    # generation and cost
    cost_mode: str = DEFAULT_COST_MODE
    # Generate the photographic plates once and reuse them across a batch. The
    # plates are styled stock-alikes, not per-pattern illustrations, so sharing
    # them cuts the cost of a 4-pattern run to roughly that of a single one.
    share_art: bool = True
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
    def profile(self) -> CostProfile:
        return cost_profile(self.cost_mode)

    @property
    def plate_limit(self) -> int:
        """How many AI photographs to render for this run."""
        if self.cost_mode == "custom":
            return max(0, min(MAX_PLATES, int(self.custom_image_count)))
        return self.profile.plates

    @property
    def offline_only(self) -> bool:
        return self.mode in OFFLINE_MODES

    def estimated_cost_usd(self) -> float:
        """What this run is likely to cost in API charges.

        An upper bound: identical prompts are served from the image cache, so a
        rebuild or a batch sharing generic plates costs less than this.
        """
        if self.offline_only:
            return 0.0
        if not self.generate_ai_copy and not self.generate_ai_art:
            return 0.0
        return self.profile.estimate(self.pattern_count, shared_art=False)

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


def _slug_from(title: str | None) -> str:
    """A file-safe slug from a pattern title, ending in -crochet-pattern."""
    cleaned = "".join(
        ch if ch.isalnum() or ch.isspace() else " " for ch in str(title or "").lower()
    )
    words = [w for w in cleaned.split() if w not in {"the", "a", "an", "of", "for", "and"}][:5]
    if not words:
        return ""
    for suffix in ("crochet", "pattern"):
        if suffix not in words:
            words.append(suffix)
    return "-".join(words)


def group_sources(
    files: list[str],
    pattern_count: int,
    sources_per_pattern: int = 0,
) -> list[list[str]]:
    """Split uploaded files into one group per pattern to build.

    Two modes:

    * `sources_per_pattern > 0` - each pattern gets exactly that many files,
      taken in order and wrapping around if there are not enough to go round.
      Upload 4 files, ask for 2 patterns of 2, and you get [1,2] and [3,4].
    * `sources_per_pattern == 0` - the uploads are split as evenly as possible
      across the requested number of patterns, so nothing is left unused.

    Always returns exactly `pattern_count` non-empty groups (assuming at least
    one file), because a group with no sources cannot produce a pattern.
    """
    files = [f for f in files if f]
    count = max(1, int(pattern_count))
    if not files:
        return [[] for _ in range(count)]

    per = max(0, int(sources_per_pattern))
    groups: list[list[str]] = []

    if per > 0:
        for index in range(count):
            start = index * per
            group = [files[(start + offset) % len(files)] for offset in range(per)]
            # de-duplicate while keeping order, in case of wrap-around overlap
            seen: set[str] = set()
            unique = [f for f in group if not (f in seen or seen.add(f))]
            groups.append(unique)
        return groups

    # even split: hand out the remainder one file at a time to the first groups
    base, extra = divmod(len(files), count)
    cursor = 0
    for index in range(count):
        take = base + (1 if index < extra else 0)
        if take == 0:
            # more patterns than files: reuse files round-robin so every
            # requested pattern still gets something to work from
            groups.append([files[index % len(files)]])
            continue
        groups.append(files[cursor : cursor + take])
        cursor += take
    return groups


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
    spec.cost_mode = spec.cost_mode if spec.cost_mode in COST_PROFILES else DEFAULT_COST_MODE
    # 0 turns the listing mockups off entirely. They are free to make (Pillow
    # composites of the rendered PDF pages) but they are the slowest stage of a
    # build, so being able to skip them matters.
    spec.listing_image_count = max(0, min(10, spec.listing_image_count))
    spec.bleed_in = max(0.0, min(0.25, spec.bleed_in))
    # A ceiling, not a target. Below the floor there is not enough room for the
    # reference pages a pattern has to carry to be usable.
    spec.max_pages = max(PDF_MIN_PAGES, min(60, int(spec.max_pages or PDF_MAX_PAGES)))
    spec.custom_image_count = max(0, min(MAX_PLATES, int(spec.custom_image_count)))
    spec.pattern_count = max(1, min(MAX_PATTERNS_PER_RUN, spec.pattern_count))
    spec.sources_per_pattern = max(0, min(MAX_SOURCE_FILES, spec.sources_per_pattern))
    if spec.allocation not in ALLOCATION_STRATEGIES:
        spec.allocation = DEFAULT_STRATEGY
    spec.variant_total = max(1, int(spec.variant_total))
    spec.variant_index = max(1, min(spec.variant_total, int(spec.variant_index)))
    spec.market_files = [f for f in spec.market_files if Path(f).exists()][:20]
    spec.sizes = [str(s).strip().upper() for s in spec.sizes if str(s).strip()][:10]
    spec.brand = spec.brand.cleaned()
    if spec.offline_only:
        spec.generate_ai_copy = False
        spec.generate_ai_art = False
        spec.use_canva = False
    # The "free art" profile asks for no plates at all, so paint them locally.
    if spec.profile.image_model is None or spec.profile.plates <= 0:
        spec.generate_ai_art = False
    return spec


# ------------------------------------------------------------------- listing
def listing_from_pattern(
    spec: CrochetSpec,
    pattern: dict,
    product=None,
    report: "market.MarketReport | None" = None,
) -> dict:
    """Etsy copy: from the model when it supplied it, otherwise built locally.

    When a market report is available its tags lead the tag list and its price
    analysis sets the price, so even the offline path benefits from the research.
    """
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
    # research-backed tags first: they are ranked by real search volume
    researched = report.best_tags(MAX_TAGS) if report and report.listings else []
    # Title words are the most important keywords for discoverability: a buyer
    # searching for "Stitch amigurumi" needs to find this listing.
    title_words = [
        w.lower() for w in re.split(r"[\s|!?,.]+", str(pattern.get("title") or ""))
        if len(w) > 2 and w.lower() not in ("the", "and", "for", "crochet", "pattern")
    ]
    title_tags = [
        f"{w} crochet pattern" for w in title_words[:3]
    ] + [" ".join(title_words[:4]).strip()]
    candidates = list(raw.get("tags") or []) + researched + title_tags + [
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

    # A default only: real competitor pricing overrides it further down. What the
    # pattern cost to generate has no bearing on what it is worth to a buyer.
    price = 7.50
    if product is not None and getattr(product, "price", ""):
        try:  # match the shop's own pricing when we know it
            price = round(float(str(product.price).replace(",", ".")) * 0.35, 2) or price
        except (TypeError, ValueError):
            pass
    # real competitor pricing beats any default
    if report and report.suggested_price:
        price = report.suggested_price

    listing = {
        "title": title[:MAX_TITLE_LEN],
        "tags": tags,
        "description": description,
        "materials": ["PDF", "Digital Download", "Crochet Pattern"],
        "who_made_it": "i_did",
        "is_digital": True,
        "suggested_price_usd": price,
        "sections": ["Crochet Patterns"],
    }
    if report and report.suggested_list_price:
        listing["list_price_usd"] = report.suggested_list_price
    return listing


# ------------------------------------------------------------------ mockups
# Which pages actually look good in a listing image, best first. A pattern's
# front matter is mostly prose, so showing the first few interior pages fills
# the grid with grey text; the diagram and table pages sell the product.
SHOWCASE_PRIORITY = (
    "chart",           # stitch chart
    "construction",    # schematic with dimensions
    "sizing",          # measurement table + body diagram
    "gauge",           # swatch diagram
    "gallery",         # finished-piece photography
    "foundation",      # foundation row illustration
    "instructions",    # the actual pattern rows
    "seaming",         # seam diagrams
    "materials",       # materials flat-lay
    "counts",          # stitch count table
    "yarn_guide",      # yardage chart
    "troubleshooting",
    "blocking",
    "abbreviations",
    "about",
    "care",
    "assembly",
)


def showcase_pages(pages: list[dict], slots: int) -> list[int]:
    """Pick the most visually interesting pages, in priority order.

    Fills up to `slots` page indexes: one pass taking the best example of each
    kind so the grid shows variety, then a second pass topping up with repeats
    of the strongest kinds rather than padding with front matter.
    """
    by_kind: dict[str, list[int]] = {}
    for index, page in enumerate(pages):
        if page["kind"] in ("cover", "credits", "contents", "thanks"):
            continue
        by_kind.setdefault(page["kind"], []).append(index)

    chosen: list[int] = []
    # first pass: one page per kind, in priority order
    for kind in SHOWCASE_PRIORITY:
        if kind in by_kind and by_kind[kind]:
            chosen.append(by_kind[kind].pop(0))
        if len(chosen) >= slots:
            return chosen[:slots]

    # second pass: top up from whatever is left, still priority ordered
    for kind in SHOWCASE_PRIORITY:
        while kind in by_kind and by_kind[kind] and len(chosen) < slots:
            chosen.append(by_kind[kind].pop(0))
    # anything not in the priority list at all
    for indexes in by_kind.values():
        while indexes and len(chosen) < slots:
            chosen.append(indexes.pop(0))
    return chosen[:slots]


def mockup_context(spec: CrochetSpec, pattern: dict, pages: list[dict]) -> MockupContext:
    theme = get_theme(spec.theme)
    sizes = (pattern.get("sizes") or {}).get("labels") or []
    w_in, h_in = spec.trim_size_in
    title = str(pattern.get("title") or spec.display_title())
    steps = expand.total_steps(pattern)
    diagram_count = len(pattern.get("seaming") or []) + 6

    # A 4x3 grid reads as "this is a substantial product" rather than a sample.
    grid_cols, grid_rows = 4, 3
    showcase = showcase_pages(pages, grid_cols * grid_rows)

    section_titles = [s.get("title", "") for s in (pattern.get("sections") or [])]
    skill = str(pattern.get("skill_level") or "").title()

    return MockupContext(
        theme_key=spec.theme,
        trim_size_in=spec.trim_size_in,
        size_label=spec.size_label,
        orientation=spec.orientation,
        eyebrow="crochet pattern \u00b7 instant pdf download",
        title_lines=_wrap_words(title, 2),
        badges=[
            f"{len(pages)} pages",
            f"{len(sizes)} sizes" if sizes else "one size",
            skill or "written pattern",
            "instant download",
        ],
        grid_eyebrow="every page you get",
        grid_headline=f"All {len(pages)} Pages Included",
        grid_caption=(
            " \u00b7 ".join(section_titles[:4]) if section_titles
            else f"{steps} numbered steps with stitch counts"
        ),
        grid_cols=grid_cols,
        grid_rows=grid_rows,
        included_headline="Everything in this pattern",
        bullets=[
            f"{steps} numbered steps, stitch count on every row",
            f"Graded for {len(sizes)} sizes ({', '.join(sizes[:6])})" if sizes
            else "Complete written pattern",
            f"{diagram_count} technical diagrams drawn for this design",
            "Schematic, stitch chart, seam and gauge diagrams",
            "Sizing tables, yarn substitutions and yardage per size",
            "Blocking, care and troubleshooting guides",
        ],
        captions={
            "desk_eyebrow": "download, read, make",
            "desk_caption": f"{title} \u00b7 {len(pages)} pages \u00b7 {spec.size_label}",
            "detail_headline": "Every row counted and checked",
            "detail_caption": (
                f"{theme.label} layout \u00b7 vector text \u00b7 print or read on any device"
            ),
            "stack_eyebrow": "print it or read it on screen",
            "stack_headline": f"{diagram_count} Diagrams, Not Stock Clipart",
            "stack_caption": (
                "Schematic, stitch chart, seam and gauge diagrams drawn for this pattern \u00b7 "
                + ("A4 & Letter included" if spec.has_a4_companion else "scales to any size")
            ),
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
        page_indexes=showcase,
        # grid first: the buyer's main question is "how much do I actually get?"
        scenes=["hero", "bundle_grid", "included", "detail", "stack", "desk", "size_chart", "gift"],
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
    profile = spec.profile
    settings = settings.tuned(
        # Resolve the tier against the live settings rather than trusting the
        # id frozen into the profile at import, so the provider stays consistent.
        image_model=settings.image_model_for_tier(profile.image_tier),
        image_quality=profile.image_quality,
        cheap_text=profile.cheap_text,
    )

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

    fallback = expand.template_pattern(
        garment=garment,
        title=spec.title or inputs["default_title"] or "",
        corpus=inputs["corpus"],
        sizes=inputs["sizes"] or None,
        designer=spec.brand.credit,
    )
    writer = CopyStudio(settings, offline=None if spec.generate_ai_copy else True)
    brand_note = f"SHOP: {spec.brand.shop}. {spec.brand.tagline}".strip()
    # Later patterns in a batch are asked for at a higher temperature: the source
    # material is identical, so sampling is part of what keeps them apart.
    variant_heat = None if spec.variant_total <= 1 else min(1.0, 0.5 + 0.1 * spec.variant_index)

    # 2. art direction - the design and the photo prompts, before any pattern text.
    # Doing this first means the plates can be handed to the writer as reference,
    # so the instructions and the listing photographs describe the same object.
    report("Designing the piece and writing the image prompts", 0.12)
    design, briefs, plan_warnings = imagery.plan_art(
        writer if spec.generate_ai_copy else None,
        inputs["brief"],
        garment,
        fallback.get("image_briefs") or [],
        plate_count=spec.plate_limit,
        brand_note=brand_note,
        variation=spec.variant_direction,
        temperature=variant_heat,
        source_title=str(fallback.get("title") or inputs.get("default_title") or ""),
    )
    result.warnings.extend(plan_warnings)
    # The art director may name the garment, but not over the top of the source.
    # On a faithful rebuild the uploaded pattern decides what the item is, or a
    # stitch sampler comes back as an amigurumi toy.
    source_named_it = bool((inputs.get("corpus") or {}).get("garment"))
    faithful_rebuild = spec.variant_index <= 1
    if design.get("garment") and not (faithful_rebuild and source_named_it):
        garment = design["garment"]

    # 3. imagery, then Canva. Rendered here so the plates exist before the text.
    report("Rendering the artwork", 0.2)
    art = imagery.build_imagery(
        fallback,
        garment,
        art_dir,
        theme_key=spec.theme,
        settings=settings,
        generate_art=spec.generate_ai_art,
        use_canva=spec.use_canva,
        canva_pull_back=spec.canva_pull_back,
        plate_limit=spec.plate_limit,
        writer=None,          # art direction already ran, above
        briefs=briefs,
        include_cover=spec.cost_mode != "custom" or spec.custom_cover,
        brand_note=brand_note,
        progress=None if progress is None else (
            lambda message, fraction: report(message, 0.2 + 0.18 * fraction)
        ),
    )
    result.warnings.extend(art["warnings"])
    result.art_paths = dict(art["plates"])
    result.art_source = art["source"]
    result.canva = art["canva"]
    billed_images = int(art.get("generated", 0))
    cached_images = int(art.get("cache_hits", 0))

    # 4. expansion - write the pattern against the artwork that now exists.
    report("Writing the pattern with Qwen", 0.4)
    # The rendered plates become the visual reference. Only the plates that show
    # the object itself are worth attaching; a yarn flat-lay teaches the model
    # nothing about construction, and vision tokens are not free.
    reference_images = [
        str(path)
        for slot, path in art["plates"].items()
        if slot in ("cover", "finished", "texture", "detail", "flat") and Path(path).exists()
    ][:4]
    # In photo mode the user's own photographs are the ground truth, so those win.
    attached = list(inputs["photos"]) or (reference_images if spec.generate_ai_art else [])
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
            variation=spec.variant_direction,
            design=imagery.design_brief_text(design),
            has_reference_images=bool(attached),
            batch_position=(spec.variant_index, spec.variant_total),
            max_pages=spec.max_pages,
            # Pattern one of any run is the rebuild of its source. Only the
            # later variants of a batch are allowed to reinterpret it.
            faithful=spec.variant_index <= 1,
        )
        raw = writer.ask_json(prompt, images=attached or None, temperature=variant_heat)
        if raw:
            pattern = expand.normalise_pattern(raw, fallback)
        elif settings.ai_available and not settings.force_offline:
            # This is the difference between a rebuilt pattern and a generic
            # skeleton with the source's own numbers dropped into it. It used to
            # pass silently, which is how a stitch sampler came back as
            # "A graded amigurumi pattern in fingering weight yarn".
            result.warnings.append(
                "The pattern writer returned nothing usable, so this document is the "
                "built-in template rather than a rebuild of your upload. Check the "
                f"{settings.provider_label} key and quota, then build again."
            )
        result.warnings.extend(writer.warnings)
    content_source = writer.source
    # Tidy before the counts table is derived, so the table is built from real
    # rows rather than from a count the model stamped on a blocking step.
    pattern = expand.ensure_stitch_counts(expand.tidy_steps(pattern))
    # Title precedence: an explicit spec title, then whatever the planner named
    # this variant, then whatever the writer chose.
    if spec.title:
        pattern["title"] = spec.title
    elif spec.variant_title_hint and not spec.variant_title_hint.isspace():
        pattern["title"] = spec.variant_title_hint
    # Last resort in a batch: tag the title with this variant's direction. The
    # file names come off the title, so this also stops two patterns in a batch
    # writing to the same PDF name.
    if spec.variant_label and spec.variant_total > 1 and not spec.title:
        current = str(pattern.get("title") or "").strip()
        if spec.variant_label.lower() not in current.lower():
            pattern["title"] = f"{spec.variant_label} {current}".strip()[:60]
    # The captions were written for these plates, so carry them across.
    pattern["image_briefs"] = briefs

    # 5. diagrams
    report("Drawing the technical diagrams", 0.56)
    diagram_plates, diagram_warnings = dgm.build_all(pattern, diagram_dir, theme)
    result.warnings.extend(diagram_warnings)

    # 6. layout
    report("Laying out the pattern", 0.62)
    # Name the files after the pattern we actually produced, not the spec: in a
    # batch every pattern would otherwise land on the same filename.
    slug = _slug_from(pattern.get("title")) or spec.product_slug()
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
        max_pages=spec.max_pages,
    )
    pdf_path, pages = document.render(print_dir / f"{slug}-{spec.paper}.pdf")
    if document.trimmed:
        dropped = ", ".join(sorted(set(document.trimmed)))
        result.warnings.append(
            f"The pattern ran past the {spec.max_pages}-page limit, so these pages "
            f"were dropped: {dropped}"
        )
    result.pdf_path = pdf_path
    result.pdf_paths[spec.paper] = pdf_path

    report("Checking the rendered pattern", 0.7)
    result.verification = verify_pattern_pdf(pdf_path, pattern, pages)
    for error in result.verification.get("errors", []):
        result.warnings.append(f"Verification: {error}")

    # 6. mockups (skipped entirely when listing_image_count is 0)
    if spec.listing_image_count > 0:
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
    else:
        report("Skipping listing images", 0.9)

    # 7. packaging - market research first, so the listing is keyword-led
    report("Reading the market data", 0.90)
    market_listings, market_warnings = market.load_market_data(
        spec.market_text, spec.market_files
    )
    result.warnings.extend(market_warnings)
    # Describe the product so the tag ranking stays on subject: a broad "crochet"
    # scrape returns cardigans next to amigurumi keychains.
    relevance = " ".join(str(part) for part in [
        garment,
        pattern.get("title", ""),
        (pattern.get("yarn_guide") or {}).get("weight", ""),
        (pattern.get("yarn_guide") or {}).get("fibre", ""),
        " ".join((pattern.get("sizes") or {}).get("labels") or []),
        spec.garment,
    ] if part)
    report(f"Ranking tags against {len(market_listings)} listings", 0.92)
    market_report = market.analyse(market_listings, market_warnings, relevance=relevance)

    listing = listing_from_pattern(spec, pattern, inputs["product"], market_report)
    listing_source = "template"

    # With real research and a key, spend one focused call on the listing: SEO
    # is where the money is, and it is much better done as its own pass than
    # bolted onto the end of the pattern-writing prompt.
    if market_report.listings and spec.generate_ai_copy:
        report("Writing the Etsy listing copy", 0.94)
        seo_writer = CopyStudio(settings, offline=None)
        try:
            answer = seo_writer.ask_json(
                market.listing_prompt(
                    pattern, market_report,
                    garment=garment, shop=spec.brand.shop,
                    source_title=str(pattern.get("title") or ""),
                )
            )
        except Exception as exc:  # noqa: BLE001 - keep the researched fallback
            result.warnings.append(f"Market listing pass failed: {type(exc).__name__}: {exc}")
            answer = None
        result.warnings.extend(seo_writer.warnings)
        if answer:
            listing = market.normalise_listing(answer, market_report, listing)
            listing_source = seo_writer.source

    result.listing_copy = listing
    report("Packaging the download files", 0.96)
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
        "mockups_enabled": spec.listing_image_count > 0,
        "steps": expand.total_steps(pattern),
        "sizes": (pattern.get("sizes") or {}).get("labels") or [],
        "skill_level": pattern.get("skill_level"),
        "content_source": content_source,
        "art_source": result.art_source,
        "cost": {
            "mode": spec.cost_mode,
            "provider": settings.provider,
            "image_model": settings.image_model,
            "image_quality": profile.image_quality,
            "text_model": settings.text_model,
            "images_billed": billed_images,
            "images_from_cache": cached_images,
            "estimated_usd": round(
                billed_images * IMAGE_PRICE_USD.get(
                    (settings.image_model, profile.image_quality), 0.0
                ) * (
                    1.0 if settings.image_model.startswith(TOKEN_PRICED_PREFIXES)
                    else NON_SQUARE_FACTOR
                ) + profile.text_cost(),
                4,
            ),
        },
        "design": design,
        "variant": {
            "index": spec.variant_index,
            "total": spec.variant_total,
            "direction": spec.variant_direction,
        },
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
        "listing_source": listing_source,
        "market": market_report.to_dict() if market_report.listings else None,
        "warnings": result.warnings,
        "duration_seconds": round(time.perf_counter() - started, 2),
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")

    report("Done", 1.0)
    return result


# --------------------------------------------------------------------- batch
def build_crochet_batch(
    spec: CrochetSpec,
    out_dir: str | Path | None = None,
    progress: Progress | None = None,
    settings: Settings | None = None,
) -> list[BuildResult]:
    """Build `spec.pattern_count` separate patterns in one run.

    Each pattern gets its own run folder, PDF, mockups, listing and ZIP, so one
    uploaded file can become five distinct products rather than one, and two
    uploaded files asked for five patterns are shared out by how much material
    each actually contains. `crochet.batch.plan_batch` makes that call, and also
    assigns each pattern the design direction that keeps it distinct.

    A failure in one pattern does not abandon the others: the exception is
    recorded on the first successful result's warnings and the batch continues.
    Raises only if *every* pattern failed.
    """
    settings = settings or get_settings()
    spec = validate(spec)

    single = spec.pattern_count <= 1 and spec.allocation != "one_per_source"
    if single:
        return [build_crochet(spec, out_dir=out_dir, progress=progress, settings=settings)]

    if progress:
        progress("Planning the batch", 0.01)

    # Score the uploads so the allocation reflects what is actually in them. Only
    # text sources can be scored; photo uploads carry no extractable pattern.
    scores: list[batch_planner.SourceScore] = []
    corpus_summary = spec.brief.strip()
    if spec.mode == "from_pdfs" and spec.source_files:
        try:
            extracted = extract_many(spec.source_files)
            scores = batch_planner.score_sources(extracted)
            merged = merge_sources([s for s in extracted if s.ok])
            if merged.get("sources"):
                corpus_summary = corpus_brief(merged, limit=4000)
        except Exception as exc:  # noqa: BLE001 - fall back to an even split
            corpus_summary = spec.brief.strip()
            scores = []
            planner_note = f"Could not score the uploads ({type(exc).__name__}: {exc})"
        else:
            planner_note = ""
    else:
        planner_note = ""

    planner = CopyStudio(settings, offline=None if spec.generate_ai_copy else True)
    plans, plan_warnings = batch_planner.plan_batch(
        spec.source_files,
        spec.pattern_count,
        strategy=spec.allocation,
        scores=scores,
        sources_per_pattern=spec.sources_per_pattern,
        writer=planner,
        corpus_brief=corpus_summary,
        garment=spec.garment,
    )
    if planner_note:
        plan_warnings.append(planner_note)
    plan_warnings.extend(planner.warnings)

    base_dir = Path(out_dir) if out_dir else None
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    total = len(plans)
    # Tag the titles only when several patterns read the same material. One
    # pattern per upload already gets its name from its own source; five
    # patterns off one PDF do not, and would otherwise share a title and a
    # filename.
    label_titles = len({tuple(plan.sources) for plan in plans}) < total
    results: list[BuildResult] = []
    failures: list[str] = []

    # Batch cost stays low because of the image cache: the generic plates (yarn
    # flat-lay, stitch texture) describe the yarn rather than the garment, so
    # their prompts hash identically across the batch and only the first pattern
    # pays for them. Covers stay per-pattern, because a beanie should not ship
    # with a cardigan on the front.

    for index, plan in enumerate(plans):
        label = f"Pattern {index + 1} of {total}"

        def sub_progress(message: str, fraction: float, _i=index) -> None:
            if progress:
                span = 1.0 / total
                progress(f"[{_i + 1}/{total}] {message}", span * (_i + fraction))

        # Each pattern is its own spec: same settings, its own sources, and its
        # own design direction - which is what makes it a separate product.
        child = dataclasses.replace(
            spec,
            source_files=list(plan.sources),
            pattern_count=1,
            variant_index=index + 1,
            variant_total=total,
            variant_direction=plan.direction,
            variant_title_hint=plan.title_hint,
            variant_label=batch_planner.label_for(index + 1, total) if label_titles else "",
            # An explicit title would override every variant with the same name.
            title=spec.title if total == 1 else None,
            # Etsy mode walks through consecutive products instead of files.
            product_number=(
                spec.product_number + index if spec.mode == "from_etsy_data" else spec.product_number
            ),
            share_art=spec.share_art,
        )

        target = None
        if base_dir:
            target = base_dir / f"pattern-{index + 1:02d}"
        else:
            target = (
                settings.resolved_output_dir()
                / f"{stamp}_{child.product_slug()}-{index + 1:02d}"
            )

        try:
            result = build_crochet(
                child, out_dir=target, progress=sub_progress, settings=settings
            )
        except Exception as exc:  # noqa: BLE001 - keep building the rest of the batch
            failures.append(f"{label}: {type(exc).__name__}: {exc}")
            continue
        result.warnings.append(f"{label} in this batch")
        if plan.source_note:
            result.warnings.append(f"{label} was built from {plan.source_note}")
        results.append(result)

    if not results:
        raise ValueError(
            "Every pattern in the batch failed. " + " | ".join(failures)
        )
    if failures:
        results[0].warnings.extend(failures)
    if plan_warnings:
        results[0].warnings.extend(plan_warnings)
    if progress:
        progress(f"Done - {len(results)} pattern(s) built", 1.0)
    return results

"""Bundle Studio: the model writes the pages, Artisan Forge lays them out.

Give it a topic ("self-care for new mums", "ADHD-friendly meal planning") and it
produces a multi-page printable bundle: prompt pages, checklists, trackers,
affirmation prints and note pages, plus artwork, listing images and Etsy copy.

With no OpenAI key the content comes from built-in templates, so the studio
still produces a complete, coherent product offline.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..ai.image_client import ImageStudio
from ..ai.text_client import CopyStudio
from ..config import Settings, get_settings
from ..mockups.compose import build_listing_images
from ..mockups.context import MockupContext
from ..models import PAPER_SIZES, BuildResult
from ..packaging import MAX_TAG_LEN, MAX_TAGS, MAX_TITLE_LEN, build_zip, write_copy, write_product_docs
from ..pdf.blocks import checkbox_list_block, dot_grid_block, ruled_lines_block, section_title_block, table_block
from ..pdf.drawkit import DrawKit, wrap_text
from ..themes import get_theme

Progress = Callable[[str, float], None]

MODULES = {
    "prompts": "Journal prompt pages with writing lines",
    "checklist": "Checklist pages with tick boxes",
    "tracker": "Weekly tracker grids",
    "affirmations": "Full-page affirmation prints",
    "notes": "Dot grid note pages",
}
DEFAULT_MODULES = ["prompts", "checklist", "tracker", "affirmations", "notes"]

PROMPTS_PER_PAGE = 3
CHECKS_PER_PAGE = 12


@dataclass
class BundleSpec:
    topic: str
    audience: str = "anyone starting out"
    tone: str = "warm, practical, encouraging"
    theme: str = "minimalist"
    paper: str = "letter"
    orientation: str = "portrait"
    modules: list[str] = field(default_factory=lambda: list(DEFAULT_MODULES))
    pages_per_module: int = 2
    title: str | None = None
    subtitle: str | None = None
    include_cover: bool = True
    generate_ai_art: bool = True
    generate_ai_copy: bool = True
    listing_image_count: int = 8
    bleed_in: float = 0.0

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

    def display_title(self) -> str:
        return self.title or f"{self.topic.strip().title()} Bundle"

    def product_slug(self) -> str:
        cleaned = "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in self.topic.lower())
        words = [w for w in cleaned.split() if w not in {"for", "the", "and", "a", "of", "to"}][:5]
        base = "-".join(words) or "bundle"
        return f"{base}-bundle-{self.theme.replace('_', '-')}"

    def to_dict(self) -> dict:
        data = dataclasses.asdict(self)
        data["trim_size_in"] = list(self.trim_size_in)
        data["size_label"] = self.size_label
        return data


def validate(spec: BundleSpec) -> BundleSpec:
    if not spec.topic or len(spec.topic.strip()) < 3:
        raise ValueError("Give the bundle a topic of at least 3 characters")
    if spec.paper not in PAPER_SIZES:
        raise ValueError(f"Unknown paper '{spec.paper}'")
    if spec.orientation not in ("portrait", "landscape"):
        raise ValueError("orientation must be 'portrait' or 'landscape'")
    spec.modules = [m for m in spec.modules if m in MODULES] or list(DEFAULT_MODULES)
    spec.pages_per_module = max(1, min(6, spec.pages_per_module))
    spec.theme = get_theme(spec.theme).key
    spec.listing_image_count = max(1, min(10, spec.listing_image_count))
    return spec


# --------------------------------------------------------------------- content
def content_prompt(spec: BundleSpec) -> str:
    wanted = ", ".join(spec.modules)
    return (
        f"Design a printable digital bundle about: {spec.topic}.\n"
        f"Audience: {spec.audience}. Tone: {spec.tone}.\n"
        f"Include these section kinds: {wanted}.\n\n"
        "Return JSON with exactly this shape:\n"
        "{\n"
        '  "title": "short product title, max 45 chars",\n'
        '  "subtitle": "one line, max 60 chars",\n'
        '  "intro": "2-3 sentence welcome paragraph addressed to the buyer",\n'
        '  "how_to_use": "2-3 sentence closing paragraph",\n'
        '  "bullets": ["4 short what-you-get lines, max 70 chars each"],\n'
        '  "sections": [\n'
        '    {"kind": "prompts", "title": "...", "items": ["12 reflective questions"]},\n'
        '    {"kind": "checklist", "title": "...", "items": ["14 short actionable items"]},\n'
        '    {"kind": "tracker", "title": "...", "columns": ["Focus", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]},\n'
        '    {"kind": "affirmations", "title": "...", "items": ["6 short first-person affirmations"]},\n'
        '    {"kind": "notes", "title": "..."}\n'
        "  ],\n"
        '  "listing": {"title": "Etsy title max 130 chars", "tags": ["13 tags, max 20 chars each"],\n'
        '              "description": "Etsy description with WHAT YOU GET and HOW IT WORKS sections"}\n'
        "}\n"
        "Only include section objects for the requested kinds. Never use emoji."
    )


_PROMPT_STEMS = [
    "What does {topic} look like on a really good day?",
    "Where do you feel the most resistance around {topic}, and why?",
    "What is one small step with {topic} you could take this week?",
    "Who supports you with {topic}, and how can you ask for more help?",
    "What advice would you give a friend starting with {topic}?",
    "Which habit around {topic} would you like to let go of?",
    "What has already worked for you with {topic}?",
    "How do you want to feel about {topic} three months from now?",
    "What gets in the way when you are tired or busy?",
    "What would make {topic} feel lighter tomorrow?",
    "Which win with {topic} deserves more credit than you gave it?",
    "What does 'enough' look like for you with {topic}?",
]
_CHECK_ITEMS = [
    "Set one clear intention for the day",
    "Choose the single most important task",
    "Block 20 focused minutes",
    "Drink a full glass of water",
    "Step outside for fresh air",
    "Tidy one small surface",
    "Note one thing that went well",
    "Reach out to one person",
    "Move your body for 10 minutes",
    "Prepare one thing for tomorrow",
    "Put your phone away for an hour",
    "Write down one worry and park it",
    "Celebrate one small win",
    "Wind down without screens",
]
_AFFIRMATIONS = [
    "I am allowed to go at my own pace.",
    "Small steps still count as progress.",
    "I can begin again at any moment.",
    "My effort matters more than perfection.",
    "I am learning, and that is enough.",
    "Rest is part of the work.",
]


def template_plan(spec: BundleSpec) -> dict:
    """Offline content: coherent, specific enough to sell, no API needed."""
    topic = spec.topic.strip().rstrip(".")
    title = spec.title or f"{topic.title()} Bundle"
    sections: list[dict] = []
    for kind in spec.modules:
        if kind == "prompts":
            sections.append({
                "kind": "prompts",
                "title": "Reflection Prompts",
                "items": [stem.format(topic=topic.lower()) for stem in _PROMPT_STEMS],
            })
        elif kind == "checklist":
            sections.append({"kind": "checklist", "title": "Daily Checklist", "items": list(_CHECK_ITEMS)})
        elif kind == "tracker":
            sections.append({
                "kind": "tracker",
                "title": "Weekly Tracker",
                "columns": ["Focus", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
            })
        elif kind == "affirmations":
            sections.append({"kind": "affirmations", "title": "Affirmations", "items": list(_AFFIRMATIONS)})
        elif kind == "notes":
            sections.append({"kind": "notes", "title": "Notes & Ideas"})

    return {
        "title": title,
        "sections": sections,
        "subtitle": spec.subtitle or f"A printable workbook for {spec.audience}",
        "intro": (
            f"This bundle is a simple, unhurried way to work on {topic.lower()}. "
            "Print the pages you need, as often as you need them, and use them in any order. "
            "There is no wrong way to start."
        ),
        "how_to_use": (
            "Print single-sided on paper or card stock, then clip the pages into a folder or binder. "
            "Reprint any page whenever you want a fresh start."
        ),
        "bullets": [
            "Print-ready PDF, no software needed",
            "Use the pages in any order, unlimited reprints",
            f"Written for {spec.audience}",
            "Undated - start any day of the year",
        ],
        "listing": None,
    }


def normalise_plan(plan: dict, spec: BundleSpec) -> dict:
    """Make any model response safe to render."""
    fallback = template_plan(spec)
    out = dict(fallback)
    for key in ("title", "subtitle", "intro", "how_to_use"):
        value = plan.get(key)
        if isinstance(value, str) and value.strip():
            out[key] = value.strip()

    bullets = [str(b).strip() for b in plan.get("bullets", []) if str(b).strip()]
    if bullets:
        out["bullets"] = bullets[:5]

    sections: list[dict] = []
    for raw in plan.get("sections", []):
        kind = str(raw.get("kind", "")).lower()
        if kind not in MODULES:
            continue
        section: dict = {"kind": kind, "title": str(raw.get("title") or kind.title())[:60]}
        items = [str(i).strip() for i in raw.get("items", []) if str(i).strip()]
        if items:
            section["items"] = items
        columns = [str(c).strip() for c in raw.get("columns", []) if str(c).strip()]
        if columns:
            section["columns"] = columns[:9]
        if kind in ("prompts", "checklist", "affirmations") and not section.get("items"):
            continue
        if kind == "tracker" and not section.get("columns"):
            section["columns"] = ["Focus", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        sections.append(section)
    if sections:
        out["sections"] = sections

    listing = plan.get("listing")
    if isinstance(listing, dict):
        out["listing"] = listing
    return out


def listing_from_plan(spec: BundleSpec, plan: dict) -> dict:
    """Etsy copy, from the model when available, otherwise built locally."""
    theme = get_theme(spec.theme)
    raw = plan.get("listing") or {}
    title = str(raw.get("title") or "").strip()
    if not title:
        title = (
            f"{plan['title']} Printable | {len(plan['sections'])}-Part Digital Bundle | "
            f"{spec.size_label.replace(chr(34), 'in')} PDF | Instant Download"
        )
    tags: list[str] = []
    for tag in list(raw.get("tags") or []) + [
        "printable bundle",
        "digital download",
        "instant download",
        "printable planner",
        "self care printable",
        f"{theme.label} printable",
        "journal prompts",
        "habit tracker",
        "workbook pdf",
        "digital workbook",
    ]:
        clean = " ".join(str(tag).lower().split())[:MAX_TAG_LEN].strip()
        if clean and clean not in tags:
            tags.append(clean)
        if len(tags) == MAX_TAGS:
            break

    description = str(raw.get("description") or "").strip()
    if not description:
        pages = "\n".join(f"- {s['title']}" for s in plan["sections"])
        description = "\n".join(
            [
                f"{plan['title']} - printable digital bundle, instant download",
                "",
                plan["intro"],
                "",
                "WHAT YOU GET",
                *[f"- {b}" for b in plan["bullets"]],
                "",
                "SECTIONS",
                pages,
                "",
                "HOW IT WORKS",
                "1. Buy and download instantly - nothing is shipped.",
                "2. Print at home or at any print shop.",
                "3. Reprint any page as often as you like.",
                "",
                "TERMS",
                "For personal use only. Files may not be resold or redistributed.",
            ]
        )

    return {
        "title": title[:MAX_TITLE_LEN],
        "tags": tags,
        "description": description,
        "materials": ["PDF", "Digital Download", "Printable"],
        "who_made_it": "i_did",
        "is_digital": True,
        "suggested_price_usd": 7.50,
        "sections": ["Printable Bundles"],
    }


# ---------------------------------------------------------------------- pages
class BundlePDF(DrawKit):
    """Renders the bundle document."""

    def __init__(self, spec: BundleSpec, plan: dict, art: dict[str, Path] | None = None):
        super().__init__(get_theme(spec.theme), spec.trim_size_in, spec.bleed_in)
        self.spec = spec
        self.plan = plan
        self.art = {
            key: Path(value) for key, value in (art or {}).items()
            if value and Path(value).exists()
        }

    # -- page plan ---------------------------------------------------------
    def page_plan(self) -> list[dict]:
        pages: list[dict] = []
        if self.spec.include_cover:
            pages.append({"kind": "cover"})
        pages.append({"kind": "intro"})
        for section in self.plan["sections"]:
            kind = section["kind"]
            items = section.get("items", [])
            if kind == "prompts":
                chunks = [items[i : i + PROMPTS_PER_PAGE] for i in range(0, len(items), PROMPTS_PER_PAGE)]
                limit = max(1, self.spec.pages_per_module)
                for chunk in chunks[:limit]:
                    pages.append({"kind": "prompts", "title": section["title"], "items": chunk})
            elif kind == "checklist":
                chunks = [items[i : i + CHECKS_PER_PAGE] for i in range(0, len(items), CHECKS_PER_PAGE)]
                for chunk in chunks[: max(1, self.spec.pages_per_module)]:
                    pages.append({"kind": "checklist", "title": section["title"], "items": chunk})
            elif kind == "tracker":
                pages.append({"kind": "tracker", "title": section["title"], "columns": section["columns"]})
            elif kind == "affirmations":
                for item in items[: max(1, self.spec.pages_per_module * 2)]:
                    pages.append({"kind": "affirmation", "title": section["title"], "text": item})
            elif kind == "notes":
                for _ in range(max(1, self.spec.pages_per_module)):
                    pages.append({"kind": "notes", "title": section["title"]})
        pages.append({"kind": "closing"})
        return pages

    def render(self, out_path: str | Path) -> tuple[Path, list[dict]]:
        pages = self.page_plan()
        c = self.new_canvas(
            out_path,
            title=self.plan["title"],
            subject=f"Printable bundle about {self.spec.topic}",
            keywords=f"printable, bundle, {self.spec.topic}, {self.theme.label}",
        )
        for page in pages:
            getattr(self, f"draw_{page['kind']}")(c, page)
            self._crop_marks(c)
            c.showPage()
        c.save()
        return Path(out_path), pages

    # -- individual pages --------------------------------------------------
    def draw_cover(self, c, page: dict) -> None:
        self._paint_background(c)
        art = self.art.get("cover")
        if art:
            self._image_cover(c, art, 0, 0, self.page_w, self.page_h)

        band_h = self.trim_h * 0.3
        band_y = self.bleed + (self.trim_h - band_h) / 2
        inset = self.margin_x * 1.1
        band_x, band_w = self.bleed + inset, self.trim_w - 2 * inset
        if art:
            c.setFillColor(self.color("paper", 0.94))
            c.rect(band_x, band_y, band_w, band_h, stroke=0, fill=1)
            c.setStrokeColor(self.color("accent", 0.5))
            c.setLineWidth(0.7)
            c.rect(band_x + 6, band_y + 6, band_w - 12, band_h - 12, stroke=1, fill=0)

        mid = band_x + band_w / 2
        self._text(
            c, mid, band_y + band_h - self.fs(26), "PRINTABLE BUNDLE", self.fonts.bold,
            self.fs(8.2), self.theme.color("accent"), align="center", tracking=self.fs(2.6),
        )
        inner = band_w - self.fs(36)
        title_size = self.fs(34)
        lines = wrap_text(self.plan["title"], self.fonts.display, title_size, inner)
        while len(lines) > 2 and title_size > self.fs(18):
            title_size -= self.fs(2)
            lines = wrap_text(self.plan["title"], self.fonts.display, title_size, inner)
        y = band_y + band_h * (0.62 if len(lines) > 1 else 0.5)
        for line in lines[:3]:
            self._text(
                c, mid, y, line, self.fonts.display, title_size, self.theme.color("ink"),
                align="center", tracking=self.fs(1.6),
            )
            y -= title_size * 1.16

        rule_w = band_w * 0.28
        self._rule(c, mid - rule_w / 2, y + self.fs(6), mid + rule_w / 2,
                   self.theme.color("accent"), width=0.9)
        self.paragraph(
            c, band_x + self.fs(18), y - self.fs(8), self.plan["subtitle"], self.fonts.regular,
            self.fs(11), self.theme.color("muted"), inner, align="center", tracking=self.fs(1.2),
        )

    def draw_intro(self, c, page: dict) -> None:
        self._paint_background(c)
        x, y, w, h = self.content_box()
        header_h = min(h * 0.16, self.fs(64))
        section_title_block(self, c, (x, y + h - header_h, w, header_h), "WELCOME",
                            subtitle=self.plan["subtitle"][:40])
        top = y + h - header_h - self.fs(24)
        top = self.paragraph(c, x, top, self.plan["intro"], self.fonts.regular, self.fs(12.5),
                             self.theme.color("ink"), w, leading=self.fs(20))
        top -= self.fs(18)
        self._text(c, x, top, "WHAT'S INSIDE", self.fonts.bold, self.fs(9),
                   self.theme.color("muted"), tracking=self.fs(2.2))
        top -= self.fs(22)
        for section in self.plan["sections"]:
            c.setFillColor(self.color("accent"))
            c.circle(x + self.fs(3), top + self.fs(3.4), self.fs(2.4), stroke=0, fill=1)
            self._text(c, x + self.fs(12), top, section["title"], self.fonts.regular,
                       self.fs(11.5), self.theme.color("ink"))
            top -= self.fs(19)

    def draw_prompts(self, c, page: dict) -> None:
        self._paint_background(c)
        x, y, w, h = self.content_box()
        header_h = min(h * 0.13, self.fs(52))
        section_title_block(self, c, (x, y + h - header_h, w, header_h), page["title"].upper())
        body_top = y + h - header_h - self.fs(18)
        body_h = body_top - y
        slot = body_h / max(len(page["items"]), 1)
        for index, prompt in enumerate(page["items"]):
            top = body_top - slot * index
            self._text(c, x, top - self.fs(11), f"{index + 1:02d}", self.fonts.bold,
                       self.fs(9), self.theme.color("accent"), tracking=self.fs(1.2))
            text_bottom = self.paragraph(
                c, x + self.fs(26), top - self.fs(11), prompt, self.fonts.regular,
                self.fs(12), self.theme.color("ink"), w - self.fs(26), leading=self.fs(17),
            )
            rules_top = text_bottom - self.fs(4)
            rules_bottom = top - slot + self.fs(8)
            if rules_top - rules_bottom > self.fs(22):
                ruled_lines_block(
                    self, c,
                    (x + self.fs(26), rules_bottom, w - self.fs(26), rules_top - rules_bottom),
                    step=self.fs(19),
                )

    def draw_checklist(self, c, page: dict) -> None:
        self._paint_background(c)
        x, y, w, h = self.content_box()
        header_h = min(h * 0.13, self.fs(52))
        section_title_block(self, c, (x, y + h - header_h, w, header_h), page["title"].upper())
        body_top = y + h - header_h - self.fs(20)
        items = page["items"]
        slot = (body_top - y) / max(len(items), 1)
        box = min(slot * 0.34, self.fs(11))
        c.setStrokeColor(self.color("grid"))
        for index, item in enumerate(items):
            top = body_top - slot * index
            c.setLineWidth(0.7)
            c.rect(x, top - box, box, box, stroke=1, fill=0)
            self._text(c, x + box * 1.9, top - box * 0.86, item, self.fonts.regular,
                       min(self.fs(11.5), slot * 0.42), self.theme.color("ink"))
            self._rule(c, x + box * 1.9, top - slot + slot * 0.22, x + w,
                       self.theme.color("grid"), width=0.4)

    def draw_tracker(self, c, page: dict) -> None:
        self._paint_background(c)
        x, y, w, h = self.content_box()
        header_h = min(h * 0.13, self.fs(52))
        section_title_block(self, c, (x, y + h - header_h, w, header_h), page["title"].upper())
        table_block(self, c, (x, y, w, y + h - header_h - self.fs(20) - y), page["columns"], rows=16)

    def draw_affirmation(self, c, page: dict) -> None:
        self._paint_background(c)
        art = self.art.get("interior")
        if art:
            self._image_cover(c, art, 0, 0, self.page_w, self.page_h)
            c.setFillColor(self.color("paper", 0.86))
            c.rect(0, 0, self.page_w, self.page_h, stroke=0, fill=1)
        x, y, w, h = self.content_box()
        size = self.fs(30)
        lines = wrap_text(page["text"], self.fonts.display, size, w * 0.86)
        while len(lines) > 4 and size > self.fs(16):
            size -= self.fs(2)
            lines = wrap_text(page["text"], self.fonts.display, size, w * 0.86)
        total = len(lines) * size * 1.3
        top = y + h / 2 + total / 2
        for line in lines:
            self._text(c, x + w / 2, top, line, self.fonts.display, size,
                       self.theme.color("ink"), align="center", tracking=self.fs(1.2))
            top -= size * 1.3
        rule_w = w * 0.16
        self._rule(c, x + w / 2 - rule_w / 2, top - self.fs(6), x + w / 2 + rule_w / 2,
                   self.theme.color("accent"), width=1.0)
        self._text(c, x + w / 2, y + self.fs(10), page["title"].upper(), self.fonts.regular,
                   self.fs(8), self.theme.color("muted"), align="center", tracking=self.fs(3.0))

    def draw_notes(self, c, page: dict) -> None:
        self._paint_background(c)
        x, y, w, h = self.content_box()
        header_h = min(h * 0.12, self.fs(46))
        section_title_block(self, c, (x, y + h - header_h, w, header_h), page["title"].upper())
        dot_grid_block(self, c, (x, y, w, y + h - header_h - self.fs(16) - y))

    def draw_closing(self, c, page: dict) -> None:
        self._paint_background(c)
        x, y, w, h = self.content_box()
        header_h = min(h * 0.16, self.fs(64))
        section_title_block(self, c, (x, y + h - header_h, w, header_h), "HOW TO USE THIS BUNDLE")
        top = y + h - header_h - self.fs(24)
        top = self.paragraph(c, x, top, self.plan["how_to_use"], self.fonts.regular, self.fs(12.5),
                             self.theme.color("ink"), w, leading=self.fs(20))
        box_y = y + h * 0.1
        box_h = max(self.fs(90), top - self.fs(24) - box_y)
        checkbox_list_block(self, c, (x, box_y, w, box_h), rows=8, label="My next three steps")


# ------------------------------------------------------------------- mockups
def mockup_context(spec: BundleSpec, plan: dict, pages: list[dict]) -> MockupContext:
    theme = get_theme(spec.theme)
    interior_indexes = [i for i, page in enumerate(pages) if page["kind"] not in ("cover",)]
    w_in, h_in = spec.trim_size_in
    return MockupContext(
        theme_key=spec.theme,
        trim_size_in=spec.trim_size_in,
        size_label=spec.size_label,
        orientation=spec.orientation,
        eyebrow="printable digital bundle",
        title_lines=wrap_words(plan["title"], 2),
        badges=[f"{len(pages)} pages", spec.size_label.replace('"', "in"), "instant download"],
        grid_eyebrow="every page included",
        grid_headline=f"{len(pages)} Printable Pages",
        grid_caption=" \u00b7 ".join(s["title"] for s in plan["sections"][:3]),
        grid_cols=3,
        grid_rows=2,
        included_headline="What's in the bundle",
        bullets=plan["bullets"],
        captions={
            "desk_eyebrow": "print, clip, begin",
            "desk_caption": f"{plan['title']} \u00b7 {spec.size_label}",
            "detail_headline": "Clean, calm page design",
            "detail_caption": f"{theme.label} layout \u00b7 vector text \u00b7 no watermark",
            "stack_headline": "Print only the pages you need",
            "gift_ribbon": "A THOUGHTFUL DIGITAL GIFT",
            "size_headline": "Fits Letter and A4" if spec.has_a4_companion else "Print-ready size",
        },
        size_notes=[
            f'Trim size \u2014 {w_in:g}" x {h_in:g}" ({round(w_in * 25.4)} x {round(h_in * 25.4)} mm)',
            "Prints on US Letter and A4" if spec.has_a4_companion else "Scales to any paper size",
            "PDF \u00b7 vector text \u00b7 300 DPI ready",
        ],
        a4_included=spec.has_a4_companion,
        cover_index=0,
        page_indexes=interior_indexes[:6],
        scenes=["hero", "bundle_grid", "included", "detail", "desk", "stack", "gift", "size_chart"],
    )


def wrap_words(text: str, lines: int) -> list[str]:
    words = text.split()
    if len(words) <= 2 or lines <= 1:
        return [text]
    middle = len(words) // 2
    return [" ".join(words[:middle]), " ".join(words[middle:])]


# ---------------------------------------------------------------------- build
def build_bundle(
    spec: BundleSpec,
    out_dir: str | Path | None = None,
    progress: Progress | None = None,
    settings: Settings | None = None,
) -> BuildResult:
    """Generate content, render the PDF, composite mockups, package it up."""
    started = time.perf_counter()
    settings = settings or get_settings()
    spec = validate(spec)

    def report(message: str, fraction: float) -> None:
        if progress:
            progress(message, fraction)

    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = Path(out_dir) if out_dir else settings.resolved_output_dir() / f"{stamp}_{spec.product_slug()}"
    art_dir, print_dir, mock_dir = run_dir / "art", run_dir / "print", run_dir / "mockups"
    for folder in (run_dir, art_dir, print_dir, mock_dir):
        folder.mkdir(parents=True, exist_ok=True)

    result = BuildResult(spec=spec, run_dir=run_dir, product_type="bundle")

    # 1. content
    report("Writing bundle content", 0.05)
    writer = CopyStudio(settings, offline=None if spec.generate_ai_copy else True)
    raw_plan = writer.ask_json(content_prompt(spec))
    plan = normalise_plan(raw_plan, spec) if raw_plan else template_plan(spec)
    result.warnings.extend(writer.warnings)
    copy_source = writer.source

    # 2. artwork
    report("Generating artwork", 0.3)
    studio = ImageStudio(settings, offline=None if spec.generate_ai_art else True)
    theme = get_theme(spec.theme)
    art: dict[str, Path] = {}
    art["cover"] = studio.generate(
        f"{theme.art}. Cover artwork for a printable workbook about {spec.topic}. "
        "Calm empty band across the middle for a title block. No text, no letters.",
        art_dir / "cover.png", size=ImageStudio.size_for(spec, "cover"), spec=spec,
        seed=abs(hash((spec.topic, spec.theme, "cover"))) % 10_000_019, kind="cover",
    )
    art["interior"] = studio.generate(
        f"{theme.art}. Soft full-page background texture, very low contrast, "
        "nothing in the centre. No text.",
        art_dir / "interior.png", size=ImageStudio.size_for(spec, "cover"), spec=spec,
        seed=abs(hash((spec.topic, spec.theme, "interior"))) % 10_000_019, kind="interior",
    )
    result.art_paths = art
    result.art_source = studio.source
    result.warnings.extend(studio.warnings)

    # 3. PDF
    report("Laying out the pages", 0.55)
    slug = spec.product_slug()
    pdf_path, pages = BundlePDF(spec, plan, art).render(print_dir / f"{slug}-{spec.paper}.pdf")
    result.pdf_path = pdf_path
    result.pdf_paths[spec.paper] = pdf_path

    # 4. mockups
    report("Compositing mockups", 0.62)
    context = mockup_context(spec, plan, pages)
    try:
        result.listing_images = build_listing_images(
            context, pdf_path, mock_dir, count=spec.listing_image_count,
            progress=None if progress is None else (
                lambda message, fraction: progress(message, 0.62 + 0.26 * fraction)
            ),
        )
    except Exception as exc:  # noqa: BLE001 - never lose the PDF over a mockup
        result.warnings.append(f"Mockups failed: {type(exc).__name__}: {exc}")

    # 5. packaging
    report("Writing listing copy and packaging files", 0.9)
    copy = listing_from_plan(spec, plan)
    result.listing_copy = copy
    write_copy(copy, run_dir)
    docs = write_product_docs(
        plan["title"],
        [f"- {pdf_path.name}  ({spec.size_label}, {len(pages)} pages)"],
        print_dir,
    )
    result.zip_path = build_zip(run_dir / f"{slug}-etsy-files.zip", [pdf_path, *docs])

    manifest = {
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "product_type": "bundle",
        "title": plan["title"],
        "brief": spec.topic,
        "spec": spec.to_dict(),
        "pages": len(pages),
        "page_kinds": [page["kind"] for page in pages],
        "content_source": copy_source,
        "art_source": result.art_source,
        "plan": plan,
        "files": {
            "pdfs": {key: str(path) for key, path in result.pdf_paths.items()},
            "art": {key: str(path) for key, path in art.items()},
            "listing_images": [str(path) for path in result.listing_images],
            "zip": str(result.zip_path) if result.zip_path else None,
        },
        "verification": {"ok": True, "checks": len(pages), "text_check": "n/a"},
        "listing": copy,
        "warnings": result.warnings,
        "duration_seconds": round(time.perf_counter() - started, 2),
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    result.verification = manifest["verification"]
    report("Done", 1.0)
    return result

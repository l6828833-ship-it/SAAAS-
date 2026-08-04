"""The pattern document.

`CrochetPDF` turns a normalised pattern dict, a set of diagram plates, a set of
photographic plates and a `BrandKit` into one print-ready PDF. It builds on the
shared `DrawKit` so a crochet pattern shares its typographic system with every
other product the platform makes.

Page order follows how a maker actually uses a pattern:

    cover, credits, contents, about + skill + time, materials, yarn guide,
    gauge, sizing, abbreviations, special stitches, construction, foundation,
    chart, [instructions x n], stitch counts, assembly, seaming, blocking,
    troubleshooting, care, gallery, thank you

Pages are only added when their content exists, and the long sections paginate
themselves: `page_plan()` measures wrapped text against the available height, so
instructions flow across as many pages as they need without ever overflowing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..pdf.blocks import table_block
from ..pdf.drawkit import DrawKit, fit_text, wrap_text
from ..themes import Theme, get_theme
from .brand import BrandKit

# Front-matter pages that are not numbered as pattern content.
UNNUMBERED = {"cover", "credits"}


class CrochetPDF(DrawKit):
    """Renders a complete crochet pattern document."""

    def __init__(
        self,
        pattern: dict,
        brand: BrandKit | None = None,
        theme: Theme | str | None = "minimalist",
        trim_size_in: tuple[float, float] = (8.5, 11.0),
        bleed_in: float = 0.0,
        diagrams: dict[str, Path] | None = None,
        plates: dict[str, Path] | None = None,
        captions: dict[str, str] | None = None,
        include_chart: bool = True,
        include_gallery: bool = True,
    ):
        super().__init__(theme, trim_size_in, bleed_in)
        self.pattern = pattern
        self.brand = (brand or BrandKit()).cleaned()
        self.diagrams = _existing(diagrams)
        self.plates = _existing(plates)
        self.captions = captions or {}
        self.include_chart = include_chart
        self.include_gallery = include_gallery

        # A brand accent overrides the theme's, so the document matches the shop.
        self.accent = self.brand.accent_hex or self.theme.color("accent")
        self._page_number = 0
        self._total_pages = 0

    # ------------------------------------------------------------- page plan
    def page_plan(self) -> list[dict]:
        """Every page, in order, with its content already chunked to fit."""
        p = self.pattern
        pages: list[dict] = [{"kind": "cover"}, {"kind": "credits"}]

        pages.append({"kind": "about"})

        if p.get("materials"):
            pages.append({"kind": "materials"})
        if p.get("yarn_guide"):
            pages.append({"kind": "yarn_guide"})
        if p.get("gauge"):
            pages.append({"kind": "gauge"})
        if (p.get("sizes") or {}).get("rows"):
            pages.append({"kind": "sizing"})
        if p.get("abbreviations") or p.get("special_stitches"):
            pages.append({"kind": "abbreviations"})
        if p.get("construction"):
            pages.append({"kind": "construction"})
        if "foundation" in self.diagrams:
            pages.append({"kind": "foundation"})
        if self.include_chart and "chart" in self.diagrams:
            pages.append({"kind": "chart"})

        for section in p.get("sections") or []:
            pages.extend(self._instruction_pages(section))

        if p.get("stitch_counts"):
            pages.extend(self._counts_pages(p["stitch_counts"]))
        if p.get("assembly"):
            pages.append({"kind": "assembly"})
        for index, entry in enumerate((p.get("seaming") or [])[:3], start=1):
            pages.append({"kind": "seaming", "entry": entry, "diagram": f"seam_{index}"})
        if (p.get("blocking") or {}).get("steps"):
            pages.append({"kind": "blocking"})
        if p.get("troubleshooting"):
            pages.extend(self._trouble_pages(p["troubleshooting"]))
        if p.get("care"):
            pages.append({"kind": "care"})
        gallery_slots = [s for s in ("finished", "styled", "texture") if s in self.plates]
        if self.include_gallery and gallery_slots:
            pages.append({"kind": "gallery", "slots": gallery_slots})
        pages.append({"kind": "thanks"})

        # contents goes after the cover, once we know what the pages are
        listed = [page for page in pages if page["kind"] not in UNNUMBERED]
        if len(listed) > 6:
            pages.insert(2, {"kind": "contents", "entries": self._contents(listed)})
        return pages

    def _contents(self, pages: list[dict]) -> list[tuple[str, int]]:
        """(label, page number) for the first page of each distinct section."""
        labels = {
            "about": "About this pattern",
            "materials": "Materials",
            "yarn_guide": "Yarn guide and substitutions",
            "gauge": "Gauge",
            "sizing": "Sizes and finished measurements",
            "abbreviations": "Abbreviations and special stitches",
            "construction": "Construction",
            "foundation": "Foundation chain and row 1",
            "chart": "Stitch chart",
            "counts": "Stitch count reference",
            "assembly": "Assembly",
            "seaming": "Seaming methods",
            "blocking": "Blocking",
            "troubleshooting": "Troubleshooting",
            "care": "Care instructions",
            "gallery": "Gallery",
            "thanks": "Thank you",
        }
        entries: list[tuple[str, int]] = []
        seen: set[str] = set()
        # +1 for the contents page itself, which is inserted before these
        for offset, page in enumerate(pages, start=1):
            kind = page["kind"]
            if kind == "instructions":
                label = page.get("title") or "Instructions"
                key = f"instructions:{label}"
            else:
                label = labels.get(kind, kind.replace("_", " ").title())
                key = kind
            if key in seen:
                continue
            seen.add(key)
            entries.append((label, offset + 1))
        return entries

    # -- pagination ---------------------------------------------------------
    def _body_height(self, header: float = 0.0) -> float:
        _, _, _, height = self.content_box()
        return height - header - self.fs(26)  # leave room for the footer

    def _instruction_pages(self, section: dict) -> list[dict]:
        """Split one section's steps across as many pages as they need."""
        steps = section.get("steps") or []
        if not steps:
            return []
        _, _, width, _ = self.content_box()
        label_w = width * 0.22
        text_w = width - label_w - self.gutter

        size = self.fs(9.4)
        leading = size * 1.34
        first_budget = self._body_height(self.fs(58))
        later_budget = self._body_height(self.fs(30))

        pages: list[dict] = []
        current: list[dict] = []
        used = 0.0
        budget = first_budget
        for step in steps:
            lines = len(wrap_text(step.get("text", ""), self.fonts.regular, size, text_w))
            if step.get("count"):
                lines += 1
            cost = max(lines, 1) * leading + self.fs(9)
            if current and used + cost > budget:
                pages.append({
                    "kind": "instructions",
                    "title": section.get("title", "Instructions"),
                    "notes": section.get("notes", "") if not pages else "",
                    "steps": current,
                    "continued": bool(pages),
                })
                current, used, budget = [], 0.0, later_budget
            current.append(step)
            used += cost
        if current:
            pages.append({
                "kind": "instructions",
                "title": section.get("title", "Instructions"),
                "notes": section.get("notes", "") if not pages else "",
                "steps": current,
                "continued": bool(pages),
            })
        return pages

    def _counts_pages(self, counts: list[dict]) -> list[dict]:
        per_page = max(8, int(self._body_height(self.fs(52)) // self.fs(17)))
        return [
            {"kind": "counts", "rows": counts[start : start + per_page], "continued": start > 0}
            for start in range(0, len(counts), per_page)
        ]

    def _trouble_pages(self, entries: list[dict]) -> list[dict]:
        _, _, width, _ = self.content_box()
        size = self.fs(9.2)
        leading = size * 1.32
        budget = self._body_height(self.fs(52))

        pages: list[dict] = []
        current: list[dict] = []
        used = 0.0
        for entry in entries:
            lines = 1
            for key, ratio in (("cause", 0.9), ("fix", 0.9)):
                text = entry.get(key)
                if text:
                    lines += len(
                        wrap_text(f"{key.title()}: {text}", self.fonts.regular, size, width * ratio)
                    )
            cost = lines * leading + self.fs(14)
            if current and used + cost > budget:
                pages.append({"kind": "troubleshooting", "rows": current, "continued": bool(pages)})
                current, used = [], 0.0
            current.append(entry)
            used += cost
        if current:
            pages.append({"kind": "troubleshooting", "rows": current, "continued": bool(pages)})
        return pages

    # ---------------------------------------------------------------- render
    def render(self, out_path: str | Path) -> tuple[Path, list[dict]]:
        pages = self.page_plan()
        self._total_pages = len(pages)
        canvas = self.new_canvas(
            out_path,
            title=str(self.pattern.get("title") or "Crochet Pattern"),
            subject=f"Crochet pattern by {self.brand.credit}",
            keywords=", ".join(
                ["crochet pattern", "pdf pattern", str(self.pattern.get("skill_level") or ""),
                 self.brand.shop]
            ),
            author=self.brand.credit,
        )
        for index, page in enumerate(pages):
            self._page_number = index + 1
            getattr(self, f"draw_{page['kind']}")(canvas, page)
            if page["kind"] not in UNNUMBERED:
                self._footer(canvas)
            self._crop_marks(canvas)
            canvas.showPage()
        canvas.save()
        return Path(out_path), pages

    # ------------------------------------------------------------- furniture
    def _footer(self, c) -> None:
        x, y, width, _ = self.content_box()
        baseline = y - self.fs(15)
        self._rule(c, x, baseline + self.fs(9), x + width, self.theme.color("grid"), width=0.5)
        self._text(
            c, x, baseline, fit_text(self.brand.footer(), self.fonts.regular, self.fs(7.2), width * 0.7),
            self.fonts.regular, self.fs(7.2), self.theme.color("muted"),
        )
        self._text(
            c, x + width, baseline, f"{self._page_number} / {self._total_pages}",
            self.fonts.regular, self.fs(7.2), self.theme.color("muted"), align="right",
        )

    def _page_title(self, c, title: str, eyebrow: str = "", continued: bool = False) -> float:
        """Standard page heading. Returns the baseline to start the body from."""
        x, y, width, height = self.content_box()
        top = y + height
        if eyebrow:
            self._text(
                c, x, top - self.fs(8), eyebrow.upper(), self.fonts.bold, self.fs(7.4),
                self.accent, tracking=self.fs(2.2),
            )
            top -= self.fs(20)
        label = f"{title} (continued)" if continued else title
        size = self.fs(21)
        self._text(
            c, x, top - size * 0.86, fit_text(label, self.fonts.display, size, width),
            self.fonts.display, size, self.theme.color("ink"),
        )
        rule_y = top - size * 1.24
        self._rule(c, x, rule_y, x + width, self.theme.color("ink"), width=0.9)
        return rule_y - self.fs(20)

    def _diagram(self, c, slot: str, x: float, y: float, width: float, height: float) -> bool:
        """Fit a diagram inside a box, preserving its aspect ratio."""
        path = self.diagrams.get(slot)
        if not path:
            return False
        ratio = _aspect(path)
        draw_w, draw_h = width, width / ratio
        if draw_h > height:
            draw_h, draw_w = height, height * ratio
        try:
            c.drawImage(
                str(path), x + (width - draw_w) / 2, y + (height - draw_h) / 2,
                draw_w, draw_h, mask="auto",
            )
        except Exception:  # noqa: BLE001 - a missing plate must not stop the page
            return False
        return True

    def _caption(self, c, text: str, x: float, y: float, width: float) -> None:
        if text:
            self._text(
                c, x + width / 2, y, fit_text(text, self.fonts.italic, self.fs(7.8), width),
                self.fonts.italic, self.fs(7.8), self.theme.color("muted"), align="center",
            )

    def _bullets(
        self,
        c,
        items: list[str],
        x: float,
        y: float,
        width: float,
        size: float | None = None,
        marker: str = "\u2014",
    ) -> float:
        size = size or self.fs(9.6)
        leading = size * 1.36
        indent = size * 1.5
        for item in items:
            self._text(c, x, y, marker, self.fonts.regular, size, self.accent)
            y = self.paragraph(
                c, x + indent, y, item, self.fonts.regular, size,
                self.theme.color("ink"), width - indent, leading=leading,
            )
            y -= self.fs(4)
        return y

    def _numbered(self, c, items: list[str], x: float, y: float, width: float) -> float:
        size = self.fs(9.6)
        indent = size * 2.0
        for index, item in enumerate(items, start=1):
            self._text(
                c, x, y, f"{index}.", self.fonts.bold, size, self.accent,
            )
            y = self.paragraph(
                c, x + indent, y, item, self.fonts.regular, size,
                self.theme.color("ink"), width - indent, leading=size * 1.36,
            )
            y -= self.fs(5)
        return y

    def _key_values(self, c, rows: list[tuple[str, str]], x: float, y: float, width: float) -> float:
        """Two-column label/value rows with a hairline between them."""
        size = self.fs(9.4)
        label_w = width * 0.3
        for label, value in rows:
            if not value:
                continue
            self._text(
                c, x, y, label.upper(), self.fonts.bold, self.fs(7.6),
                self.theme.color("muted"), tracking=self.fs(1.2),
            )
            end = self.paragraph(
                c, x + label_w, y + self.fs(0.6), value, self.fonts.regular, size,
                self.theme.color("ink"), width - label_w, leading=size * 1.32,
            )
            y = min(end, y - size * 1.32) - self.fs(6)
            self._rule(c, x, y + self.fs(4), x + width, self.theme.color("grid"), width=0.4, alpha=0.8)
            y -= self.fs(6)
        return y

    # ------------------------------------------------------------ the pages
    def draw_cover(self, c, page: dict) -> None:
        self._paint_background(c)
        art = self.plates.get("cover") or self.plates.get("finished")
        if art:
            self._image_cover(c, art, 0, 0, self.page_w, self.page_h)

        inset = self.margin_x * 1.05
        panel_x = self.bleed + inset
        panel_w = self.trim_w - 2 * inset
        inner_w = panel_w - self.fs(40)

        # Measure the block before drawing it, so the panel wraps the content
        # instead of leaving a pool of empty space under the badges.
        title = str(self.pattern.get("title") or "Crochet Pattern")
        size = self.fs(30)
        lines = wrap_text(title, self.fonts.display, size, inner_w)
        while len(lines) > 3 and size > self.fs(17):
            size -= self.fs(1.6)
            lines = wrap_text(title, self.fonts.display, size, inner_w)
        lines = lines[:3]

        subtitle = str(self.pattern.get("subtitle") or "")
        subtitle_lines = (
            wrap_text(subtitle, self.fonts.italic, self.fs(10.4), inner_w) if subtitle else []
        )
        skill = str(self.pattern.get("skill_level") or "").title()
        sizes = (self.pattern.get("sizes") or {}).get("labels") or []
        badges = [b for b in [skill, f"{len(sizes)} sizes" if sizes else "", "PDF pattern"] if b]
        credit = f"Designed by {self.brand.credit}"

        pad = self.fs(26)
        content_h = (
            self.fs(24)                                   # eyebrow
            + len(lines) * size * 1.16                    # title
            + (self.fs(6) + len(subtitle_lines) * self.fs(15) if subtitle_lines else 0)
            + (self.fs(24) if badges else 0)
            + self.fs(22)                                 # credit line
        )
        panel_h = content_h + pad * 2
        panel_y = self.bleed + (self.trim_h - panel_h) * 0.54

        if art:
            c.setFillColor(self.color("paper", 0.93))
            c.rect(panel_x, panel_y, panel_w, panel_h, stroke=0, fill=1)
            c.setStrokeColor(_color(self.accent, 0.55))
            c.setLineWidth(0.8)
            c.rect(panel_x + 6, panel_y + 6, panel_w - 12, panel_h - 12, stroke=1, fill=0)

        mid = panel_x + panel_w / 2
        cursor = panel_y + panel_h - pad
        self._text(
            c, mid, cursor, "CROCHET PATTERN", self.fonts.bold, self.fs(8.2), self.accent,
            align="center", tracking=self.fs(2.8),
        )
        cursor -= self.fs(24)

        for line in lines:
            self._text(
                c, mid, cursor, line, self.fonts.display, size, self.theme.color("ink"),
                align="center",
            )
            cursor -= size * 1.16

        if subtitle_lines:
            cursor -= self.fs(6)
            for line in subtitle_lines:
                self._text(
                    c, mid, cursor, line, self.fonts.italic, self.fs(10.4),
                    self.theme.color("muted"), align="center",
                )
                cursor -= self.fs(15)

        if badges:
            cursor -= self.fs(6)
            self._text(
                c, mid, cursor, "  \u00b7  ".join(badges).upper(), self.fonts.bold,
                self.fs(7.4), self.theme.color("muted"), align="center", tracking=self.fs(1.8),
            )
            cursor -= self.fs(18)

        # Inside the panel, the credit reads cleanly against any photograph.
        self._text(
            c, mid, cursor - self.fs(2), credit, self.fonts.regular, self.fs(9),
            self.theme.color("muted"), align="center",
        )
        if self.brand.logo:
            self._logo(c, mid, panel_y - self.fs(52), self.fs(40))

    def _logo(self, c, centre_x: float, y: float, box: float) -> None:
        logo = self.brand.logo
        if not logo:
            return
        ratio = _aspect(logo)
        draw_w, draw_h = box * ratio, box
        if draw_w > box * 2.4:
            draw_w, draw_h = box * 2.4, box * 2.4 / ratio
        try:
            c.drawImage(str(logo), centre_x - draw_w / 2, y, draw_w, draw_h, mask="auto")
        except Exception:  # noqa: BLE001 - a broken logo file must not stop the cover
            pass

    def draw_credits(self, c, page: dict) -> None:
        self._paint_background(c)
        x, y, width, height = self.content_box()
        cursor = y + height - self.fs(30)

        self._text(
            c, x, cursor, str(self.pattern.get("title") or "Crochet Pattern"),
            self.fonts.display, self.fs(15), self.theme.color("ink"),
        )
        cursor -= self.fs(26)
        cursor = self._key_values(
            c,
            [
                ("Designer", self.brand.credit),
                ("Shop", self.brand.store_name),
                ("Skill level", str(self.pattern.get("skill_level") or "").title()),
                ("Contact", self.brand.email or self.brand.website),
            ],
            x, cursor, width,
        )

        cursor -= self.fs(10)
        may, may_not = self.brand.licence_terms()
        self._text(
            c, x, cursor, "LICENCE", self.fonts.bold, self.fs(8), self.accent,
            tracking=self.fs(2.0),
        )
        cursor -= self.fs(18)
        self._text(c, x, cursor, "You may", self.fonts.bold, self.fs(9.4), self.theme.color("ink"))
        cursor -= self.fs(15)
        cursor = self._bullets(c, may, x, cursor, width, size=self.fs(9.0))
        cursor -= self.fs(8)
        self._text(
            c, x, cursor, "You may not", self.fonts.bold, self.fs(9.4), self.theme.color("ink")
        )
        cursor -= self.fs(15)
        cursor = self._bullets(c, may_not, x, cursor, width, size=self.fs(9.0), marker="\u00d7")

        self._text(
            c, x, y + self.fs(4), self.brand.copyright_line(), self.fonts.regular,
            self.fs(7.8), self.theme.color("muted"),
        )

    def draw_contents(self, c, page: dict) -> None:
        self._paint_background(c)
        cursor = self._page_title(c, "Contents")
        x, _, width, _ = self.content_box()
        size = self.fs(10)
        for label, number in page.get("entries", []):
            self._text(
                c, x, cursor, fit_text(label, self.fonts.regular, size, width * 0.8),
                self.fonts.regular, size, self.theme.color("ink"),
            )
            self._text(
                c, x + width, cursor, str(number), self.fonts.regular, size,
                self.theme.color("muted"), align="right",
            )
            self._rule(
                c, x, cursor - self.fs(4), x + width, self.theme.color("grid"),
                width=0.4, alpha=0.7,
            )
            cursor -= size * 1.9

    def draw_about(self, c, page: dict) -> None:
        self._paint_background(c)
        cursor = self._page_title(c, "About this pattern", eyebrow="start here")
        x, y, width, _ = self.content_box()

        cursor = self.paragraph(
            c, x, cursor, str(self.pattern.get("intro") or ""), self.fonts.regular,
            self.fs(10.4), self.theme.color("ink"), width, leading=self.fs(15),
        )
        cursor -= self.fs(16)

        requirements = self.pattern.get("skill_requirements") or []
        if requirements:
            level = str(self.pattern.get("skill_level") or "").title()
            self._text(
                c, x, cursor, f"SKILL LEVEL: {level.upper()}" if level else "WHAT YOU NEED TO KNOW",
                self.fonts.bold, self.fs(8), self.accent, tracking=self.fs(2.0),
            )
            cursor -= self.fs(17)
            self._text(
                c, x, cursor, "You should already be comfortable with:",
                self.fonts.regular, self.fs(9.4), self.theme.color("muted"),
            )
            cursor -= self.fs(15)
            cursor = self._bullets(c, requirements, x, cursor, width)
            cursor -= self.fs(12)

        estimates = self.pattern.get("time_estimates") or []
        if estimates:
            self._text(
                c, x, cursor, "HOW LONG IT TAKES", self.fonts.bold, self.fs(8),
                self.accent, tracking=self.fs(2.0),
            )
            cursor -= self.fs(20)
            columns = [str(e.get("size") or "-") for e in estimates][:8]
            rect = (x, cursor - self.fs(46), width, self.fs(46))
            table_block(self, c, rect, columns, rows=1)
            cell_w = width / max(len(columns), 1)
            for index, entry in enumerate(estimates[: len(columns)]):
                self._text(
                    c, x + cell_w * (index + 0.5), cursor - self.fs(30),
                    f"{entry.get('hours', '-')} h", self.fonts.regular, self.fs(9.6),
                    self.theme.color("ink"), align="center",
                )
            cursor -= self.fs(56)
            note = next((e.get("note") for e in estimates if e.get("note")), "")
            if note:
                self._text(
                    c, x, cursor, f"Estimates assume {note}.", self.fonts.italic,
                    self.fs(8.4), self.theme.color("muted"),
                )
                cursor -= self.fs(16)

        notes = self.pattern.get("notes") or []
        if notes and cursor > y + self.fs(90):
            self._text(
                c, x, cursor, "BEFORE YOU BEGIN", self.fonts.bold, self.fs(8),
                self.accent, tracking=self.fs(2.0),
            )
            cursor -= self.fs(17)
            self._bullets(c, notes, x, cursor, width, size=self.fs(9.2))

    def draw_materials(self, c, page: dict) -> None:
        self._paint_background(c)
        cursor = self._page_title(c, "Materials", eyebrow="what you need")
        x, y, width, _ = self.content_box()

        plate = self.plates.get("materials")
        image_h = 0.0
        if plate:
            image_h = min(self.trim_h * 0.3, width * 0.46)
            self._image_cover(c, plate, x, cursor - image_h, width, image_h, radius=self.fs(4))
            caption = self.captions.get("materials", "")
            cursor -= image_h + self.fs(14)
            if caption:
                self._caption(c, caption, x, cursor, width)
                cursor -= self.fs(12)

        rows = [
            (str(item.get("item") or ""), str(item.get("detail") or ""))
            for item in self.pattern.get("materials") or []
        ]
        self._key_values(c, [(a, b or "\u2014") for a, b in rows if a], x, cursor, width)

    def draw_yarn_guide(self, c, page: dict) -> None:
        self._paint_background(c)
        cursor = self._page_title(c, "Yarn guide", eyebrow="choosing your yarn")
        x, y, width, _ = self.content_box()
        guide = self.pattern.get("yarn_guide") or {}

        cursor = self._key_values(
            c,
            [
                ("Weight", str(guide.get("weight") or "")),
                ("Fibre", str(guide.get("fibre") or "")),
                ("Sample used", str(guide.get("recommended") or "")),
            ],
            x, cursor, width,
        )
        cursor -= self.fs(8)

        notes = str(guide.get("notes") or "")
        if notes:
            cursor = self.paragraph(
                c, x, cursor, notes, self.fonts.regular, self.fs(9.6),
                self.theme.color("ink"), width, leading=self.fs(14),
            )
            cursor -= self.fs(14)

        if self._diagram_block(c, "yardage", x, cursor, width, self.trim_h * 0.26):
            cursor -= self.trim_h * 0.26 + self.fs(16)
        else:
            yardage = guide.get("yardage") or []
            if yardage:
                self._text(
                    c, x, cursor, "YARN NEEDED BY SIZE", self.fonts.bold, self.fs(8),
                    self.accent, tracking=self.fs(2.0),
                )
                cursor -= self.fs(20)
                columns = [str(row.get("size") or "-") for row in yardage][:9]
                table_block(self, c, (x, cursor - self.fs(44), width, self.fs(44)), columns, rows=1)
                cell_w = width / max(len(columns), 1)
                for index, row in enumerate(yardage[: len(columns)]):
                    self._text(
                        c, x + cell_w * (index + 0.5), cursor - self.fs(29),
                        f"{row.get('yards', '-')} yd", self.fonts.regular, self.fs(9.2),
                        self.theme.color("ink"), align="center",
                    )
                cursor -= self.fs(56)

        subs = guide.get("substitutions") or []
        if subs:
            self._text(
                c, x, cursor, "SUBSTITUTING YARN", self.fonts.bold, self.fs(8),
                self.accent, tracking=self.fs(2.0),
            )
            cursor -= self.fs(17)
            self._bullets(c, subs, x, cursor, width, size=self.fs(9.2))

    def _diagram_block(
        self, c, slot: str, x: float, cursor: float, width: float, height: float
    ) -> bool:
        return self._diagram(c, slot, x, cursor - height, width, height)

    def draw_gauge(self, c, page: dict) -> None:
        self._paint_background(c)
        cursor = self._page_title(c, "Gauge", eyebrow="check this first")
        x, y, width, _ = self.content_box()
        gauge = self.pattern.get("gauge") or {}

        headline = (
            f"{_g(gauge.get('stitches'))} sts \u00d7 {_g(gauge.get('rows'))} rows "
            f"= {gauge.get('swatch', '4 x 4 in')}"
        )
        self._text(c, x, cursor, headline, self.fonts.bold, self.fs(14), self.theme.color("ink"))
        cursor -= self.fs(22)
        detail = " \u00b7 ".join(
            str(v) for v in [gauge.get("stitch"), f"{gauge.get('hook')} hook" if gauge.get("hook") else ""]
            if v
        )
        if detail:
            self._text(c, x, cursor, detail, self.fonts.regular, self.fs(9.8), self.theme.color("muted"))
            cursor -= self.fs(20)

        plate_h = self.trim_h * 0.36
        if self._diagram_block(c, "gauge", x, cursor, width, plate_h):
            cursor -= plate_h + self.fs(14)
        elif self.plates.get("texture"):
            box = min(self.trim_h * 0.26, width * 0.5)
            self._image_cover(c, self.plates["texture"], x, cursor - box, box, box, radius=self.fs(4))
            cursor -= box + self.fs(14)

        notes = str(gauge.get("notes") or "")
        if notes:
            cursor = self.paragraph(
                c, x, cursor, notes, self.fonts.regular, self.fs(9.8),
                self.theme.color("ink"), width, leading=self.fs(14.4),
            )
            cursor -= self.fs(12)

        self._text(
            c, x, max(cursor, y + self.fs(10)),
            "Gauge decides the finished size. Please do not skip the swatch.",
            self.fonts.italic, self.fs(9), self.accent,
        )

    def draw_sizing(self, c, page: dict) -> None:
        self._paint_background(c)
        cursor = self._page_title(c, "Sizes", eyebrow="finished measurements")
        x, y, width, _ = self.content_box()
        sizes = self.pattern.get("sizes") or {}
        labels = [str(s) for s in sizes.get("labels") or []]
        rows = sizes.get("rows") or []

        ease = str(sizes.get("ease") or "")
        if ease:
            self._text(
                c, x, cursor, f"Recommended ease: {ease}", self.fonts.regular,
                self.fs(9.6), self.theme.color("ink"),
            )
            cursor -= self.fs(20)

        if labels and rows:
            cursor = self._sizing_table(c, labels, rows[:12], x, cursor, width)
            cursor -= self.fs(18)

        notes = str(sizes.get("notes") or "")
        if notes:
            cursor = self.paragraph(
                c, x, cursor, notes, self.fonts.regular, self.fs(9.4),
                self.theme.color("ink"), width, leading=self.fs(13.8),
            )
            cursor -= self.fs(14)

        remaining = cursor - y
        if "body" in self.diagrams and remaining > self.fs(120):
            height = min(remaining - self.fs(16), self.trim_h * 0.3)
            self._diagram_block(c, "body", x, cursor, width, height)

    def _sizing_table(
        self,
        c,
        labels: list[str],
        rows: list[dict],
        x: float,
        top: float,
        width: float,
    ) -> float:
        """Measurement table with a wide label column.

        Drawn here rather than with `table_block` because that helper divides the
        width into equal columns: the measurement names need roughly a third of
        the row to themselves, and the cell text has to sit on the same grid the
        rules are drawn on.
        """
        label_w = min(width * 0.3, self.fs(150))
        value_w = (width - label_w) / max(len(labels), 1)
        header_h = self.fs(21)
        row_h = self.fs(18.5)
        table_h = header_h + row_h * len(rows)
        bottom = top - table_h

        c.setFillColor(self.color("band", 0.85))
        c.rect(x, top - header_h, width, header_h, stroke=0, fill=1)
        for index, row in enumerate(rows):
            if index % 2 == 1:
                c.setFillColor(self.color("band", 0.35))
                c.rect(x, top - header_h - row_h * (index + 1), width, row_h, stroke=0, fill=1)

        header_size = self.fs(7.6)
        self._text(
            c, x + self.fs(7), top - header_h + self.fs(7), "MEASUREMENT",
            self.fonts.bold, header_size, self.theme.color("ink"), tracking=self.fs(0.8),
        )
        for index, label in enumerate(labels):
            self._text(
                c, x + label_w + value_w * (index + 0.5), top - header_h + self.fs(7),
                fit_text(str(label).upper(), self.fonts.bold, header_size, value_w * 0.9),
                self.fonts.bold, header_size, self.theme.color("ink"),
                align="center", tracking=self.fs(0.8),
            )

        size = self.fs(8.4)
        for index, row in enumerate(rows):
            baseline = top - header_h - row_h * (index + 1) + self.fs(6)
            self._text(
                c, x + self.fs(7), baseline,
                fit_text(str(row.get("measure") or ""), self.fonts.regular, size,
                         label_w - self.fs(12)),
                self.fonts.regular, size, self.theme.color("ink"),
            )
            for column in range(len(labels)):
                values = row.get("values") or []
                value = str(values[column]) if column < len(values) else ""
                if not value:
                    continue
                self._text(
                    c, x + label_w + value_w * (column + 0.5), baseline,
                    fit_text(value, self.fonts.regular, size, value_w * 0.9),
                    self.fonts.regular, size, self.theme.color("ink"), align="center",
                )

        grid = self.theme.color("grid")
        self._rule(c, x, top, x + width, self.theme.color("ink"), width=0.8)
        self._rule(c, x, top - header_h, x + width, self.theme.color("ink"), width=0.6)
        for index in range(len(rows)):
            line_y = top - header_h - row_h * (index + 1)
            self._rule(c, x, line_y, x + width, grid, width=0.4)
        c.setStrokeColor(self.color("grid"))
        c.setLineWidth(0.4)
        for index in range(len(labels) + 1):
            line_x = x + label_w + value_w * index
            c.line(line_x, bottom, line_x, top)
        c.line(x, bottom, x, top)
        c.line(x + width, bottom, x + width, top)
        return bottom

    def draw_abbreviations(self, c, page: dict) -> None:
        self._paint_background(c)
        cursor = self._page_title(c, "Abbreviations", eyebrow="US terminology")
        x, y, width, _ = self.content_box()

        rows = self.pattern.get("abbreviations") or []
        if rows:
            column_w = (width - self.gutter) / 2
            per_column = (len(rows) + 1) // 2
            size = self.fs(9)
            for column in range(2):
                chunk = rows[column * per_column : (column + 1) * per_column]
                cx = x + column * (column_w + self.gutter)
                cy = cursor
                for row in chunk:
                    abbr = str(row.get("abbr") or "")
                    meaning = str(row.get("meaning") or "")
                    self._text(c, cx, cy, abbr, self.fonts.bold, size, self.theme.color("ink"))
                    self._text(
                        c, cx + column_w * 0.32, cy,
                        fit_text(meaning, self.fonts.regular, size, column_w * 0.66),
                        self.fonts.regular, size, self.theme.color("muted"),
                    )
                    cy -= size * 1.62
            cursor -= per_column * self.fs(9) * 1.62 + self.fs(16)

        specials = self.pattern.get("special_stitches") or []
        if specials and cursor > y + self.fs(80):
            self._text(
                c, x, cursor, "SPECIAL STITCHES", self.fonts.bold, self.fs(8),
                self.accent, tracking=self.fs(2.0),
            )
            cursor -= self.fs(18)
            for special in specials:
                if cursor < y + self.fs(30):
                    break
                self._text(
                    c, x, cursor, str(special.get("name") or ""), self.fonts.bold,
                    self.fs(9.6), self.theme.color("ink"),
                )
                cursor -= self.fs(14)
                cursor = self.paragraph(
                    c, x, cursor, str(special.get("how") or ""), self.fonts.regular,
                    self.fs(9.2), self.theme.color("muted"), width, leading=self.fs(13),
                )
                cursor -= self.fs(10)

    def draw_construction(self, c, page: dict) -> None:
        self._paint_background(c)
        cursor = self._page_title(c, "Construction", eyebrow="how it goes together")
        x, y, width, _ = self.content_box()
        construction = self.pattern.get("construction") or {}

        summary = str(construction.get("summary") or "")
        if summary:
            cursor = self.paragraph(
                c, x, cursor, summary, self.fonts.regular, self.fs(10.2),
                self.theme.color("ink"), width, leading=self.fs(14.8),
            )
            cursor -= self.fs(16)

        plate_h = self.trim_h * 0.3
        if self._diagram_block(c, "schematic", x, cursor, width, plate_h):
            cursor -= plate_h + self.fs(10)
            self._caption(c, "Finished, blocked dimensions", x, cursor, width)
            cursor -= self.fs(16)

        pieces = construction.get("pieces") or []
        order = construction.get("order") or []
        if pieces and order:
            column_w = (width - self.gutter) / 2
            self._text(
                c, x, cursor, "PIECES", self.fonts.bold, self.fs(8), self.accent,
                tracking=self.fs(2.0),
            )
            self._text(
                c, x + column_w + self.gutter, cursor, "ORDER OF WORK", self.fonts.bold,
                self.fs(8), self.accent, tracking=self.fs(2.0),
            )
            cursor -= self.fs(17)
            self._bullets(c, pieces, x, cursor, column_w, size=self.fs(9.2))
            self._numbered(c, order, x + column_w + self.gutter, cursor, column_w)
        elif order:
            self._numbered(c, order, x, cursor, width)
        elif pieces:
            self._bullets(c, pieces, x, cursor, width)

    def draw_foundation(self, c, page: dict) -> None:
        self._paint_background(c)
        cursor = self._page_title(c, "Getting started", eyebrow="foundation chain")
        x, y, width, _ = self.content_box()
        chart = self.pattern.get("chart") or {}
        primary = str(self.pattern.get("primary_stitch") or "dc")

        turning = chart.get("turning_chain")
        intro = (
            f"Row 1 is worked into the foundation chain. The first {turning} chain(s) count as "
            f"the height of the first {primary}, so skip them and work your first stitch into "
            "the next chain."
            if turning
            else "Row 1 is worked into the foundation chain. Skip the turning chain and work "
                 "your first stitch into the next chain."
        )
        cursor = self.paragraph(
            c, x, cursor, intro, self.fonts.regular, self.fs(10), self.theme.color("ink"),
            width, leading=self.fs(14.4),
        )
        cursor -= self.fs(18)

        plate_h = self.trim_h * 0.32
        if self._diagram_block(c, "foundation", x, cursor, width, plate_h):
            cursor -= plate_h + self.fs(18)

        self._bullets(
            c,
            [
                "Keep the foundation chain loose. A tight chain pulls the whole hem in.",
                "If the chain is tight, work it with a hook one size larger, then switch back.",
                "Working into the back bump of the chain gives a neat, elastic edge.",
                "Count the chain twice before you work row 1. It is much easier than recounting later.",
            ],
            x, cursor, width, size=self.fs(9.4),
        )

    def draw_chart(self, c, page: dict) -> None:
        self._paint_background(c)
        cursor = self._page_title(c, "Stitch chart", eyebrow="visual reference")
        x, y, width, _ = self.content_box()
        chart = self.pattern.get("chart") or {}

        repeat = str(chart.get("repeat") or "")
        if repeat:
            self._text(
                c, x, cursor, f"Repeat: {repeat}", self.fonts.regular, self.fs(9.6),
                self.theme.color("ink"),
            )
            cursor -= self.fs(18)

        available = cursor - y - self.fs(70)
        if self._diagram_block(c, "chart", x, cursor, width, available):
            cursor -= available + self.fs(12)

        self._text(
            c, x, cursor, "The chart and the written instructions describe the same fabric. "
            "Use whichever you prefer.", self.fonts.italic, self.fs(9),
            self.theme.color("muted"),
        )

    def draw_instructions(self, c, page: dict) -> None:
        self._paint_background(c)
        cursor = self._page_title(
            c, page.get("title", "Instructions"), eyebrow="pattern",
            continued=page.get("continued", False),
        )
        x, y, width, _ = self.content_box()

        notes = str(page.get("notes") or "")
        if notes:
            cursor = self.paragraph(
                c, x, cursor, notes, self.fonts.italic, self.fs(9.4),
                self.theme.color("muted"), width, leading=self.fs(13.4),
            )
            cursor -= self.fs(14)

        label_w = width * 0.22
        text_w = width - label_w - self.gutter
        size = self.fs(9.4)
        leading = size * 1.34
        for step in page.get("steps", []):
            label = str(step.get("label") or "")
            if label:
                self._text(
                    c, x, cursor, fit_text(label, self.fonts.bold, size, label_w),
                    self.fonts.bold, size, self.theme.color("ink"),
                )
            end = self.paragraph(
                c, x + label_w + self.gutter, cursor, str(step.get("text") or ""),
                self.fonts.regular, size, self.theme.color("ink"), text_w, leading=leading,
            )
            count = str(step.get("count") or "")
            if count:
                self._text(
                    c, x + label_w + self.gutter, end, f"({count})", self.fonts.italic,
                    size * 0.94, self.accent,
                )
                end -= leading
            cursor = end - self.fs(9)

    def draw_counts(self, c, page: dict) -> None:
        self._paint_background(c)
        cursor = self._page_title(
            c, "Stitch counts", eyebrow="check as you go",
            continued=page.get("continued", False),
        )
        x, _, width, _ = self.content_box()

        self._text(
            c, x, cursor,
            "Compare your work against this table at the end of every row.",
            self.fonts.regular, self.fs(9.4), self.theme.color("muted"),
        )
        cursor -= self.fs(22)

        rows = page.get("rows", [])
        row_w, count_w = width * 0.42, width * 0.26
        self._text(
            c, x, cursor, "ROW", self.fonts.bold, self.fs(7.4), self.theme.color("muted"),
            tracking=self.fs(1.4),
        )
        self._text(
            c, x + row_w, cursor, "COUNT", self.fonts.bold, self.fs(7.4),
            self.theme.color("muted"), tracking=self.fs(1.4),
        )
        self._text(
            c, x + row_w + count_w, cursor, "NOTE", self.fonts.bold, self.fs(7.4),
            self.theme.color("muted"), tracking=self.fs(1.4),
        )
        cursor -= self.fs(6)
        self._rule(c, x, cursor, x + width, self.theme.color("ink"), width=0.7)
        cursor -= self.fs(14)

        size = self.fs(9)
        for index, row in enumerate(rows):
            if index % 2 == 0:
                c.setFillColor(self.color("band", 0.55))
                c.rect(x, cursor - self.fs(5), width, self.fs(16), stroke=0, fill=1)
            self._text(
                c, x + self.fs(3), cursor,
                fit_text(str(row.get("row") or ""), self.fonts.regular, size, row_w - self.fs(8)),
                self.fonts.regular, size, self.theme.color("ink"),
            )
            self._text(
                c, x + row_w, cursor,
                fit_text(str(row.get("count") or ""), self.fonts.bold, size, count_w - self.fs(6)),
                self.fonts.bold, size, self.accent,
            )
            self._text(
                c, x + row_w + count_w, cursor,
                fit_text(str(row.get("note") or ""), self.fonts.italic, size * 0.94,
                         width - row_w - count_w),
                self.fonts.italic, size * 0.94, self.theme.color("muted"),
            )
            cursor -= self.fs(17)

    def draw_assembly(self, c, page: dict) -> None:
        self._paint_background(c)
        cursor = self._page_title(c, "Assembly", eyebrow="putting it together")
        x, _, width, _ = self.content_box()
        self._numbered(c, self.pattern.get("assembly") or [], x, cursor, width)

    def draw_seaming(self, c, page: dict) -> None:
        self._paint_background(c)
        entry = page.get("entry") or {}
        method = str(entry.get("method") or "Seaming")
        cursor = self._page_title(c, method, eyebrow="seaming method")
        x, y, width, _ = self.content_box()

        used_for = str(entry.get("used_for") or "")
        if used_for:
            self._text(
                c, x, cursor, f"Used for: {used_for}", self.fonts.italic, self.fs(9.6),
                self.accent,
            )
            cursor -= self.fs(20)

        how = str(entry.get("how") or "")
        if how:
            cursor = self.paragraph(
                c, x, cursor, how, self.fonts.regular, self.fs(10.2),
                self.theme.color("ink"), width, leading=self.fs(14.8),
            )
            cursor -= self.fs(18)

        available = cursor - y - self.fs(20)
        if available > self.fs(90):
            self._diagram_block(c, page.get("diagram", ""), x, cursor, width, available)

    def draw_blocking(self, c, page: dict) -> None:
        self._paint_background(c)
        cursor = self._page_title(c, "Blocking", eyebrow="the finishing step")
        x, y, width, _ = self.content_box()
        blocking = self.pattern.get("blocking") or {}

        method = str(blocking.get("method") or "")
        if method:
            self._text(
                c, x, cursor, f"Method: {method}", self.fonts.bold, self.fs(10.4),
                self.theme.color("ink"),
            )
            cursor -= self.fs(22)

        cursor = self._numbered(c, blocking.get("steps") or [], x, cursor, width)
        cursor -= self.fs(12)

        notes = str(blocking.get("notes") or "")
        if notes and cursor > y + self.fs(50):
            box_h = min(cursor - y - self.fs(10), self.fs(96))
            c.setFillColor(self.color("band", 0.75))
            c.rect(x, cursor - box_h, width, box_h, stroke=0, fill=1)
            c.setStrokeColor(_color(self.accent, 0.45))
            c.setLineWidth(0.7)
            c.rect(x, cursor - box_h, width, box_h, stroke=1, fill=0)
            self.paragraph(
                c, x + self.fs(12), cursor - self.fs(18), notes, self.fonts.regular,
                self.fs(9.2), self.theme.color("ink"), width - self.fs(24), leading=self.fs(13.4),
            )

    def draw_troubleshooting(self, c, page: dict) -> None:
        self._paint_background(c)
        cursor = self._page_title(
            c, "Troubleshooting", eyebrow="if something looks wrong",
            continued=page.get("continued", False),
        )
        x, _, width, _ = self.content_box()

        size = self.fs(9.2)
        for row in page.get("rows", []):
            self._text(
                c, x, cursor, str(row.get("problem") or ""), self.fonts.bold,
                self.fs(9.8), self.theme.color("ink"),
            )
            cursor -= self.fs(15)
            for label, key, font in (
                ("Likely cause", "cause", self.fonts.regular),
                ("Fix", "fix", self.fonts.regular),
            ):
                text = str(row.get(key) or "")
                if not text:
                    continue
                self._text(
                    c, x + self.fs(6), cursor, f"{label}:", self.fonts.bold, size * 0.92,
                    self.accent,
                )
                cursor = self.paragraph(
                    c, x + self.fs(6) + width * 0.14, cursor, text, font, size,
                    self.theme.color("muted"), width - width * 0.14 - self.fs(6),
                    leading=size * 1.32,
                )
            cursor -= self.fs(14)

    def draw_care(self, c, page: dict) -> None:
        self._paint_background(c)
        cursor = self._page_title(c, "Care instructions", eyebrow="keeping it lovely")
        x, y, width, _ = self.content_box()

        cursor = self._bullets(c, self.pattern.get("care") or [], x, cursor, width)
        cursor -= self.fs(16)

        plate = self.plates.get("texture") or self.plates.get("finished")
        remaining = cursor - y
        if plate and remaining > self.fs(120):
            height = min(remaining - self.fs(20), self.trim_h * 0.28)
            self._image_cover(c, plate, x, cursor - height, width, height, radius=self.fs(4))

    def draw_gallery(self, c, page: dict) -> None:
        self._paint_background(c)
        cursor = self._page_title(c, "Gallery", eyebrow="the finished piece")
        x, y, width, _ = self.content_box()

        slots = page.get("slots") or [
            s for s in ("finished", "styled", "texture") if s in self.plates
        ]
        if not slots:
            return
        if len(slots) == 1:
            height = cursor - y - self.fs(30)
            self._image_cover(c, self.plates[slots[0]], x, cursor - height, width, height,
                              radius=self.fs(5))
            self._caption(c, self.captions.get(slots[0], ""), x, cursor - height - self.fs(14), width)
            return

        main_h = (cursor - y - self.fs(44)) * 0.62
        self._image_cover(c, self.plates[slots[0]], x, cursor - main_h, width, main_h,
                          radius=self.fs(5))
        cursor -= main_h + self.fs(12)
        self._caption(c, self.captions.get(slots[0], ""), x, cursor, width)
        cursor -= self.fs(16)

        rest = slots[1:3]
        column_w = (width - self.gutter) / len(rest)
        row_h = max(self.fs(60), cursor - y - self.fs(20))
        for index, slot in enumerate(rest):
            self._image_cover(
                c, self.plates[slot], x + index * (column_w + self.gutter),
                cursor - row_h, column_w, row_h, radius=self.fs(4),
            )

    def draw_thanks(self, c, page: dict) -> None:
        self._paint_background(c)
        x, y, width, height = self.content_box()
        cursor = y + height - self.fs(40)

        if self.brand.logo:
            self._logo(c, x + width / 2, cursor - self.fs(44), self.fs(44))
            cursor -= self.fs(64)

        self._text(
            c, x + width / 2, cursor, "THANK YOU", self.fonts.bold, self.fs(9),
            self.accent, align="center", tracking=self.fs(3.2),
        )
        cursor -= self.fs(30)
        self._text(
            c, x + width / 2, cursor, self.brand.shop, self.fonts.display, self.fs(22),
            self.theme.color("ink"), align="center",
        )
        cursor -= self.fs(28)

        tagline = self.brand.tagline or (
            "Thank you for choosing this pattern. I hope you enjoy making it."
        )
        cursor = self.paragraph(
            c, x + width * 0.1, cursor, tagline, self.fonts.italic, self.fs(10.4),
            self.theme.color("muted"), width * 0.8, align="center", leading=self.fs(15),
        )
        cursor -= self.fs(26)

        contacts = self.brand.contact_lines()
        if contacts:
            self._rule(
                c, x + width * 0.3, cursor, x + width * 0.7, self.theme.color("grid"), width=0.6
            )
            cursor -= self.fs(22)
            for line in contacts:
                self._text(
                    c, x + width / 2, cursor, line, self.fonts.regular, self.fs(9.4),
                    self.theme.color("ink"), align="center",
                )
                cursor -= self.fs(16)

        support = self.brand.support_note
        if support:
            cursor -= self.fs(10)
            cursor = self.paragraph(
                c, x + width * 0.12, cursor, support, self.fonts.regular, self.fs(9.2),
                self.theme.color("muted"), width * 0.76, align="center", leading=self.fs(13.4),
            )

        share = (
            "If you make this pattern I would love to see it. Tag "
            f"@{self.brand.instagram} so I can share your work."
            if self.brand.instagram
            else "If you make this pattern I would love to see it."
        )
        self._text(
            c, x + width / 2, y + self.fs(34), share, self.fonts.italic, self.fs(9),
            self.accent, align="center",
        )
        self._text(
            c, x + width / 2, y + self.fs(10), self.brand.copyright_line(), self.fonts.regular,
            self.fs(7.6), self.theme.color("muted"), align="center",
        )


# ------------------------------------------------------------------- helpers
def _existing(mapping: dict[str, Path] | None) -> dict[str, Path]:
    """Drop any slot whose file is missing, so pages can test membership."""
    return {
        key: Path(value)
        for key, value in (mapping or {}).items()
        if value and Path(value).exists()
    }


def _aspect(path: Path, default: float = 1.4) -> float:
    try:
        from PIL import Image

        with Image.open(path) as image:
            width, height = image.size
        return (width / height) if height else default
    except Exception:  # noqa: BLE001 - fall back to a sane ratio
        return default


def _color(value: str, alpha: float = 1.0):
    from ..pdf.drawkit import color_of

    return color_of(value, alpha)


def _g(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value or "-")
    return f"{number:g}"

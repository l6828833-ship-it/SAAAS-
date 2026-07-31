"""Shared page-drawing primitives for every product engine.

`DrawKit` owns page geometry (trim, bleed, margins, type scale) and the small
set of drawing operations each product needs. Calendars, planners, journals and
wall art all build on it, so they share one typographic system.
"""

from __future__ import annotations

from pathlib import Path

from reportlab.lib.colors import Color, HexColor
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas as rl_canvas

from ..themes import Theme, get_theme
from .fonts import FontSet, resolve_fonts


def color_of(value: str | Color, alpha: float = 1.0) -> Color:
    col = value if isinstance(value, Color) else HexColor(value)
    if alpha >= 1.0:
        return col
    return Color(col.red, col.green, col.blue, alpha)


def fit_text(text: str, font: str, size: float, max_width: float) -> str:
    """Truncate with an ellipsis so labels never bleed out of their box."""
    if pdfmetrics.stringWidth(text, font, size) <= max_width:
        return text
    ellipsis = "\u2026"
    trimmed = text
    while trimmed and pdfmetrics.stringWidth(trimmed + ellipsis, font, size) > max_width:
        trimmed = trimmed[:-1]
    return (trimmed.rstrip() + ellipsis) if trimmed else ""


def wrap_text(text: str, font: str, size: float, max_width: float) -> list[str]:
    """Greedy word wrap. Long single words are hard-split."""
    lines: list[str] = []
    for paragraph in text.split("\n"):
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        current = words[0]
        for word in words[1:]:
            candidate = f"{current} {word}"
            if pdfmetrics.stringWidth(candidate, font, size) <= max_width:
                current = candidate
            else:
                lines.append(current)
                current = word
        lines.append(current)

    final: list[str] = []
    for line in lines:
        while pdfmetrics.stringWidth(line, font, size) > max_width and len(line) > 1:
            cut = len(line) - 1
            while cut > 1 and pdfmetrics.stringWidth(line[:cut], font, size) > max_width:
                cut -= 1
            final.append(line[:cut])
            line = line[cut:]
        final.append(line)
    return final


class DrawKit:
    """Page geometry plus drawing primitives, shared by all renderers."""

    def __init__(
        self,
        theme: Theme | str | None,
        trim_size_in: tuple[float, float],
        bleed_in: float = 0.0,
        margin_x_ratio: float = 0.072,
        margin_y_ratio: float = 0.058,
        min_margin_in: float = 0.36,
    ):
        self.theme: Theme = theme if isinstance(theme, Theme) else get_theme(theme)
        self.fonts: FontSet = resolve_fonts(self.theme.serif)

        trim_w_in, trim_h_in = trim_size_in
        self.trim_w = trim_w_in * inch
        self.trim_h = trim_h_in * inch
        self.bleed = bleed_in * inch
        self.page_w = self.trim_w + 2 * self.bleed
        self.page_h = self.trim_h + 2 * self.bleed

        self.scale = max(0.55, min(trim_w_in, trim_h_in) / 8.5)
        self.margin_x = max(min_margin_in * inch, margin_x_ratio * self.trim_w)
        self.margin_y = max(min_margin_in * inch, margin_y_ratio * self.trim_h)
        self.gutter = max(0.16 * inch, 0.022 * min(self.trim_w, self.trim_h))

    # ------------------------------------------------------------- geometry
    def content_box(self) -> tuple[float, float, float, float]:
        """(x, y, w, h) of the safe content area inside the trim box."""
        return (
            self.bleed + self.margin_x,
            self.bleed + self.margin_y,
            self.trim_w - 2 * self.margin_x,
            self.trim_h - 2 * self.margin_y,
        )

    def fs(self, points: float) -> float:
        """Scale a type size from the 8.5in reference edge to this trim."""
        return points * self.scale

    def color(self, role: str, alpha: float = 1.0) -> Color:
        return color_of(self.theme.color(role, "#000000"), alpha)

    # ------------------------------------------------------------ documents
    def new_canvas(
        self,
        out_path: str | Path,
        title: str = "",
        subject: str = "",
        keywords: str = "",
        author: str = "Artisan Forge",
    ) -> rl_canvas.Canvas:
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        c = rl_canvas.Canvas(str(out_path), pagesize=(self.page_w, self.page_h))
        c.setTitle(title)
        c.setAuthor(author)
        c.setSubject(subject)
        c.setKeywords(keywords)
        return c

    # ----------------------------------------------------------- primitives
    def _text(
        self,
        c: rl_canvas.Canvas,
        x: float,
        y: float,
        text: str,
        font: str,
        size: float,
        color: str | Color,
        align: str = "left",
        tracking: float = 0.0,
    ) -> float:
        """Draw a single line of text. Returns its width."""
        if not text:
            return 0.0
        width = pdfmetrics.stringWidth(text, font, size) + tracking * max(len(text) - 1, 0)
        if align == "center":
            x -= width / 2
        elif align == "right":
            x -= width
        obj = c.beginText(x, y)
        obj.setFont(font, size)
        obj.setFillColor(color_of(color))
        if tracking:
            obj.setCharSpace(tracking)
        obj.textOut(text)
        c.drawText(obj)
        return width

    def _rule(self, c, x1: float, y: float, x2: float, color, width: float = 0.6, alpha: float = 1.0):
        c.setStrokeColor(color_of(color, alpha))
        c.setLineWidth(width)
        c.setLineCap(0)
        c.line(x1, y, x2, y)

    def _paint_background(self, c: rl_canvas.Canvas, color: str | Color | None = None) -> None:
        c.setFillColor(color_of(color or self.theme.color("paper", "#FFFFFF")))
        c.rect(0, 0, self.page_w, self.page_h, stroke=0, fill=1)

    def _image_cover(self, c, path: Path, x, y, w, h, radius: float = 0.0) -> None:
        """Draw an image scaled to *cover* the box, centre-cropped."""
        try:
            from PIL import Image

            with Image.open(path) as im:
                iw, ih = im.size
        except Exception:
            iw = ih = 1024
        factor = max(w / iw, h / ih)
        dw, dh = iw * factor, ih * factor
        c.saveState()
        clip = c.beginPath()
        if radius > 0:
            clip.roundRect(x, y, w, h, radius)
        else:
            clip.rect(x, y, w, h)
        c.clipPath(clip, stroke=0, fill=0)
        try:
            c.drawImage(str(path), x + (w - dw) / 2, y + (h - dh) / 2, dw, dh, mask="auto")
        except Exception:
            c.setFillColor(self.color("band", 1.0))
            c.rect(x, y, w, h, stroke=0, fill=1)
        c.restoreState()

    def _crop_marks(self, c: rl_canvas.Canvas) -> None:
        if self.bleed <= 0:
            return
        length = min(self.bleed, 0.18 * inch)
        c.setStrokeColor(self.color("muted", 0.9))
        c.setLineWidth(0.35)
        b = self.bleed
        corners = [
            (b, b, 1, 1),
            (b + self.trim_w, b, -1, 1),
            (b, b + self.trim_h, 1, -1),
            (b + self.trim_w, b + self.trim_h, -1, -1),
        ]
        for x, y, sx, sy in corners:
            c.line(x - sx * length, y, x - sx * length * 0.25, y)
            c.line(x, y - sy * length, x, y - sy * length * 0.25)

    # ------------------------------------------------------------- helpers
    def paragraph(
        self,
        c,
        x: float,
        y: float,
        text: str,
        font: str,
        size: float,
        color,
        max_width: float,
        leading: float | None = None,
        align: str = "left",
        tracking: float = 0.0,
    ) -> float:
        """Draw wrapped text downward from `y`. Returns the final baseline."""
        leading = leading or size * 1.32
        anchor_x = x if align == "left" else (x + max_width / 2 if align == "center" else x + max_width)
        for line in wrap_text(text, font, size, max_width):
            self._text(c, anchor_x, y, line, font, size, color, align=align, tracking=tracking)
            y -= leading
        return y

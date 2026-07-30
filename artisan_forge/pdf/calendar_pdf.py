"""The calendar PDF engine.

Pure reportlab vector output - no templates, no external assets required.
Dates come from `artisan_forge.pdf.dates`, which is backed by the standard
library, so every year (leap years included) is correct by construction.

    from artisan_forge.pdf import generate_calendar
    generate_calendar(2026, start_day="Sunday", orientation="landscape")
"""

from __future__ import annotations

from pathlib import Path

from reportlab.lib.colors import Color, HexColor
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas as rl_canvas

from ..models import CalendarSpec
from ..themes import Theme, get_theme
from . import dates as D
from .fonts import FontSet, resolve_fonts

__all__ = ["generate_calendar", "generate_calendar_pdf", "CalendarPDF"]


def _color(value: str | Color, alpha: float = 1.0) -> Color:
    col = value if isinstance(value, Color) else HexColor(value)
    if alpha >= 1.0:
        return col
    return Color(col.red, col.green, col.blue, alpha)


def _fit(text: str, font: str, size: float, max_width: float) -> str:
    """Truncate with an ellipsis so labels never bleed out of their cell."""
    if pdfmetrics.stringWidth(text, font, size) <= max_width:
        return text
    ellipsis = "\u2026"
    trimmed = text
    while trimmed and pdfmetrics.stringWidth(trimmed + ellipsis, font, size) > max_width:
        trimmed = trimmed[:-1]
    return (trimmed.rstrip() + ellipsis) if trimmed else ""


class CalendarPDF:
    """Renders a full calendar document for one `CalendarSpec`."""

    def __init__(self, spec: CalendarSpec, art: dict[str, Path] | None = None):
        self.spec = spec
        self.theme: Theme = get_theme(spec.theme)
        self.fonts: FontSet = resolve_fonts(self.theme.serif)
        self.art: dict[str, Path] = {
            key: Path(value)
            for key, value in (art or {}).items()
            if value and Path(value).exists()
        }

        trim_w_in, trim_h_in = spec.trim_size_in
        self.trim_w = trim_w_in * inch
        self.trim_h = trim_h_in * inch
        self.bleed = spec.bleed_in * inch
        self.page_w = self.trim_w + 2 * self.bleed
        self.page_h = self.trim_h + 2 * self.bleed

        # Typography scale, relative to an 8.5in short edge.
        self.scale = max(0.55, min(trim_w_in, trim_h_in) / 8.5)
        self.margin_x = max(0.36 * inch, 0.072 * self.trim_w)
        self.margin_y = max(0.36 * inch, 0.058 * self.trim_h)
        self.gutter = max(0.16 * inch, 0.022 * min(self.trim_w, self.trim_h))

        self.holidays = D.holiday_map(spec.year, spec.holidays)
        self.landscape = spec.orientation == "landscape"

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
        return points * self.scale

    # ------------------------------------------------------------ primitives
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
        if not text:
            return 0.0
        width = pdfmetrics.stringWidth(text, font, size) + tracking * max(len(text) - 1, 0)
        if align == "center":
            x -= width / 2
        elif align == "right":
            x -= width
        obj = c.beginText(x, y)
        obj.setFont(font, size)
        obj.setFillColor(_color(color))
        if tracking:
            obj.setCharSpace(tracking)
        obj.textOut(text)
        c.drawText(obj)
        return width

    def _rule(self, c, x1, y, x2, color, width=0.6, alpha=1.0):
        c.setStrokeColor(_color(color, alpha))
        c.setLineWidth(width)
        c.setLineCap(0)
        c.line(x1, y, x2, y)

    def _paint_background(self, c: rl_canvas.Canvas) -> None:
        c.setFillColor(_color(self.theme.color("paper", "#FFFFFF")))
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
            c.setFillColor(_color(self.theme.color("band", "#EEEEEE")))
            c.rect(x, y, w, h, stroke=0, fill=1)
        c.restoreState()

    def _crop_marks(self, c: rl_canvas.Canvas) -> None:
        if self.bleed <= 0:
            return
        length = min(self.bleed, 0.18 * inch)
        c.setStrokeColor(_color(self.theme.color("muted", "#888888"), 0.9))
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

    # ------------------------------------------------------------- documents
    def render(self, out_path: str | Path) -> Path:
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        c = rl_canvas.Canvas(str(out_path), pagesize=(self.page_w, self.page_h))
        c.setTitle(f"{self.spec.display_title()} - {self.theme.label}")
        c.setAuthor("Artisan Forge")
        c.setSubject(
            f"{self.spec.year} printable calendar, {self.spec.size_label}, "
            f"{self.spec.start_day} start"
        )
        c.setKeywords(
            f"{self.spec.year}, calendar, printable, {self.theme.label}, {self.spec.orientation}"
        )

        if self.spec.include_cover:
            self.draw_cover(c)
            c.showPage()
        if self.spec.include_year_overview:
            self.draw_year_overview(c)
            c.showPage()
        for month in range(1, 13):
            self.draw_month(c, month)
            c.showPage()

        c.save()
        return out_path

    # ----------------------------------------------------------------- cover
    def _cover_subtitle(self) -> str:
        title = self.spec.display_title().strip()
        year = str(self.spec.year)
        if title.lower().startswith(year):
            title = title[len(year) :].strip(" -\u2013\u2014:")
        return title or "Calendar"

    def draw_cover(self, c: rl_canvas.Canvas) -> None:
        self._paint_background(c)
        art = self.art.get("cover")
        if art:
            self._image_cover(c, art, 0, 0, self.page_w, self.page_h)

        band_h = self.trim_h * (0.24 if not self.landscape else 0.3)
        band_y = self.bleed + (self.trim_h - band_h) / 2
        inset = self.margin_x * (1.15 if not self.landscape else 1.6)
        band_x = self.bleed + inset
        band_w = self.trim_w - 2 * inset

        if art:
            c.setFillColor(_color(self.theme.color("paper", "#FFFFFF"), 0.93))
            c.rect(band_x, band_y, band_w, band_h, stroke=0, fill=1)
            c.setStrokeColor(_color(self.theme.color("accent", "#000000"), 0.55))
            c.setLineWidth(0.7)
            c.rect(band_x + 6, band_y + 6, band_w - 12, band_h - 12, stroke=1, fill=0)

        mid_x = band_x + band_w / 2
        eyebrow = ("Printable Wall Calendar").upper()
        self._text(
            c,
            mid_x,
            band_y + band_h - self.fs(24),
            eyebrow,
            self.fonts.bold,
            self.fs(8.2),
            self.theme.color("accent"),
            align="center",
            tracking=self.fs(2.6),
        )

        year_size = self.fs(64) if not self.landscape else self.fs(58)
        self._text(
            c,
            mid_x,
            band_y + band_h * 0.36,
            str(self.spec.year),
            self.fonts.display,
            year_size,
            self.theme.color("ink"),
            align="center",
            tracking=self.fs(4.0),
        )

        rule_w = band_w * 0.3
        self._rule(
            c,
            mid_x - rule_w / 2,
            band_y + band_h * 0.27,
            mid_x + rule_w / 2,
            self.theme.color("accent"),
            width=0.9,
        )

        sub = self._cover_subtitle().upper()
        self._text(
            c,
            mid_x,
            band_y + band_h * 0.15,
            sub,
            self.fonts.regular,
            self.fs(11),
            self.theme.color("muted"),
            align="center",
            tracking=self.fs(3.0),
        )
        detail = f"{self.spec.size_label}  \u00b7  12 MONTHS  \u00b7  {self.spec.start_day.upper()} START"
        self._text(
            c,
            mid_x,
            band_y + self.fs(11),
            detail,
            self.fonts.regular,
            self.fs(7.4),
            self.theme.color("muted"),
            align="center",
            tracking=self.fs(1.4),
        )
        self._crop_marks(c)

    # ------------------------------------------------------------ month page
    def draw_month(self, c: rl_canvas.Canvas, month: int) -> None:
        self._paint_background(c)
        x0, y0, w, h = self.content_box()

        art_path = self.art.get(f"month_{month:02d}") or self.art.get("interior")
        show_art = self.spec.include_month_art and art_path is not None

        ax, ay, aw, ah = x0, y0, w, h
        if show_art:
            if self.landscape:
                art_w = w * 0.34
                self._image_cover(c, art_path, x0, y0, art_w, h, radius=self.fs(2))
                ax, aw = x0 + art_w + self.gutter, w - art_w - self.gutter
            else:
                art_h = h * 0.27
                self._image_cover(c, art_path, x0, y0 + h - art_h, w, art_h, radius=self.fs(2))
                ah = h - art_h - self.gutter

        notes_rect: tuple[float, float, float, float] | None = None
        if self.spec.include_notes_column:
            notes_w = aw * (0.2 if self.landscape else 0.24)
            notes_rect = (ax + aw - notes_w, ay, notes_w, ah)
            aw = aw - notes_w - self.gutter

        header_h = min(ah * 0.17, self.fs(60))
        weekday_h = min(ah * 0.075, self.fs(22))
        grid_h = ah - header_h - weekday_h
        grid_y = ay
        weekday_y = ay + grid_h
        header_y = weekday_y + weekday_h

        # --- header: month name + year -------------------------------------
        month_name = D.MONTH_NAMES[month - 1]
        label = month_name.upper() if self.theme.uppercase_title else month_name
        title_size = min(header_h * 0.56, aw * 0.17, self.fs(34))
        tracking = self.fs(self.theme.tracking)
        baseline = header_y + header_h * 0.30
        self._text(
            c, ax, baseline, label, self.fonts.display, title_size,
            self.theme.color("ink"), tracking=tracking,
        )
        self._text(
            c, ax + aw, baseline + title_size * 0.06, str(self.spec.year),
            self.fonts.regular, min(title_size * 0.42, self.fs(13)),
            self.theme.color("accent"), align="right", tracking=self.fs(2.2),
        )
        self._rule(c, ax, header_y + self.fs(1.5), ax + aw, self.theme.color("ink"), width=0.9)

        # --- weekday header -------------------------------------------------
        cell_w = aw / 7
        wd_style = "short" if cell_w > self.fs(34) else "narrow"
        labels = D.weekday_labels(self.spec.first_weekday, style=wd_style)
        wd_size = max(self.fs(5.4), min(weekday_h * 0.46, cell_w * 0.2, self.fs(9)))
        for index, name in enumerate(labels):
            weekday = (self.spec.first_weekday + index) % 7
            color = self.theme.color("weekend") if weekday >= 5 else self.theme.color("muted")
            self._text(
                c, ax + cell_w * (index + 0.5), weekday_y + weekday_h * 0.34,
                name.upper(), self.fonts.bold, wd_size, color,
                align="center", tracking=self.fs(1.2),
            )

        self._draw_grid(c, month, ax, grid_y, aw, grid_h)
        if notes_rect:
            self._draw_notes(c, *notes_rect)
        self._crop_marks(c)

    def _draw_grid(self, c, month: int, gx: float, gy: float, gw: float, gh: float) -> None:
        spec = self.spec
        weeks = D.month_grid(spec.year, month, spec.first_weekday)
        rows = len(weeks)
        cw, ch = gw / 7, gh / rows
        moon = D.moon_phases(spec.year, month) if spec.include_moon_phases else {}

        # weekend column tint
        c.setFillColor(_color(self.theme.color("band"), 0.6))
        for index in range(7):
            if (spec.first_weekday + index) % 7 >= 5:
                c.rect(gx + index * cw, gy, cw, gh, stroke=0, fill=1)

        # grid lines
        c.setStrokeColor(_color(self.theme.color("grid")))
        c.setLineWidth(0.5)
        for row in range(rows + 1):
            y = gy + row * ch
            c.line(gx, y, gx + gw, y)
        for index in range(8):
            x = gx + index * cw
            c.line(x, gy, x, gy + gh)

        num_size = min(ch * 0.36, cw * 0.36, self.fs(18))
        label_size = max(self.fs(4.4), min(ch * 0.16, cw * 0.12, self.fs(6.8)))
        pad = min(cw, ch) * 0.12

        for row, week in enumerate(weeks):
            top = gy + gh - row * ch
            for index, day in enumerate(week):
                left = gx + index * cw
                in_month = day.month == month
                if not in_month:
                    if spec.include_adjacent_days:
                        self._text(
                            c, left + pad, top - pad - num_size * 0.68,
                            str(day.day), self.fonts.regular, num_size * 0.72,
                            self.theme.color("grid"),
                        )
                    continue

                color = (
                    self.theme.color("weekend")
                    if D.is_weekend(day)
                    else self.theme.color("ink")
                )
                self._text(
                    c, left + pad, top - pad - num_size * 0.8,
                    str(day.day), self.fonts.regular, num_size, color,
                )

                name = self.holidays.get(day)
                if name:
                    self._text(
                        c, left + pad, gy + (rows - row - 1) * ch + pad * 0.9,
                        _fit(name, self.fonts.italic, label_size, cw - 2 * pad),
                        self.fonts.italic, label_size, self.theme.color("accent"),
                    )

                phase = moon.get(day)
                if phase:
                    radius = min(cw, ch) * 0.075
                    self._draw_moon(
                        c, left + cw - pad - radius, top - pad - radius, radius, phase
                    )

    def _draw_moon(self, c, cx: float, cy: float, radius: float, phase: str) -> None:
        ink = _color(self.theme.color("muted"))
        c.setStrokeColor(ink)
        c.setLineWidth(0.4)
        if phase == "new":
            c.setFillColor(ink)
            c.circle(cx, cy, radius, stroke=1, fill=1)
            return
        c.circle(cx, cy, radius, stroke=1, fill=0)
        if phase == "full":
            return
        c.saveState()
        clip = c.beginPath()
        if phase == "first":
            clip.rect(cx, cy - radius, radius, radius * 2)
        else:  # last quarter
            clip.rect(cx - radius, cy - radius, radius, radius * 2)
        c.clipPath(clip, stroke=0, fill=0)
        c.setFillColor(ink)
        c.circle(cx, cy, radius, stroke=0, fill=1)
        c.restoreState()

    def _draw_notes(self, c, x: float, y: float, w: float, h: float) -> None:
        title_size = max(self.fs(6.2), min(self.fs(9), w * 0.13))
        self._text(
            c, x, y + h - title_size * 1.6, "NOTES", self.fonts.bold, title_size,
            self.theme.color("muted"), tracking=self.fs(2.0),
        )
        top = y + h - title_size * 3.0
        step = max(self.fs(15), h * 0.055)
        line_y = top
        while line_y > y + step * 0.4:
            self._rule(c, x, line_y, x + w, self.theme.color("grid"), width=0.45)
            line_y -= step

    # ---------------------------------------------------------- overview page
    def draw_year_overview(self, c: rl_canvas.Canvas) -> None:
        self._paint_background(c)
        x0, y0, w, h = self.content_box()

        header_h = min(h * 0.15, self.fs(96))
        year_size = min(header_h * 0.5, self.fs(44))
        self._text(
            c, x0 + w / 2, y0 + h - year_size * 1.05, str(self.spec.year),
            self.fonts.display, year_size, self.theme.color("ink"),
            align="center", tracking=self.fs(8),
        )
        self._text(
            c, x0 + w / 2, y0 + h - header_h * 0.82, "YEAR AT A GLANCE",
            self.fonts.regular, self.fs(8.4), self.theme.color("muted"),
            align="center", tracking=self.fs(3.4),
        )
        rule_w = w * 0.16
        self._rule(
            c, x0 + w / 2 - rule_w / 2, y0 + h - header_h * 0.95,
            x0 + w / 2 + rule_w / 2, self.theme.color("accent"), width=0.8,
        )

        cols, rows = (4, 3) if self.landscape else (3, 4)
        area_h = h - header_h
        cell_w, cell_h = w / cols, area_h / rows
        inset_x, inset_y = cell_w * 0.07, cell_h * 0.09

        for month in range(1, 13):
            col = (month - 1) % cols
            row = (month - 1) // cols
            cx = x0 + col * cell_w + inset_x
            cy = y0 + area_h - (row + 1) * cell_h + inset_y
            self._draw_mini_month(
                c, month, cx, cy, cell_w - 2 * inset_x, cell_h - 2 * inset_y
            )
        self._crop_marks(c)

    def _draw_mini_month(self, c, month: int, x: float, y: float, w: float, h: float) -> None:
        weeks = D.month_grid(self.spec.year, month, self.spec.first_weekday)
        title_size = max(self.fs(6.5), min(h * 0.13, w * 0.11, self.fs(11)))
        self._text(
            c, x, y + h - title_size, D.MONTH_NAMES[month - 1].upper(),
            self.fonts.bold, title_size, self.theme.color("ink"),
            tracking=self.fs(1.6),
        )
        self._rule(c, x, y + h - title_size * 1.7, x + w, self.theme.color("grid"), width=0.5)

        body_top = y + h - title_size * 2.3
        cw = w / 7
        rowc = len(weeks) + 1  # +1 for the weekday header row
        ch = min((body_top - y) / rowc, cw * 1.05)
        num_size = max(self.fs(4.2), min(ch * 0.62, cw * 0.5, self.fs(7.4)))

        labels = D.weekday_labels(self.spec.first_weekday, style="narrow")
        for index, name in enumerate(labels):
            weekday = (self.spec.first_weekday + index) % 7
            color = self.theme.color("weekend") if weekday >= 5 else self.theme.color("muted")
            self._text(
                c, x + cw * (index + 0.5), body_top - ch * 0.72, name,
                self.fonts.bold, num_size * 0.92, color, align="center",
            )

        for row, week in enumerate(weeks):
            top = body_top - ch * (row + 1)
            for index, day in enumerate(week):
                if day.month != month:
                    continue
                color = (
                    self.theme.color("weekend")
                    if D.is_weekend(day)
                    else self.theme.color("ink")
                )
                if day in self.holidays:
                    color = self.theme.color("accent")
                self._text(
                    c, x + cw * (index + 0.5), top - ch * 0.72, str(day.day),
                    self.fonts.regular, num_size, color, align="center",
                )


# ------------------------------------------------------------ public helpers
def generate_calendar_pdf(
    spec: CalendarSpec,
    out_path: str | Path | None = None,
    art: dict[str, Path] | None = None,
) -> Path:
    """Render `spec` to a PDF and return the path."""
    if out_path is None:
        out_path = Path(f"calendar_{spec.year}_{spec.theme}_{spec.orientation}.pdf")
    return CalendarPDF(spec, art).render(out_path)


def generate_calendar(
    year: int,
    start_day: str = "Sunday",
    orientation: str = "landscape",
    theme: str = "minimalist",
    paper: str = "letter",
    out_path: str | Path | None = None,
    art: dict[str, Path] | None = None,
    **spec_kwargs,
) -> Path:
    """Convenience wrapper: one call, one perfect 12-month calendar PDF.

    >>> generate_calendar(2026, start_day="Sunday", orientation="portrait")
    """
    spec = CalendarSpec(
        year=year,
        theme=theme,
        start_day=start_day,  # type: ignore[arg-type]
        orientation=orientation,  # type: ignore[arg-type]
        paper=paper,
        **spec_kwargs,
    )
    from ..brief import validate

    spec = validate(spec)
    if out_path is None:
        out_path = Path(f"calendar_{year}_{spec.theme}_{spec.orientation}.pdf")
    return generate_calendar_pdf(spec, out_path, art)


def month_page_count(spec: CalendarSpec) -> int:
    """Total pages the document will contain."""
    return 12 + int(spec.include_cover) + int(spec.include_year_overview)


def first_month_page_index(spec: CalendarSpec) -> int:
    """0-based index of the January page inside the rendered document."""
    return int(spec.include_cover) + int(spec.include_year_overview)

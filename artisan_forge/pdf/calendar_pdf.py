"""The calendar PDF engine.

Pure reportlab vector output - no templates, no external assets required.
Dates come from `artisan_forge.pdf.dates` via the shared `month_grid_block`,
which is backed by the standard library, so every year (leap years included)
is correct by construction.

    from artisan_forge.pdf import generate_calendar
    generate_calendar(2026, start_day="Sunday", orientation="landscape")
"""

from __future__ import annotations

from pathlib import Path

from reportlab.pdfgen import canvas as rl_canvas

from ..models import CalendarSpec
from ..themes import get_theme
from . import dates as D
from .blocks import mini_month_block, month_grid_block, weekday_header_block
from .drawkit import DrawKit

__all__ = ["generate_calendar", "generate_calendar_pdf", "CalendarPDF"]


class CalendarPDF(DrawKit):
    """Renders a full calendar document for one `CalendarSpec`."""

    def __init__(self, spec: CalendarSpec, art: dict[str, Path] | None = None):
        super().__init__(get_theme(spec.theme), spec.trim_size_in, spec.bleed_in)
        self.spec = spec
        self.art: dict[str, Path] = {
            key: Path(value)
            for key, value in (art or {}).items()
            if value and Path(value).exists()
        }
        self.holidays = D.holiday_map(spec.year, spec.holidays)
        self.moon = (
            {m: D.moon_phases(spec.year, m) for m in range(1, 13)}
            if spec.include_moon_phases
            else {}
        )
        self.landscape = spec.orientation == "landscape"

    # ------------------------------------------------------------- documents
    def render(self, out_path: str | Path) -> Path:
        out_path = Path(out_path)
        c = self.new_canvas(
            out_path,
            title=f"{self.spec.display_title()} - {self.theme.label}",
            subject=(
                f"{self.spec.year} printable calendar, {self.spec.size_label}, "
                f"{self.spec.start_day} start"
            ),
            keywords=(
                f"{self.spec.year}, calendar, printable, {self.theme.label}, "
                f"{self.spec.orientation}"
            ),
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
            c.setFillColor(self.color("paper", 0.93))
            c.rect(band_x, band_y, band_w, band_h, stroke=0, fill=1)
            c.setStrokeColor(self.color("accent", 0.55))
            c.setLineWidth(0.7)
            c.rect(band_x + 6, band_y + 6, band_w - 12, band_h - 12, stroke=1, fill=0)

        mid_x = band_x + band_w / 2
        self._text(
            c, mid_x, band_y + band_h - self.fs(24), "PRINTABLE WALL CALENDAR",
            self.fonts.bold, self.fs(8.2), self.theme.color("accent"),
            align="center", tracking=self.fs(2.6),
        )

        year_size = self.fs(64) if not self.landscape else self.fs(58)
        self._text(
            c, mid_x, band_y + band_h * 0.36, str(self.spec.year), self.fonts.display,
            year_size, self.theme.color("ink"), align="center", tracking=self.fs(4.0),
        )

        rule_w = band_w * 0.3
        self._rule(
            c, mid_x - rule_w / 2, band_y + band_h * 0.27, mid_x + rule_w / 2,
            self.theme.color("accent"), width=0.9,
        )

        self._text(
            c, mid_x, band_y + band_h * 0.15, self._cover_subtitle().upper(),
            self.fonts.regular, self.fs(11), self.theme.color("muted"),
            align="center", tracking=self.fs(3.0),
        )
        detail = (
            f"{self.spec.size_label}  \u00b7  12 MONTHS  \u00b7  "
            f"{self.spec.start_day.upper()} START"
        )
        self._text(
            c, mid_x, band_y + self.fs(11), detail, self.fonts.regular, self.fs(7.4),
            self.theme.color("muted"), align="center", tracking=self.fs(1.4),
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

        # header: month name + year
        month_name = D.MONTH_NAMES[month - 1]
        label = month_name.upper() if self.theme.uppercase_title else month_name
        title_size = min(header_h * 0.56, aw * 0.17, self.fs(34))
        baseline = header_y + header_h * 0.30
        self._text(
            c, ax, baseline, label, self.fonts.display, title_size,
            self.theme.color("ink"), tracking=self.fs(self.theme.tracking),
        )
        self._text(
            c, ax + aw, baseline + title_size * 0.06, str(self.spec.year),
            self.fonts.regular, min(title_size * 0.42, self.fs(13)),
            self.theme.color("accent"), align="right", tracking=self.fs(2.2),
        )
        self._rule(c, ax, header_y + self.fs(1.5), ax + aw, self.theme.color("ink"), width=0.9)

        weekday_header_block(self, c, (ax, weekday_y, aw, weekday_h), self.spec.first_weekday)
        month_grid_block(
            self, c, (ax, grid_y, aw, grid_h), self.spec.year, month,
            first_weekday=self.spec.first_weekday,
            holidays=self.holidays,
            moon=self.moon.get(month),
            adjacent_days=self.spec.include_adjacent_days,
        )
        if notes_rect:
            self._draw_notes(c, *notes_rect)
        self._crop_marks(c)

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
            mini_month_block(
                self,
                c,
                (
                    x0 + col * cell_w + inset_x,
                    y0 + area_h - (row + 1) * cell_h + inset_y,
                    cell_w - 2 * inset_x,
                    cell_h - 2 * inset_y,
                ),
                self.spec.year,
                month,
                first_weekday=self.spec.first_weekday,
                holidays=self.holidays,
            )
        self._crop_marks(c)


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

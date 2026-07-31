"""Reusable page blocks.

Each block draws one component into a rectangle: a month grid, a weekday
header, ruled lines, a dot grid, a checkbox list, a table. Products compose
pages out of blocks, so the date grid in particular has exactly one
implementation shared by calendars and planners.
"""

from __future__ import annotations

import datetime as dt

from . import dates as D
from .drawkit import DrawKit, color_of, fit_text

Rect = tuple[float, float, float, float]


# --------------------------------------------------------------------- dates
def weekday_header_block(
    kit: DrawKit,
    c,
    rect: Rect,
    first_weekday: int = 6,
    style: str | None = None,
    weight: str = "bold",
) -> None:
    x, y, w, h = rect
    cell_w = w / 7
    if style is None:
        style = "short" if cell_w > kit.fs(34) else "narrow"
    labels = D.weekday_labels(first_weekday, style=style)
    size = max(kit.fs(5.4), min(h * 0.46, cell_w * 0.2, kit.fs(9)))
    font = kit.fonts.bold if weight == "bold" else kit.fonts.regular
    for index, name in enumerate(labels):
        weekday = (first_weekday + index) % 7
        color = kit.theme.color("weekend") if weekday >= 5 else kit.theme.color("muted")
        kit._text(
            c, x + cell_w * (index + 0.5), y + h * 0.34, name.upper(), font, size, color,
            align="center", tracking=kit.fs(1.2),
        )


def moon_marker(kit: DrawKit, c, cx: float, cy: float, radius: float, phase: str) -> None:
    ink = kit.color("muted")
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


def month_grid_block(
    kit: DrawKit,
    c,
    rect: Rect,
    year: int,
    month: int,
    first_weekday: int = 6,
    holidays: dict[dt.date, str] | None = None,
    moon: dict[dt.date, str] | None = None,
    adjacent_days: bool = True,
    weekend_tint: bool = True,
) -> None:
    """The one true month grid: dates come straight from `dates.month_grid`."""
    holidays = holidays or {}
    moon = moon or {}
    gx, gy, gw, gh = rect
    weeks = D.month_grid(year, month, first_weekday)
    rows = len(weeks)
    cw, ch = gw / 7, gh / rows

    if weekend_tint:
        c.setFillColor(kit.color("band", 0.6))
        for index in range(7):
            if (first_weekday + index) % 7 >= 5:
                c.rect(gx + index * cw, gy, cw, gh, stroke=0, fill=1)

    c.setStrokeColor(kit.color("grid"))
    c.setLineWidth(0.5)
    for row in range(rows + 1):
        y = gy + row * ch
        c.line(gx, y, gx + gw, y)
    for index in range(8):
        x = gx + index * cw
        c.line(x, gy, x, gy + gh)

    num_size = min(ch * 0.36, cw * 0.36, kit.fs(18))
    label_size = max(kit.fs(4.4), min(ch * 0.16, cw * 0.12, kit.fs(6.8)))
    pad = min(cw, ch) * 0.12

    for row, week in enumerate(weeks):
        top = gy + gh - row * ch
        for index, day in enumerate(week):
            left = gx + index * cw
            in_month = day.month == month
            if not in_month:
                if adjacent_days:
                    kit._text(
                        c, left + pad, top - pad - num_size * 0.68, str(day.day),
                        kit.fonts.regular, num_size * 0.72, kit.theme.color("grid"),
                    )
                continue

            color = (
                kit.theme.color("weekend") if D.is_weekend(day) else kit.theme.color("ink")
            )
            kit._text(
                c, left + pad, top - pad - num_size * 0.8, str(day.day),
                kit.fonts.regular, num_size, color,
            )

            name = holidays.get(day)
            if name:
                kit._text(
                    c, left + pad, gy + (rows - row - 1) * ch + pad * 0.9,
                    fit_text(name, kit.fonts.italic, label_size, cw - 2 * pad),
                    kit.fonts.italic, label_size, kit.theme.color("accent"),
                )

            phase = moon.get(day)
            if phase:
                radius = min(cw, ch) * 0.075
                moon_marker(kit, c, left + cw - pad - radius, top - pad - radius, radius, phase)


def mini_month_block(
    kit: DrawKit,
    c,
    rect: Rect,
    year: int,
    month: int,
    first_weekday: int = 6,
    holidays: dict[dt.date, str] | None = None,
) -> None:
    """Compact month used on overview pages and planner spreads."""
    holidays = holidays or {}
    x, y, w, h = rect
    weeks = D.month_grid(year, month, first_weekday)
    title_size = max(kit.fs(6.5), min(h * 0.13, w * 0.11, kit.fs(11)))
    kit._text(
        c, x, y + h - title_size, D.MONTH_NAMES[month - 1].upper(), kit.fonts.bold,
        title_size, kit.theme.color("ink"), tracking=kit.fs(1.6),
    )
    kit._rule(c, x, y + h - title_size * 1.7, x + w, kit.theme.color("grid"), width=0.5)

    body_top = y + h - title_size * 2.3
    cw = w / 7
    rowc = len(weeks) + 1
    ch = min((body_top - y) / rowc, cw * 1.05)
    num_size = max(kit.fs(4.2), min(ch * 0.62, cw * 0.5, kit.fs(7.4)))

    for index, name in enumerate(D.weekday_labels(first_weekday, style="narrow")):
        weekday = (first_weekday + index) % 7
        color = kit.theme.color("weekend") if weekday >= 5 else kit.theme.color("muted")
        kit._text(
            c, x + cw * (index + 0.5), body_top - ch * 0.72, name, kit.fonts.bold,
            num_size * 0.92, color, align="center",
        )

    for row, week in enumerate(weeks):
        top = body_top - ch * (row + 1)
        for index, day in enumerate(week):
            if day.month != month:
                continue
            color = (
                kit.theme.color("weekend") if D.is_weekend(day) else kit.theme.color("ink")
            )
            if day in holidays:
                color = kit.theme.color("accent")
            kit._text(
                c, x + cw * (index + 0.5), top - ch * 0.72, str(day.day),
                kit.fonts.regular, num_size, color, align="center",
            )


# ------------------------------------------------------------------ writing
def ruled_lines_block(
    kit: DrawKit,
    c,
    rect: Rect,
    step: float | None = None,
    color: str | None = None,
    width: float = 0.45,
) -> int:
    """Horizontal writing rules. Returns the number drawn."""
    x, y, w, h = rect
    step = step or max(kit.fs(15), h * 0.05)
    color = color or kit.theme.color("grid")
    drawn = 0
    line_y = y + h - step
    while line_y > y:
        kit._rule(c, x, line_y, x + w, color, width=width)
        line_y -= step
        drawn += 1
    return drawn


def dot_grid_block(kit: DrawKit, c, rect: Rect, spacing: float | None = None, radius: float | None = None) -> None:
    x, y, w, h = rect
    spacing = spacing or kit.fs(14)
    radius = radius or max(0.35, kit.fs(0.6))
    c.setFillColor(kit.color("grid"))
    rows = int(h // spacing)
    cols = int(w // spacing)
    offset_x = (w - cols * spacing) / 2
    offset_y = (h - rows * spacing) / 2
    for row in range(rows + 1):
        for col in range(cols + 1):
            c.circle(x + offset_x + col * spacing, y + offset_y + row * spacing, radius, stroke=0, fill=1)


def graph_grid_block(kit: DrawKit, c, rect: Rect, spacing: float | None = None) -> None:
    x, y, w, h = rect
    spacing = spacing or kit.fs(14)
    c.setStrokeColor(kit.color("grid", 0.9))
    c.setLineWidth(0.35)
    cols = int(w // spacing)
    rows = int(h // spacing)
    offset_x = (w - cols * spacing) / 2
    offset_y = (h - rows * spacing) / 2
    for col in range(cols + 1):
        px = x + offset_x + col * spacing
        c.line(px, y + offset_y, px, y + offset_y + rows * spacing)
    for row in range(rows + 1):
        py = y + offset_y + row * spacing
        c.line(x + offset_x, py, x + offset_x + cols * spacing, py)


def checkbox_list_block(
    kit: DrawKit,
    c,
    rect: Rect,
    rows: int,
    label: str | None = None,
    rule: bool = True,
) -> None:
    """A checkbox column - to-dos, habits, priorities."""
    x, y, w, h = rect
    top = y + h
    if label:
        size = max(kit.fs(6.5), min(kit.fs(9), w * 0.12))
        kit._text(c, x, top - size, label.upper(), kit.fonts.bold, size,
                  kit.theme.color("muted"), tracking=kit.fs(1.8))
        top -= size * 2.1
    step = (top - y) / max(rows, 1)
    box = min(step * 0.42, kit.fs(9))
    c.setStrokeColor(kit.color("grid"))
    c.setLineWidth(0.6)
    for index in range(rows):
        line_y = top - step * (index + 1)
        c.rect(x, line_y + step * 0.28, box, box, stroke=1, fill=0)
        if rule:
            kit._rule(c, x + box * 1.7, line_y + step * 0.24, x + w, kit.theme.color("grid"), width=0.4)


def table_block(
    kit: DrawKit,
    c,
    rect: Rect,
    columns: list[str],
    rows: int,
    header_fill: bool = True,
) -> None:
    """A simple ruled table with an optional tinted header row."""
    x, y, w, h = rect
    cols = max(len(columns), 1)
    cw = w / cols
    header_h = min(h * 0.14, kit.fs(22))
    body_h = h - header_h
    row_h = body_h / max(rows, 1)

    if header_fill:
        c.setFillColor(kit.color("band", 0.85))
        c.rect(x, y + body_h, w, header_h, stroke=0, fill=1)

    size = max(kit.fs(5.4), min(header_h * 0.42, cw * 0.16, kit.fs(8.5)))
    for index, name in enumerate(columns):
        kit._text(
            c, x + cw * (index + 0.5), y + body_h + header_h * 0.34,
            fit_text(name.upper(), kit.fonts.bold, size, cw * 0.9), kit.fonts.bold, size,
            kit.theme.color("ink"), align="center", tracking=kit.fs(1.0),
        )

    c.setStrokeColor(kit.color("grid"))
    c.setLineWidth(0.45)
    for row in range(rows + 1):
        line_y = y + row * row_h
        c.line(x, line_y, x + w, line_y)
    c.line(x, y + h, x + w, y + h)
    for col in range(cols + 1):
        line_x = x + col * cw
        c.line(line_x, y, line_x, y + h)


def section_title_block(
    kit: DrawKit,
    c,
    rect: Rect,
    title: str,
    subtitle: str | None = None,
    rule: bool = True,
    align: str = "left",
) -> None:
    x, y, w, h = rect
    size = min(h * 0.52, w * 0.16, kit.fs(30))
    anchor = x if align == "left" else (x + w / 2 if align == "center" else x + w)
    kit._text(
        c, anchor, y + h * 0.34, title, kit.fonts.display, size, kit.theme.color("ink"),
        align=align, tracking=kit.fs(kit.theme.tracking),
    )
    if subtitle:
        kit._text(
            c, x + w, y + h * 0.38, subtitle, kit.fonts.regular,
            min(size * 0.42, kit.fs(13)), kit.theme.color("accent"),
            align="right", tracking=kit.fs(2.0),
        )
    if rule:
        kit._rule(c, x, y + kit.fs(1.5), x + w, kit.theme.color("ink"), width=0.9)


def label_box_block(
    kit: DrawKit,
    c,
    rect: Rect,
    label: str,
    lines: int = 0,
    dotted: bool = False,
) -> None:
    """A titled box for notes, goals, gratitude, water intake, etc."""
    x, y, w, h = rect
    radius = min(kit.fs(6), h * 0.12)
    c.setStrokeColor(kit.color("grid"))
    c.setLineWidth(0.6)
    c.roundRect(x, y, w, h, radius, stroke=1, fill=0)
    size = max(kit.fs(5.8), min(kit.fs(8.5), w * 0.1))
    pad = min(kit.fs(8), h * 0.12)
    kit._text(
        c, x + pad, y + h - pad - size * 0.9, label.upper(), kit.fonts.bold, size,
        kit.theme.color("muted"), tracking=kit.fs(1.6),
    )
    inner = (x + pad, y + pad, w - 2 * pad, h - 2 * pad - size * 2.2)
    if dotted:
        dot_grid_block(kit, c, inner)
    elif lines:
        ruled_lines_block(kit, c, inner, step=inner[3] / max(lines, 1))

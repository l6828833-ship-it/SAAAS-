"""Calendar product: presentation layer for the calendar engine."""

from __future__ import annotations

from ..mockups.context import MockupContext
from ..models import CalendarSpec
from ..pdf.calendar_pdf import first_month_page_index
from ..themes import get_theme


def mockup_context(spec: CalendarSpec) -> MockupContext:
    """Describe a calendar product to the mockup compositor."""
    theme = get_theme(spec.theme)
    start = first_month_page_index(spec)
    size_compact = spec.size_label.replace('"', "in")
    w_in, h_in = spec.trim_size_in

    size_notes = [
        f'Included trim size \u2014 {w_in:g}" x {h_in:g}" '
        f"({round(w_in * 25.4)} x {round(h_in * 25.4)} mm)",
        "A4 version included \u2014 210 x 297 mm"
        if spec.has_a4_companion
        else "Scales cleanly to any paper size",
        "PDF \u00b7 vector text \u00b7 300 DPI ready",
    ]

    bullets = [
        f"12 month pages \u2014 January to December {spec.year}",
        f"{spec.size_label} print-ready PDF"
        + (" (US Letter & A4)" if spec.has_a4_companion else ""),
        f"{spec.start_day}-start week layout",
    ]
    if spec.include_cover or spec.include_year_overview:
        bullets.append("Cover page + year-at-a-glance overview")
    bullets.append("Print at home or at any print shop, unlimited times")

    return MockupContext(
        theme_key=spec.theme,
        trim_size_in=spec.trim_size_in,
        size_label=spec.size_label,
        orientation=spec.orientation,
        eyebrow="printable digital download",
        title_lines=[f"{spec.year} {theme.label}", "Calendar"],
        badges=["12 months", size_compact, "instant download"],
        grid_eyebrow="everything included",
        grid_headline="12 Months Ready to Print",
        grid_caption=f"January \u2013 December {spec.year}",
        grid_cols=4,
        grid_rows=3,
        included_headline="Instant digital bundle",
        bullets=bullets,
        captions={
            "gallery_headline": "Print any month you love",
            "gallery_caption": "Mix, match and reframe all year long",
            "desk_eyebrow": "desk, wall or planner ready",
            "desk_caption": f"January {spec.year} \u00b7 {spec.size_label}",
            "detail_headline": "Crisp, print-perfect dates",
            "detail_caption": "Verified date grid \u00b7 clean typography \u00b7 no pixelation",
            "size_headline": "Fits Letter and A4" if spec.has_a4_companion else "Print-ready sizes",
        },
        size_notes=size_notes,
        a4_included=spec.has_a4_companion,
        cover_index=0 if spec.include_cover else start,
        page_indexes=list(range(start, start + 12)),
    )

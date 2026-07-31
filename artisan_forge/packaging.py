"""Listing copy, buyer documents and the delivery ZIP."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from .models import CalendarSpec
from .themes import get_theme

MAX_TAG_LEN = 20
MAX_TAGS = 13
MAX_TITLE_LEN = 140


def _clean_tag(value: str) -> str:
    tag = " ".join(value.lower().split())
    return tag[:MAX_TAG_LEN].strip()


def etsy_tags(spec: CalendarSpec) -> list[str]:
    theme = get_theme(spec.theme)
    candidates = [
        f"{spec.year} calendar",
        "printable calendar",
        "digital calendar",
        f"{theme.label} calendar",
        "wall calendar",
        "instant download",
        "monthly calendar",
        "calendar pdf",
        "home office decor",
        "planner printable",
    ]
    candidates.extend(f"{kw} calendar" for kw in theme.keywords[:3])
    candidates.extend(["desk calendar", "12 month calendar", "gift for her", "teacher gift"])

    tags: list[str] = []
    for candidate in candidates:
        tag = _clean_tag(candidate)
        if tag and tag not in tags:
            tags.append(tag)
        if len(tags) == MAX_TAGS:
            break
    return tags


def etsy_title(spec: CalendarSpec) -> str:
    theme = get_theme(spec.theme)
    size = spec.size_label.replace(chr(34), "in")
    parts = [
        f"{spec.year} {theme.label} Calendar Printable",
        "12 Month Wall Calendar",
        f"{size} + A4" if spec.has_a4_companion else size,
        f"{spec.start_day} Start",
        "Instant Download PDF",
    ]
    title = " | ".join(parts)
    while len(title) > MAX_TITLE_LEN and len(parts) > 2:
        parts.pop(-2)
        title = " | ".join(parts)
    return title[:MAX_TITLE_LEN].rstrip(" |")


def etsy_description(spec: CalendarSpec) -> str:
    theme = get_theme(spec.theme)
    holiday_line = (
        f"- {spec.holidays} holidays and observances marked"
        if spec.holidays
        else "- Clean grid with no pre-printed holidays"
    )
    extras = []
    if spec.include_notes_column:
        extras.append("- Notes column on every month")
    if spec.include_moon_phases:
        extras.append("- Moon phases marked (new, first quarter, full, last quarter)")
    if spec.include_year_overview:
        extras.append("- Year-at-a-glance overview page")
    if spec.include_cover:
        extras.append("- Matching cover page")

    pages = 12 + int(spec.include_cover) + int(spec.include_year_overview)
    file_line = (
        f"- 2 print-ready PDFs ({pages} pages each): US Letter and A4"
        if spec.has_a4_companion
        else f"- 1 print-ready PDF, {pages} pages"
    )
    size_line = (
        f"- {spec.size_label} ({spec.orientation}), scales cleanly to A4"
        if spec.has_a4_companion
        else f"- {spec.size_label} ({spec.orientation}), scales to any paper size"
    )
    return "\n".join(
        [
            f"{spec.year} {theme.label} Calendar - printable, instant download",
            "",
            f"A calm, {theme.label.lower()} 12-month calendar you can print as many times as you "
            f"like. Every date is generated and machine-verified, so there are no wrong weekdays "
            f"and no missing days.",
            "",
            "WHAT YOU GET",
            file_line,
            f"- January to December {spec.year}, {spec.start_day}-start weeks",
            size_line,
            holiday_line,
            *extras,
            "",
            "HOW IT WORKS",
            "1. Buy and download instantly - nothing is shipped.",
            "2. Print at home on card stock or send the PDF to a print shop.",
            "3. Frame it, clip it to a board, or bind it. Print again any time.",
            "",
            "PRINTING TIPS",
            "- Choose 'Actual size' (100%) for exact dimensions, or 'Fit to page' for A4.",
            "- 100-160 gsm paper or matte card stock gives the best result.",
            "",
            "TERMS",
            "For personal use only. Files may not be resold, shared or redistributed.",
            "",
            "Colours may vary slightly between screens and printers.",
        ]
    )


def listing_copy(spec: CalendarSpec) -> dict:
    return {
        "title": etsy_title(spec),
        "tags": etsy_tags(spec),
        "description": etsy_description(spec),
        "materials": ["PDF", "Digital Download", "Printable"],
        "who_made_it": "i_did",
        "is_digital": True,
        "suggested_price_usd": 6.50 if spec.include_month_art else 4.50,
        "sections": ["Printable Calendars", f"{spec.year} Calendars"],
    }


def buyer_readme(spec: CalendarSpec) -> str:
    theme = get_theme(spec.theme)
    return "\n".join(
        [
            f"{spec.year} {theme.label} Calendar",
            "=" * 40,
            "",
            "Thank you for your purchase.",
            "",
            "FILES",
            f"- {spec.product_slug()}-{spec.paper}.pdf  ({spec.size_label}, {spec.orientation})",
            *(
                [f"- {spec.product_slug()}-a4.pdf  (A4, 210 x 297 mm)"]
                if spec.has_a4_companion
                else []
            ),
            "",
            "PRINTING",
            "1. Open the PDF in Adobe Reader (free) or your browser.",
            "2. Print settings: Actual size / 100% scale, portrait or landscape to match.",
            "3. Paper: 100-160 gsm matte card stock is ideal.",
            "",
            "TROUBLESHOOTING",
            "- Margins cut off? Choose 'Fit to printable area'.",
            "- Colours look different? Printer profiles vary; the file is print-ready RGB.",
            "",
            "LICENCE",
            "Personal use only. Please do not resell, share or redistribute these files.",
            "",
            "Made with Artisan Forge.",
        ]
    )


def license_text(spec: CalendarSpec) -> str:
    return "\n".join(
        [
            "PERSONAL USE LICENCE",
            "",
            f"Product: {spec.year} {get_theme(spec.theme).label} Calendar",
            "",
            "You may:",
            "- Print this file as many times as you like for personal use",
            "- Print copies as gifts for friends and family",
            "",
            "You may not:",
            "- Resell, share, or redistribute the digital files",
            "- Sell printed copies commercially",
            "- Claim the design as your own",
            "",
            "All rights reserved by the seller.",
        ]
    )


def write_buyer_docs(spec: CalendarSpec, out_dir: str | Path) -> list[Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    files = []
    readme = out_dir / "READ ME FIRST.txt"
    readme.write_text(buyer_readme(spec), encoding="utf-8")
    files.append(readme)
    licence = out_dir / "LICENSE - personal use.txt"
    licence.write_text(license_text(spec), encoding="utf-8")
    files.append(licence)
    return files


def generic_license_text(title: str) -> str:
    return "\n".join(
        [
            "PERSONAL USE LICENCE",
            "",
            f"Product: {title}",
            "",
            "You may:",
            "- Print and use this file as many times as you like for personal use",
            "- Print copies as gifts for friends and family",
            "",
            "You may not:",
            "- Resell, share, or redistribute the digital files",
            "- Sell printed copies commercially",
            "- Claim the design as your own",
            "",
            "All rights reserved by the seller.",
        ]
    )


def write_product_docs(
    title: str,
    file_lines: list[str],
    out_dir: str | Path,
    printing: list[str] | None = None,
) -> list[Path]:
    """Buyer README + licence for any product type."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    printing = printing or [
        "1. Open the PDF in Adobe Reader (free) or your browser.",
        "2. Print settings: Actual size / 100% scale.",
        "3. Paper: 100-160 gsm matte card stock gives the best result.",
    ]
    readme = out_dir / "READ ME FIRST.txt"
    readme.write_text(
        "\n".join(
            [
                title,
                "=" * max(len(title), 12),
                "",
                "Thank you for your purchase.",
                "",
                "FILES",
                *file_lines,
                "",
                "PRINTING",
                *printing,
                "",
                "LICENCE",
                "Personal use only. Please do not resell, share or redistribute these files.",
                "",
                "Made with Artisan Forge.",
            ]
        ),
        encoding="utf-8",
    )
    licence = out_dir / "LICENSE - personal use.txt"
    licence.write_text(generic_license_text(title), encoding="utf-8")
    return [readme, licence]


def write_copy(copy: dict, out_dir: str | Path) -> tuple[Path, Path]:
    """Write etsy_listing.json / .txt for any product type."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "etsy_listing.json"
    json_path.write_text(json.dumps(copy, indent=2), encoding="utf-8")
    text_path = out_dir / "etsy_listing.txt"
    text_path.write_text(
        "\n".join(
            [
                "TITLE",
                copy["title"],
                "",
                f"TAGS ({len(copy['tags'])})",
                ", ".join(copy["tags"]),
                "",
                "DESCRIPTION",
                copy["description"],
                "",
                f"SUGGESTED PRICE: ${copy.get('suggested_price_usd', 0):.2f}",
            ]
        ),
        encoding="utf-8",
    )
    return json_path, text_path


def write_listing_copy(spec: CalendarSpec, out_dir: str | Path) -> tuple[Path, Path, dict]:
    copy = listing_copy(spec)
    json_path, text_path = write_copy(copy, out_dir)
    return json_path, text_path, copy


def build_zip(out_path: str | Path, files: list[Path], arc_prefix: str = "") -> Path:
    """Zip the buyer deliverables (deflated, deterministic order)."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            path = Path(path)
            if not path.exists():
                continue
            arcname = f"{arc_prefix}{path.name}" if arc_prefix else path.name
            archive.write(path, arcname)
    return out_path

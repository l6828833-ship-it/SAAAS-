"""Command line interface.

    python -m artisan_forge "2026 minimalist calendar with watercolor floral theme, 8.5x11, Sunday start"
    python -m artisan_forge --year 2027 --theme boho --orientation landscape --paper a4
    python -m artisan_forge --verify 2000-2100
    python -m artisan_forge --list-themes
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .brief import parse_brief
from .config import get_settings
from .models import PAPER_SIZES
from .pdf.verify import verify_years
from .pipeline import build_product
from .themes import theme_labels


def _bar(message: str, fraction: float) -> None:
    filled = int(fraction * 28)
    sys.stdout.write(f"\r[{'#' * filled}{'.' * (28 - filled)}] {fraction * 100:5.1f}%  {message[:52]:<52}")
    sys.stdout.flush()
    if fraction >= 1.0:
        sys.stdout.write("\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="artisan_forge",
        description="Artisan Forge - generate print-ready calendar products and Etsy assets.",
    )
    parser.add_argument("brief", nargs="?", help="Plain-English product description")
    parser.add_argument("--year", type=int)
    parser.add_argument("--theme", choices=sorted(theme_labels()))
    parser.add_argument("--paper", choices=sorted(PAPER_SIZES))
    parser.add_argument("--orientation", choices=["portrait", "landscape"])
    parser.add_argument("--start-day", choices=["Sunday", "Monday"], dest="start_day")
    parser.add_argument("--title")
    parser.add_argument("--holidays", choices=["US", "UK", "none"])
    parser.add_argument("--month-art", choices=["unique", "seasonal", "single"], dest="month_art_mode")
    parser.add_argument("--notes", action="store_true", help="Add a notes column to each month")
    parser.add_argument("--moon", action="store_true", help="Mark moon phases")
    parser.add_argument("--bleed", type=float, default=None, help="Bleed in inches (0-0.5)")
    parser.add_argument("--images", type=int, default=None, help="Listing images to composite (1-10)")
    parser.add_argument("--no-art", action="store_true", help="Plain pages, no artwork panels")
    parser.add_argument("--offline", action="store_true", help="Force procedural art (no API calls)")
    parser.add_argument("--canva", action="store_true", help="Also create an editable Canva design")
    parser.add_argument("--out", type=Path, help="Output directory for this run")
    parser.add_argument("--json", action="store_true", help="Print the result summary as JSON")
    parser.add_argument("--verify", metavar="YEARS", help="Verify date maths, e.g. 2000-2100")
    parser.add_argument("--list-themes", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.list_themes:
        for key, label in theme_labels().items():
            print(f"{key:20} {label}")
        return 0

    if args.verify:
        text = args.verify
        if "-" in text:
            start, _, end = text.partition("-")
            years = range(int(start), int(end) + 1)
        else:
            years = range(int(text), int(text) + 1)
        report = verify_years(years, 6)
        report_monday = verify_years(years, 0)
        ok = report["ok"] and report_monday["ok"]
        print(json.dumps({"sunday_start": report, "monday_start": report_monday}, indent=2))
        print("OK" if ok else "FAILED")
        return 0 if ok else 1

    if not args.brief and not args.year:
        build_parser().print_help()
        return 2

    overrides = {
        "year": args.year,
        "theme": args.theme,
        "paper": args.paper,
        "orientation": args.orientation,
        "start_day": args.start_day,
        "title": args.title,
        "month_art_mode": args.month_art_mode,
        "bleed_in": args.bleed,
    }
    if args.holidays:
        overrides["holidays"] = None if args.holidays == "none" else args.holidays
    if args.notes:
        overrides["include_notes_column"] = True
    if args.moon:
        overrides["include_moon_phases"] = True
    if args.no_art:
        overrides["include_month_art"] = False
    if args.canva:
        overrides["canva_export"] = True
    if args.images:
        overrides["listing_image_count"] = args.images

    spec = parse_brief(args.brief or "", **{k: v for k, v in overrides.items() if v is not None})

    settings = get_settings()
    if args.offline:
        settings.force_offline = True

    result = build_product(spec, out_dir=args.out, progress=_bar, settings=settings)

    if args.json:
        print(json.dumps(result.summary(), indent=2))
    else:
        print(f"\nRun directory : {result.run_dir}")
        print(f"PDF           : {result.pdf_path}")
        for key, path in result.pdf_paths.items():
            print(f"  {key:<10}  : {path.name}")
        print(f"Listing images: {len(result.listing_images)} in {result.run_dir / 'mockups'}")
        print(f"Buyer ZIP     : {result.zip_path}")
        print(f"Artwork       : {result.art_source}")
        print(f"Dates verified: {result.verification.get('ok')} "
              f"({result.verification.get('checks')} checks, "
              f"{result.verification.get('text_check')})")
        print(f"Listing title : {result.listing_copy.get('title')}")
        if result.canva.get("status") not in (None, "not requested"):
            print(f"Canva         : {result.canva}")
        for warning in result.warnings:
            print(f"WARNING       : {warning}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

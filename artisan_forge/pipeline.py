"""End-to-end build orchestration.

    build_product("2026 minimalist calendar with watercolor floral theme, 8.5x11, Sunday start")

produces, in one run directory:
    print/     the print-ready PDF(s)
    art/       cover + interior artwork
    mockups/   Etsy listing images
    <slug>-etsy-files.zip, etsy_listing.txt/json, manifest.json
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import time
from pathlib import Path
from typing import Callable

from .ai.image_client import ImageStudio
from .brief import parse_brief, validate
from .canva.client import export_to_canva
from .config import Settings, get_settings
from .mockups.compose import build_listing_images
from .models import BuildResult, CalendarSpec
from .packaging import write_buyer_docs, write_listing_copy
from .packaging import build_zip
from .pdf.calendar_pdf import generate_calendar_pdf, month_page_count
from .pdf.verify import verify_calendar_pdf, verify_grid_math

Progress = Callable[[str, float], None]

COMPANION_PAPER = {"letter": "a4", "a4": "letter"}


class DateVerificationError(RuntimeError):
    """Raised when the generated date grid does not match the calendar truth."""


def _scaled(progress: Progress | None, start: float, end: float) -> Progress | None:
    if progress is None:
        return None

    def inner(message: str, fraction: float) -> None:
        progress(message, start + (end - start) * max(0.0, min(1.0, fraction)))

    return inner


def build_product(
    source: str | CalendarSpec,
    out_dir: str | Path | None = None,
    progress: Progress | None = None,
    settings: Settings | None = None,
    strict_dates: bool = True,
    companion_paper: bool = True,
    listing_count: int | None = None,
) -> BuildResult:
    """Run the whole pipeline for one product brief or spec."""
    started = time.perf_counter()
    settings = settings or get_settings()
    spec = parse_brief(source) if isinstance(source, str) else validate(source)

    def report(message: str, fraction: float) -> None:
        if progress:
            progress(message, fraction)

    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = Path(out_dir) if out_dir else settings.resolved_output_dir() / f"{stamp}_{spec.product_slug()}"
    art_dir, print_dir, mock_dir = run_dir / "art", run_dir / "print", run_dir / "mockups"
    for folder in (run_dir, art_dir, print_dir, mock_dir):
        folder.mkdir(parents=True, exist_ok=True)

    result = BuildResult(spec=spec, run_dir=run_dir)

    # 1. artwork -----------------------------------------------------------
    report("Generating artwork", 0.03)
    studio = ImageStudio(settings, offline=None if spec.generate_ai_art else True)
    art = studio.generate_set(spec, art_dir, progress=_scaled(progress, 0.05, 0.42))
    result.art_paths = art
    result.art_source = studio.source
    result.warnings.extend(studio.warnings)

    # 2. print-ready PDF ---------------------------------------------------
    report("Generating calendar PDF", 0.45)
    slug = spec.product_slug()
    primary = generate_calendar_pdf(spec, print_dir / f"{slug}-{spec.paper}.pdf", art)
    result.pdf_path = primary
    result.pdf_paths[spec.paper] = primary

    if companion_paper and spec.paper in COMPANION_PAPER:
        alt_paper = COMPANION_PAPER[spec.paper]
        alt_spec = dataclasses.replace(spec, paper=alt_paper, custom_size_in=None)
        report(f"Generating {alt_paper.upper()} companion PDF", 0.52)
        result.pdf_paths[alt_paper] = generate_calendar_pdf(
            alt_spec, print_dir / f"{slug}-{alt_paper}.pdf", art
        )

    # 3. date verification -------------------------------------------------
    report("Verifying every date", 0.56)
    math_report = verify_grid_math(spec.year, spec.first_weekday)
    if not math_report["ok"] and strict_dates:
        raise DateVerificationError(
            f"Date grid verification failed for {spec.year}: {math_report['errors'][:5]}"
        )
    full_report = verify_calendar_pdf(primary, spec)
    result.verification = full_report
    if not full_report["ok"]:
        result.warnings.append(
            "Rendered-page verification reported issues: " + "; ".join(full_report["errors"][:5])
        )

    # 4. listing images ----------------------------------------------------
    report("Compositing mockups", 0.6)
    try:
        result.listing_images = build_listing_images(
            spec,
            primary,
            mock_dir,
            count=listing_count or spec.listing_image_count,
            progress=_scaled(progress, 0.6, 0.88),
        )
    except Exception as exc:  # noqa: BLE001 - never lose the PDF over a mockup
        result.warnings.append(f"Mockups failed: {type(exc).__name__}: {exc}")

    # 5. packaging ---------------------------------------------------------
    report("Writing listing copy and packaging files", 0.9)
    docs = write_buyer_docs(spec, print_dir)
    _, _, copy = write_listing_copy(spec, run_dir)
    result.listing_copy = copy
    deliverables = [*result.pdf_paths.values(), *docs]
    result.zip_path = build_zip(run_dir / f"{slug}-etsy-files.zip", deliverables)

    # 6. optional Canva ----------------------------------------------------
    if spec.canva_export:
        report("Creating editable Canva design", 0.95)
        result.canva = export_to_canva(spec, art, settings)
        if result.canva.get("status") == "failed":
            result.warnings.append(f"Canva export failed: {result.canva.get('reason')}")
    else:
        result.canva = {"status": "not requested"}

    # 7. manifest ----------------------------------------------------------
    manifest = {
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "artisan_forge_version": _version(),
        "brief": spec.raw_brief,
        "spec": spec.to_dict(),
        "pages": month_page_count(spec),
        "art_source": result.art_source,
        "art_prompts": studio.prompt_manifest(spec),
        "files": {
            "pdfs": {key: str(path) for key, path in result.pdf_paths.items()},
            "art": {key: str(path) for key, path in art.items()},
            "listing_images": [str(path) for path in result.listing_images],
            "zip": str(result.zip_path) if result.zip_path else None,
        },
        "verification": result.verification,
        "listing": result.listing_copy,
        "canva": result.canva,
        "warnings": result.warnings,
        "duration_seconds": round(time.perf_counter() - started, 2),
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    report("Done", 1.0)
    return result


def build_many(
    briefs: list[str | CalendarSpec],
    progress: Progress | None = None,
    **kwargs,
) -> list[BuildResult]:
    """Batch mode: one run directory per brief."""
    results: list[BuildResult] = []
    total = len(briefs)
    for index, brief in enumerate(briefs):
        step = _scaled(progress, index / total, (index + 1) / total)
        results.append(build_product(brief, progress=step, **kwargs))
    return results


def _version() -> str:
    try:
        from . import __version__

        return __version__
    except Exception:  # pragma: no cover
        return "unknown"

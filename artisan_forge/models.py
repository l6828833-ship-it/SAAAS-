"""Data model for a product build request and its result."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Literal

StartDay = Literal["Sunday", "Monday"]
Orientation = Literal["portrait", "landscape"]

# Named paper sizes in INCHES (width x height, portrait form).
PAPER_SIZES: dict[str, tuple[float, float]] = {
    "letter": (8.5, 11.0),
    "a4": (8.27, 11.69),
    "legal": (8.5, 14.0),
    "a3": (11.69, 16.54),
    "tabloid": (11.0, 17.0),
    "5x7": (5.0, 7.0),
    "8x10": (8.0, 10.0),
    "11x14": (11.0, 14.0),
    "12x12": (12.0, 12.0),
    "16x20": (16.0, 20.0),
    "18x24": (18.0, 24.0),
}

# Etsy listing images are square-ish; 2000px is the sweet spot for zoom.
LISTING_IMAGE_PX = 2000


@dataclass
class CalendarSpec:
    """Everything the engine needs to produce a calendar product."""

    year: int
    theme: str = "minimalist"
    start_day: StartDay = "Sunday"
    orientation: Orientation = "portrait"
    paper: str = "letter"
    # Explicit trim size in inches; overrides `paper` when set.
    custom_size_in: tuple[float, float] | None = None

    title: str | None = None
    subtitle: str | None = None

    # Page composition
    include_cover: bool = True
    include_year_overview: bool = True
    include_notes_column: bool = False
    include_adjacent_days: bool = True
    include_moon_phases: bool = False
    include_month_art: bool = True
    holidays: str | None = "US"  # "US" | "UK" | None

    # Print prep
    bleed_in: float = 0.0

    # Asset generation
    generate_ai_art: bool = True
    art_style_hint: str | None = None
    # "unique" (12 images) | "seasonal" (4) | "single" (1)
    month_art_mode: str = "seasonal"
    listing_image_count: int = 10
    canva_export: bool = False

    # Bookkeeping
    raw_brief: str | None = None
    slug: str | None = None

    # ---------------------------------------------------------------- helpers
    @property
    def trim_size_in(self) -> tuple[float, float]:
        """Trim size in inches, already rotated for the chosen orientation."""
        w, h = self.custom_size_in or PAPER_SIZES.get(self.paper, PAPER_SIZES["letter"])
        w, h = min(w, h), max(w, h)  # normalise to portrait first
        if self.orientation == "landscape":
            return (h, w)
        return (w, h)

    @property
    def size_label(self) -> str:
        w, h = self.trim_size_in
        return f'{_num(w)}" x {_num(h)}"'

    @property
    def has_a4_companion(self) -> bool:
        """Letter and A4 are close enough to ship as a matched pair."""
        return self.paper in ("letter", "a4")

    @property
    def first_weekday(self) -> int:
        """`calendar` module convention: Monday=0 ... Sunday=6."""
        return 6 if self.start_day == "Sunday" else 0

    def display_title(self) -> str:
        return self.title or f"{self.year} Calendar"

    def product_slug(self) -> str:
        if self.slug:
            return self.slug
        parts = [str(self.year), self.theme.replace("_", "-"), self.orientation]
        return "-".join(p.lower().replace(" ", "-") for p in parts if p)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["trim_size_in"] = list(self.trim_size_in)
        data["size_label"] = self.size_label
        return data


@dataclass
class BuildResult:
    """Paths and metadata produced by a completed build."""

    spec: CalendarSpec
    run_dir: Path
    pdf_path: Path | None = None
    pdf_paths: dict[str, Path] = field(default_factory=dict)  # variant -> pdf
    art_paths: dict[str, Path] = field(default_factory=dict)  # "cover"/"month_01"/...
    listing_images: list[Path] = field(default_factory=list)
    zip_path: Path | None = None
    listing_copy: dict = field(default_factory=dict)
    verification: dict = field(default_factory=dict)
    canva: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    art_source: str = "procedural"

    def summary(self) -> dict:
        return {
            "run_dir": str(self.run_dir),
            "pdf": str(self.pdf_path) if self.pdf_path else None,
            "extra_pdfs": {k: str(v) for k, v in self.pdf_paths.items()},
            "listing_images": [str(p) for p in self.listing_images],
            "zip": str(self.zip_path) if self.zip_path else None,
            "art_source": self.art_source,
            "dates_verified": self.verification.get("ok"),
            "warnings": self.warnings,
        }


def _num(value: float) -> str:
    return f"{value:g}"

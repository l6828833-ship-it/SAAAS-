"""Presentation data for the mockup factory.

The compositor knows nothing about calendars, planners or posters. Each product
type hands it a `MockupContext` describing the trim size, the copy and which
rendered pages to feature, and gets a full listing image set back.
"""

from __future__ import annotations

from dataclasses import dataclass, field

ALL_SCENES = [
    "hero",
    "frame_wall",
    "bundle_grid",
    "desk",
    "frame_gallery",
    "detail",
    "included",
    "stack",
    "gift",
    "size_chart",
]


@dataclass
class MockupContext:
    """Everything the compositor needs, with usable defaults."""

    theme_key: str = "minimalist"
    trim_size_in: tuple[float, float] = (8.5, 11.0)
    size_label: str = '8.5" x 11"'
    orientation: str = "portrait"

    # Hero
    eyebrow: str = "printable digital download"
    title_lines: list[str] = field(default_factory=lambda: ["Printable", "Download"])
    badges: list[str] = field(default_factory=lambda: ["instant download", "print at home"])

    # Bundle grid
    grid_eyebrow: str = "everything included"
    grid_headline: str = "Every Page Included"
    grid_caption: str = ""
    grid_cols: int = 4
    grid_rows: int = 3

    # "What you get" card
    included_headline: str = "Instant digital bundle"
    bullets: list[str] = field(default_factory=list)

    # Scene copy overrides
    captions: dict[str, str] = field(default_factory=dict)
    size_notes: list[str] = field(default_factory=list)

    # Which rendered pages to feature
    cover_index: int = 0
    page_indexes: list[int] = field(default_factory=list)

    scenes: list[str] | None = None

    def caption(self, key: str, default: str = "") -> str:
        return self.captions.get(key, default)

    def scene_keys(self, count: int) -> list[str]:
        keys = self.scenes or ALL_SCENES
        return keys[: max(1, count)]

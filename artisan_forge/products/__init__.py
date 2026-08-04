"""Product catalog.

One entry per digital product the platform can forge. `status` drives the UI:
"live" products get a studio page, "soon" products render as a teaser card.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProductType:
    key: str
    label: str
    tagline: str
    icon: str
    status: str = "live"  # "live" | "soon"
    outputs: tuple[str, ...] = ()
    eta: str = ""

    @property
    def is_live(self) -> bool:
        return self.status == "live"


CATALOG: tuple[ProductType, ...] = (
    ProductType(
        key="calendar",
        label="Calendar Studio",
        tagline="12-month printable calendars with machine-verified dates",
        icon="\U0001f4c5",
        status="live",
        outputs=("Print-ready PDF (Letter + A4)", "10 listing images", "Etsy copy", "Buyer ZIP"),
    ),
    ProductType(
        key="bundle",
        label="Bundle Studio",
        tagline="ChatGPT writes the pages, Artisan Forge lays them out",
        icon="\u2728",
        status="live",
        outputs=("Multi-page PDF bundle", "Listing images", "Etsy copy", "Buyer ZIP"),
    ),
    ProductType(
        key="crochet",
        label="Crochet Studio",
        tagline="Upload patterns or Etsy data, get a graded pattern PDF with diagrams",
        icon="\U0001f9f6",
        status="live",
        outputs=(
            "Graded pattern PDF with stitch counts",
            "Technical diagrams and schematic",
            "Listing images",
            "Etsy copy",
            "Buyer ZIP",
        ),
    ),
    ProductType(
        key="planner",
        label="Planner Studio",
        tagline="Dated and undated weekly, daily and habit planners",
        icon="\U0001f5d3",
        status="soon",
        outputs=("Weekly spreads", "Daily pages", "Habit trackers"),
        eta="next release",
    ),
    ProductType(
        key="wall_art",
        label="Wall Art Studio",
        tagline="Quote posters and abstract prints in every Etsy ratio",
        icon="\U0001f5bc",
        status="soon",
        outputs=("2:3, 3:4, 4:5 and ISO ratios", "Frame mockups"),
        eta="next release",
    ),
    ProductType(
        key="journal",
        label="Journal Studio",
        tagline="Low-content books: lined, dot grid and guided interiors",
        icon="\U0001f4d3",
        status="soon",
        outputs=("KDP trim sizes", "Interior + cover files"),
        eta="planned",
    ),
    ProductType(
        key="social",
        label="Social Kit Studio",
        tagline="Matching Instagram and Pinterest promo graphics",
        icon="\U0001f4f1",
        status="soon",
        outputs=("Pin templates", "Story graphics"),
        eta="planned",
    ),
)

BY_KEY = {product.key: product for product in CATALOG}


def catalog() -> tuple[ProductType, ...]:
    return CATALOG


def get(key: str) -> ProductType | None:
    return BY_KEY.get(key)


def live() -> list[ProductType]:
    return [p for p in CATALOG if p.is_live]


def coming_soon() -> list[ProductType]:
    return [p for p in CATALOG if not p.is_live]

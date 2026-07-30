"""Prompt construction for cover and interior art.

Two rules keep generated art usable in a print product:
  * never ask for text (renders unreliably and localises badly)
  * always ask for a calm zone / negative space where the grid or title sits
"""

from __future__ import annotations

from ..models import CalendarSpec
from ..pdf.dates import MONTH_NAMES
from ..themes import Theme, get_theme

SEASONAL: dict[int, str] = {
    1: "deep winter, frosted bare branches, snow-quiet stillness, cool light",
    2: "late winter thaw, first pale buds, soft grey light",
    3: "early spring, crocus and budding twigs, fresh green",
    4: "spring rain, tulips and cherry blossom, dewy freshness",
    5: "full spring bloom, lilac and peony, warm sunlight",
    6: "early summer, wildflower meadow, long golden light",
    7: "high summer, sun-bleached grasses, bright airy haze",
    8: "late summer, ripe seed heads and dry stems, amber warmth",
    9: "early autumn, first turning leaves, soft honey light",
    10: "deep autumn, rust and ochre foliage, misty morning",
    11: "late autumn, bare stems and berries, muted cool tones",
    12: "midwinter, evergreen sprigs and frost, candlelit warmth",
}

_NEGATIVE = (
    "no text, no letters, no numbers, no words, no watermark, no signature, "
    "no calendar grid, no frame border"
)


def _aspect_note(spec: CalendarSpec, kind: str) -> str:
    if kind == "cover":
        return (
            "vertical poster composition with a calm empty area across the middle third "
            "for a title block"
            if spec.orientation == "portrait"
            else "wide poster composition with a calm empty band across the middle for a title block"
        )
    return (
        "wide horizontal banner composition, subject in the outer thirds, quiet centre"
        if spec.orientation == "portrait"
        else "vertical panel composition, subject anchored low, quiet upper area"
    )


def cover_prompt(spec: CalendarSpec, theme: Theme | None = None) -> str:
    theme = theme or get_theme(spec.theme)
    extra = f", {spec.art_style_hint}" if spec.art_style_hint else ""
    return (
        f"{theme.art}{extra}. Cover artwork for a premium printable {spec.year} wall calendar. "
        f"{_aspect_note(spec, 'cover')}. Flat even lighting, print-ready, high detail, "
        f"cohesive limited palette. {_NEGATIVE}."
    )


def month_prompt(spec: CalendarSpec, month: int, theme: Theme | None = None) -> str:
    theme = theme or get_theme(spec.theme)
    extra = f", {spec.art_style_hint}" if spec.art_style_hint else ""
    return (
        f"{theme.art}{extra}. Interior header artwork for {MONTH_NAMES[month - 1]}: "
        f"{SEASONAL[month]}. {_aspect_note(spec, 'interior')}. Same palette and technique across "
        f"the whole series so all twelve months match. {_NEGATIVE}."
    )


QUARTER_LABEL = {1: "winter into spring", 2: "spring into summer", 3: "summer into autumn", 4: "autumn into winter"}


def month_group(spec: CalendarSpec, month: int) -> str:
    """Which generated image a month page should use.

    `month_art_mode` trades cost against variety:
      unique   -> 12 images (one per month)
      seasonal -> 4 images (one per quarter, the default)
      single   -> 1 image reused all year
    """
    mode = (spec.month_art_mode or "seasonal").lower()
    if mode == "unique":
        return f"month_{month:02d}"
    if mode == "single":
        return "interior"
    return f"season_{(month - 1) // 3 + 1}"


def art_plan(spec: CalendarSpec) -> tuple[dict[str, str], dict[str, str]]:
    """Return (prompts_by_group, month_key -> group).

    `prompts_by_group` is what actually gets generated; the mapping tells the
    pipeline which generated file each month page should reference.
    """
    theme = get_theme(spec.theme)
    prompts: dict[str, str] = {"cover": cover_prompt(spec, theme)}
    mapping: dict[str, str] = {}
    if not spec.include_month_art:
        return prompts, mapping

    representative = {1: 2, 2: 5, 3: 8, 4: 11}
    for month in range(1, 13):
        group = month_group(spec, month)
        mapping[f"month_{month:02d}"] = group
        if group in prompts:
            continue
        if group.startswith("season_"):
            quarter = int(group.split("_")[1])
            base = month_prompt(spec, representative[quarter], theme)
            prompts[group] = base + f" Reads as {QUARTER_LABEL[quarter]}."
        elif group == "interior":
            prompts[group] = month_prompt(spec, 6, theme)
        else:
            prompts[group] = month_prompt(spec, month, theme)
    return prompts, mapping

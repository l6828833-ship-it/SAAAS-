"""Visual themes: palettes, typography and art-direction per style.

A theme drives three things at once so the PDF, the AI art and the mockups
always look like one product:
  * `palette`  -> vector colours used by the reportlab layout
  * `art`      -> prompt fragment for the image model
  * `art_palette` / `motif` -> procedural fallback art (works with no API key)
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Theme:
    key: str
    label: str
    palette: dict[str, str]
    art: str
    art_palette: list[str]
    motif: str = "wash"
    serif: bool = False
    tracking: float = 3.2  # letter-spacing for the month title
    uppercase_title: bool = True
    keywords: list[str] = field(default_factory=list)

    def color(self, role: str, fallback: str = "#000000") -> str:
        return self.palette.get(role, fallback)


def _p(paper, ink, muted, grid, accent, weekend, band) -> dict[str, str]:
    return {
        "paper": paper,
        "ink": ink,
        "muted": muted,
        "grid": grid,
        "accent": accent,
        "weekend": weekend,
        "band": band,
    }


THEMES: dict[str, Theme] = {
    "minimalist": Theme(
        key="minimalist",
        label="Minimalist",
        palette=_p("#FFFFFF", "#1A1A1A", "#8A8A8A", "#E2E2E2", "#1A1A1A", "#9A9A9A", "#F4F4F4"),
        art=(
            "ultra minimal abstract composition, single fine line arc, generous negative space, "
            "off-white paper, matte finish, editorial, no text"
        ),
        art_palette=["#FFFFFF", "#F2F1EE", "#D8D5CF", "#1A1A1A"],
        motif="lines",
        tracking=4.5,
        keywords=["minimalist", "modern", "clean", "simple"],
    ),
    "watercolor_floral": Theme(
        key="watercolor_floral",
        label="Watercolor Floral",
        palette=_p("#FFFDFB", "#3B3138", "#9A8A90", "#EFE3E6", "#B4728A", "#C79AA8", "#FBF1F2"),
        art=(
            "delicate watercolour floral arrangement, soft blush pink peonies, dusty rose and sage "
            "leaves, loose wet-on-wet washes, hand painted on cold press paper, airy negative space, "
            "no text"
        ),
        art_palette=["#FDF6F4", "#F3D9DD", "#D9A3B3", "#B4728A", "#A9B79D", "#7E8C74"],
        motif="floral",
        serif=True,
        tracking=3.6,
        keywords=["watercolor", "floral", "flowers", "feminine", "soft"],
    ),
    "botanical": Theme(
        key="botanical",
        label="Botanical Line Art",
        palette=_p("#FCFDFB", "#26332B", "#7C8C80", "#E1E9E2", "#4E6B55", "#8FA394", "#F0F5F0"),
        art=(
            "single-stroke botanical line drawing, eucalyptus and fern sprigs, deep green ink on "
            "cream, herbarium plate feeling, lots of white space, no text"
        ),
        art_palette=["#F7F8F3", "#DCE5D8", "#8FA394", "#4E6B55", "#26332B"],
        motif="leaves",
        serif=True,
        keywords=["botanical", "greenery", "leaf", "plant"],
    ),
    "boho": Theme(
        key="boho",
        label="Boho Terracotta",
        palette=_p("#FFFBF6", "#42342A", "#9C8674", "#EDE0D2", "#B5643C", "#C89272", "#F7EDE2"),
        art=(
            "boho abstract shapes, terracotta rust and sand tones, hand drawn arches and dots, "
            "organic texture, mid-century desert mood, no text"
        ),
        art_palette=["#FAF0E6", "#E8CDB4", "#C89272", "#B5643C", "#7A4A34"],
        motif="arches",
        serif=True,
        keywords=["boho", "terracotta", "neutral", "earthy"],
    ),
    "japandi": Theme(
        key="japandi",
        label="Japandi",
        palette=_p("#FBFAF7", "#2E2C28", "#8B8779", "#E6E3DA", "#6E7C6B", "#A9A497", "#F2F0E9"),
        art=(
            "japandi inspired ink wash, one soft sumi-e brush stroke, warm oat background, "
            "wabi-sabi calm, subtle paper grain, no text"
        ),
        art_palette=["#F5F2EA", "#DFDACD", "#A9A497", "#6E7C6B", "#2E2C28"],
        motif="wash",
        keywords=["japandi", "zen", "neutral", "calm"],
    ),
    "dark_luxe": Theme(
        key="dark_luxe",
        label="Dark Luxe",
        palette=_p("#14161A", "#F3F1EC", "#9BA1AA", "#2A2E35", "#C9A227", "#B9BEC6", "#1C1F25"),
        art=(
            "luxurious dark abstract, charcoal and midnight gradient with brushed gold leaf accent, "
            "moody editorial lighting, premium stationery feel, no text"
        ),
        art_palette=["#14161A", "#22262E", "#3A4049", "#C9A227", "#EADFA0"],
        motif="wash",
        serif=True,
        keywords=["dark", "luxe", "gold", "elegant", "moody"],
    ),
    "scandi": Theme(
        key="scandi",
        label="Scandi Pastel",
        palette=_p("#FFFFFF", "#2B2B33", "#8E8E99", "#E7E7EC", "#5B7DB1", "#A8B8D1", "#F1F3F8"),
        art=(
            "scandinavian abstract poster art, muted dusty blue and warm grey blocks, simple "
            "geometric balance, flat matte print, no text"
        ),
        art_palette=["#FFFFFF", "#EBEEF4", "#A8B8D1", "#5B7DB1", "#2B2B33"],
        motif="geo",
        keywords=["scandi", "pastel", "nordic", "geometric"],
    ),
    "coastal": Theme(
        key="coastal",
        label="Coastal",
        palette=_p("#FCFEFF", "#1F3A44", "#7C97A1", "#DFEAEE", "#2C7A8C", "#96B7C1", "#EDF5F7"),
        art=(
            "soft coastal watercolour horizon, sea glass teal and sand, gentle ocean wash, "
            "airy light, no text"
        ),
        art_palette=["#F4FAFB", "#D8E9EC", "#96B7C1", "#2C7A8C", "#1F3A44"],
        motif="wash",
        keywords=["coastal", "ocean", "beach", "teal"],
    ),
    "vintage": Theme(
        key="vintage",
        label="Vintage Almanac",
        palette=_p("#FBF6E9", "#3A2E20", "#93856F", "#E6DAC2", "#8C5A2B", "#B79A6E", "#F3EAD5"),
        art=(
            "vintage almanac engraving, aged parchment texture, sepia etched botanical and celestial "
            "motifs, antique print marks, no text"
        ),
        art_palette=["#F6EEDC", "#E2D2B4", "#B79A6E", "#8C5A2B", "#3A2E20"],
        motif="leaves",
        serif=True,
        keywords=["vintage", "antique", "retro", "sepia", "almanac"],
    ),
    "kids": Theme(
        key="kids",
        label="Playful Kids",
        palette=_p("#FFFFFF", "#26303D", "#7B8794", "#E8EDF2", "#F2825B", "#8FC7C1", "#FDF3EC"),
        art=(
            "cheerful flat illustration for children, rounded shapes, coral teal and mustard, "
            "friendly playful composition, crayon texture, no text"
        ),
        art_palette=["#FFFFFF", "#FDE7D5", "#8FC7C1", "#F2825B", "#F4C95D", "#26303D"],
        motif="geo",
        keywords=["kids", "playful", "colorful", "fun"],
    ),
}

DEFAULT_THEME = "minimalist"


def get_theme(key: str | None) -> Theme:
    if not key:
        return THEMES[DEFAULT_THEME]
    normalised = key.strip().lower().replace(" ", "_").replace("-", "_")
    if normalised in THEMES:
        return THEMES[normalised]
    # tolerate partial names like "watercolor" or "floral"
    for theme in THEMES.values():
        if normalised in theme.key or any(normalised == k for k in theme.keywords):
            return theme
    return THEMES[DEFAULT_THEME]


def theme_labels() -> dict[str, str]:
    return {t.key: t.label for t in THEMES.values()}

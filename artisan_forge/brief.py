"""Turn a plain-English product description into a validated CalendarSpec.

    parse_brief("2026 minimalist calendar with watercolor floral theme, 8.5x11, Sunday start")
"""

from __future__ import annotations

import datetime as _dt
import re

from .models import PAPER_SIZES, CalendarSpec
from .themes import THEMES, get_theme

_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
_SIZE_RE = re.compile(r"\b(\d{1,2}(?:[.,]\d{1,2})?)\s*(?:x|×|by)\s*(\d{1,2}(?:[.,]\d{1,2})?)\b", re.I)
_ISO_PAPER_RE = re.compile(r"\b(a3|a4|a5|letter|legal|tabloid)\b", re.I)


def default_year(today: _dt.date | None = None) -> int:
    """Creators sell next year's calendar from roughly mid-year onward."""
    today = today or _dt.date.today()
    return today.year if today.month <= 6 else today.year + 1


def _detect_theme(text: str) -> str:
    scores: dict[str, int] = {}
    for theme in THEMES.values():
        score = 0
        if theme.key.replace("_", " ") in text:
            score += 5
        if theme.label.lower() in text:
            score += 5
        for kw in theme.keywords:
            if re.search(rf"\b{re.escape(kw)}\b", text):
                score += 2
        if score:
            scores[theme.key] = score
    if not scores:
        return "minimalist"
    return max(scores.items(), key=lambda kv: kv[1])[0]


def _detect_size(text: str) -> tuple[str, tuple[float, float] | None]:
    """Return (paper_key, custom_size_inches)."""
    match = _SIZE_RE.search(text)
    if match:
        w = float(match.group(1).replace(",", "."))
        h = float(match.group(2).replace(",", "."))
        w, h = min(w, h), max(w, h)
        for key, (pw, ph) in PAPER_SIZES.items():
            if abs(pw - w) < 0.06 and abs(ph - h) < 0.06:
                return key, None
        return "custom", (w, h)

    iso = _ISO_PAPER_RE.search(text)
    if iso:
        key = iso.group(1).lower()
        if key in PAPER_SIZES:
            return key, None
        if key == "a5":
            return "custom", (5.83, 8.27)
    return "letter", None


def _flag_off(text: str, *words: str) -> bool:
    """True when the brief explicitly says 'no <word>' / 'without <word>'."""
    return any(re.search(rf"\b(no|without|skip|exclude)\s+\w*\s*{w}", text) for w in words)


def parse_brief(text: str, **overrides) -> CalendarSpec:
    """Parse a natural-language brief. Any keyword arg overrides the parse."""
    raw = (text or "").strip()
    low = raw.lower()

    year_match = _YEAR_RE.search(low)
    year = int(year_match.group(0)) if year_match else default_year()

    theme_key = _detect_theme(low)
    paper, custom = _detect_size(low)

    if re.search(r"\bmonday\b", low):
        start_day = "Monday"
    elif re.search(r"\bsunday\b", low):
        start_day = "Sunday"
    else:
        start_day = "Sunday"

    if re.search(r"\blandscape\b|\bhorizontal\b|\bwide\b", low):
        orientation = "landscape"
    elif re.search(r"\bportrait\b|\bvertical\b", low):
        orientation = "portrait"
    else:
        # 12x12 and other squares read better as portrait; wide trims default landscape
        orientation = "portrait"

    if custom and abs(custom[0] - custom[1]) < 0.01:
        orientation = "portrait"

    holidays: str | None = "US"
    if _flag_off(low, "holiday", "holidays") or "no holidays" in low:
        holidays = None
    elif re.search(r"\buk\b|british|england", low):
        holidays = "UK"
    elif re.search(r"\bblank\b|\bplain\b", low):
        holidays = None

    theme = get_theme(theme_key)

    spec = CalendarSpec(
        year=year,
        theme=theme.key,
        start_day=start_day,  # type: ignore[arg-type]
        orientation=orientation,  # type: ignore[arg-type]
        paper=paper,
        custom_size_in=custom,
        title=_detect_title(raw, year),
        include_cover=not _flag_off(low, "cover"),
        include_year_overview=not _flag_off(low, "overview", "year at a glance"),
        include_notes_column=bool(re.search(r"\bnotes?\b|\bjournal\b|\bmemo\b", low)),
        include_adjacent_days=not _flag_off(low, "adjacent", "trailing"),
        include_moon_phases=bool(re.search(r"\bmoon\b|\blunar\b", low)),
        include_month_art=not _flag_off(low, "art", "illustration", "image"),
        holidays=holidays,
        generate_ai_art=not _flag_off(low, "ai", "ai art"),
        canva_export=bool(re.search(r"\bcanva\b|\beditable\b", low)),
        raw_brief=raw or None,
    )

    for key, value in overrides.items():
        if value is not None and hasattr(spec, key):
            setattr(spec, key, value)
    return validate(spec)


def _detect_title(raw: str, year: int) -> str | None:
    quoted = re.search(r"[\"“']([^\"”']{3,60})[\"”']", raw)
    if quoted:
        return quoted.group(1).strip()
    return f"{year} Calendar"


def validate(spec: CalendarSpec) -> CalendarSpec:
    """Guard rails so a bad brief can never produce a broken PDF."""
    if not (1900 <= spec.year <= 2200):
        raise ValueError(f"Year {spec.year} is out of the supported 1900-2200 range")
    if spec.start_day not in ("Sunday", "Monday"):
        raise ValueError("start_day must be 'Sunday' or 'Monday'")
    if spec.orientation not in ("portrait", "landscape"):
        raise ValueError("orientation must be 'portrait' or 'landscape'")
    if spec.paper != "custom" and spec.paper not in PAPER_SIZES:
        raise ValueError(f"Unknown paper '{spec.paper}'. Known: {sorted(PAPER_SIZES)}")
    if spec.paper == "custom" and not spec.custom_size_in:
        raise ValueError("paper='custom' requires custom_size_in=(width_in, height_in)")
    w, h = spec.trim_size_in
    if not (3 <= w <= 40 and 3 <= h <= 40):
        raise ValueError(f"Trim size {w}x{h} in is outside the printable 3-40 inch range")
    if spec.bleed_in < 0 or spec.bleed_in > 0.5:
        raise ValueError("bleed_in must be between 0 and 0.5 inches")
    spec.theme = get_theme(spec.theme).key
    spec.listing_image_count = max(1, min(20, spec.listing_image_count))
    return spec

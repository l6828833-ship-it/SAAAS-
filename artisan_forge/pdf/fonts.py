"""Font resolution.

Built-in Type1 fonts are used by default so the engine has zero asset
dependencies. Drop TTF files into `assets/fonts/` and they are picked up
automatically: any file whose name contains "bold"/"italic" is mapped to the
matching slot, everything else becomes the regular weight.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from ..config import ASSETS_DIR

_registered: dict[str, str] = {}


@dataclass(frozen=True)
class FontSet:
    regular: str
    bold: str
    italic: str
    display: str  # month names / cover title


def _register_ttf(path: Path) -> str | None:
    name = f"AF-{path.stem}"
    if name in _registered.values():
        return name
    try:
        pdfmetrics.registerFont(TTFont(name, str(path)))
    except Exception:
        return None
    _registered[path.stem.lower()] = name
    return name


def _scan_custom_fonts() -> dict[str, str]:
    slots: dict[str, str] = {}
    font_dir = ASSETS_DIR / "fonts"
    if not font_dir.is_dir():
        return slots
    for path in sorted(font_dir.glob("*.tt[fc]")):
        registered = _register_ttf(path)
        if not registered:
            continue
        stem = path.stem.lower()
        if "bolditalic" in stem or ("bold" in stem and "italic" in stem):
            slots.setdefault("bolditalic", registered)
        elif "bold" in stem or "semibold" in stem:
            slots.setdefault("bold", registered)
        elif "italic" in stem or "oblique" in stem:
            slots.setdefault("italic", registered)
        elif "display" in stem or "heading" in stem or "title" in stem:
            slots.setdefault("display", registered)
        else:
            slots.setdefault("regular", registered)
    return slots


def resolve_fonts(serif: bool = False) -> FontSet:
    base = (
        FontSet("Times-Roman", "Times-Bold", "Times-Italic", "Times-Roman")
        if serif
        else FontSet("Helvetica", "Helvetica-Bold", "Helvetica-Oblique", "Helvetica")
    )
    custom = _scan_custom_fonts()
    if not custom:
        return base
    regular = custom.get("regular", base.regular)
    return FontSet(
        regular=regular,
        bold=custom.get("bold", regular if "regular" in custom else base.bold),
        italic=custom.get("italic", regular if "regular" in custom else base.italic),
        display=custom.get("display", custom.get("bold", regular)),
    )

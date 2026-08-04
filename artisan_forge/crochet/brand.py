"""Seller branding applied to every page of a pattern.

A crochet pattern is a document a buyer keeps for years, so the shop name,
designer credit and support address matter as much as the instructions. The
`BrandKit` is threaded through the PDF renderer: cover, running footer,
copyright page and the closing thank-you page all read from it.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import re
from dataclasses import dataclass, field
from pathlib import Path

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+\.[^@\s]+$")

# Keep footers from wrapping: these are hard limits, not suggestions.
MAX_NAME = 60
MAX_TAGLINE = 90
MAX_HANDLE = 40


@dataclass
class BrandKit:
    """Everything the pattern needs to look like it came from one shop."""

    store_name: str = ""
    designer_name: str = ""
    email: str = ""
    website: str = ""
    instagram: str = ""
    ravelry: str = ""
    tagline: str = ""
    logo_path: str | None = None
    accent_hex: str | None = None
    copyright_year: int = field(default_factory=lambda: dt.date.today().year)
    support_note: str = ""
    licence: str = "personal"  # "personal" | "small_business"

    # ------------------------------------------------------------- normalise
    def cleaned(self) -> "BrandKit":
        """A tidied copy: trimmed, length-capped, handles without the @."""
        data = dataclasses.asdict(self)
        for key in ("store_name", "designer_name", "email", "website", "tagline", "support_note"):
            data[key] = " ".join(str(data.get(key) or "").split())
        data["store_name"] = data["store_name"][:MAX_NAME]
        data["designer_name"] = data["designer_name"][:MAX_NAME]
        data["tagline"] = data["tagline"][:MAX_TAGLINE]
        data["instagram"] = _handle(self.instagram)
        data["ravelry"] = _handle(self.ravelry)
        data["website"] = _site(data["website"])
        if data["email"] and not EMAIL_RE.match(data["email"]):
            data["email"] = ""
        if data["accent_hex"] and not re.fullmatch(r"#[0-9A-Fa-f]{6}", data["accent_hex"].strip()):
            data["accent_hex"] = None
        elif data["accent_hex"]:
            data["accent_hex"] = data["accent_hex"].strip().upper()
        if data["logo_path"] and not Path(data["logo_path"]).is_file():
            data["logo_path"] = None
        if data["licence"] not in ("personal", "small_business"):
            data["licence"] = "personal"
        return BrandKit(**data)

    # ---------------------------------------------------------------- labels
    @property
    def shop(self) -> str:
        return self.store_name or self.designer_name or "Independent Pattern Design"

    @property
    def credit(self) -> str:
        """The "designed by" line."""
        if self.designer_name and self.store_name:
            return f"{self.designer_name} for {self.store_name}"
        return self.designer_name or self.store_name or "an independent designer"

    @property
    def logo(self) -> Path | None:
        path = Path(self.logo_path) if self.logo_path else None
        return path if path and path.is_file() else None

    def copyright_line(self) -> str:
        return f"\u00a9 {self.copyright_year} {self.shop}. All rights reserved."

    def footer(self) -> str:
        """Short running footer: shop, then the best available contact."""
        contact = self.website or self.email or (f"@{self.instagram}" if self.instagram else "")
        return f"{self.shop}  \u00b7  {contact}" if contact else self.shop

    def contact_lines(self) -> list[str]:
        """Labelled contact rows for the closing page."""
        rows = [
            ("Shop", self.store_name),
            ("Designer", self.designer_name),
            ("Email", self.email),
            ("Website", self.website),
            ("Instagram", f"@{self.instagram}" if self.instagram else ""),
            ("Ravelry", self.ravelry),
        ]
        return [f"{label}: {value}" for label, value in rows if value]

    def licence_terms(self) -> tuple[list[str], list[str]]:
        """(may, may not) bullet lists for the licence page."""
        may = [
            "Make this design as many times as you like for yourself and as gifts",
            "Keep a printed copy of this pattern for your own reference",
        ]
        may_not = [
            "Resell, share, copy or redistribute this pattern file",
            "Republish the pattern text, charts or photographs as your own",
            "Translate or rewrite the pattern for distribution without permission",
        ]
        if self.licence == "small_business":
            may.append("Sell finished items you have made from this pattern")
            may.append(
                f"Credit \u201cpattern by {self.credit}\u201d in your finished item listings"
            )
        else:
            may_not.append("Sell finished items made from this pattern")
        return may, may_not

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def _handle(value: str | None) -> str:
    """Strip @, trailing slashes and any profile URL down to the handle."""
    text = " ".join(str(value or "").split())
    if not text:
        return ""
    text = re.sub(r"^https?://(www\.)?[^/]+/", "", text)
    return text.lstrip("@").rstrip("/")[:MAX_HANDLE]


def _site(value: str) -> str:
    """Display form of a website: no scheme, no trailing slash."""
    if not value:
        return ""
    return re.sub(r"^https?://(www\.)?", "", value).rstrip("/")[:MAX_NAME]

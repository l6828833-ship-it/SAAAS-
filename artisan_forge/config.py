"""Environment / runtime configuration for Artisan Forge."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:  # optional dependency
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover - dotenv is a convenience only
    pass

ROOT = Path(__file__).resolve().parent.parent
ASSETS_DIR = ROOT / "assets"


def _flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _clean(name: str, default: str = "") -> str:
    """Read an env var, trimming whitespace and stray surrounding quotes.

    Pasting `KEY="abc"` or a value with a trailing newline into a hosting
    dashboard is common, and an API key with quotes around it fails in ways
    that are hard to diagnose.
    """
    raw = os.getenv(name)
    value = default if raw is None else raw
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1].strip()
    return value


@dataclass
class Settings:
    """Resolved settings. Read once per process, cheap to rebuild."""

    openai_api_key: str | None = field(default_factory=lambda: _clean("OPENAI_API_KEY") or None)
    image_model: str = field(default_factory=lambda: _clean("AF_IMAGE_MODEL", "gpt-image-1.5"))
    image_quality: str = field(default_factory=lambda: _clean("AF_IMAGE_QUALITY", "high"))
    text_model: str = field(default_factory=lambda: _clean("AF_TEXT_MODEL"))
    force_offline: bool = field(default_factory=lambda: _flag("AF_OFFLINE"))
    output_dir: Path = field(default_factory=lambda: Path(os.getenv("AF_OUTPUT_DIR", "output")))

    etsy_keystring: str | None = field(default_factory=lambda: _clean("ETSY_KEYSTRING") or None)
    etsy_shared_secret: str | None = field(
        default_factory=lambda: _clean("ETSY_SHARED_SECRET") or None
    )
    etsy_redirect_uri: str = field(
        default_factory=lambda: _clean("ETSY_REDIRECT_URI", "http://localhost:8501")
    )
    # Override if your app needs "keystring:shared_secret" in the x-api-key header.
    etsy_api_key_header: str | None = field(
        default_factory=lambda: _clean("AF_ETSY_API_KEY_HEADER") or None
    )

    canva_client_id: str | None = field(default_factory=lambda: os.getenv("CANVA_CLIENT_ID") or None)
    canva_client_secret: str | None = field(
        default_factory=lambda: os.getenv("CANVA_CLIENT_SECRET") or None
    )
    canva_access_token: str | None = field(
        default_factory=lambda: os.getenv("CANVA_ACCESS_TOKEN") or None
    )

    @property
    def ai_available(self) -> bool:
        """True when real AI image generation can run."""
        return bool(self.openai_api_key) and not self.force_offline

    @property
    def canva_available(self) -> bool:
        return bool(self.canva_access_token)

    @property
    def etsy_configured(self) -> bool:
        """True when the app can start an Etsy OAuth flow."""
        return bool(self.etsy_keystring and self.etsy_redirect_uri)

    def resolved_output_dir(self) -> Path:
        path = self.output_dir
        if not path.is_absolute():
            path = ROOT / path
        path.mkdir(parents=True, exist_ok=True)
        return path


def get_settings() -> Settings:
    """Build a fresh Settings object (picks up .env edits without a restart)."""
    return Settings()

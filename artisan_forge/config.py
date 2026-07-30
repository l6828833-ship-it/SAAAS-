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


@dataclass
class Settings:
    """Resolved settings. Read once per process, cheap to rebuild."""

    openai_api_key: str | None = field(default_factory=lambda: os.getenv("OPENAI_API_KEY") or None)
    image_model: str = field(default_factory=lambda: os.getenv("AF_IMAGE_MODEL", "gpt-image-1.5"))
    image_quality: str = field(default_factory=lambda: os.getenv("AF_IMAGE_QUALITY", "high"))
    force_offline: bool = field(default_factory=lambda: _flag("AF_OFFLINE"))
    output_dir: Path = field(default_factory=lambda: Path(os.getenv("AF_OUTPUT_DIR", "output")))

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

    def resolved_output_dir(self) -> Path:
        path = self.output_dir
        if not path.is_absolute():
            path = ROOT / path
        path.mkdir(parents=True, exist_ok=True)
        return path


def get_settings() -> Settings:
    """Build a fresh Settings object (picks up .env edits without a restart)."""
    return Settings()

"""Environment / runtime configuration for Artisan Forge."""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path

try:  # optional dependency
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover - dotenv is a convenience only
    pass

ROOT = Path(__file__).resolve().parent.parent
ASSETS_DIR = ROOT / "assets"

# --------------------------------------------------------------- AI providers
# Everything talks OpenAI's Chat Completions shape, so a provider is really just
# a base URL, a key and a set of model ids. Inworld's Router is the default: one
# key reaches Qwen for the writing and Gemini for the artwork, at a fraction of
# what the OpenAI models cost.
PROVIDERS = ("inworld", "openai")
DEFAULT_PROVIDER = "inworld"

BASE_URLS = {
    "inworld": "https://api.inworld.ai/v1",
    "openai": "https://api.openai.com/v1",
}

# Inworld Router model ids are "provider/model". Prices are per million tokens.
#
#   deepinfra/Qwen/Qwen3-VL-30B-A3B-Instruct      $0.15 in / $0.60 out
#   google-ai-studio/gemini-2.5-flash-image       $0.30 in / $2.50 out
#
# Exactly two models, one job each: Qwen3-VL does every piece of writing and
# every piece of reading, Gemini draws. Qwen3-VL is used for the text-only
# stages as well as the ones with photographs attached, on purpose - a second
# text-only model would be marginally cheaper per token but it cannot see, so
# it has to be swapped out mid-pipeline, and every model that gets tried and
# fails is billed anyway. One model that handles both is cheaper in practice
# than a ladder of cheaper ones.
MODEL_DEFAULTS = {
    "inworld": {
        "text": "deepinfra/Qwen/Qwen3-VL-30B-A3B-Instruct",
        "cheap_text": "deepinfra/Qwen/Qwen3-VL-30B-A3B-Instruct",
        "vision": "deepinfra/Qwen/Qwen3-VL-30B-A3B-Instruct",
        "image": "google-ai-studio/gemini-2.5-flash-image",
        "cheap_image": "google-ai-studio/gemini-2.5-flash-image",
    },
    "openai": {
        # empty text model means "use the built-in fallback chain"
        "text": "",
        "cheap_text": "gpt-4o-mini",
        "vision": "",
        "image": "gpt-image-1.5",
        "cheap_image": "gpt-image-1-mini",
    },
}

# How images are asked for. Gemini is a multimodal chat model, so its renders
# come back from /chat/completions; the OpenAI image models have their own
# /images/generations endpoint.
IMAGE_APIS = {"inworld": "chat", "openai": "images"}


def _provider() -> str:
    """Which gateway to talk to. Unknown names fall back to the default."""
    name = _clean("AF_AI_PROVIDER", DEFAULT_PROVIDER).lower()
    return name if name in PROVIDERS else DEFAULT_PROVIDER



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

    # Which gateway, and the credentials for it. Inworld reaches Qwen and Gemini
    # through one OpenAI-compatible endpoint; "openai" keeps the original wiring.
    provider: str = field(default_factory=_provider)
    inworld_api_key: str | None = field(default_factory=lambda: _clean("INWORLD_API_KEY") or None)
    openai_api_key: str | None = field(default_factory=lambda: _clean("OPENAI_API_KEY") or None)
    base_url: str = field(default_factory=lambda: _clean("AF_AI_BASE_URL"))

    image_model: str = field(default_factory=lambda: _clean("AF_IMAGE_MODEL"))
    image_quality: str = field(default_factory=lambda: _clean("AF_IMAGE_QUALITY", "high"))
    text_model: str = field(default_factory=lambda: _clean("AF_TEXT_MODEL"))
    # Used instead of `text_model` whenever reference photographs are attached:
    # the strongest text Qwen cannot see images, the VL one can.
    vision_model: str = field(default_factory=lambda: _clean("AF_VISION_MODEL"))
    force_offline: bool = field(default_factory=lambda: _flag("AF_OFFLINE"))
    output_dir: Path = field(default_factory=lambda: Path(os.getenv("AF_OUTPUT_DIR", "output")))

    # Cheaper stand-ins used by "lean" cost modes. See `lean()`.
    cheap_text_model: str = field(default_factory=lambda: _clean("AF_TEXT_MODEL_CHEAP"))
    cheap_image_model: str = field(default_factory=lambda: _clean("AF_IMAGE_MODEL_CHEAP"))

    # "chat" asks a multimodal chat model for a picture (Gemini), "images" posts
    # to a dedicated image endpoint (OpenAI). Normally left to the provider.
    image_api: str = field(default_factory=lambda: _clean("AF_IMAGE_API"))

    # Generated images are cached by prompt hash, so rebuilding a product with
    # the same brief costs nothing. Images dominate the cost of a run.
    image_cache: bool = field(default_factory=lambda: _flag("AF_IMAGE_CACHE", True))
    cache_dir: Path = field(default_factory=lambda: Path(os.getenv("AF_CACHE_DIR", "cache")))

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
        default_factory=lambda: _clean("CANVA_ACCESS_TOKEN") or None
    )
    canva_refresh_token: str | None = field(
        default_factory=lambda: _clean("CANVA_REFRESH_TOKEN") or None
    )

    def __post_init__(self) -> None:
        """Fill in whatever the environment did not pin, per provider.

        Only empty values are filled, so `replace()`-derived copies (see `lean()`
        and `tuned()`) keep the models their caller chose.
        """
        if self.provider not in PROVIDERS:
            self.provider = DEFAULT_PROVIDER
        defaults = MODEL_DEFAULTS[self.provider]
        self.base_url = (self.base_url or BASE_URLS[self.provider]).rstrip("/")
        self.image_model = self.image_model or defaults["image"]
        self.cheap_image_model = self.cheap_image_model or defaults["cheap_image"]
        self.cheap_text_model = self.cheap_text_model or defaults["cheap_text"]
        self.vision_model = self.vision_model or defaults["vision"]
        self.image_api = self.image_api or IMAGE_APIS[self.provider]
        # `text_model` is deliberately allowed to stay empty: an empty value means
        # "use the built-in fallback chain", which the text client owns.
        if not self.text_model:
            self.text_model = defaults["text"]

    @property
    def api_key(self) -> str | None:
        """The credential for the configured gateway.

        Inworld falls back to an OpenAI key so an existing .env keeps working if
        the provider is switched back, and vice versa.
        """
        if self.provider == "inworld":
            return self.inworld_api_key or None
        return self.openai_api_key or None

    @property
    def provider_label(self) -> str:
        return {"inworld": "Inworld", "openai": "OpenAI"}.get(self.provider, self.provider)

    @property
    def key_env_var(self) -> str:
        """The environment variable a user needs to set. Used in UI messages."""
        return "INWORLD_API_KEY" if self.provider == "inworld" else "OPENAI_API_KEY"

    @property
    def ai_available(self) -> bool:
        """True when real AI generation can run."""
        return bool(self.api_key) and not self.force_offline

    def image_model_for_tier(self, tier: str) -> str | None:
        """Resolve a cost tier ("none" / "cheap" / "best") to a concrete model."""
        if tier == "none":
            return None
        if tier == "cheap":
            return self.cheap_image_model or self.image_model
        return self.image_model

    @property
    def canva_available(self) -> bool:
        return bool(self.canva_access_token)

    def lean(self) -> "Settings":
        """A copy that prefers the cheap model tier.

        Used by cost-conscious runs: same pipeline, smaller models, lower image
        quality. Returns a new object so the caller's settings are untouched.
        """
        return replace(
            self,
            text_model=self.cheap_text_model or self.text_model,
            image_model=self.cheap_image_model or self.image_model,
            image_quality="low",
        )

    def tuned(
        self,
        image_model: str | None = None,
        image_quality: str | None = None,
        cheap_text: bool = False,
    ) -> "Settings":
        """A copy with the model tier a cost profile asks for.

        Images dominate the cost of a run, so the caller needs to be able to
        pin the exact model and quality rather than choosing between two presets.
        """
        return replace(
            self,
            text_model=(self.cheap_text_model or self.text_model) if cheap_text else self.text_model,
            image_model=image_model or self.image_model,
            image_quality=image_quality or self.image_quality,
        )

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

    def resolved_cache_dir(self) -> Path:
        path = self.cache_dir
        if not path.is_absolute():
            path = ROOT / path
        path.mkdir(parents=True, exist_ok=True)
        return path


def get_settings() -> Settings:
    """Build a fresh Settings object (picks up .env edits without a restart)."""
    return Settings()

"""AI-written product content, with an offline template fallback.

Writes the pages of a digital product - crochet pattern sections, prompts,
checklists, tracker columns - plus its Etsy listing copy.

The default backend is the Inworld Router with `Qwen3-14B` doing the writing: a
pattern run is roughly six thousand output tokens, so about a sixth of a cent.
Anything with pictures attached is routed to `Qwen3-VL-30B-A3B-Instruct`
instead, because Qwen3-14B has no vision - that covers both uploaded reference
photos and the Gemini plates fed back in to write the pattern against.

Model ids move fast, so the chain is tried in order and the first one that
answers wins. Override the front of the chain with `AF_TEXT_MODEL`, or switch
the whole gateway back with `AF_AI_PROVIDER=openai`.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
from pathlib import Path
from typing import Any

from ..config import Settings, get_settings
from .gateway import GatewayError, post_json

CHAT_PATH = "/chat/completions"

# Keep vision payloads sane: a handful of reference photos is plenty.
MAX_IMAGES = 6
MAX_IMAGE_BYTES = 8 * 1024 * 1024

# Fallback ladders per gateway. The first entry of the effective chain is always
# whatever `AF_TEXT_MODEL` (or the cost profile) asked for.
MODEL_CHAINS = {
    "inworld": [
        "deepinfra/Qwen/Qwen3-14B",                       # $0.12 / $0.24 per Mtok
        "deepinfra/Qwen/Qwen3-32B",                       # $0.08 / $0.28
        "deepinfra/Qwen/Qwen3-235B-A22B-Instruct-2507",   # $0.09 / $0.10
    ],
    "openai": ["gpt-5.6-luna", "gpt-5-mini", "gpt-4.1-mini", "gpt-4o-mini"],
}
# Only these can read the attached photographs.
VISION_CHAINS = {
    "inworld": [
        "deepinfra/Qwen/Qwen3-VL-30B-A3B-Instruct",      # $0.15 / $0.60
        "google-ai-studio/gemini-2.5-flash",
    ],
    "openai": ["gpt-4.1-mini", "gpt-4o-mini"],
}

# Backwards-compatible alias: this used to be the single OpenAI-only ladder.
MODEL_CHAIN = MODEL_CHAINS["openai"]

# Reasoning-tuned models like Qwen3-32B narrate before they answer. The JSON is
# still in there, but the preamble has to come off first.
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)

SYSTEM = (
    "You are a senior digital product designer who ships best-selling printable "
    "bundles on Etsy. You write warm, concrete, practical copy with no filler, no "
    "emoji and no cliches. You always answer with valid JSON only."
)


def model_chain(settings: Settings | None = None, vision: bool = False) -> list[str]:
    """The models to try, in order, for this gateway.

    `vision=True` returns the chain that can actually read attached images.
    """
    settings = settings or get_settings()
    provider = settings.provider
    if vision:
        preferred = settings.vision_model or ""
        ladder = VISION_CHAINS.get(provider, VISION_CHAINS["openai"])
    else:
        preferred = (settings.text_model if settings else os.getenv("AF_TEXT_MODEL")) or ""
        ladder = MODEL_CHAINS.get(provider, MODEL_CHAINS["openai"])
    chain = [m for m in [preferred.strip()] if m]
    chain += [m for m in ladder if m != preferred]
    return chain


def _encode_image(path: Path) -> str | None:
    """Inline an image as a data URL, or None if it is unusable.

    Data URLs rather than links on purpose: Google's models are served base64
    only, and an inline payload cannot 404 halfway through a build.
    """
    try:
        if not path.is_file() or path.stat().st_size > MAX_IMAGE_BYTES:
            return None
        mime = mimetypes.guess_type(path.name)[0] or "image/png"
        if not mime.startswith("image/"):
            return None
        payload = base64.b64encode(path.read_bytes()).decode("ascii")
    except Exception:  # noqa: BLE001 - an unreadable reference photo is not fatal
        return None
    return f"data:{mime};base64,{payload}"


def _extract_json(text: str) -> dict:
    """Tolerate models that wrap JSON in prose, fences or reasoning blocks."""
    text = _THINK_RE.sub("", str(text)).strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?|```$", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        return json.loads(text[start : end + 1])
    raise ValueError("No JSON object found in model response")


def _message_text(body: dict) -> str:
    """Pull the assistant text out of a chat completion.

    Gateways differ on whether `content` is a string or a list of typed parts,
    and reasoning models sometimes put the answer in `reasoning_content`.
    """
    choices = body.get("choices") or []
    if not choices:
        raise ValueError("Chat completion contained no choices")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, list):
        parts = [
            str(part.get("text", ""))
            for part in content
            if isinstance(part, dict) and part.get("type") in (None, "text", "output_text")
        ]
        content = "".join(parts)
    text = str(content or "").strip()
    return text or str(message.get("reasoning_content") or "").strip()


class CopyStudio:
    """One object, two backends: an AI gateway or local templates."""

    def __init__(self, settings: Settings | None = None, offline: bool | None = None):
        self.settings = settings or get_settings()
        self.offline = (not self.settings.ai_available) if offline is None else offline
        self.warnings: list[str] = []
        self.model_used: str | None = None
        self.source = "template" if self.offline else self.settings.provider

    # -------------------------------------------------------------- backend
    def _post(self, payload: dict) -> str:
        body = post_json(self.settings, CHAT_PATH, payload)
        return _message_text(body)

    def ask_json(
        self,
        prompt: str,
        images: list[str | Path] | None = None,
        temperature: float | None = None,
    ) -> dict | None:
        """Ask for a JSON object. Returns None when offline or on failure.

        Pass `images` to have the model look at pictures alongside the prompt -
        used when a pattern is reverse-engineered from photos of a finished
        piece. That switches to the vision model chain, since the strongest text
        model cannot see. Unreadable files are skipped with a warning rather
        than failing the build.

        `temperature` is how a batch gets variety: the same brief asked twice at
        a higher temperature gives two different designs rather than one design
        twice.
        """
        if self.offline:
            return None

        content: str | list[dict] = prompt
        attached = 0
        if images:
            parts: list[dict] = [{"type": "text", "text": prompt}]
            for path in list(images)[:MAX_IMAGES]:
                encoded = _encode_image(Path(path))
                if encoded:
                    parts.append({"type": "image_url", "image_url": {"url": encoded}})
                    attached += 1
                else:
                    self.warnings.append(f"Could not attach image {Path(path).name}")
            if attached:
                content = parts

        messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": content}]
        last_error: Exception | None = None
        for model in model_chain(self.settings, vision=bool(attached)):
            for use_schema in (True, False):
                payload: dict[str, Any] = {"model": model, "messages": messages}
                if use_schema:
                    payload["response_format"] = {"type": "json_object"}
                if temperature is not None:
                    payload["temperature"] = float(temperature)
                try:
                    answer = self._post(payload)
                    data = _extract_json(answer)
                    self.model_used = model
                    self.source = f"{self.settings.provider}:{model}"
                    return data
                except Exception as exc:  # noqa: BLE001 - try the next option
                    last_error = exc
                    # A bad key fails identically on every model in the chain, so
                    # do not spend four round trips proving it.
                    if isinstance(exc, GatewayError) and exc.status in (401, 403):
                        self.warnings.append(
                            f"{self.settings.provider_label} rejected the credential "
                            f"({exc}); check {self.settings.key_env_var}"
                        )
                        self.source = "template"
                        return None
        self.warnings.append(
            f"{self.settings.provider_label} content unavailable "
            f"({type(last_error).__name__}: {last_error}); used built-in templates"
        )
        self.source = "template"
        return None

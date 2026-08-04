"""Image generation client.

Talks to whichever gateway is configured and degrades to procedural art
otherwise, so a build never fails just because of a missing credential or a
transient API error.

Two request styles, because the models differ in kind:

* **chat** (default, Inworld -> `google-ai-studio/gemini-2.5-flash-image`).
  Gemini is a multimodal chat model, so a picture comes back from
  `/chat/completions` as a base64 data URL rather than from a dedicated image
  endpoint. It also has no `size` parameter, so the aspect ratio is asked for in
  the prompt and then enforced locally - the page layout needs exact pixels.
* **images** (OpenAI -> `gpt-image-*`). The classic `/images/generations` call,
  which takes `size` and `quality` directly.

Renders are cached by prompt hash either way, because artwork is the only part
of a build that costs real money.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import io
import re
import time
from pathlib import Path
from typing import Callable

from ..config import Settings, get_settings
from ..models import CalendarSpec
from ..themes import get_theme
from .gateway import GatewayError, fetch_bytes, post_json
from .procedural import generate_procedural_art
from .prompts import art_plan

IMAGES_PATH = "/images/generations"
CHAT_PATH = "/chat/completions"

# GPT image models accept a fixed set of output sizes. Gemini is normalised to
# the same three so the rest of the engine has one vocabulary for aspect.
PORTRAIT = "1024x1536"
LANDSCAPE = "1536x1024"
SQUARE = "1024x1024"

# What to ask Gemini for, since it takes an aspect ratio rather than pixels.
ASPECT_FOR_SIZE = {PORTRAIT: "2:3 portrait", LANDSCAPE: "3:2 landscape", SQUARE: "1:1 square"}

# Magic bytes for the formats a gateway might hand back.
IMAGE_MAGIC = (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF8", b"RIFF", b"BM")

_DATA_URL_RE = re.compile(r"data:image/[a-zA-Z0-9.+-]+;base64,([A-Za-z0-9+/=\s]+)")

Progress = Callable[[str, float], None]


def _looks_like_image(data: bytes) -> bool:
    """A cheap sanity check, so an error page never lands in the PDF."""
    return bool(data) and data.startswith(IMAGE_MAGIC)


def _decode_b64(payload: str) -> bytes:
    """Decode base64 that may be a full data URL, and may carry whitespace."""
    text = str(payload or "").strip()
    if text.startswith("data:"):
        match = _DATA_URL_RE.search(text)
        text = match.group(1) if match else text.split(",", 1)[-1]
    text = "".join(text.split())
    # tolerate missing padding, which some gateways strip
    text += "=" * (-len(text) % 4)
    try:
        return base64.b64decode(text, validate=False)
    except (binascii.Error, ValueError) as exc:
        raise GatewayError(f"Could not decode the returned image: {exc}") from exc


def _from_url_or_b64(url: str) -> bytes:
    """A gateway hands back either an inline data URL or a link. Take either."""
    if url.startswith("data:") or not url.startswith("http"):
        return _decode_b64(url)
    return fetch_bytes(url)


def _image_from_chat(body: dict) -> bytes:
    """Pull the rendered picture out of a chat completion.

    Gateways have not converged on where an image lives in an OpenAI-shaped
    response, so every known shape is tried before giving up: a dedicated
    `images` array, typed content parts, Google-style `inline_data`, or a bare
    data URL embedded in the assistant's text.
    """
    choices = body.get("choices") or []
    if not choices:
        raise GatewayError("The image model returned no choices")
    message = choices[0].get("message") or {}

    # 1. the convention most OpenAI-compatible gateways use for image output
    for entry in message.get("images") or []:
        if not isinstance(entry, dict):
            continue
        holder = entry.get("image_url")
        url = holder.get("url") if isinstance(holder, dict) else holder
        candidate = url or entry.get("b64_json") or entry.get("data")
        if candidate:
            return _from_url_or_b64(str(candidate))

    # 2. typed content parts
    content = message.get("content")
    if isinstance(content, list):
        for part in content:
            if not isinstance(part, dict):
                continue
            holder = part.get("image_url")
            if isinstance(holder, dict) and holder.get("url"):
                return _from_url_or_b64(str(holder["url"]))
            if isinstance(holder, str) and holder:
                return _from_url_or_b64(holder)
            inline = part.get("inline_data") or part.get("inlineData")
            if isinstance(inline, dict) and inline.get("data"):
                return _decode_b64(str(inline["data"]))
            source = part.get("source")
            if isinstance(source, dict) and source.get("data"):
                return _decode_b64(str(source["data"]))
            if part.get("b64_json"):
                return _decode_b64(str(part["b64_json"]))

    # 3. a data URL sitting in the text, e.g. markdown ![](data:image/png;base64,...)
    text = content if isinstance(content, str) else ""
    match = _DATA_URL_RE.search(text or "")
    if match:
        return _decode_b64(match.group(1))

    # 4. an images-endpoint shaped body that arrived on the chat route
    for entry in body.get("data") or []:
        if isinstance(entry, dict) and (entry.get("b64_json") or entry.get("url")):
            return _from_url_or_b64(str(entry.get("b64_json") or entry["url"]))

    refusal = (text or str(message.get("refusal") or ""))[:200]
    raise GatewayError(
        "The image model replied with no picture" + (f": {refusal}" if refusal else "")
    )


def _fit_to_size(data: bytes, size: str) -> bytes:
    """Centre-crop and resize to exactly `size`.

    Gemini honours an aspect ratio approximately, and the PDF frames are exact.
    A best-effort operation: if Pillow cannot read the bytes they are passed
    through untouched rather than failing the render.
    """
    try:
        from PIL import Image

        target_w, target_h = (int(part) for part in size.split("x"))
        with Image.open(io.BytesIO(data)) as opened:
            image = opened.convert("RGB")
            if image.width == target_w and image.height == target_h:
                return data
            # scale so the shorter side covers the frame, then crop the overflow
            scale = max(target_w / image.width, target_h / image.height)
            resized = image.resize(
                (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
                Image.LANCZOS,
            )
            left = (resized.width - target_w) // 2
            top = (resized.height - target_h) // 2
            cropped = resized.crop((left, top, left + target_w, top + target_h))
            buffer = io.BytesIO()
            cropped.save(buffer, format="PNG")
            return buffer.getvalue()
    except Exception:  # noqa: BLE001 - an un-croppable render is still a render
        return data


class ImageStudio:
    """One object, two backends: an AI gateway or local procedural art."""

    def __init__(self, settings: Settings | None = None, offline: bool | None = None):
        self.settings = settings or get_settings()
        self.offline = (not self.settings.ai_available) if offline is None else offline
        self.warnings: list[str] = []
        self.source = (
            "procedural" if self.offline
            else f"{self.settings.provider}:{self.settings.image_model}"
        )
        # Billing counters, reported in the manifest so a run's cost is visible.
        self.generated = 0
        self.cache_hits = 0

    # ------------------------------------------------------------- geometry
    @staticmethod
    def size_for(spec: CalendarSpec, kind: str) -> str:
        """Cover mirrors the page; interior art is the opposite orientation."""
        portrait_page = spec.orientation == "portrait"
        if kind == "cover":
            return PORTRAIT if portrait_page else LANDSCAPE
        return LANDSCAPE if portrait_page else PORTRAIT

    @staticmethod
    def _pixels(size: str) -> tuple[int, int]:
        w, _, h = size.partition("x")
        return int(w), int(h)

    # -------------------------------------------------------------- backend
    def _chat_request(self, prompt: str, size: str) -> bytes:
        """Ask a multimodal chat model (Gemini) for a picture."""
        aspect = ASPECT_FOR_SIZE.get(size, "3:2 landscape")
        instruction = (
            f"Generate one image, {aspect} aspect ratio. {prompt}\n\n"
            "Output the image only. Do not describe it, do not add any text, "
            "lettering or watermark inside the picture."
        )
        payload = {
            "model": self.settings.image_model,
            "messages": [{"role": "user", "content": instruction}],
            # Gateways that follow the OpenRouter convention need to be told the
            # reply may contain a picture. Ones that do not simply ignore this.
            "modalities": ["image", "text"],
        }
        body = post_json(self.settings, CHAT_PATH, payload)
        data = _image_from_chat(body)
        if not _looks_like_image(data):
            raise GatewayError("The image model returned something that is not an image")
        return _fit_to_size(data, size)

    def _images_request(self, prompt: str, size: str) -> bytes:
        """The classic /images/generations call, for the gpt-image models."""
        model = self.settings.image_model
        payload: dict = {"model": model, "prompt": prompt, "size": size, "n": 1}
        if model.startswith("gpt-image"):
            payload["quality"] = self.settings.image_quality

        body = post_json(self.settings, IMAGES_PATH, payload)
        entries = body.get("data") or []
        if not entries:
            raise GatewayError("Image response contained no data")
        item = entries[0]
        candidate = item.get("b64_json") or item.get("url")
        if not candidate:
            raise GatewayError("Image response contained no payload")
        data = _from_url_or_b64(str(candidate))
        if not _looks_like_image(data):
            raise GatewayError("The image endpoint returned something that is not an image")
        return data

    def _render_request(self, prompt: str, size: str) -> bytes:
        """Dispatch to whichever request style this provider needs."""
        if self.settings.image_api == "images":
            return self._images_request(prompt, size)
        return self._chat_request(prompt, size)

    # ---------------------------------------------------------------- caching
    def _cache_path(self, prompt: str, size: str) -> Path | None:
        """Where an identical request would already be stored.

        Images are the dominant cost of a build, and rebuilding a product with
        the same theme and brief asks for byte-identical artwork. Keying on
        model, quality, size and prompt means a rerun is free.
        """
        if not self.settings.image_cache:
            return None
        digest = hashlib.sha256(
            "\u0000".join(
                [self.settings.image_model, self.settings.image_quality, size, prompt]
            ).encode("utf-8")
        ).hexdigest()[:32]
        folder = self.settings.resolved_cache_dir() / "images"
        folder.mkdir(parents=True, exist_ok=True)
        return folder / f"{digest}.png"

    def generate(
        self,
        prompt: str,
        out_path: str | Path,
        size: str = PORTRAIT,
        spec: CalendarSpec | None = None,
        seed: int = 0,
        kind: str = "cover",
        attempts: int = 2,
    ) -> Path:
        """Generate one image. Falls back to procedural art on any failure.

        A cache hit costs nothing and is recorded in `cache_hits`.
        """
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        theme = get_theme(spec.theme if spec else None)

        if not self.offline:
            cached = self._cache_path(prompt, size)
            if cached and cached.exists() and cached.stat().st_size > 1024:
                out_path.write_bytes(cached.read_bytes())
                self.cache_hits += 1
                return out_path

            last_error: Exception | None = None
            for attempt in range(attempts):
                try:
                    data = self._render_request(prompt, size)
                    out_path.write_bytes(data)
                    self.generated += 1
                    if cached:
                        try:
                            cached.write_bytes(data)
                        except OSError:
                            pass  # a read-only cache dir must not fail the build
                    return out_path
                except Exception as exc:  # noqa: BLE001 - any failure -> fallback
                    last_error = exc
                    # A rejected credential will not fix itself on retry.
                    if isinstance(exc, GatewayError) and exc.status in (401, 403):
                        break
                    time.sleep(1.5 * (attempt + 1))
            self.warnings.append(
                f"AI image failed ({type(last_error).__name__}: {last_error}); "
                f"used procedural art for {out_path.stem}"
            )

        return generate_procedural_art(
            out_path.with_suffix(".png"), theme, self._pixels(size), seed=seed, kind=kind
        )

    # -------------------------------------------------------------- batches
    def generate_set(
        self,
        spec: CalendarSpec,
        out_dir: str | Path,
        progress: Progress | None = None,
    ) -> dict[str, Path]:
        """Generate every image the product needs and return {key: path}.

        Month keys are expanded so the PDF engine can look up
        `month_01` ... `month_12` directly, even when images are shared.
        """
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        prompts, mapping = art_plan(spec)

        produced: dict[str, Path] = {}
        total = len(prompts)
        for index, (key, prompt) in enumerate(prompts.items(), start=1):
            if progress:
                progress(f"Generating art {index}/{total} ({key})", index / max(total, 1))
            kind = "cover" if key == "cover" else "interior"
            size = self.size_for(spec, kind)
            seed = abs(hash((spec.year, spec.theme, key))) % 10_000_019
            produced[key] = self.generate(
                prompt,
                out_dir / f"{key}.png",
                size=size,
                spec=spec,
                seed=seed,
                kind=kind,
            )

        for month_key, group in mapping.items():
            if group in produced:
                produced[month_key] = produced[group]
        return produced

    def prompt_manifest(self, spec: CalendarSpec) -> dict[str, str]:
        prompts, _ = art_plan(spec)
        return prompts

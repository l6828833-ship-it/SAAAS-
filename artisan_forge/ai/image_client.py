"""Image generation client.

Talks to the OpenAI Images API when a key is available and degrades to
procedural art otherwise, so a build never fails just because of a missing
credential or a transient API error.
"""

from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

from ..config import Settings, get_settings
from ..models import CalendarSpec
from ..themes import get_theme
from .procedural import generate_procedural_art
from .prompts import art_plan

API_URL = "https://api.openai.com/v1/images/generations"

# GPT image models accept a fixed set of output sizes.
PORTRAIT = "1024x1536"
LANDSCAPE = "1536x1024"
SQUARE = "1024x1024"

Progress = Callable[[str, float], None]


class ImageStudio:
    """One object, two backends: OpenAI Images or local procedural art."""

    def __init__(self, settings: Settings | None = None, offline: bool | None = None):
        self.settings = settings or get_settings()
        self.offline = (not self.settings.ai_available) if offline is None else offline
        self.warnings: list[str] = []
        self.source = "procedural" if self.offline else f"openai:{self.settings.image_model}"

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
    def _openai_request(self, prompt: str, size: str) -> bytes:
        model = self.settings.image_model
        payload: dict = {
            "model": model,
            "prompt": prompt,
            "size": size,
            "n": 1,
        }
        if model.startswith("gpt-image"):
            payload["quality"] = self.settings.image_quality

        try:  # preferred: official SDK
            from openai import OpenAI

            client = OpenAI(api_key=self.settings.openai_api_key)
            result = client.images.generate(**payload)
            item = result.data[0]
            if getattr(item, "b64_json", None):
                return base64.b64decode(item.b64_json)
            if getattr(item, "url", None):
                with urllib.request.urlopen(item.url, timeout=120) as response:
                    return response.read()
            raise RuntimeError("Image response contained no data")
        except ImportError:
            pass  # fall through to plain HTTP

        request = urllib.request.Request(
            API_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.settings.openai_api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=180) as response:
            body = json.loads(response.read().decode("utf-8"))
        item = body["data"][0]
        if item.get("b64_json"):
            return base64.b64decode(item["b64_json"])
        with urllib.request.urlopen(item["url"], timeout=120) as response:
            return response.read()

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
        """Generate one image. Falls back to procedural art on any failure."""
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        theme = get_theme(spec.theme if spec else None)

        if not self.offline:
            last_error: Exception | None = None
            for attempt in range(attempts):
                try:
                    data = self._openai_request(prompt, size)
                    out_path.write_bytes(data)
                    return out_path
                except Exception as exc:  # noqa: BLE001 - any failure -> fallback
                    last_error = exc
                    if isinstance(exc, urllib.error.HTTPError):
                        detail = exc.read().decode("utf-8", "replace")[:200]
                        last_error = RuntimeError(f"HTTP {exc.code}: {detail}")
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

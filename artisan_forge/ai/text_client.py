"""ChatGPT-powered content generation, with an offline template fallback.

Used by the bundle generator to write the pages of a digital product (prompts,
checklists, tracker columns, affirmations) plus its Etsy listing copy.

Model ids move fast, so `MODEL_CHAIN` is tried in order and the first one that
answers wins. Override the front of the chain with `AF_TEXT_MODEL`.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Any

from ..config import Settings, get_settings

CHAT_URL = "https://api.openai.com/v1/chat/completions"

MODEL_CHAIN = ["gpt-5.6-luna", "gpt-5-mini", "gpt-4.1-mini", "gpt-4o-mini"]

SYSTEM = (
    "You are a senior digital product designer who ships best-selling printable "
    "bundles on Etsy. You write warm, concrete, practical copy with no filler, no "
    "emoji and no cliches. You always answer with valid JSON only."
)


def model_chain(settings: Settings | None = None) -> list[str]:
    preferred = (settings.text_model if settings else os.getenv("AF_TEXT_MODEL")) or ""
    chain = [m for m in [preferred.strip()] if m]
    chain += [m for m in MODEL_CHAIN if m != preferred]
    return chain


def _extract_json(text: str) -> dict:
    """Tolerate models that wrap JSON in prose or fences."""
    text = text.strip()
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


class CopyStudio:
    """One object, two backends: ChatGPT or local templates."""

    def __init__(self, settings: Settings | None = None, offline: bool | None = None):
        self.settings = settings or get_settings()
        self.offline = (not self.settings.ai_available) if offline is None else offline
        self.warnings: list[str] = []
        self.model_used: str | None = None
        self.source = "template" if self.offline else "chatgpt"

    # -------------------------------------------------------------- backend
    def _post(self, payload: dict) -> str:
        try:
            from openai import OpenAI

            client = OpenAI(api_key=self.settings.openai_api_key)
            result = client.chat.completions.create(**payload)
            return result.choices[0].message.content or ""
        except ImportError:
            pass

        request = urllib.request.Request(
            CHAT_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.settings.openai_api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=180) as response:
            body = json.loads(response.read().decode("utf-8"))
        return body["choices"][0]["message"]["content"]

    def ask_json(self, prompt: str) -> dict | None:
        """Ask for a JSON object. Returns None when offline or on failure."""
        if self.offline:
            return None

        messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}]
        last_error: Exception | None = None
        for model in model_chain(self.settings):
            for use_schema in (True, False):
                payload: dict[str, Any] = {"model": model, "messages": messages}
                if use_schema:
                    payload["response_format"] = {"type": "json_object"}
                try:
                    content = self._post(payload)
                    data = _extract_json(content)
                    self.model_used = model
                    self.source = f"chatgpt:{model}"
                    return data
                except Exception as exc:  # noqa: BLE001 - try the next option
                    last_error = exc
                    if isinstance(exc, urllib.error.HTTPError):
                        detail = exc.read().decode("utf-8", "replace")[:160]
                        last_error = RuntimeError(f"HTTP {exc.code}: {detail}")
        self.warnings.append(
            f"ChatGPT content unavailable ({type(last_error).__name__}: {last_error}); "
            "used built-in templates"
        )
        self.source = "template"
        return None

"""Minimal HTTP layer with a swappable transport.

Everything the Etsy client does goes through `Transport.send`, so tests can
inject a fake and exercise the whole integration without touching the network.
"""

from __future__ import annotations

import json
import mimetypes
import secrets
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass
class Response:
    status: int
    body: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    def json(self) -> Any:
        if not self.body:
            return {}
        try:
            return json.loads(self.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {"raw": self.body[:400].decode("utf-8", "replace")}

    def text(self, limit: int = 400) -> str:
        return self.body[:limit].decode("utf-8", "replace")


class Transport(Protocol):
    def send(
        self,
        method: str,
        url: str,
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
        timeout: int = 60,
    ) -> Response: ...


class UrllibTransport:
    """Standard library transport. HTTP errors come back as responses."""

    def send(
        self,
        method: str,
        url: str,
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
        timeout: int = 60,
    ) -> Response:
        request = urllib.request.Request(url, data=body, method=method, headers=headers or {})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return Response(
                    status=response.status,
                    body=response.read(),
                    headers={k.lower(): v for k, v in response.headers.items()},
                )
        except urllib.error.HTTPError as exc:
            return Response(
                status=exc.code,
                body=exc.read(),
                headers={k.lower(): v for k, v in (exc.headers or {}).items()},
            )
        except urllib.error.URLError as exc:
            raise ConnectionError(f"Could not reach {url}: {exc.reason}") from exc


def form_body(fields: dict[str, Any]) -> bytes:
    """application/x-www-form-urlencoded body."""
    clean = {key: value for key, value in fields.items() if value is not None}
    return urllib.parse.urlencode(clean).encode("utf-8")


def encode_multipart(
    fields: dict[str, Any] | None = None,
    files: list[tuple[str, str, bytes]] | None = None,
    boundary: str | None = None,
) -> tuple[bytes, str]:
    """Build a multipart/form-data body.

    `files` items are (field_name, filename, content). Returns (body, content_type).
    """
    boundary = boundary or f"----ArtisanForge{secrets.token_hex(12)}"
    parts: list[bytes] = []
    marker = f"--{boundary}\r\n".encode()

    for name, value in (fields or {}).items():
        if value is None:
            continue
        parts.append(marker)
        parts.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        parts.append(f"{value}\r\n".encode())

    for name, filename, content in files or []:
        guessed = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        parts.append(marker)
        parts.append(
            f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'.encode()
        )
        parts.append(f"Content-Type: {guessed}\r\n\r\n".encode())
        parts.append(content)
        parts.append(b"\r\n")

    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def file_part(field_name: str, path: str | Path) -> tuple[str, str, bytes]:
    path = Path(path)
    return (field_name, path.name, path.read_bytes())

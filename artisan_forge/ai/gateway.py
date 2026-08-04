"""One HTTP door to whichever OpenAI-compatible gateway is configured.

Both the text client and the image client speak the same protocol - OpenAI's
`/chat/completions` shape - so the only things that vary between providers are
the base URL, the credential and the model ids. Keeping the transport in one
place means the Inworld Router, the OpenAI API, or any other compatible gateway
are a config change rather than a code change.

Two details are worth knowing:

* **Auth scheme.** Inworld's portal hands out a base64 credential and its
  reference docs show `Authorization: Basic <credential>`, while the
  request-level routing docs show `Bearer <key>`. Rather than make the user
  guess, `post_json` tries Bearer, and on a 401/403 retries with Basic and
  remembers which one that gateway accepted.
* **Retries.** 429s and 5xx are transient and worth a second look; a 400 from a
  bad payload is not, so it fails immediately and lets the caller fall back.
"""

from __future__ import annotations

import base64
import json
import re
import time
import urllib.error
import urllib.request

from ..config import Settings

# Transient statuses worth retrying, and the one that means "wrong auth scheme".
RETRY_STATUSES = {408, 409, 425, 429, 500, 502, 503, 504}
AUTH_STATUSES = {401, 403}

# base_url -> the scheme that gateway accepted, so only the first call pays for
# the discovery. Process-local and intentionally not persisted.
_SCHEME_CACHE: dict[str, str] = {}

_BASE64_RE = re.compile(r"^[A-Za-z0-9+/]+={0,2}$")


class GatewayError(RuntimeError):
    """An HTTP or protocol failure from the AI gateway, with readable detail."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def _basic_credential(key: str) -> str:
    """The value for an `Authorization: Basic` header.

    Inworld's portal already gives you base64 credentials, so those are passed
    through untouched. A raw key (or a `key:secret` pair) is encoded here.
    """
    if ":" not in key and len(key) % 4 == 0 and _BASE64_RE.match(key):
        return key
    pair = key if ":" in key else f"{key}:"
    return base64.b64encode(pair.encode("utf-8")).decode("ascii")


def _auth_header(key: str, scheme: str) -> str:
    return f"Basic {_basic_credential(key)}" if scheme == "basic" else f"Bearer {key}"


def _schemes_to_try(settings: Settings) -> list[str]:
    """Bearer first, unless this gateway has already told us it wants Basic."""
    known = _SCHEME_CACHE.get(settings.base_url)
    if known:
        return [known]
    return ["bearer", "basic"]


def _read_error(exc: urllib.error.HTTPError) -> str:
    try:
        body = exc.read().decode("utf-8", "replace")
    except Exception:  # noqa: BLE001 - an unreadable error body is still an error
        return ""
    # Gateways wrap the useful sentence in {"error": {"message": ...}}
    try:
        parsed = json.loads(body)
    except Exception:  # noqa: BLE001
        return body[:300]
    error = parsed.get("error") if isinstance(parsed, dict) else None
    if isinstance(error, dict) and error.get("message"):
        return str(error["message"])[:300]
    if isinstance(error, str):
        return error[:300]
    return body[:300]


def post_json(
    settings: Settings,
    path: str,
    payload: dict,
    timeout: int = 180,
    attempts: int = 2,
) -> dict:
    """POST JSON to the gateway and return the decoded response.

    `path` is relative to the configured base URL, e.g. "/chat/completions".
    Raises `GatewayError` on any failure so callers can fall back to templates
    or procedural art rather than crashing a build.
    """
    key = settings.api_key
    if not key:
        raise GatewayError(f"No API key configured ({settings.key_env_var} is empty)")

    url = f"{settings.base_url}{path if path.startswith('/') else '/' + path}"
    body = json.dumps(payload).encode("utf-8")
    last: GatewayError | None = None

    for attempt in range(max(1, attempts)):
        for scheme in _schemes_to_try(settings):
            request = urllib.request.Request(
                url,
                data=body,
                headers={
                    "Authorization": _auth_header(key, scheme),
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    decoded = json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                detail = _read_error(exc)
                last = GatewayError(f"HTTP {exc.code}: {detail}", status=exc.code)
                if exc.code in AUTH_STATUSES:
                    continue  # wrong scheme for this gateway - try the other one
                break  # a non-auth error will not be fixed by another scheme
            except Exception as exc:  # noqa: BLE001 - timeouts, DNS, bad JSON
                last = GatewayError(f"{type(exc).__name__}: {exc}")
                break
            else:
                _SCHEME_CACHE[settings.base_url] = scheme
                return decoded

        if last is not None and last.status is not None and last.status not in RETRY_STATUSES:
            break
        if attempt + 1 < max(1, attempts):
            time.sleep(1.5 * (attempt + 1))

    raise last or GatewayError("The AI gateway returned no response")


def fetch_bytes(url: str, timeout: int = 120) -> bytes:
    """Download a URL the gateway pointed at (some return links, not payloads)."""
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return response.read()

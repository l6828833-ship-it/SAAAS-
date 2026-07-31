"""Etsy Open API v3 client.

Every v3 request needs two headers: `x-api-key` with the app keystring and
`Authorization: Bearer {user_id}.{token}`. Requests are paced to stay inside the
personal-access budget (5 QPS / 5,000 QPD) and a 401 triggers one silent token
refresh before retrying.
"""

from __future__ import annotations

import json
import time
import urllib.parse
from pathlib import Path
from typing import Any, Callable

from . import oauth
from .http import Response, Transport, UrllibTransport, encode_multipart
from .tokens import TokenSet

API_BASE = "https://openapi.etsy.com/v3/application"

# Etsy's docs are inconsistent about whether x-api-key is the keystring alone or
# "keystring:shared_secret". Keystring alone is what works in practice; set
# AF_ETSY_API_KEY_HEADER to override without touching code.
MIN_INTERVAL = 0.21  # ~4.8 requests/second, just under the 5 QPS limit


class EtsyApiError(RuntimeError):
    def __init__(self, status: int, message: str, payload: Any = None):
        super().__init__(f"Etsy API {status}: {message}")
        self.status = status
        self.message = message
        self.payload = payload


class EtsyRateLimited(EtsyApiError):
    def __init__(self, retry_after: float, message: str = "Rate limit reached"):
        super().__init__(429, message)
        self.retry_after = retry_after


class EtsyClient:
    def __init__(
        self,
        keystring: str,
        tokens: TokenSet,
        transport: Transport | None = None,
        api_key_header: str | None = None,
        min_interval: float = MIN_INTERVAL,
        on_refresh: Callable[[TokenSet], None] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.keystring = keystring
        self.tokens = tokens
        self.transport = transport or UrllibTransport()
        self.api_key_header = api_key_header or keystring
        self.min_interval = min_interval
        self.on_refresh = on_refresh
        self._sleep = sleep
        self._clock = clock
        self._last_call: float | None = None
        self.calls = 0

    # ------------------------------------------------------------- plumbing
    def _pace(self) -> None:
        """Keep at least `min_interval` between requests (5 QPS budget)."""
        if self.min_interval <= 0:
            return
        now = self._clock()
        if self._last_call is not None:
            wait = self.min_interval - (now - self._last_call)
            if wait > 0:
                self._sleep(wait)
                now = self._last_call + self.min_interval
        self._last_call = now

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {
            "x-api-key": self.api_key_header,
            "Authorization": f"Bearer {self.tokens.access_token}",
            "Accept": "application/json",
        }
        headers.update(extra or {})
        return headers

    def refresh(self) -> TokenSet:
        data = oauth.refresh_tokens(self.keystring, self.tokens.refresh_token, self.transport)
        refreshed = TokenSet.from_response(
            data,
            shop_id=self.tokens.shop_id,
            shop_name=self.tokens.shop_name,
            connected_at=self.tokens.connected_at,
        )
        if not refreshed.refresh_token:
            refreshed.refresh_token = self.tokens.refresh_token
        self.tokens = refreshed
        if self.on_refresh:
            self.on_refresh(refreshed)
        return refreshed

    def request(
        self,
        method: str,
        path: str,
        params: dict | None = None,
        json_body: dict | None = None,
        multipart: tuple[bytes, str] | None = None,
        allow_refresh: bool = True,
    ) -> Any:
        url = f"{API_BASE}{path}"
        if params:
            clean = {k: v for k, v in params.items() if v is not None}
            if clean:
                url = f"{url}?{urllib.parse.urlencode(clean)}"

        body: bytes | None = None
        extra: dict[str, str] = {}
        if multipart is not None:
            body, content_type = multipart
            extra["Content-Type"] = content_type
        elif json_body is not None:
            body = json.dumps(json_body).encode("utf-8")
            extra["Content-Type"] = "application/json"

        self._pace()
        self.calls += 1
        response: Response = self.transport.send(method, url, self._headers(extra), body)

        if response.status == 401 and allow_refresh and self.tokens.refresh_token:
            self.refresh()
            return self.request(
                method, path, params, json_body, multipart, allow_refresh=False
            )

        if response.status == 429:
            retry_after = float(response.headers.get("retry-after", "1") or 1)
            raise EtsyRateLimited(retry_after, _message(response))

        if not response.ok:
            raise EtsyApiError(response.status, _message(response), response.json())
        return response.json()

    # --------------------------------------------------------------- reads
    def me(self) -> dict:
        return self.request("GET", "/users/me")

    def shop(self, shop_id: int) -> dict:
        return self.request("GET", f"/shops/{shop_id}")

    def shop_sections(self, shop_id: int) -> list[dict]:
        data = self.request("GET", f"/shops/{shop_id}/sections")
        return data.get("results", [])

    def shipping_profiles(self, shop_id: int) -> list[dict]:
        data = self.request("GET", f"/shops/{shop_id}/shipping-profiles")
        return data.get("results", [])

    def taxonomy_nodes(self) -> list[dict]:
        """Flattened seller taxonomy: [{id, name, path, level}]."""
        data = self.request("GET", "/seller-taxonomy/nodes")
        flat: list[dict] = []

        def walk(nodes: list[dict], trail: list[str]) -> None:
            for node in nodes:
                path = trail + [node.get("name", "")]
                flat.append(
                    {
                        "id": node.get("id"),
                        "name": node.get("name", ""),
                        "path": " > ".join(part for part in path if part),
                        "level": node.get("level", len(path)),
                    }
                )
                walk(node.get("children") or [], path)

        walk(data.get("results", []), [])
        return flat

    def listing(self, listing_id: int) -> dict:
        return self.request("GET", f"/listings/{listing_id}")

    # -------------------------------------------------------------- writes
    def create_draft_listing(self, shop_id: int, payload: dict) -> dict:
        """POST /shops/{shop_id}/listings - always lands in draft state."""
        return self.request("POST", f"/shops/{shop_id}/listings", json_body=payload)

    def upload_listing_image(
        self,
        shop_id: int,
        listing_id: int,
        path: str | Path,
        rank: int = 1,
        alt_text: str = "",
    ) -> dict:
        path = Path(path)
        fields: dict[str, Any] = {"rank": rank}
        if alt_text:
            fields["alt_text"] = alt_text[:250]
        body = encode_multipart(fields, [("image", path.name, path.read_bytes())])
        return self.request(
            "POST", f"/shops/{shop_id}/listings/{listing_id}/images", multipart=body
        )

    def upload_listing_file(
        self,
        shop_id: int,
        listing_id: int,
        path: str | Path,
        name: str | None = None,
        rank: int = 0,
    ) -> dict:
        path = Path(path)
        body = encode_multipart(
            {"name": name or path.name, "rank": rank},
            [("file", path.name, path.read_bytes())],
        )
        return self.request(
            "POST", f"/shops/{shop_id}/listings/{listing_id}/files", multipart=body
        )

    # ------------------------------------------------------------ discovery
    def resolve_shop(self) -> tuple[int, str]:
        """Shop id and name for the connected token."""
        if self.tokens.shop_id and self.tokens.shop_name:
            return self.tokens.shop_id, self.tokens.shop_name
        me = self.me()
        shop_id = me.get("shop_id")
        if not shop_id:
            user_id = me.get("user_id") or self.tokens.etsy_user_id
            data = self.request("GET", f"/users/{user_id}/shops")
            results = data.get("results") if isinstance(data, dict) else None
            if results:
                shop_id = results[0].get("shop_id")
            elif isinstance(data, dict):
                shop_id = data.get("shop_id")
        if not shop_id:
            raise EtsyApiError(404, "No Etsy shop found for this account")
        details = self.shop(int(shop_id))
        return int(shop_id), details.get("shop_name", "")


def _message(response: Response) -> str:
    payload = response.json()
    if isinstance(payload, dict):
        for key in ("error", "error_description", "message"):
            if payload.get(key):
                return str(payload[key])
    return response.text(200) or "no response body"

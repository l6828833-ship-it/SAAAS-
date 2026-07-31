"""Etsy OAuth 2.0 authorization code flow with PKCE.

Docs: https://developers.etsy.com/documentation/essentials/authentication
- authorize:  https://www.etsy.com/oauth/connect
- token:      https://api.etsy.com/v3/public/oauth/token
- PKCE `code_challenge_method` must be "S256"
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import urllib.parse

from .http import Response, Transport, UrllibTransport, form_body

AUTHORIZE_URL = "https://www.etsy.com/oauth/connect"
TOKEN_URL = "https://api.etsy.com/v3/public/oauth/token"

# Draft-only integration: read shop data, read and write listings. No delete,
# no transactions, no billing.
SCOPES = ("listings_r", "listings_w", "shops_r")


class OAuthError(RuntimeError):
    pass


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def new_verifier() -> str:
    """PKCE code verifier (43-128 chars of unreserved characters)."""
    return _b64url(secrets.token_bytes(48))


def new_state() -> str:
    return secrets.token_urlsafe(24)


def challenge_for(verifier: str) -> str:
    return _b64url(hashlib.sha256(verifier.encode("ascii")).digest())


def authorize_url(
    keystring: str,
    redirect_uri: str,
    verifier: str,
    state: str,
    scopes: tuple[str, ...] | list[str] = SCOPES,
) -> str:
    query = urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": keystring,
            "redirect_uri": redirect_uri,
            "scope": " ".join(scopes),
            "state": state,
            "code_challenge": challenge_for(verifier),
            "code_challenge_method": "S256",
        }
    )
    return f"{AUTHORIZE_URL}?{query}"


def _token_request(payload: dict, transport: Transport | None = None) -> dict:
    transport = transport or UrllibTransport()
    response: Response = transport.send(
        "POST",
        TOKEN_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        body=form_body(payload),
    )
    data = response.json() if response.body else {}
    if not response.ok:
        detail = data.get("error_description") or data.get("error") or response.text(200)
        raise OAuthError(f"Etsy token request failed ({response.status}): {detail}")
    if "access_token" not in data:
        raise OAuthError(f"Etsy token response had no access_token: {response.text(200)}")
    return data


def exchange_code(
    keystring: str,
    redirect_uri: str,
    code: str,
    verifier: str,
    transport: Transport | None = None,
) -> dict:
    return _token_request(
        {
            "grant_type": "authorization_code",
            "client_id": keystring,
            "redirect_uri": redirect_uri,
            "code": code,
            "code_verifier": verifier,
        },
        transport,
    )


def refresh_tokens(keystring: str, refresh_token: str, transport: Transport | None = None) -> dict:
    return _token_request(
        {
            "grant_type": "refresh_token",
            "client_id": keystring,
            "refresh_token": refresh_token,
        },
        transport,
    )


def user_id_from_token(access_token: str) -> str:
    """Etsy access tokens are '{user_id}.{token}'."""
    return access_token.split(".", 1)[0] if "." in access_token else ""

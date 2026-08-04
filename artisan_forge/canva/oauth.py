"""Canva OAuth 2.0 authorization code flow with PKCE (SHA-256).

Docs: https://www.canva.dev/docs/connect/authentication/
- authorize:  https://www.canva.com/api/oauth/authorize
- token:      https://api.canva.com/rest/v1/oauth/token

The flow:
1. User clicks "Connect Canva" in the app
2. They are sent to Canva's authorize URL with a PKCE challenge
3. Canva redirects back to your app with a ?code=...
4. Your app exchanges the code for an access_token + refresh_token
5. The token is stored (encrypted) and used for all Canva API calls

Scopes needed by Crochet Studio:
  asset:write            - upload rendered artwork
  design:content:write   - create editable designs from the artwork
  design:content:read    - export designs back as PNGs
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import urllib.error
import urllib.parse
import urllib.request


AUTHORIZE_URL = "https://www.canva.com/api/oauth/authorize"
TOKEN_URL = "https://api.canva.com/rest/v1/oauth/token"

SCOPES = ("asset:read", "asset:write", "design:content:write", "design:content:read")


class CanvaOAuthError(RuntimeError):
    pass


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def new_verifier() -> str:
    """PKCE code verifier: 128-char URL-safe random string."""
    return _b64url(secrets.token_bytes(96))


def new_state() -> str:
    """Anti-CSRF state token."""
    return secrets.token_urlsafe(32)


def challenge_for(verifier: str) -> str:
    """SHA-256 hash of the verifier, base64url-encoded."""
    return _b64url(hashlib.sha256(verifier.encode("ascii")).digest())


def authorize_url(
    client_id: str,
    redirect_uri: str,
    verifier: str,
    state: str,
    scopes: tuple[str, ...] | list[str] = SCOPES,
) -> str:
    """Build the URL to send the user to for authorization."""
    query = urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": " ".join(scopes),
            "state": state,
            "code_challenge": challenge_for(verifier),
            "code_challenge_method": "S256",
        }
    )
    return f"{AUTHORIZE_URL}?{query}"


def _basic_auth(client_id: str, client_secret: str) -> str:
    """HTTP Basic auth header value: base64(client_id:client_secret)."""
    credentials = f"{client_id}:{client_secret}".encode("utf-8")
    return "Basic " + base64.b64encode(credentials).decode("ascii")


def exchange_code(
    code: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
    code_verifier: str,
) -> dict:
    """Exchange an authorization code for an access token + refresh token.

    Returns the full token response dict:
    {
        "access_token": "...",
        "token_type": "Bearer",
        "expires_in": 14400,
        "refresh_token": "...",
        "scope": "asset:write design:content:write design:content:read"
    }
    """
    body = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "code": code,
        "code_verifier": code_verifier,
        "redirect_uri": redirect_uri,
    }).encode("utf-8")

    request = urllib.request.Request(
        TOKEN_URL,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": _basic_auth(client_id, client_secret),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:400]
        raise CanvaOAuthError(
            f"Canva token exchange failed (HTTP {exc.code}): {detail}"
        ) from exc

    if "access_token" not in data:
        raise CanvaOAuthError(f"No access_token in response: {data}")
    return data


def refresh_access_token(
    refresh_token: str,
    client_id: str,
    client_secret: str,
) -> dict:
    """Use a refresh token to get a new access token.

    Each refresh token can only be used once. The response includes a new
    refresh token that replaces the old one.
    """
    body = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }).encode("utf-8")

    request = urllib.request.Request(
        TOKEN_URL,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": _basic_auth(client_id, client_secret),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:400]
        raise CanvaOAuthError(
            f"Canva token refresh failed (HTTP {exc.code}): {detail}"
        ) from exc

    if "access_token" not in data:
        raise CanvaOAuthError(f"No access_token in refresh response: {data}")
    return data

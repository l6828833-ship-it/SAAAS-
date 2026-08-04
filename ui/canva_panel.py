"""Canva Connect panel: OAuth flow handled inside the Streamlit app.

The OAuth redirect kills the Streamlit session (the WebSocket drops when the
browser navigates away). So we persist the PKCE verifier and state to disk
before redirecting, and read them back when the callback arrives. The access
token is also written to disk so it survives both redeploys (on a volume) and
session resets.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import streamlit as st

from artisan_forge.canva.oauth import (
    CanvaOAuthError,
    authorize_url,
    exchange_code,
    new_state,
    new_verifier,
    refresh_access_token,
)
from artisan_forge.config import get_settings

from . import theme

_DATA_DIR = Path(os.getenv("AF_DATA_DIR", "data"))
TOKEN_FILE = _DATA_DIR / "canva_tokens.json"
PENDING_FILE = _DATA_DIR / "canva_oauth_pending.json"


# ----------------------------------------------------------------- persistence
def _save_tokens(tokens: dict) -> None:
    """Persist tokens to disk so they survive a restart."""
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(json.dumps(tokens, indent=2), encoding="utf-8")


def _load_tokens() -> dict | None:
    if TOKEN_FILE.exists():
        try:
            return json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def _save_pending(verifier: str, state: str, redirect_uri: str) -> None:
    """Write the PKCE verifier and state to disk before redirecting.

    Streamlit session state does NOT survive a full-page redirect (the
    WebSocket disconnects), so the callback handler must read these from a
    file rather than session state.
    """
    PENDING_FILE.parent.mkdir(parents=True, exist_ok=True)
    PENDING_FILE.write_text(json.dumps({
        "verifier": verifier,
        "state": state,
        "redirect_uri": redirect_uri,
    }), encoding="utf-8")


def _load_pending() -> dict | None:
    if PENDING_FILE.exists():
        try:
            return json.loads(PENDING_FILE.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def _clear_pending() -> None:
    if PENDING_FILE.exists():
        try:
            PENDING_FILE.unlink()
        except Exception:
            pass


def _apply_token(access_token: str) -> None:
    """Put the token where Settings will find it on next get_settings() call."""
    os.environ["CANVA_ACCESS_TOKEN"] = access_token
    st.session_state["canva_connected"] = True


# ------------------------------------------------------------------- callback
def handle_callback() -> None:
    """Check if Canva redirected back with a code, and exchange it.

    This runs at the top of every page load, BEFORE the auth gate, so that
    the token exchange happens even though the user's session was reset by the
    redirect. The user still needs to sign back in (Streamlit has no persistent
    sessions), but the Canva token is saved to disk for all future sessions.
    """
    params = st.query_params

    # If the URL has an error from Canva, clean it up so it doesn't keep
    # showing or interfere with the login form.
    if params.get("error") and _load_pending():
        _clear_pending()
        error_desc = params.get("error_description", params.get("error", "unknown"))
        st.query_params.clear()
        st.error(f"Canva authorization failed: {error_desc}")
        return

    code = params.get("code")
    state = params.get("state")

    if not code or not state:
        return

    # Read the PKCE data from disk (session state is gone after redirect)
    pending = _load_pending()
    if not pending:
        return  # Not a Canva callback, or already processed

    if state != pending.get("state"):
        return  # Not ours (might be an Etsy callback)

    settings = get_settings()
    client_id = settings.canva_client_id
    client_secret = settings.canva_client_secret
    verifier = pending.get("verifier")
    redirect_uri = pending.get("redirect_uri")

    if not all([client_id, client_secret, verifier, redirect_uri]):
        _clear_pending()
        st.error("Canva OAuth configuration is incomplete. Check CANVA_CLIENT_ID and CANVA_CLIENT_SECRET.")
        st.query_params.clear()
        return

    try:
        tokens = exchange_code(
            code=code,
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
            code_verifier=verifier,
        )
    except CanvaOAuthError as exc:
        _clear_pending()
        st.query_params.clear()
        st.error(f"Canva connection failed: {exc}")
        return

    _save_tokens(tokens)
    _apply_token(tokens["access_token"])
    _clear_pending()

    # Clean the URL so the code isn't reused on refresh
    st.query_params.clear()
    st.success("\u2705 Canva connected successfully! Please sign in again to continue.")


# -------------------------------------------------------------------- loading
def _try_load_saved_token() -> bool:
    """If we have a saved token on disk, apply it to the environment."""
    tokens = _load_tokens()
    if tokens and tokens.get("access_token"):
        _apply_token(tokens["access_token"])
        return True
    return False


def ensure_token_loaded() -> None:
    """Call once per page load to restore the Canva token from disk."""
    if not st.session_state.get("canva_connected"):
        _try_load_saved_token()


# --------------------------------------------------------------------- panel
def render_connect_button() -> None:
    """Show the Canva connection status and a Connect/Disconnect button."""
    ensure_token_loaded()
    settings = get_settings()

    if settings.canva_available:
        theme.note("Canva is connected. Artwork will be pushed as editable designs.", "ok")
        if st.button("Disconnect Canva", key="canva_disconnect"):
            os.environ.pop("CANVA_ACCESS_TOKEN", None)
            if TOKEN_FILE.exists():
                TOKEN_FILE.unlink()
            st.session_state["canva_connected"] = False
            st.rerun()
        return

    if not settings.canva_client_id or not settings.canva_client_secret:
        theme.note(
            "To connect Canva, set CANVA_CLIENT_ID and CANVA_CLIENT_SECRET in your .env file. "
            "Get these from https://www.canva.dev/integrations",
            "warn",
        )
        st.caption(
            "In your Canva integration settings, set the redirect URL to: "
            f"**{_redirect_uri()}**"
        )
        return

    st.caption(
        "Make sure your Canva integration's redirect URL is set to exactly: "
        f"**{_redirect_uri()}**"
    )

    if st.button("\U0001f3a8 Connect Canva", type="primary", key="canva_connect"):
        verifier = new_verifier()
        state = new_state()
        redirect_uri = _redirect_uri()

        # Persist to disk so the callback can read them after the redirect
        _save_pending(verifier, state, redirect_uri)

        url = authorize_url(
            client_id=settings.canva_client_id,
            redirect_uri=redirect_uri,
            verifier=verifier,
            state=state,
        )
        st.markdown(
            f'<meta http-equiv="refresh" content="0;url={url}">',
            unsafe_allow_html=True,
        )
        st.info("Redirecting to Canva... Click the link if it doesn't open automatically:")
        st.markdown(f"[Open Canva authorization]({url})")
        st.stop()


def try_refresh() -> bool:
    """Attempt to refresh an expired token. Returns True on success."""
    tokens = _load_tokens()
    if not tokens or not tokens.get("refresh_token"):
        return False

    settings = get_settings()
    if not settings.canva_client_id or not settings.canva_client_secret:
        return False

    try:
        new_tokens = refresh_access_token(
            refresh_token=tokens["refresh_token"],
            client_id=settings.canva_client_id,
            client_secret=settings.canva_client_secret,
        )
    except CanvaOAuthError:
        return False

    _save_tokens(new_tokens)
    _apply_token(new_tokens["access_token"])
    return True


def _redirect_uri() -> str:
    """The redirect URL for the OAuth flow - must match what's in Canva's portal exactly."""
    settings = get_settings()
    base = settings.etsy_redirect_uri or "http://localhost:8501"
    # Canva requires the trailing slash if that's what was registered
    if not base.endswith("/"):
        base += "/"
    return base

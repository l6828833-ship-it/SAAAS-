"""Canva Connect panel: OAuth flow handled inside the Streamlit app.

The user clicks "Connect Canva", gets sent to Canva's authorize page, Canva
redirects back to this app with ?code=..., and we exchange it for tokens.
The access token is stored in session state and written to a local file so
it survives a page refresh.
"""

from __future__ import annotations

import json
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

TOKEN_FILE = Path("data") / "canva_tokens.json"


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


def _apply_token(access_token: str) -> None:
    """Put the token where Settings will find it on next get_settings() call."""
    import os
    os.environ["CANVA_ACCESS_TOKEN"] = access_token
    st.session_state["canva_connected"] = True


def handle_callback() -> None:
    """Check if Canva redirected back with a code, and exchange it."""
    params = st.query_params
    code = params.get("code")
    state = params.get("state")

    if not code or not state:
        return

    # Only process if this looks like a Canva callback (not Etsy)
    saved_state = st.session_state.get("canva_oauth_state")
    if not saved_state or state != saved_state:
        return

    settings = get_settings()
    client_id = settings.canva_client_id
    client_secret = settings.canva_client_secret
    verifier = st.session_state.get("canva_oauth_verifier")
    redirect_uri = st.session_state.get("canva_redirect_uri")

    if not all([client_id, client_secret, verifier, redirect_uri]):
        st.error("Canva OAuth session data is missing. Please try connecting again.")
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
        st.error(f"Canva connection failed: {exc}")
        return

    _save_tokens(tokens)
    _apply_token(tokens["access_token"])

    # Clean up the URL so the code isn't reused
    st.query_params.clear()
    st.session_state.pop("canva_oauth_state", None)
    st.session_state.pop("canva_oauth_verifier", None)
    st.success("Canva connected successfully!")


def _try_load_saved_token() -> bool:
    """If we have a saved token, apply it."""
    tokens = _load_tokens()
    if tokens and tokens.get("access_token"):
        _apply_token(tokens["access_token"])
        return True
    return False


def render_connect_button() -> None:
    """Show the Canva connection status and a Connect/Disconnect button."""
    settings = get_settings()

    # Try loading saved tokens on first run
    if not st.session_state.get("canva_connected"):
        _try_load_saved_token()
        settings = get_settings()

    if settings.canva_available:
        theme.note("Canva is connected. Artwork will be pushed as editable designs.", "ok")
        if st.button("Disconnect Canva", key="canva_disconnect"):
            import os
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
        "Make sure your Canva integration's redirect URL is set to: "
        f"**{_redirect_uri()}**"
    )

    if st.button("\U0001f3a8 Connect Canva", type="primary", key="canva_connect"):
        verifier = new_verifier()
        state = new_state()
        redirect_uri = _redirect_uri()

        st.session_state["canva_oauth_verifier"] = verifier
        st.session_state["canva_oauth_state"] = state
        st.session_state["canva_redirect_uri"] = redirect_uri

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
    # Use the same base URL the app is deployed at (shared with Etsy callback)
    base = settings.etsy_redirect_uri or "http://localhost:8501"
    # Canva requires the trailing slash if that's what was registered
    if not base.endswith("/"):
        base += "/"
    return base

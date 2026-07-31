"""Etsy connect + draft publishing UI."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from artisan_forge.config import get_settings
from artisan_forge.etsy import EtsyApiError, EtsyClient, PublishOptions, publish_draft
from artisan_forge.etsy import oauth, tokens as token_store
from artisan_forge.etsy.publisher import validate_manifest
from artisan_forge.saas import db

from . import theme

CATEGORY_HINTS = {
    "calendar": "calendar",
    "bundle": "digital",
}


# ------------------------------------------------------------------ plumbing
def _clear_query_params() -> None:
    try:
        st.query_params.clear()
    except Exception:  # pragma: no cover - older Streamlit
        pass


def get_client(user: dict) -> tuple[EtsyClient | None, str]:
    """Build a client for the connected shop, or explain why we cannot."""
    settings = get_settings()
    if not settings.etsy_configured:
        return None, "ETSY_KEYSTRING and ETSY_REDIRECT_URI are not configured on this deployment."
    try:
        tokens = token_store.load(user["id"])
    except token_store.TokenStorageError as exc:
        return None, str(exc)
    if not tokens:
        return None, "No Etsy shop connected yet."

    client = EtsyClient(
        settings.etsy_keystring,
        tokens,
        api_key_header=settings.etsy_api_key_header,
        on_refresh=lambda refreshed: token_store.save(user["id"], refreshed),
    )
    return client, ""


def handle_callback(user: dict) -> None:
    """Complete the OAuth redirect if Etsy sent us back with a code."""
    params = st.query_params
    code = params.get("code")
    if not code:
        return
    state = params.get("state")
    expected_state = st.session_state.pop("etsy_state", None)
    verifier = st.session_state.pop("etsy_verifier", None)
    _clear_query_params()

    if not verifier or not expected_state or state != expected_state:
        st.session_state["etsy_flash"] = (
            "error",
            "The Etsy sign-in link did not match this session. Start the connection again.",
        )
        return

    settings = get_settings()
    try:
        data = oauth.exchange_code(
            settings.etsy_keystring, settings.etsy_redirect_uri, code, verifier
        )
        tokens = token_store.TokenSet.from_response(data)
        client = EtsyClient(
            settings.etsy_keystring, tokens, api_key_header=settings.etsy_api_key_header
        )
        shop_id, shop_name = client.resolve_shop()
        tokens.shop_id, tokens.shop_name = shop_id, shop_name
        token_store.save(user["id"], tokens)
        st.session_state["etsy_flash"] = ("ok", f"Connected to Etsy shop \u201c{shop_name}\u201d.")
    except Exception as exc:  # noqa: BLE001 - show the real reason
        st.session_state["etsy_flash"] = ("error", f"{type(exc).__name__}: {exc}")


def _flash() -> None:
    flash = st.session_state.pop("etsy_flash", None)
    if not flash:
        return
    tone, message = flash
    if tone == "ok":
        st.success(message)
    else:
        st.error(message)


# ------------------------------------------------------------------- connect
def connect_panel(user: dict) -> None:
    settings = get_settings()
    theme.section("Etsy shop", "draft listings only - nothing is published automatically")
    _flash()

    if not settings.etsy_configured:
        theme.note(
            "Set ETSY_KEYSTRING and ETSY_REDIRECT_URI to enable Etsy publishing. The redirect URI "
            "must match the callback registered in Your Apps on Etsy exactly.",
            "info",
        )
        return
    if not token_store.crypto_available():
        theme.note(
            "The 'cryptography' package is missing, so Etsy tokens cannot be stored securely. "
            "Install it (pip install cryptography) before connecting a shop.",
            "warn",
        )
        return

    try:
        tokens = token_store.load(user["id"])
    except token_store.TokenStorageError as exc:
        theme.note(str(exc), "warn")
        if st.button("Forget stored Etsy tokens"):
            token_store.delete(user["id"])
            st.rerun()
        return

    if tokens:
        columns = st.columns([3, 1])
        with columns[0]:
            st.markdown(
                f"**{tokens.shop_name or 'Etsy shop'}**  \n"
                f"<span style='color:#9A9AAE;font-size:.85rem'>shop id {tokens.shop_id} \u00b7 "
                f"scopes {tokens.scopes or 'listings_r listings_w shops_r'} \u00b7 "
                f"connected {tokens.connected_at[:16].replace('T', ' ')}</span>",
                unsafe_allow_html=True,
            )
        with columns[1]:
            if st.button("Disconnect", use_container_width=True):
                token_store.delete(user["id"])
                st.rerun()

        listings = db.list_etsy_listings(user["id"], limit=10)
        if listings:
            st.caption(f"{len(listings)} draft(s) created from Artisan Forge")
            for row in listings:
                st.markdown(
                    f"- [{row['title'] or row['listing_id']}]({row['edit_url']}) \u00b7 "
                    f"{row['images']} images \u00b7 {row['files']} files \u00b7 "
                    f"{row['created_at'][:16].replace('T', ' ')}"
                )
        return

    verifier = st.session_state.setdefault("etsy_verifier", oauth.new_verifier())
    state = st.session_state.setdefault("etsy_state", oauth.new_state())
    url = oauth.authorize_url(settings.etsy_keystring, settings.etsy_redirect_uri, verifier, state)
    st.link_button("Connect Etsy shop", url, type="primary", use_container_width=True)
    st.caption(
        f"Requests {', '.join(oauth.SCOPES)}. Redirect URI: {settings.etsy_redirect_uri}"
    )


# ------------------------------------------------------------------- publish
def _taxonomy_options(client: EtsyClient) -> list[dict]:
    cached = st.session_state.get("etsy_taxonomy")
    if cached:
        return cached
    nodes = client.taxonomy_nodes()
    st.session_state["etsy_taxonomy"] = nodes
    return nodes


def publish_panel(user: dict, manifest: dict, run_dir: str | Path) -> None:
    run_dir = Path(run_dir)
    settings = get_settings()
    _flash()

    existing = db.etsy_listings_for_run(user["id"], run_dir)
    if existing:
        for row in existing:
            theme.note(
                f"Draft #{row['listing_id']} created "
                f"{row['created_at'][:16].replace('T', ' ')} \u00b7 "
                f"{row['images']} images \u00b7 {row['files']} files",
                "ok",
            )
            st.link_button("Open draft in Etsy", row["edit_url"])

    client, problem = get_client(user)
    if not client:
        theme.note(problem + " Connect a shop on the Account page.", "info")
        return

    tokens = client.tokens
    if not tokens.shop_id:
        theme.note("The connected token has no shop id. Reconnect the shop.", "warn")
        return

    listing = manifest.get("listing", {})
    product_type = manifest.get("product_type", "")

    with st.form(f"etsy_publish_{run_dir.name}"):
        st.markdown(f"**Shop:** {tokens.shop_name or tokens.shop_id}")

        query = st.text_input(
            "Search Etsy category",
            value=CATEGORY_HINTS.get(product_type, ""),
            help="Etsy requires a taxonomy category. Type to search, e.g. 'calendar'.",
        )
        try:
            nodes = _taxonomy_options(client)
        except EtsyApiError as exc:
            st.error(f"Could not load Etsy categories: {exc.message}")
            nodes = []

        matches = [
            node for node in nodes if query.lower() in node["path"].lower()
        ][:60] if query else nodes[:60]
        labels = {node["path"]: node["id"] for node in matches}
        category = st.selectbox(
            "Category", list(labels) or ["(no matches)"],
            help="Pick the deepest category that fits the product.",
        )

        columns = st.columns(3)
        price = columns[0].number_input(
            "Price (USD)", min_value=0.5, max_value=999.0,
            value=float(listing.get("suggested_price_usd") or 6.5), step=0.5,
        )
        quantity = columns[1].number_input("Quantity", min_value=1, max_value=999, value=999)
        max_images = columns[2].slider("Images to upload", 1, 10, 10)

        sections = []
        try:
            sections = client.shop_sections(int(tokens.shop_id))
        except EtsyApiError:
            pass
        section_labels = {"(none)": None}
        section_labels.update(
            {str(s.get("title")): s.get("shop_section_id") for s in sections}
        )
        section = st.selectbox("Shop section", list(section_labels))

        col_a, col_b = st.columns(2)
        include_zip = col_a.checkbox("Attach buyer ZIP", value=True)
        include_pdfs = col_b.checkbox("Attach PDFs separately", value=False)

        title = st.text_input("Title", value=listing.get("title", ""), max_chars=140)
        tags_raw = st.text_input("Tags (comma separated)", value=", ".join(listing.get("tags", [])))
        description = st.text_area("Description", value=listing.get("description", ""), height=200)

        submitted = st.form_submit_button(
            "Create Etsy draft", type="primary", use_container_width=True
        )

    if not submitted:
        return

    options = PublishOptions(
        taxonomy_id=int(labels.get(category) or 0),
        price=float(price),
        quantity=int(quantity),
        shop_section_id=section_labels.get(section),
        title=title,
        tags=[t.strip() for t in tags_raw.split(",") if t.strip()],
        description=description,
        max_images=int(max_images),
        include_zip=include_zip,
        include_pdfs=include_pdfs,
    )

    errors = validate_manifest(manifest, options)
    if errors:
        for error in errors:
            st.error(error)
        return

    progress = st.progress(0.0, text="Starting\u2026")
    try:
        result = publish_draft(
            client,
            int(tokens.shop_id),
            manifest,
            options,
            progress=lambda message, fraction: progress.progress(
                min(max(fraction, 0.0), 1.0), text=message
            ),
        )
    except Exception as exc:  # noqa: BLE001 - report API errors in the UI
        progress.empty()
        st.error(f"Etsy draft failed \u2014 {type(exc).__name__}: {exc}")
        return

    db.record_etsy_listing(
        user_id=user["id"],
        listing_id=result["listing_id"],
        shop_id=int(tokens.shop_id),
        title=result["title"],
        run_dir=run_dir,
        edit_url=result["edit_url"],
        images=result["images_uploaded"],
        files=result["files_uploaded"],
        state=result.get("state", "draft"),
    )
    progress.progress(1.0, text="Draft ready")
    st.success(
        f"Draft #{result['listing_id']} created with {result['images_uploaded']} images and "
        f"{result['files_uploaded']} file(s) in {result['api_calls']} API calls. "
        "It stays a draft until you publish it in Etsy."
    )
    for warning in result["warnings"]:
        theme.note(warning, "warn")
    st.link_button("Open draft in Etsy", result["edit_url"], type="primary")

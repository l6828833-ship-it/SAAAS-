"""Etsy integration: OAuth, client plumbing, token storage and draft publishing.

No network: every request goes through a fake transport that records calls.
"""

from __future__ import annotations

import base64
import hashlib
import json
import urllib.parse

import pytest

from artisan_forge.etsy import oauth
from artisan_forge.etsy.client import EtsyApiError, EtsyClient, EtsyRateLimited
from artisan_forge.etsy.http import Response, encode_multipart, form_body
from artisan_forge.etsy.publisher import (
    MAX_FILE_BYTES,
    PublishOptions,
    build_payload,
    digital_files,
    listing_images,
    publish_draft,
    sanitise_tags,
    sanitise_title,
    validate_manifest,
)
from artisan_forge.etsy.tokens import TokenSet


class FakeTransport:
    """Serves canned responses and records every request."""

    def __init__(self, routes: dict | None = None, default: Response | None = None):
        self.routes = routes or {}
        self.default = default or Response(200, b"{}")
        self.calls: list[dict] = []

    def send(self, method, url, headers=None, body=None, timeout=60):
        path = urllib.parse.urlsplit(url).path
        self.calls.append(
            {
                "method": method,
                "url": url,
                "path": path,
                "headers": headers or {},
                "body": body or b"",
            }
        )
        key = (method, path)
        response = self.routes.get(key, self.default)
        if isinstance(response, list):  # sequence of responses per route
            return response.pop(0) if response else self.default
        return response


def json_response(payload: dict, status: int = 200, headers: dict | None = None) -> Response:
    return Response(status, json.dumps(payload).encode(), headers or {})


def tokens(**overrides) -> TokenSet:
    base = {
        "access_token": "123456.access",
        "refresh_token": "refresh-me",
        "expires_at": 9_999_999_999,
        "etsy_user_id": "123456",
        "shop_id": 42,
        "shop_name": "Gigisy",
    }
    base.update(overrides)
    return TokenSet(**base)


# --------------------------------------------------------------------- oauth
def test_pkce_verifier_and_challenge():
    verifier = oauth.new_verifier()
    assert 43 <= len(verifier) <= 128
    assert "=" not in verifier
    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .decode()
        .rstrip("=")
    )
    assert oauth.challenge_for(verifier) == expected


def test_authorize_url_carries_every_required_param():
    verifier = oauth.new_verifier()
    url = oauth.authorize_url("KEY123", "https://app.example.com", verifier, "state-abc")
    assert url.startswith("https://www.etsy.com/oauth/connect?")
    query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
    assert query["response_type"] == "code"
    assert query["client_id"] == "KEY123"
    assert query["redirect_uri"] == "https://app.example.com"
    assert query["state"] == "state-abc"
    assert query["code_challenge_method"] == "S256"
    assert query["code_challenge"] == oauth.challenge_for(verifier)
    assert set(query["scope"].split()) == set(oauth.SCOPES)


def test_exchange_code_and_refresh():
    transport = FakeTransport(
        {("POST", "/v3/public/oauth/token"): json_response(
            {"access_token": "77.abc", "refresh_token": "r2", "expires_in": 3600}
        )}
    )
    data = oauth.exchange_code("KEY", "https://app", "code-1", "verifier-1", transport)
    assert data["access_token"] == "77.abc"
    sent = dict(urllib.parse.parse_qsl(transport.calls[0]["body"].decode()))
    assert sent["grant_type"] == "authorization_code"
    assert sent["code_verifier"] == "verifier-1"
    assert sent["client_id"] == "KEY"

    oauth.refresh_tokens("KEY", "r2", transport)
    sent = dict(urllib.parse.parse_qsl(transport.calls[1]["body"].decode()))
    assert sent["grant_type"] == "refresh_token"
    assert sent["refresh_token"] == "r2"


def test_token_errors_are_explained():
    transport = FakeTransport(
        {("POST", "/v3/public/oauth/token"): json_response(
            {"error": "invalid_grant", "error_description": "code expired"}, status=400
        )}
    )
    with pytest.raises(oauth.OAuthError) as exc:
        oauth.exchange_code("KEY", "https://app", "bad", "v", transport)
    assert "code expired" in str(exc.value)

    transport = FakeTransport({("POST", "/v3/public/oauth/token"): json_response({"ok": True})})
    with pytest.raises(oauth.OAuthError):
        oauth.exchange_code("KEY", "https://app", "c", "v", transport)


def test_user_id_from_token():
    assert oauth.user_id_from_token("998877.tokenpart") == "998877"
    assert oauth.user_id_from_token("nodot") == ""


# ----------------------------------------------------------------------- http
def test_multipart_encoding_round_trip():
    body, content_type = encode_multipart(
        {"rank": 1, "skipped": None}, [("image", "hero.jpg", b"\xff\xd8binary")], boundary="BOUND"
    )
    text = body.decode("latin-1")
    assert content_type == "multipart/form-data; boundary=BOUND"
    assert '--BOUND\r\nContent-Disposition: form-data; name="rank"' in text
    assert "skipped" not in text
    assert 'name="image"; filename="hero.jpg"' in text
    assert "Content-Type: image/jpeg" in text
    assert "binary" in text
    assert text.endswith("--BOUND--\r\n")


def test_form_body_skips_none():
    assert form_body({"a": "1", "b": None}) == b"a=1"


# --------------------------------------------------------------------- client
def test_client_sends_both_required_headers():
    transport = FakeTransport({("GET", "/v3/application/users/me"): json_response({"shop_id": 42})})
    client = EtsyClient("KEY123", tokens(), transport=transport, min_interval=0)
    assert client.me()["shop_id"] == 42
    headers = transport.calls[0]["headers"]
    assert headers["x-api-key"] == "KEY123"
    assert headers["Authorization"] == "Bearer 123456.access"


def test_api_key_header_can_be_overridden():
    transport = FakeTransport()
    client = EtsyClient("KEY", tokens(), transport=transport, api_key_header="KEY:SECRET", min_interval=0)
    client.me()
    assert transport.calls[0]["headers"]["x-api-key"] == "KEY:SECRET"


def test_create_draft_listing_posts_json_to_shop_path():
    transport = FakeTransport(
        {("POST", "/v3/application/shops/42/listings"): json_response(
            {"listing_id": 555, "state": "draft"}
        )}
    )
    client = EtsyClient("KEY", tokens(), transport=transport, min_interval=0)
    created = client.create_draft_listing(42, {"title": "Hello"})
    assert created["listing_id"] == 555
    call = transport.calls[0]
    assert call["headers"]["Content-Type"] == "application/json"
    assert json.loads(call["body"]) == {"title": "Hello"}


def test_uploads_use_multipart(tmp_path):
    image = tmp_path / "01_hero.jpg"
    image.write_bytes(b"jpegdata")
    zip_file = tmp_path / "product.zip"
    zip_file.write_bytes(b"zipdata")

    transport = FakeTransport(
        {
            ("POST", "/v3/application/shops/42/listings/9/images"): json_response({"listing_image_id": 1}),
            ("POST", "/v3/application/shops/42/listings/9/files"): json_response({"listing_file_id": 2}),
        }
    )
    client = EtsyClient("KEY", tokens(), transport=transport, min_interval=0)
    client.upload_listing_image(42, 9, image, rank=3, alt_text="Cover")
    client.upload_listing_file(42, 9, zip_file, rank=1)

    image_call, file_call = transport.calls
    assert image_call["headers"]["Content-Type"].startswith("multipart/form-data; boundary=")
    assert b'name="rank"' in image_call["body"] and b"3" in image_call["body"]
    assert b'filename="01_hero.jpg"' in image_call["body"]
    assert b'filename="product.zip"' in file_call["body"]
    assert b'name="name"' in file_call["body"]


def test_401_triggers_one_refresh_then_retries():
    saved: list[TokenSet] = []
    transport = FakeTransport(
        {
            ("GET", "/v3/application/users/me"): [
                Response(401, b'{"error":"token expired"}'),
                json_response({"shop_id": 42}),
            ],
            ("POST", "/v3/public/oauth/token"): json_response(
                {"access_token": "123456.fresh", "refresh_token": "r-new", "expires_in": 3600}
            ),
        }
    )
    client = EtsyClient(
        "KEY", tokens(), transport=transport, min_interval=0, on_refresh=saved.append
    )
    assert client.me()["shop_id"] == 42
    assert client.tokens.access_token == "123456.fresh"
    assert saved and saved[0].refresh_token == "r-new"
    # 401, token refresh, retry
    assert [call["path"] for call in transport.calls] == [
        "/v3/application/users/me",
        "/v3/public/oauth/token",
        "/v3/application/users/me",
    ]
    assert transport.calls[-1]["headers"]["Authorization"] == "Bearer 123456.fresh"


def test_refresh_keeps_old_refresh_token_when_absent():
    transport = FakeTransport(
        {("POST", "/v3/public/oauth/token"): json_response(
            {"access_token": "123456.fresh", "expires_in": 60}
        )}
    )
    client = EtsyClient("KEY", tokens(), transport=transport, min_interval=0)
    refreshed = client.refresh()
    assert refreshed.refresh_token == "refresh-me"
    assert refreshed.shop_id == 42  # shop identity survives a refresh


def test_rate_limit_and_error_mapping():
    transport = FakeTransport(
        {("GET", "/v3/application/users/me"): json_response(
            {"error": "too many"}, status=429, headers={"retry-after": "2.5"}
        )}
    )
    client = EtsyClient("KEY", tokens(), transport=transport, min_interval=0)
    with pytest.raises(EtsyRateLimited) as exc:
        client.me()
    assert exc.value.retry_after == 2.5

    transport = FakeTransport(
        {("GET", "/v3/application/users/me"): json_response({"error": "nope"}, status=403)}
    )
    client = EtsyClient("KEY", tokens(), transport=transport, min_interval=0)
    with pytest.raises(EtsyApiError) as exc:
        client.me()
    assert exc.value.status == 403 and "nope" in exc.value.message


def test_requests_are_paced_to_stay_under_5_qps():
    slept: list[float] = []
    # one clock reading per request: t=0.0, then t=0.05 (only 50 ms later)
    ticks = iter([0.0, 0.05, 0.9])
    transport = FakeTransport()
    client = EtsyClient(
        "KEY",
        tokens(),
        transport=transport,
        min_interval=0.21,
        sleep=slept.append,
        clock=lambda: next(ticks),
    )
    client.me()
    client.me()
    assert slept == pytest.approx([0.16], abs=0.01)

    # far enough apart -> no throttling
    client.me()
    assert len(slept) == 1


def test_taxonomy_nodes_are_flattened():
    transport = FakeTransport(
        {("GET", "/v3/application/seller-taxonomy/nodes"): json_response(
            {
                "results": [
                    {
                        "id": 1,
                        "name": "Paper & Party Supplies",
                        "children": [
                            {"id": 2, "name": "Paper", "children": [{"id": 3, "name": "Calendars"}]}
                        ],
                    }
                ]
            }
        )}
    )
    client = EtsyClient("KEY", tokens(), transport=transport, min_interval=0)
    nodes = client.taxonomy_nodes()
    paths = {node["id"]: node["path"] for node in nodes}
    assert paths[3] == "Paper & Party Supplies > Paper > Calendars"


def test_resolve_shop_uses_me_then_shop_details():
    transport = FakeTransport(
        {
            ("GET", "/v3/application/users/me"): json_response({"shop_id": 77, "user_id": 5}),
            ("GET", "/v3/application/shops/77"): json_response({"shop_name": "Gigisy"}),
        }
    )
    client = EtsyClient("KEY", tokens(shop_id=None, shop_name=""), transport=transport, min_interval=0)
    assert client.resolve_shop() == (77, "Gigisy")


# --------------------------------------------------------------- token store
@pytest.fixture()
def store(tmp_path, monkeypatch):
    import importlib

    monkeypatch.setenv("AF_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("AF_SECRET_KEY", "unit-test-secret")
    from artisan_forge.saas import auth as auth_module
    from artisan_forge.saas import db as db_module

    importlib.reload(db_module)
    importlib.reload(auth_module)
    from artisan_forge.etsy import tokens as tokens_module

    importlib.reload(tokens_module)
    db_module.init_db()
    user = auth_module.signup("owner@example.com", "supersecret1")
    return tokens_module, db_module, user


def test_tokens_are_encrypted_at_rest(store):
    tokens_module, db_module, user = store
    tokens_module.save(user["id"], tokens())

    row = db_module.get_etsy_account(user["id"])
    assert "123456.access" not in row["access_token"]
    assert "refresh-me" not in row["refresh_token"]

    loaded = tokens_module.load(user["id"])
    assert loaded.access_token == "123456.access"
    assert loaded.refresh_token == "refresh-me"
    assert (loaded.shop_id, loaded.shop_name) == (42, "Gigisy")

    tokens_module.delete(user["id"])
    assert tokens_module.load(user["id"]) is None


def test_wrong_secret_key_is_reported_not_ignored(store, monkeypatch):
    tokens_module, _db, user = store
    tokens_module.save(user["id"], tokens())

    monkeypatch.setenv("AF_SECRET_KEY", "a-different-secret")
    tokens_module._fernet.cache_clear()
    with pytest.raises(tokens_module.TokenStorageError):
        tokens_module.load(user["id"])
    tokens_module._fernet.cache_clear()


def test_token_expiry_window():
    assert tokens(expires_at=0).expired is True
    assert tokens(expires_at=9_999_999_999).expired is False


# ------------------------------------------------------------------ publisher
@pytest.fixture()
def manifest(tmp_path):
    mock_dir = tmp_path / "mockups"
    mock_dir.mkdir()
    images = []
    for index in range(1, 4):
        path = mock_dir / f"0{index}_scene.jpg"
        path.write_bytes(b"jpeg" * 32)
        images.append(str(path))

    print_dir = tmp_path / "print"
    print_dir.mkdir()
    pdf = print_dir / "calendar-letter.pdf"
    pdf.write_bytes(b"%PDF-1.4 tiny")
    zip_path = tmp_path / "product.zip"
    zip_path.write_bytes(b"zip" * 64)

    return {
        "product_type": "calendar",
        "title": "2026 Watercolor Floral Calendar",
        "pages": 14,
        "files": {
            "pdfs": {"letter": str(pdf)},
            "listing_images": images,
            "zip": str(zip_path),
        },
        "listing": {
            "title": "2026 Watercolor Floral Calendar Printable | Instant Download",
            "tags": ["2026 calendar", "printable calendar"],
            "description": "A calm printable calendar.\n\nWHAT YOU GET\n- 12 pages",
            "suggested_price_usd": 6.5,
        },
    }


def options(**overrides) -> PublishOptions:
    base = {"taxonomy_id": 3, "price": 6.5}
    base.update(overrides)
    return PublishOptions(**base)


def test_title_and_tag_sanitising():
    assert sanitise_title("Cute \U0001f338 Calendar \u2014 2026") == "Cute Calendar 2026"
    assert len(sanitise_title("x" * 200)) == 140
    tags = sanitise_tags(
        ["2026 calendar!", "2026 CALENDAR", "watercolor & floral", "a" * 40, "", "ok"]
    )
    assert tags == ["2026 calendar", "watercolor floral", "a" * 20, "ok"]
    assert len(sanitise_tags([f"tag {i}" for i in range(30)])) == 13


def test_payload_is_a_digital_draft(manifest):
    payload = build_payload(manifest, options(quantity=10, shop_section_id=8))
    assert payload["type"] == "download"
    assert payload["who_made"] == "i_did"
    assert payload["when_made"] == "made_to_order"
    assert payload["taxonomy_id"] == 3
    assert payload["quantity"] == 10
    assert payload["shop_section_id"] == 8
    assert payload["should_auto_renew"] is False
    assert payload["price"] == 6.5
    assert "state" not in payload  # createDraftListing is draft by definition
    assert len(payload["title"]) <= 140


def test_digital_file_selection_respects_etsy_limits(manifest, tmp_path):
    files, warnings = digital_files(manifest, options())
    assert [path.name for path in files] == ["product.zip"]
    assert not warnings

    files, warnings = digital_files(manifest, options(include_zip=False, include_pdfs=True))
    assert [path.name for path in files] == ["calendar-letter.pdf"]

    fat = tmp_path / "huge.zip"
    fat.write_bytes(b"0" * (MAX_FILE_BYTES + 10))
    manifest["files"]["zip"] = str(fat)
    files, warnings = digital_files(manifest, options())
    assert files == [] and "20 MB" in warnings[0]


def test_validation_catches_missing_pieces(manifest):
    assert validate_manifest(manifest, options()) == []
    assert "category" in " ".join(validate_manifest(manifest, options(taxonomy_id=0)))
    assert "price" in " ".join(validate_manifest(manifest, options(price=0)))

    no_images = dict(manifest, files=dict(manifest["files"], listing_images=[]))
    assert "images" in " ".join(validate_manifest(no_images, options()))

    no_files = dict(manifest, files={"listing_images": manifest["files"]["listing_images"]})
    assert "20 MB" in " ".join(validate_manifest(no_files, options()))


def test_publish_draft_uploads_everything_and_stays_draft(manifest):
    transport = FakeTransport(
        {
            ("POST", "/v3/application/shops/42/listings"): json_response(
                {"listing_id": 12345, "state": "draft"}
            ),
            ("POST", "/v3/application/shops/42/listings/12345/images"): json_response({"ok": 1}),
            ("POST", "/v3/application/shops/42/listings/12345/files"): json_response({"ok": 1}),
        }
    )
    client = EtsyClient("KEY", tokens(), transport=transport, min_interval=0)
    steps: list[str] = []
    result = publish_draft(
        client, 42, manifest, options(), progress=lambda message, _f: steps.append(message)
    )

    assert result["listing_id"] == 12345
    assert result["state"] == "draft"
    assert result["images_uploaded"] == 3
    assert result["files_uploaded"] == 1
    assert result["edit_url"].endswith("/listings/12345")
    assert not result["warnings"]
    assert steps[0] == "Creating draft listing"

    paths = [call["path"] for call in transport.calls]
    assert paths.count("/v3/application/shops/42/listings/12345/images") == 3
    assert paths.count("/v3/application/shops/42/listings/12345/files") == 1
    # nothing ever flips the listing to active
    assert all(call["method"] != "PATCH" and call["method"] != "PUT" for call in transport.calls)


def test_publish_draft_reports_upload_failures_without_losing_the_draft(manifest):
    transport = FakeTransport(
        {
            ("POST", "/v3/application/shops/42/listings"): json_response({"listing_id": 999}),
            ("POST", "/v3/application/shops/42/listings/999/images"): json_response(
                {"error": "image too small"}, status=400
            ),
            ("POST", "/v3/application/shops/42/listings/999/files"): json_response({"ok": 1}),
        }
    )
    client = EtsyClient("KEY", tokens(), transport=transport, min_interval=0)
    result = publish_draft(client, 42, manifest, options())
    assert result["images_uploaded"] == 0
    assert result["files_uploaded"] == 1
    assert len(result["warnings"]) == 3
    assert "image too small" in result["warnings"][0]


def test_publish_draft_refuses_invalid_input(manifest):
    client = EtsyClient("KEY", tokens(), transport=FakeTransport(), min_interval=0)
    with pytest.raises(ValueError) as exc:
        publish_draft(client, 42, manifest, options(taxonomy_id=0))
    assert "category" in str(exc.value)


def test_listing_images_filters_non_images(manifest, tmp_path):
    stray = tmp_path / "notes.txt"
    stray.write_text("hi")
    manifest["files"]["listing_images"].append(str(stray))
    manifest["files"]["listing_images"].append(str(tmp_path / "missing.jpg"))
    assert len(listing_images(manifest)) == 3
    assert len(listing_images(manifest, limit=2)) == 2

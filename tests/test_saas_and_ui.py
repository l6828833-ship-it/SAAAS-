"""Accounts, the bundle generator and the dashboard UI."""

from __future__ import annotations

import importlib

import pytest

from artisan_forge import products
from artisan_forge.products.bundle import BundleSpec, BundlePDF, normalise_plan, template_plan, validate
from artisan_forge.products.bundle import listing_from_plan


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    """Point the SQLite database at a temp folder and reload the modules."""
    monkeypatch.setenv("AF_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("AF_SIGNUP_CODE", raising=False)
    from artisan_forge.saas import auth, db

    importlib.reload(db)
    importlib.reload(auth)
    db.init_db()
    return db, auth


# ------------------------------------------------------------------ accounts
def test_password_hashing_roundtrip(fresh_db):
    _db, auth = fresh_db
    stored = auth.hash_password("correct horse battery")
    assert stored.startswith("scrypt$")
    assert "correct horse battery" not in stored
    assert auth.verify_password(stored, "correct horse battery")
    assert not auth.verify_password(stored, "wrong password")
    assert not auth.verify_password("garbage", "correct horse battery")


def test_signup_and_login(fresh_db):
    db, auth = fresh_db
    user = auth.signup("Owner@Example.com ", "supersecret1", "Owner")
    assert user["email"] == "owner@example.com"
    assert user["role"] == "admin"  # first account
    assert db.count_users() == 1

    second = auth.signup("two@example.com", "supersecret2")
    assert second["role"] == "member"

    logged_in = auth.login("OWNER@example.com", "supersecret1")
    assert logged_in["id"] == user["id"]

    with pytest.raises(auth.AuthError):
        auth.login("owner@example.com", "nope")
    with pytest.raises(auth.AuthError):
        auth.login("ghost@example.com", "supersecret1")


def test_signup_validation_and_invite_code(fresh_db, monkeypatch):
    _db, auth = fresh_db
    with pytest.raises(auth.AuthError):
        auth.signup("not-an-email", "supersecret1")
    with pytest.raises(auth.AuthError):
        auth.signup("short@example.com", "tiny")

    auth.signup("first@example.com", "supersecret1")
    monkeypatch.setenv("AF_SIGNUP_CODE", "let-me-in")
    assert auth.signup_requires_code() is True
    with pytest.raises(auth.AuthError):
        auth.signup("blocked@example.com", "supersecret1", invite_code="guess")
    ok = auth.signup("invited@example.com", "supersecret1", invite_code="let-me-in")
    assert ok["email"] == "invited@example.com"

    with pytest.raises(auth.AuthError):
        auth.signup("first@example.com", "supersecret1", invite_code="let-me-in")


def test_library_records_builds(fresh_db, tmp_path):
    db, auth = fresh_db
    user = auth.signup("owner@example.com", "supersecret1")
    db.record_build(user["id"], "calendar", "2026 Calendar", tmp_path, pages=14, images=10)
    db.record_build(user["id"], "bundle", "Self-Care Bundle", tmp_path, pages=12, images=8)

    builds = db.list_builds(user["id"])
    assert [b["product_type"] for b in builds] == ["bundle", "calendar"]
    assert db.list_builds(user["id"], product_type="calendar")[0]["pages"] == 14

    stats = db.user_stats(user["id"])
    assert stats == {
        "builds": 2,
        "pages": 26,
        "images": 18,
        "this_month": 2,
        "by_type": {"bundle": 1, "calendar": 1},
    }

    db.delete_build(user["id"], builds[0]["id"])
    assert len(db.list_builds(user["id"])) == 1


def test_change_password(fresh_db):
    _db, auth = fresh_db
    user = auth.signup("owner@example.com", "supersecret1")
    with pytest.raises(auth.AuthError):
        auth.change_password(user["id"], "wrong", "brandnewpass")
    auth.change_password(user["id"], "supersecret1", "brandnewpass")
    assert auth.login("owner@example.com", "brandnewpass")["id"] == user["id"]


# ------------------------------------------------------------------- catalog
def test_catalog_has_live_and_soon_products():
    keys = {p.key for p in products.catalog()}
    assert {"calendar", "bundle", "planner", "wall_art"} <= keys
    assert {p.key for p in products.live()} == {"calendar", "bundle"}
    assert all(p.eta for p in products.coming_soon())
    assert products.get("calendar").is_live
    assert products.get("nope") is None


# -------------------------------------------------------------------- bundle
def test_template_plan_is_renderable():
    spec = validate(BundleSpec(topic="gentle morning routines", theme="japandi"))
    plan = template_plan(spec)
    assert plan["title"]
    assert {s["kind"] for s in plan["sections"]} <= set(spec.modules)
    for section in plan["sections"]:
        if section["kind"] in ("prompts", "checklist", "affirmations"):
            assert section["items"]
    assert "gentle morning routines" in plan["intro"].lower()


def test_normalise_plan_survives_a_hostile_model_response():
    spec = validate(BundleSpec(topic="budgeting basics"))
    plan = normalise_plan(
        {
            "title": "  Budget Reset  ",
            "sections": [
                {"kind": "nonsense", "items": ["x"]},
                {"kind": "prompts", "items": []},
                {"kind": "checklist", "title": "Weekly money check", "items": ["Review spending"]},
                {"kind": "tracker", "title": "Spending"},
            ],
            "bullets": [""],
        },
        spec,
    )
    assert plan["title"] == "Budget Reset"
    kinds = [s["kind"] for s in plan["sections"]]
    assert kinds == ["checklist", "tracker"]
    assert plan["sections"][1]["columns"][0] == "Focus"
    assert plan["bullets"]  # fell back to templates
    assert plan["intro"]


def test_bundle_pdf_pages_and_listing(tmp_path):
    spec = validate(BundleSpec(topic="self-care for new mums", pages_per_module=1))
    plan = template_plan(spec)
    pdf_path, pages = BundlePDF(spec, plan).render(tmp_path / "bundle.pdf")
    assert pdf_path.exists() and pdf_path.stat().st_size > 4_000
    kinds = [page["kind"] for page in pages]
    assert kinds[0] == "cover" and kinds[1] == "intro" and kinds[-1] == "closing"
    assert "prompts" in kinds and "checklist" in kinds

    from artisan_forge.pdf.verify import extract_page_texts

    texts = extract_page_texts(pdf_path)
    assert len(texts) == len(pages)
    assert "WELCOME" in texts[1].upper()

    copy = listing_from_plan(spec, plan)
    assert len(copy["title"]) <= 140
    assert len(copy["tags"]) <= 13
    assert all(len(tag) <= 20 for tag in copy["tags"])
    assert "WHAT YOU GET" in copy["description"]


def test_bundle_spec_validation():
    with pytest.raises(ValueError):
        validate(BundleSpec(topic="a"))
    with pytest.raises(ValueError):
        validate(BundleSpec(topic="valid topic", paper="papyrus"))
    spec = validate(BundleSpec(topic="valid topic", modules=["nonsense"], pages_per_module=99))
    assert spec.modules  # falls back to defaults
    assert spec.pages_per_module == 6
    assert spec.product_slug().startswith("valid-topic-bundle")


# ------------------------------------------------------------------------ UI
@pytest.fixture()
def app_test(tmp_path, monkeypatch):
    pytest.importorskip("streamlit.testing.v1")
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("AF_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("AF_OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.delenv("AF_SIGNUP_CODE", raising=False)
    return AppTest.from_file("app.py", default_timeout=60)


def _sign_up(app):
    app.run()
    assert not app.exception
    # signup form is the second tab: name, email, password
    app.text_input[2].set_value("Sam")
    app.text_input[3].set_value("sam@example.com")
    app.text_input[4].set_value("supersecret1")
    app.button[1].click().run()
    return app


def test_app_shows_auth_screen_first(app_test):
    app_test.run()
    assert not app_test.exception
    body = " ".join(md.value for md in app_test.markdown)
    assert "Artisan Forge" in body
    assert app_test.button  # sign in / create account buttons exist


def test_signup_then_every_page_renders(app_test):
    app = _sign_up(app_test)
    assert not app.exception
    assert app.session_state["user"]["email"] == "sam@example.com"

    pages = [
        "Dashboard",
        "Calendar Studio",
        "Bundle Studio",
        "Planner Studio",
        "Wall Art Studio",
        "Journal Studio",
        "Social Kit Studio",
        "Library",
        "Account",
    ]
    for page in pages:
        app.session_state["nav"] = page
        app.run()
        assert not app.exception, f"{page} raised {app.exception}"

    app.session_state["nav"] = "Calendar Studio"
    app.run()
    assert any("Generate everything" in button.label for button in app.button)

    app.session_state["nav"] = "Bundle Studio"
    app.run()
    assert any("Generate bundle" in button.label for button in app.button)

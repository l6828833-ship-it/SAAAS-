"""SQLite storage for accounts and generated products.

Single file database, no ORM, no migrations framework. `init_db` is idempotent
and safe to call on every app start.
"""

from __future__ import annotations

import datetime as dt
import os
import sqlite3
from pathlib import Path

from ..config import ROOT

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    email         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    display_name  TEXT,
    role          TEXT NOT NULL DEFAULT 'member',
    plan          TEXT NOT NULL DEFAULT 'free',
    created_at    TEXT NOT NULL,
    last_login_at TEXT
);

CREATE TABLE IF NOT EXISTS builds (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL REFERENCES users(id),
    product_type  TEXT NOT NULL,
    title         TEXT NOT NULL,
    brief         TEXT,
    run_dir       TEXT NOT NULL,
    thumbnail     TEXT,
    pages         INTEGER DEFAULT 0,
    images        INTEGER DEFAULT 0,
    zip_path      TEXT,
    art_source    TEXT,
    created_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_builds_user ON builds(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS etsy_accounts (
    user_id       INTEGER PRIMARY KEY REFERENCES users(id),
    access_token  TEXT NOT NULL,
    refresh_token TEXT NOT NULL,
    expires_at    REAL NOT NULL DEFAULT 0,
    etsy_user_id  TEXT,
    shop_id       INTEGER,
    shop_name     TEXT,
    scopes        TEXT,
    connected_at  TEXT
);

CREATE TABLE IF NOT EXISTS etsy_listings (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL REFERENCES users(id),
    listing_id   INTEGER NOT NULL,
    shop_id      INTEGER,
    title        TEXT,
    state        TEXT NOT NULL DEFAULT 'draft',
    run_dir      TEXT,
    edit_url     TEXT,
    images       INTEGER DEFAULT 0,
    files        INTEGER DEFAULT 0,
    created_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_etsy_listings_user
    ON etsy_listings(user_id, created_at DESC);
"""


def data_dir() -> Path:
    raw = os.getenv("AF_DATA_DIR")
    path = Path(raw) if raw else ROOT / "data"
    if not path.is_absolute():
        path = ROOT / path
    path.mkdir(parents=True, exist_ok=True)
    return path


def db_path() -> Path:
    return data_dir() / "artisan_forge.db"


def connect() -> sqlite3.Connection:
    connection = sqlite3.connect(db_path(), timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def init_db() -> None:
    with connect() as connection:
        connection.executescript(SCHEMA)


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


# ----------------------------------------------------------------------- users
def count_users() -> int:
    with connect() as connection:
        return connection.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]


def insert_user(email: str, password_hash: str, display_name: str, role: str) -> dict:
    with connect() as connection:
        cursor = connection.execute(
            "INSERT INTO users (email, password_hash, display_name, role, plan, created_at) "
            "VALUES (?, ?, ?, ?, 'free', ?)",
            (email, password_hash, display_name, role, _now()),
        )
        return dict(
            connection.execute("SELECT * FROM users WHERE id = ?", (cursor.lastrowid,)).fetchone()
        )


def get_user_by_email(email: str) -> dict | None:
    with connect() as connection:
        row = connection.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        return dict(row) if row else None


def get_user(user_id: int) -> dict | None:
    with connect() as connection:
        row = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None


def touch_login(user_id: int) -> None:
    with connect() as connection:
        connection.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (_now(), user_id))


def set_password(user_id: int, password_hash: str) -> None:
    with connect() as connection:
        connection.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, user_id)
        )


def set_plan(user_id: int, plan: str) -> None:
    with connect() as connection:
        connection.execute("UPDATE users SET plan = ? WHERE id = ?", (plan, user_id))


# ---------------------------------------------------------------------- builds
def record_build(
    user_id: int,
    product_type: str,
    title: str,
    run_dir: str | Path,
    brief: str | None = None,
    thumbnail: str | Path | None = None,
    pages: int = 0,
    images: int = 0,
    zip_path: str | Path | None = None,
    art_source: str | None = None,
) -> int:
    with connect() as connection:
        cursor = connection.execute(
            "INSERT INTO builds (user_id, product_type, title, brief, run_dir, thumbnail, "
            "pages, images, zip_path, art_source, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                user_id,
                product_type,
                title,
                brief,
                str(run_dir),
                str(thumbnail) if thumbnail else None,
                pages,
                images,
                str(zip_path) if zip_path else None,
                art_source,
                _now(),
            ),
        )
        return int(cursor.lastrowid)


def list_builds(user_id: int, limit: int = 50, product_type: str | None = None) -> list[dict]:
    query = "SELECT * FROM builds WHERE user_id = ?"
    params: list = [user_id]
    if product_type:
        query += " AND product_type = ?"
        params.append(product_type)
    query += " ORDER BY datetime(created_at) DESC, id DESC LIMIT ?"
    params.append(limit)
    with connect() as connection:
        return [dict(row) for row in connection.execute(query, params).fetchall()]


def delete_build(user_id: int, build_id: int) -> None:
    with connect() as connection:
        connection.execute("DELETE FROM builds WHERE id = ? AND user_id = ?", (build_id, user_id))


def user_stats(user_id: int) -> dict:
    with connect() as connection:
        row = connection.execute(
            "SELECT COUNT(*) AS builds, COALESCE(SUM(pages), 0) AS pages, "
            "COALESCE(SUM(images), 0) AS images FROM builds WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        month = dt.datetime.now().strftime("%Y-%m")
        this_month = connection.execute(
            "SELECT COUNT(*) AS n FROM builds WHERE user_id = ? AND substr(created_at, 1, 7) = ?",
            (user_id, month),
        ).fetchone()["n"]
        by_type = {
            r["product_type"]: r["n"]
            for r in connection.execute(
                "SELECT product_type, COUNT(*) AS n FROM builds WHERE user_id = ? "
                "GROUP BY product_type",
                (user_id,),
            ).fetchall()
        }
    return {
        "builds": row["builds"],
        "pages": row["pages"],
        "images": row["images"],
        "this_month": this_month,
        "by_type": by_type,
    }


# ----------------------------------------------------------------- etsy oauth
def save_etsy_account(
    user_id: int,
    access_token: str,
    refresh_token: str,
    expires_at: float,
    etsy_user_id: str = "",
    shop_id: int | None = None,
    shop_name: str = "",
    scopes: str = "",
    connected_at: str | None = None,
) -> None:
    with connect() as connection:
        connection.execute(
            "INSERT INTO etsy_accounts (user_id, access_token, refresh_token, expires_at, "
            "etsy_user_id, shop_id, shop_name, scopes, connected_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET access_token=excluded.access_token, "
            "refresh_token=excluded.refresh_token, expires_at=excluded.expires_at, "
            "etsy_user_id=excluded.etsy_user_id, shop_id=excluded.shop_id, "
            "shop_name=excluded.shop_name, scopes=excluded.scopes, "
            "connected_at=excluded.connected_at",
            (
                user_id,
                access_token,
                refresh_token,
                expires_at,
                etsy_user_id,
                shop_id,
                shop_name,
                scopes,
                connected_at or _now(),
            ),
        )


def get_etsy_account(user_id: int) -> dict | None:
    with connect() as connection:
        row = connection.execute(
            "SELECT * FROM etsy_accounts WHERE user_id = ?", (user_id,)
        ).fetchone()
        return dict(row) if row else None


def delete_etsy_account(user_id: int) -> None:
    with connect() as connection:
        connection.execute("DELETE FROM etsy_accounts WHERE user_id = ?", (user_id,))


# --------------------------------------------------------------- etsy listings
def record_etsy_listing(
    user_id: int,
    listing_id: int,
    shop_id: int | None,
    title: str,
    run_dir: str | Path | None = None,
    edit_url: str = "",
    images: int = 0,
    files: int = 0,
    state: str = "draft",
) -> int:
    with connect() as connection:
        cursor = connection.execute(
            "INSERT INTO etsy_listings (user_id, listing_id, shop_id, title, state, run_dir, "
            "edit_url, images, files, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                user_id,
                listing_id,
                shop_id,
                title,
                state,
                str(run_dir) if run_dir else None,
                edit_url,
                images,
                files,
                _now(),
            ),
        )
        return int(cursor.lastrowid)


def list_etsy_listings(user_id: int, limit: int = 50) -> list[dict]:
    with connect() as connection:
        return [
            dict(row)
            for row in connection.execute(
                "SELECT * FROM etsy_listings WHERE user_id = ? "
                "ORDER BY datetime(created_at) DESC, id DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
        ]


def etsy_listings_for_run(user_id: int, run_dir: str | Path) -> list[dict]:
    with connect() as connection:
        return [
            dict(row)
            for row in connection.execute(
                "SELECT * FROM etsy_listings WHERE user_id = ? AND run_dir = ? "
                "ORDER BY id DESC",
                (user_id, str(run_dir)),
            ).fetchall()
        ]

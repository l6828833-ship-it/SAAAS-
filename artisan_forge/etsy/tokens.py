"""Encrypted storage for Etsy OAuth tokens.

An Etsy token grants write access to a live shop, so tokens are encrypted at
rest with Fernet (AES-CBC + HMAC). The key comes from `AF_SECRET_KEY`; if that
is unset a random key is generated once into the data directory with
owner-only permissions.

If `cryptography` is not installed we refuse to store tokens rather than
silently writing them in plain text.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import os
import stat
import time
from dataclasses import dataclass, field
from functools import lru_cache

from ..saas import db


class TokenStorageError(RuntimeError):
    pass


@dataclass
class TokenSet:
    access_token: str
    refresh_token: str
    expires_at: float = 0.0
    etsy_user_id: str = ""
    shop_id: int | None = None
    shop_name: str = ""
    scopes: str = ""
    connected_at: str = ""
    extra: dict = field(default_factory=dict)

    @classmethod
    def from_response(cls, data: dict, **overrides) -> TokenSet:
        from .oauth import user_id_from_token

        access = data["access_token"]
        token = cls(
            access_token=access,
            refresh_token=data.get("refresh_token", ""),
            expires_at=time.time() + float(data.get("expires_in", 3600)),
            etsy_user_id=user_id_from_token(access),
            scopes=data.get("scope", ""),
            connected_at=dt.datetime.now().isoformat(timespec="seconds"),
        )
        for key, value in overrides.items():
            setattr(token, key, value)
        return token

    @property
    def expired(self) -> bool:
        # refresh a minute early to avoid racing the expiry
        return time.time() >= (self.expires_at - 60)

    def masked(self) -> str:
        return f"{self.access_token[:10]}\u2026" if self.access_token else ""


# --------------------------------------------------------------------- crypto
def _key_file_key() -> bytes:
    path = db.data_dir() / "secret.key"
    if path.exists():
        return path.read_bytes()
    key = base64.urlsafe_b64encode(os.urandom(32))
    path.write_bytes(key)
    try:  # best effort on Windows
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    return key


@lru_cache(maxsize=1)
def _fernet():
    try:
        from cryptography.fernet import Fernet
    except ImportError as exc:  # pragma: no cover - dependency missing
        raise TokenStorageError(
            "The 'cryptography' package is required to store Etsy tokens securely "
            "(pip install cryptography)."
        ) from exc

    secret = os.getenv("AF_SECRET_KEY", "").strip()
    if secret:
        key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    else:
        key = _key_file_key()
    return Fernet(key)


def encrypt(value: str) -> str:
    if not value:
        return ""
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt(value: str) -> str:
    if not value:
        return ""
    return _fernet().decrypt(value.encode("ascii")).decode("utf-8")


def crypto_available() -> bool:
    try:
        _fernet()
        return True
    except TokenStorageError:
        return False


# ---------------------------------------------------------------------- store
def save(user_id: int, tokens: TokenSet) -> None:
    db.save_etsy_account(
        user_id=user_id,
        access_token=encrypt(tokens.access_token),
        refresh_token=encrypt(tokens.refresh_token),
        expires_at=tokens.expires_at,
        etsy_user_id=tokens.etsy_user_id,
        shop_id=tokens.shop_id,
        shop_name=tokens.shop_name,
        scopes=tokens.scopes,
        connected_at=tokens.connected_at or dt.datetime.now().isoformat(timespec="seconds"),
    )


def load(user_id: int) -> TokenSet | None:
    row = db.get_etsy_account(user_id)
    if not row:
        return None
    try:
        access = decrypt(row["access_token"])
        refresh = decrypt(row["refresh_token"])
    except Exception as exc:  # noqa: BLE001 - wrong key, corrupt value
        raise TokenStorageError(
            "Stored Etsy tokens could not be decrypted. If AF_SECRET_KEY changed, "
            "disconnect and reconnect the shop."
        ) from exc
    return TokenSet(
        access_token=access,
        refresh_token=refresh,
        expires_at=float(row["expires_at"] or 0),
        etsy_user_id=row["etsy_user_id"] or "",
        shop_id=row["shop_id"],
        shop_name=row["shop_name"] or "",
        scopes=row["scopes"] or "",
        connected_at=row["connected_at"] or "",
    )


def delete(user_id: int) -> None:
    db.delete_etsy_account(user_id)

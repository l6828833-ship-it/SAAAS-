"""Deployment settings entered in the app instead of in a `.env` file.

Editing `.env` means shell access and a restart, and on Railway it means a
redeploy. This module lets the owner paste the same values into the Account page
and have them take effect on the next page load.

How the two sources interact:

    `.env` / Railway variables are loaded into `os.environ` at import time.
    `apply_saved()` then overwrites those with anything stored in the database,
    so a value entered in the app wins. Clearing a field deletes the stored
    value, which falls back to whatever the environment says.

Secrets are encrypted at rest with the same Fernet key used for the Etsy OAuth
tokens (`AF_SECRET_KEY`, or a generated key file in the data directory). They
are never sent back to the browser - the panel shows a masked fingerprint and
nothing else.

The values are deployment-wide, not per user: an API key is billed to whoever
owns the gateway account. Only the `admin` role (the first account created) may
read or write them.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from . import db


@dataclass(frozen=True)
class Field:
    """One editable environment variable."""

    key: str
    label: str
    group: str
    secret: bool = False
    help: str = ""
    placeholder: str = ""
    choices: tuple[str, ...] = ()


# The variables worth exposing. Anything to do with where files live
# (AF_DATA_DIR, AF_OUTPUT_DIR) is deliberately left out: changing a storage path
# from inside the running app would orphan the database it is reading from.
FIELDS: tuple[Field, ...] = (
    # ---- AI gateway ----
    Field("AF_AI_PROVIDER", "Provider", "AI gateway", choices=("inworld", "openai"),
          help="inworld reaches Qwen and Gemini through one endpoint. openai is the "
               "old gpt-image wiring."),
    Field("INWORLD_API_KEY", "Inworld API key", "AI gateway", secret=True,
          help="portal.inworld.ai -> API Keys. Paste the base64 credential as-is.",
          placeholder="base64 credential"),
    Field("OPENAI_API_KEY", "OpenAI API key", "AI gateway", secret=True,
          help="Only used when the provider is openai.", placeholder="sk-..."),
    Field("AF_AI_BASE_URL", "Base URL override", "AI gateway",
          help="Leave empty unless you are pointing at another OpenAI-compatible gateway.",
          placeholder="https://api.inworld.ai/v1"),
    Field("AF_OFFLINE", "Force offline mode", "AI gateway", choices=("", "0", "1"),
          help="1 paints artwork procedurally and writes from templates, even with a key set."),

    # ---- models ----
    Field("AF_TEXT_MODEL", "Writer model", "Models",
          help="Empty uses the provider default.",
          placeholder="deepinfra/Qwen/Qwen3-VL-30B-A3B-Instruct"),
    Field("AF_VISION_MODEL", "Vision model", "Models",
          help="Reads uploaded photos and the rendered plates.",
          placeholder="deepinfra/Qwen/Qwen3-VL-30B-A3B-Instruct"),
    Field("AF_IMAGE_MODEL", "Image model", "Models",
          placeholder="google-ai-studio/gemini-2.5-flash-image"),
    Field("AF_IMAGE_CACHE", "Cache generated art", "Models", choices=("", "1", "0"),
          help="1 reuses artwork for an identical prompt, which makes a rebuild free."),

    # ---- Etsy ----
    Field("ETSY_KEYSTRING", "Keystring", "Etsy", secret=True,
          help="etsy.com/developers -> Your Apps. This is the OAuth client_id, about "
               "24 lowercase letters and digits.",
          placeholder="24 lowercase letters and digits"),
    Field("ETSY_REDIRECT_URI", "Redirect URI", "Etsy",
          help="Must match a callback registered on the Etsy app exactly: same scheme, "
               "same port, no trailing slash.",
          placeholder="http://localhost:8501"),
    Field("ETSY_SHARED_SECRET", "Shared secret", "Etsy", secret=True,
          help="Not used by the PKCE flow. Only needed alongside the header override below."),
    Field("AF_ETSY_API_KEY_HEADER", "x-api-key override", "Etsy",
          help="Only if your app needs \"keystring:shared_secret\" in the header."),

    # ---- Canva ----
    Field("CANVA_CLIENT_ID", "Client ID", "Canva"),
    Field("CANVA_CLIENT_SECRET", "Client secret", "Canva", secret=True),

    # ---- accounts ----
    Field("AF_SIGNUP_CODE", "Signup invite code", "Accounts", secret=True,
          help="Set this once your own account exists to close open signups."),
    Field("AF_FREE_BUILD_LIMIT", "Free plan build limit", "Accounts",
          help="Builds per month on the free plan. 0 or empty means unlimited.",
          placeholder="0"),
)

BY_KEY: dict[str, Field] = {field.key: field for field in FIELDS}
GROUPS: tuple[str, ...] = tuple(dict.fromkeys(field.group for field in FIELDS))

# Values from the environment that were present before anything was applied.
# Kept so the panel can say where a value came from, and so clearing a saved
# value can put the original back rather than leaving a hole.
_ENV_BASELINE: dict[str, str] = {}


class SettingsError(RuntimeError):
    pass


def _crypto():
    """The Fernet helpers, imported late to keep the import graph shallow."""
    from ..etsy import tokens

    return tokens


def crypto_available() -> bool:
    """Whether secrets can be encrypted. False means `cryptography` is missing."""
    return _crypto().crypto_available()


def mask(value: str) -> str:
    """A fingerprint that confirms which key is stored without revealing it."""
    if not value:
        return ""
    if len(value) <= 8:
        return "\u2022" * len(value)
    return f"{value[:4]}\u2026{value[-4:]}  ({len(value)} chars)"


# ------------------------------------------------------------------ read/write
def stored() -> dict[str, str]:
    """Every saved setting, decrypted. Unreadable values come back empty.

    A value that cannot be decrypted means `AF_SECRET_KEY` changed since it was
    saved. That is recoverable by re-entering it, so it is reported as missing
    rather than raised - one stale key should not take the whole page down.
    """
    crypto = _crypto()
    values: dict[str, str] = {}
    for key, row in db.get_app_settings().items():
        raw = row["value"] or ""
        if not raw:
            continue
        if row["secret"]:
            try:
                raw = crypto.decrypt(raw)
            except Exception:  # noqa: BLE001 - wrong key, corrupt value
                continue
        values[key] = raw
    return values


def undecryptable() -> list[str]:
    """Keys that are stored but cannot be read back with the current secret."""
    crypto = _crypto()
    broken: list[str] = []
    for key, row in db.get_app_settings().items():
        if not row["secret"] or not row["value"]:
            continue
        try:
            crypto.decrypt(row["value"])
        except Exception:  # noqa: BLE001
            broken.append(key)
    return broken


def save(key: str, value: str, user_id: int | None = None) -> None:
    """Store one setting, or delete it when `value` is blank.

    Deleting rather than storing an empty string is deliberate: it hands the
    variable back to the environment instead of masking it with a blank.
    """
    if key not in BY_KEY:
        raise SettingsError(f"{key} is not an editable setting")
    field = BY_KEY[key]
    value = (value or "").strip()

    if not value:
        db.delete_app_setting(key)
        _restore_env(key)
        return

    if field.secret:
        if not crypto_available():
            raise SettingsError(
                "The 'cryptography' package is required before secrets can be stored "
                "(pip install cryptography)."
            )
        db.save_app_setting(key, _crypto().encrypt(value), True, user_id)
    else:
        db.save_app_setting(key, value, False, user_id)
    os.environ[key] = value


def save_many(values: dict[str, str], user_id: int | None = None) -> list[str]:
    """Save a form's worth of settings. Returns the keys that changed."""
    current = stored()
    changed: list[str] = []
    for key, value in values.items():
        value = (value or "").strip()
        if value == current.get(key, ""):
            continue
        save(key, value, user_id)
        changed.append(key)
    return changed


# -------------------------------------------------------------------- env glue
def _restore_env(key: str) -> None:
    """Put the pre-existing environment value back after a saved one is cleared."""
    baseline = _ENV_BASELINE.get(key, "")
    if baseline:
        os.environ[key] = baseline
    else:
        os.environ.pop(key, None)


def apply_saved() -> dict[str, str]:
    """Push every stored setting into `os.environ`. Safe to call per page load.

    `Settings` is rebuilt from `os.environ` on every `get_settings()` call, so a
    value applied here is live immediately - no restart.
    """
    if not _ENV_BASELINE:
        _ENV_BASELINE.update({field.key: os.getenv(field.key, "") for field in FIELDS})
    values = stored()
    for key, value in values.items():
        if value:
            os.environ[key] = value
    return values


def source_of(key: str, saved: dict[str, str] | None = None) -> str:
    """Where the value in force came from: "app", "environment" or "unset"."""
    saved = stored() if saved is None else saved
    if saved.get(key):
        return "app"
    if _ENV_BASELINE.get(key) or os.getenv(key, "").strip():
        return "environment"
    return "unset"


def effective(key: str, saved: dict[str, str] | None = None) -> str:
    """The value the app is actually using for `key`."""
    saved = stored() if saved is None else saved
    return saved.get(key) or os.getenv(key, "").strip()

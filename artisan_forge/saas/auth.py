"""Accounts: signup, login, password hashing.

Passwords are hashed with scrypt from the standard library (per-user random
salt) and compared in constant time. No password is ever logged or stored in
plain text.

Signup can be gated with `AF_SIGNUP_CODE` so a public deployment does not
accept open registrations.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets

from . import db

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[a-zA-Z]{2,}$")
MIN_PASSWORD = 8

# scrypt work factors: ~64 MB, comfortable for an interactive login
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1

PLAN_LIMITS = {"free": int(os.getenv("AF_FREE_BUILD_LIMIT", "0")), "pro": 0}


class AuthError(RuntimeError):
    """Raised for any signup/login problem, with a user-safe message."""


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32
    )
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(stored: str, password: str) -> bool:
    try:
        scheme, n, r, p, salt_hex, digest_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        candidate = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(bytes.fromhex(digest_hex)),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(candidate.hex(), digest_hex)


def normalise_email(email: str) -> str:
    return (email or "").strip().lower()


def signup(email: str, password: str, display_name: str = "", invite_code: str = "") -> dict:
    """Create an account. The first account becomes the admin."""
    db.init_db()
    email = normalise_email(email)
    if not EMAIL_RE.match(email):
        raise AuthError("Enter a valid email address")
    if len(password or "") < MIN_PASSWORD:
        raise AuthError(f"Password must be at least {MIN_PASSWORD} characters")

    required_code = os.getenv("AF_SIGNUP_CODE", "").strip()
    first_user = db.count_users() == 0
    if required_code and not first_user:
        if not hmac.compare_digest((invite_code or "").strip(), required_code):
            raise AuthError("That invite code is not valid")

    if db.get_user_by_email(email):
        raise AuthError("An account with that email already exists")

    role = "admin" if first_user else "member"
    user = db.insert_user(email, hash_password(password), display_name.strip() or email.split("@")[0], role)
    db.touch_login(user["id"])
    return user


def login(email: str, password: str) -> dict:
    db.init_db()
    email = normalise_email(email)
    user = db.get_user_by_email(email)
    # Same message either way so the form does not reveal which emails exist.
    if not user or not verify_password(user["password_hash"], password or ""):
        raise AuthError("Email or password is incorrect")
    db.touch_login(user["id"])
    return user


def change_password(user_id: int, current: str, new: str) -> None:
    user = db.get_user(user_id)
    if not user or not verify_password(user["password_hash"], current or ""):
        raise AuthError("Current password is incorrect")
    if len(new or "") < MIN_PASSWORD:
        raise AuthError(f"New password must be at least {MIN_PASSWORD} characters")
    db.set_password(user_id, hash_password(new))


def signup_requires_code() -> bool:
    return bool(os.getenv("AF_SIGNUP_CODE", "").strip()) and db.count_users() > 0


def current_plan(user: dict) -> dict:
    """Plan name plus this month's usage and any limit (0 = unlimited)."""
    plan = (user.get("plan") or "free").lower()
    stats = db.user_stats(user["id"])
    limit = PLAN_LIMITS.get(plan, 0)
    return {
        "plan": plan,
        "limit": limit,
        "used_this_month": stats["this_month"],
        "remaining": None if limit == 0 else max(0, limit - stats["this_month"]),
        "stats": stats,
    }


def can_build(user: dict) -> tuple[bool, str]:
    plan = current_plan(user)
    if plan["limit"] and plan["used_this_month"] >= plan["limit"]:
        return False, (
            f"You have used all {plan['limit']} builds on the {plan['plan']} plan this month."
        )
    return True, ""

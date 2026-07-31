"""SaaS layer: accounts and the build library (SQLite, stdlib only)."""

from .auth import AuthError, current_plan, login, signup  # noqa: F401
from .db import init_db, list_builds, record_build, user_stats  # noqa: F401

__all__ = [
    "AuthError",
    "login",
    "signup",
    "current_plan",
    "init_db",
    "record_build",
    "list_builds",
    "user_stats",
]

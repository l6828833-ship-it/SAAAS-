"""API keys and deployment settings, editable in the app.

The alternative is editing `.env` and restarting, or setting Railway variables
and waiting for a redeploy. This panel writes the same variables to the database
and applies them to the process immediately.

Two rules shape the UI:

  Secrets are write-only. A stored key is shown as a masked fingerprint so you
  can tell which one is loaded, and the input is always empty - there is no
  reason to send a live credential back to a browser.

  Clearing a field deletes the stored value rather than saving a blank, which
  hands the variable back to the environment. The source of every value is
  labelled so it is obvious which of the two is winning.
"""

from __future__ import annotations

import streamlit as st

from artisan_forge.config import get_settings
from artisan_forge.saas import settings_store as store

from . import theme

SOURCE_LABELS = {
    "app": ("saved here", "#6EE7B7"),
    "environment": ("from .env or Railway", "#C4B5FD"),
    "unset": ("not set", "#9A9AAE"),
}


def is_owner(user: dict) -> bool:
    """Only the admin account may see or change deployment credentials.

    The first account created is the admin (see `auth.signup`). Everyone else is
    a member: these keys are billed to whoever owns the gateway account, so a
    member being able to read them would be a privilege escalation.
    """
    return (user.get("role") or "member").lower() == "admin"


def _status_line(field: store.Field, saved: dict[str, str]) -> None:
    source = store.source_of(field.key, saved)
    label, colour = SOURCE_LABELS[source]
    detail = ""
    if source != "unset":
        value = store.effective(field.key, saved)
        detail = store.mask(value) if field.secret else theme.esc(value)
    st.markdown(
        f"<div style='margin:-.55rem 0 .5rem;font-size:.78rem;color:{colour}'>"
        f"{theme.esc(field.key)} \u00b7 {label}"
        + (f" \u00b7 {detail}" if detail else "")
        + "</div>",
        unsafe_allow_html=True,
    )


def _input(field: store.Field, saved: dict[str, str]) -> str:
    """One editable field. Returns the submitted value."""
    if field.choices:
        current = store.effective(field.key, saved)
        options = list(field.choices)
        if current and current not in options:
            options.insert(0, current)
        return st.selectbox(
            field.label, options,
            index=options.index(current) if current in options else 0,
            key=f"set_{field.key}", help=field.help or None,
        )
    if field.secret:
        # Never pre-fill a secret. Empty means "leave it alone"; the caller
        # skips blanks so an untouched form does not wipe stored keys.
        return st.text_input(
            field.label, value="", type="password",
            placeholder="unchanged" if saved.get(field.key) else (field.placeholder or ""),
            key=f"set_{field.key}", help=field.help or None,
        )
    return st.text_input(
        field.label, value=store.effective(field.key, saved),
        placeholder=field.placeholder or "",
        key=f"set_{field.key}", help=field.help or None,
    )


def render(user: dict) -> None:
    theme.section("API keys and settings", "saved in the database, applied immediately")

    if not is_owner(user):
        theme.note(
            "Only the owner account can view or change deployment credentials. Ask "
            "whoever set this deployment up.",
            "info",
        )
        return

    if not store.crypto_available():
        theme.note(
            "The 'cryptography' package is missing, so secrets cannot be stored safely. "
            "Install it (pip install cryptography) before entering API keys.",
            "warn",
        )
        return

    saved = store.stored()

    broken = store.undecryptable()
    if broken:
        theme.note(
            "These saved secrets cannot be decrypted, which means AF_SECRET_KEY changed "
            f"since they were entered: {', '.join(broken)}. Re-enter them below.",
            "warn",
        )

    with st.form("app_settings_form"):
        submitted_values: dict[str, str] = {}
        for group in store.GROUPS:
            st.markdown(f"**{theme.esc(group)}**")
            fields = [f for f in store.FIELDS if f.group == group]
            for row_start in range(0, len(fields), 2):
                for column, field in zip(st.columns(2), fields[row_start : row_start + 2]):
                    with column:
                        submitted_values[field.key] = _input(field, saved)
                        _status_line(field, saved)
            st.write("")

        st.caption(
            "A secret left blank keeps whatever is already stored. To remove one, tick "
            "the box below and save."
        )
        wipe = st.multiselect(
            "Delete stored values",
            [f.key for f in store.FIELDS if saved.get(f.key)],
            key="set_wipe",
            help="Deletes the value saved here so the .env or Railway variable takes over again.",
        )
        submit = st.form_submit_button("Save settings", type="primary")

    if not submit:
        _summary()
        return

    # A blank secret input means "unchanged", so drop it before saving. Plain
    # text fields are submitted as-is, because an emptied text box is a genuine
    # instruction to clear that value.
    to_save = {
        key: value
        for key, value in submitted_values.items()
        if not (store.BY_KEY[key].secret and not (value or "").strip())
    }
    for key in wipe:
        to_save[key] = ""

    try:
        changed = store.save_many(to_save, user["id"])
    except store.SettingsError as exc:
        st.error(str(exc))
        return

    if not changed:
        st.info("Nothing changed.")
        return
    st.success(f"Saved: {', '.join(changed)}. The change is already live.")
    st.rerun()


def _summary() -> None:
    """What the app is using right now, after everything is applied."""
    settings = get_settings()
    bits = [
        f"provider **{settings.provider_label}**",
        "AI **on**" if settings.ai_available else "AI **off** (offline mode)",
        "Etsy **configured**" if settings.etsy_configured else "Etsy **not configured**",
    ]
    st.caption(" \u00b7 ".join(bits))

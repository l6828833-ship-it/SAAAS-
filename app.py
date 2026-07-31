"""Artisan Forge - SaaS dashboard.

    streamlit run app.py
"""

from __future__ import annotations

import streamlit as st

from artisan_forge import __version__
from artisan_forge.config import get_settings
from artisan_forge.saas import db
from ui import account, auth_view, bundle_studio, calendar_studio, dashboard, library, soon, theme

st.set_page_config(
    page_title="Artisan Forge",
    page_icon="\u2692\ufe0f",
    layout="wide",
    initial_sidebar_state="expanded",
)
theme.inject()
db.init_db()

user = auth_view.gate()
if not user:
    st.stop()

WORKSPACE = [
    ("Dashboard", "\U0001f3e0"),
    ("Calendar Studio", "\U0001f4c5"),
    ("Bundle Studio", "\u2728"),
]
SOON = [
    ("Planner Studio", "\U0001f5d3\ufe0f", "planner"),
    ("Wall Art Studio", "\U0001f5bc\ufe0f", "wall_art"),
    ("Journal Studio", "\U0001f4d3", "journal"),
    ("Social Kit Studio", "\U0001f4f1", "social"),
]
ACCOUNT = [("Library", "\U0001f4da"), ("Account", "\u2699\ufe0f")]
ALL_PAGES = [name for name, _ in WORKSPACE] + [name for name, _, _ in SOON] + [
    name for name, _ in ACCOUNT
]

settings = get_settings()

theme.brand()
theme.user_chip(
    user["display_name"],
    f"{(user.get('plan') or 'free').title()} plan \u00b7 {user['email']}",
)

if st.session_state.get("nav") not in ALL_PAGES:
    st.session_state["nav"] = "Dashboard"


def nav_button(label: str, icon: str) -> None:
    active = st.session_state["nav"] == label
    if st.sidebar.button(
        f"{icon}\u2003{label}",
        key=f"nav_{label}",
        use_container_width=True,
        type="primary" if active else "secondary",
    ):
        st.session_state["nav"] = label
        st.rerun()


theme.nav_label("Workspace")
for label, icon in WORKSPACE:
    nav_button(label, icon)

theme.nav_label("Coming soon")
for label, icon, _key in SOON:
    nav_button(label, icon)

theme.nav_label("Account")
for label, icon in ACCOUNT:
    nav_button(label, icon)

st.sidebar.markdown("<div style='height:.6rem'></div>", unsafe_allow_html=True)
if st.sidebar.button("\u21a9\ufe0e\u2003Sign out", key="nav_signout", use_container_width=True):
    auth_view.logout()
    st.rerun()
st.sidebar.caption(
    f"v{__version__} \u00b7 " + ("AI engine on" if settings.ai_available else "offline mode")
)

page = st.session_state["nav"]
soon_keys = {label: key for label, _icon, key in SOON}

if page == "Dashboard":
    dashboard.render(user)
elif page == "Calendar Studio":
    calendar_studio.render(user)
elif page == "Bundle Studio":
    bundle_studio.render(user)
elif page in soon_keys:
    soon.render(soon_keys[page])
elif page == "Library":
    library.render(user)
elif page == "Account":
    account.render(user)

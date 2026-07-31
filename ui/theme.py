"""Dark dashboard styling and reusable UI pieces."""

from __future__ import annotations

import html

import streamlit as st

INK = "#ECECF1"
MUTED = "#9A9AAE"
PRIMARY = "#7C5CFF"
CYAN = "#22D3EE"
SURFACE = "#12121A"
BORDER = "rgba(255,255,255,.08)"

CSS = f"""
<style>
:root {{
  --af-ink: {INK};
  --af-muted: {MUTED};
  --af-primary: {PRIMARY};
  --af-cyan: {CYAN};
  --af-surface: {SURFACE};
  --af-border: {BORDER};
}}

.stApp {{
  background:
    radial-gradient(1100px 520px at 12% -8%, rgba(124,92,255,.18), transparent 60%),
    radial-gradient(900px 480px at 92% 4%, rgba(34,211,238,.10), transparent 60%),
    #08080C;
}}

/* tighten the default page chrome */
.block-container {{ padding-top: 2.1rem; padding-bottom: 4rem; max-width: 1240px; }}
#MainMenu, footer, header [data-testid="stStatusWidget"] {{ visibility: hidden; }}

/* ---------------- sidebar ---------------- */
section[data-testid="stSidebar"] {{
  background: linear-gradient(180deg, #0C0C13 0%, #0A0A10 100%);
  border-right: 1px solid var(--af-border);
}}
section[data-testid="stSidebar"] .block-container {{ padding-top: 1.4rem; }}

.af-brand {{ display:flex; align-items:center; gap:.65rem; margin:.1rem 0 .2rem; }}
.af-brand-mark {{
  width:34px; height:34px; border-radius:10px; flex:0 0 34px;
  background: linear-gradient(135deg, var(--af-primary), var(--af-cyan));
  display:flex; align-items:center; justify-content:center; font-size:18px;
  box-shadow: 0 6px 18px rgba(124,92,255,.35);
}}
.af-brand-name {{ font-size:1.05rem; font-weight:700; letter-spacing:.2px; color:var(--af-ink); }}
.af-brand-sub {{ font-size:.7rem; color:var(--af-muted); letter-spacing:.14em; text-transform:uppercase; }}

.af-navlabel {{
  font-size:.68rem; letter-spacing:.16em; text-transform:uppercase;
  color:#6E6E85; margin:1.1rem 0 .35rem .15rem; font-weight:700;
}}

/* radio group -> nav list */
section[data-testid="stSidebar"] div[role="radiogroup"] {{ gap:.18rem; }}
section[data-testid="stSidebar"] div[role="radiogroup"] label {{
  width:100%; padding:.52rem .7rem; border-radius:10px; cursor:pointer;
  border:1px solid transparent; transition:all .14s ease; color:#C6C6D6;
}}
section[data-testid="stSidebar"] div[role="radiogroup"] label:hover {{
  background:rgba(255,255,255,.05); color:var(--af-ink);
}}
section[data-testid="stSidebar"] div[role="radiogroup"] label:has(input:checked) {{
  background:linear-gradient(90deg, rgba(124,92,255,.22), rgba(124,92,255,.05));
  border-color:rgba(124,92,255,.45); color:#fff;
}}
section[data-testid="stSidebar"] div[role="radiogroup"] label > div:first-child {{ display:none; }}
section[data-testid="stSidebar"] div[role="radiogroup"] label p {{
  font-size:.92rem; font-weight:500; margin:0;
}}

/* sidebar buttons act as nav items */
section[data-testid="stSidebar"] .stButton > button {{
  justify-content:flex-start; text-align:left; font-weight:500;
  background:transparent; border:1px solid transparent; color:#C6C6D6;
  padding:.5rem .7rem;
}}
section[data-testid="stSidebar"] .stButton > button:hover {{
  background:rgba(255,255,255,.05); border-color:transparent; color:var(--af-ink);
}}
section[data-testid="stSidebar"] .stButton > button[kind="primary"] {{
  background:linear-gradient(90deg, rgba(124,92,255,.30), rgba(124,92,255,.06));
  border:1px solid rgba(124,92,255,.45); color:#fff; font-weight:650;
  box-shadow:none; font-size:.92rem;
}}

.af-user {{
  display:flex; align-items:center; gap:.6rem; padding:.6rem .7rem; margin-top:.4rem;
  background:rgba(255,255,255,.035); border:1px solid var(--af-border); border-radius:12px;
}}
.af-avatar {{
  width:30px; height:30px; border-radius:9px; flex:0 0 30px; color:#fff;
  background:linear-gradient(135deg,#7C5CFF,#22D3EE);
  display:flex; align-items:center; justify-content:center; font-weight:700; font-size:.82rem;
}}
.af-user-name {{ font-size:.84rem; font-weight:600; color:var(--af-ink); line-height:1.15; }}
.af-user-meta {{ font-size:.7rem; color:var(--af-muted); }}

/* ---------------- headings ---------------- */
.af-kicker {{
  font-size:.7rem; letter-spacing:.18em; text-transform:uppercase;
  color:var(--af-primary); font-weight:700; margin-bottom:.5rem;
}}
.af-h1 {{
  font-size:2.15rem; font-weight:750; line-height:1.12; margin:0 0 .5rem;
  color:#fff; letter-spacing:-.02em;
}}
.af-h1 span {{
  background:linear-gradient(100deg,#A78BFA,#22D3EE);
  -webkit-background-clip:text; background-clip:text; color:transparent;
}}
.af-sub {{ color:var(--af-muted); font-size:1rem; max-width:62ch; margin:0; }}
.af-section {{
  display:flex; align-items:baseline; gap:.7rem; margin:2.1rem 0 .9rem;
}}
.af-section h3 {{ font-size:1.06rem; font-weight:700; color:var(--af-ink); margin:0; }}
.af-section span {{ font-size:.84rem; color:var(--af-muted); }}

/* ---------------- cards ---------------- */
.af-card {{
  background:linear-gradient(180deg, rgba(255,255,255,.045), rgba(255,255,255,.015));
  border:1px solid var(--af-border); border-radius:16px; padding:1.05rem 1.15rem;
  height:100%;
}}
.af-card.af-live:hover {{ border-color:rgba(124,92,255,.45); }}
.af-card-top {{ display:flex; align-items:center; gap:.6rem; margin-bottom:.55rem; }}
.af-card-icon {{
  width:36px; height:36px; border-radius:11px; flex:0 0 36px; font-size:18px;
  display:flex; align-items:center; justify-content:center;
  background:rgba(124,92,255,.14); border:1px solid rgba(124,92,255,.28);
}}
.af-card h4 {{ margin:0; font-size:1rem; font-weight:680; color:var(--af-ink); }}
.af-card p {{ margin:.2rem 0 0; font-size:.86rem; color:var(--af-muted); line-height:1.45; }}
.af-card ul {{ margin:.7rem 0 0; padding-left:1.05rem; }}
.af-card li {{ font-size:.82rem; color:#B9B9C9; margin:.16rem 0; }}
.af-card.af-soon {{ opacity:.72; }}

.af-pill {{
  display:inline-block; padding:.16rem .55rem; border-radius:999px;
  font-size:.66rem; font-weight:700; letter-spacing:.1em; text-transform:uppercase;
}}
.af-pill-live {{ background:rgba(52,211,153,.14); color:#34D399; border:1px solid rgba(52,211,153,.3); }}
.af-pill-soon {{ background:rgba(148,163,184,.12); color:#94A3B8; border:1px solid rgba(148,163,184,.28); }}
.af-pill-beta {{ background:rgba(124,92,255,.16); color:#A78BFA; border:1px solid rgba(124,92,255,.35); }}

/* stat tiles */
.af-stat {{
  background:linear-gradient(180deg, rgba(255,255,255,.05), rgba(255,255,255,.015));
  border:1px solid var(--af-border); border-radius:14px; padding:.85rem 1rem;
}}
.af-stat .v {{ font-size:1.55rem; font-weight:730; color:#fff; line-height:1.1; }}
.af-stat .l {{ font-size:.72rem; letter-spacing:.12em; text-transform:uppercase; color:var(--af-muted); }}
.af-stat .h {{ font-size:.74rem; color:#7C7C93; margin-top:.15rem; }}

/* ---------------- inputs & buttons ---------------- */
.stTextInput input, .stTextArea textarea, .stNumberInput input {{
  background:rgba(255,255,255,.04) !important;
  border:1px solid var(--af-border) !important; border-radius:11px !important;
  color:var(--af-ink) !important;
}}
.stTextInput input:focus, .stTextArea textarea:focus {{
  border-color:rgba(124,92,255,.6) !important; box-shadow:0 0 0 3px rgba(124,92,255,.14) !important;
}}
div[data-baseweb="select"] > div {{
  background:rgba(255,255,255,.04) !important; border-color:var(--af-border) !important;
  border-radius:11px !important;
}}
.stButton > button, .stDownloadButton > button {{
  border-radius:11px; border:1px solid var(--af-border);
  background:rgba(255,255,255,.05); color:var(--af-ink); font-weight:600;
  transition:all .15s ease;
}}
.stButton > button:hover, .stDownloadButton > button:hover {{
  border-color:rgba(124,92,255,.5); background:rgba(124,92,255,.12); color:#fff;
}}
.stButton > button[kind="primary"] {{
  background:linear-gradient(100deg,#7C5CFF,#5B8CFF); border:0; color:#fff;
  padding:.72rem 1rem; font-size:1rem; font-weight:700;
  box-shadow:0 10px 26px rgba(124,92,255,.32);
}}
.stButton > button[kind="primary"]:hover {{
  filter:brightness(1.08); box-shadow:0 12px 30px rgba(124,92,255,.42);
}}
div[data-testid="stExpander"] {{
  border:1px solid var(--af-border) !important; border-radius:13px !important;
  background:rgba(255,255,255,.022) !important;
}}
button[data-baseweb="tab"] {{ font-weight:600; }}
div[data-testid="stImage"] img {{ border-radius:12px; border:1px solid var(--af-border); }}
hr {{ border-color:var(--af-border) !important; }}
</style>
"""


def inject() -> None:
    st.markdown(CSS, unsafe_allow_html=True)


def esc(text: object) -> str:
    return html.escape(str(text))


def hero(title: str, highlight: str = "", subtitle: str = "", kicker: str = "") -> None:
    head = f"{esc(title)} <span>{esc(highlight)}</span>" if highlight else esc(title)
    st.markdown(
        f"""<div>
        {f'<div class="af-kicker">{esc(kicker)}</div>' if kicker else ''}
        <div class="af-h1">{head}</div>
        {f'<p class="af-sub">{esc(subtitle)}</p>' if subtitle else ''}
        </div>""",
        unsafe_allow_html=True,
    )


def section(title: str, hint: str = "") -> None:
    st.markdown(
        f'<div class="af-section"><h3>{esc(title)}</h3>'
        f'{f"<span>{esc(hint)}</span>" if hint else ""}</div>',
        unsafe_allow_html=True,
    )


def stat(label: str, value: object, hint: str = "") -> None:
    hint_html = f'<div class="h">{esc(hint)}</div>' if hint else ""
    st.markdown(
        f'<div class="af-stat"><div class="l">{esc(label)}</div>'
        f'<div class="v">{esc(value)}</div>{hint_html}</div>',
        unsafe_allow_html=True,
    )


def stats_row(items: list[tuple[str, object, str]]) -> None:
    for column, (label, value, hint) in zip(st.columns(len(items)), items):
        with column:
            stat(label, value, hint)


def product_card(
    icon: str,
    title: str,
    tagline: str,
    outputs: tuple[str, ...] | list[str] = (),
    status: str = "live",
    eta: str = "",
) -> None:
    if status == "live":
        pill = '<span class="af-pill af-pill-live">live</span>'
    else:
        suffix = f" \u00b7 {esc(eta)}" if eta else ""
        pill = f'<span class="af-pill af-pill-soon">soon{suffix}</span>'
    bullets = "".join(f"<li>{esc(item)}</li>" for item in outputs)
    st.markdown(
        f"""<div class="af-card af-{'live' if status == 'live' else 'soon'}">
          <div class="af-card-top">
            <div class="af-card-icon">{icon}</div>
            <div><h4>{esc(title)}</h4></div>
            <div style="margin-left:auto">{pill}</div>
          </div>
          <p>{esc(tagline)}</p>
          {f'<ul>{bullets}</ul>' if bullets else ''}
        </div>""",
        unsafe_allow_html=True,
    )


def note(text: str, tone: str = "info") -> None:
    colors = {
        "info": ("rgba(124,92,255,.10)", "rgba(124,92,255,.35)", "#C4B5FD"),
        "warn": ("rgba(251,191,36,.10)", "rgba(251,191,36,.32)", "#FCD34D"),
        "ok": ("rgba(52,211,153,.10)", "rgba(52,211,153,.32)", "#6EE7B7"),
    }
    background, border, ink = colors.get(tone, colors["info"])
    st.markdown(
        f'<div style="background:{background};border:1px solid {border};border-radius:12px;'
        f'padding:.7rem .9rem;color:{ink};font-size:.88rem">{esc(text)}</div>',
        unsafe_allow_html=True,
    )


def brand(sidebar: bool = True) -> None:
    target = st.sidebar if sidebar else st
    target.markdown(
        """<div class="af-brand">
             <div class="af-brand-mark">\u2692\ufe0f</div>
             <div>
               <div class="af-brand-name">Artisan Forge</div>
               <div class="af-brand-sub">Product engine</div>
             </div>
           </div>""",
        unsafe_allow_html=True,
    )


def user_chip(name: str, meta: str) -> None:
    initial = (name or "?").strip()[:1].upper()
    st.sidebar.markdown(
        f"""<div class="af-user">
              <div class="af-avatar">{esc(initial)}</div>
              <div>
                <div class="af-user-name">{esc(name)}</div>
                <div class="af-user-meta">{esc(meta)}</div>
              </div>
            </div>""",
        unsafe_allow_html=True,
    )


def nav_label(text: str) -> None:
    st.sidebar.markdown(f'<div class="af-navlabel">{esc(text)}</div>', unsafe_allow_html=True)

# Artisan Forge

A SaaS automation engine for digital product creators. Sign in, describe a product, press one
button: you get a print-ready PDF, matching artwork, Etsy listing images, listing copy and a buyer
ZIP.

```
streamlit run app.py     # dashboard with accounts, studios and a library
python -m artisan_forge "2026 minimalist calendar with watercolor floral theme, 8.5x11, Sunday start"
```

## Studios

| Studio | Status | What it does |
|---|---|---|
| \U0001f4c5 Calendar Studio | live | 12-month printable calendars, dates verified twice |
| \u2728 Bundle Studio | live | ChatGPT writes prompts, checklists, trackers and affirmations; the engine lays them out |
| \U0001f5d3\ufe0f Planner Studio | coming soon | dated/undated weekly, daily and habit planners |
| \U0001f5bc\ufe0f Wall Art Studio | coming soon | quote posters in every Etsy ratio |
| \U0001f4d3 Journal Studio | coming soon | low-content books, KDP trim sizes |
| \U0001f4f1 Social Kit Studio | coming soon | matching Pinterest and Instagram promo graphics |

The catalog lives in `artisan_forge/products/__init__.py`; `status="soon"` entries render as teaser
cards in the dashboard instead of a studio page.

## Accounts

Email plus password, stored in SQLite (`data/artisan_forge.db`). Passwords are hashed with scrypt
from the standard library using a per-user random salt and compared in constant time. The first
account created becomes the admin; set `AF_SIGNUP_CODE` afterwards to make signups invite-only.
Every finished product is recorded in that account's library.

```
output/20260730-232852_2026-watercolor-floral-portrait/
├── print/
│   ├── 2026-watercolor-floral-portrait-letter.pdf   14 pages (cover + overview + 12 months)
│   ├── 2026-watercolor-floral-portrait-a4.pdf       A4 companion
│   ├── READ ME FIRST.txt
│   └── LICENSE - personal use.txt
├── art/            cover.png, season_1..4.png
├── mockups/        01_hero.jpg ... 10_size_chart.jpg  (2000x2000)
├── 2026-watercolor-floral-portrait-etsy-files.zip    what the buyer downloads
├── etsy_listing.txt / etsy_listing.json              title, 13 tags, description, price
└── manifest.json                                     spec, prompts, verification report
```

## Quickstart

```bash
python -m pip install -r requirements.txt
copy .env.example .env          # optional: add OPENAI_API_KEY / CANVA_ACCESS_TOKEN

python -m artisan_forge "2026 minimalist calendar with watercolor floral theme, 8.5x11, Sunday start"
streamlit run app.py            # form + status dashboard + gallery
```

No API keys are required. Without an OpenAI key the engine paints theme-matched artwork locally
with Pillow, so every build still completes end to end.

## Architecture

| Component | Technology | Where |
|---|---|---|
| Dashboard UI | Streamlit + custom CSS | `app.py`, `ui/` |
| Accounts & library | SQLite + scrypt | `artisan_forge/saas/` |
| Product catalog | dataclasses | `artisan_forge/products/__init__.py` |
| CLI | argparse | `artisan_forge/cli.py` |
| Orchestration | plain Python | `artisan_forge/pipeline.py`, `products/bundle.py` |
| Brief parsing | regex + theme scoring | `artisan_forge/brief.py` |
| Page geometry & primitives | reportlab | `artisan_forge/pdf/drawkit.py` |
| Page components | reportlab | `artisan_forge/pdf/blocks.py` |
| Calendar engine | reportlab | `artisan_forge/pdf/calendar_pdf.py` |
| Date maths | stdlib `calendar` / `datetime` | `artisan_forge/pdf/dates.py` |
| Date verification | grid maths + PDF text extraction | `artisan_forge/pdf/verify.py` |
| AI images | OpenAI Images (`gpt-image-1.5`) | `artisan_forge/ai/image_client.py` |
| AI content | Chat Completions, model fallback chain | `artisan_forge/ai/text_client.py` |
| Offline art / copy | Pillow painter + templates | `artisan_forge/ai/procedural.py`, `products/bundle.py` |
| Mockups | Pillow compositing | `artisan_forge/mockups/` |
| Canva (optional) | Canva Connect API | `artisan_forge/canva/client.py` |
| Packaging | listing copy + ZIP | `artisan_forge/packaging.py` |

## Step 1 - the PDF engine

`generate_calendar` is the low-level entry point; no templates or external assets are involved.

```python
from artisan_forge.pdf import generate_calendar

generate_calendar(2026, start_day="Sunday", orientation="landscape")
generate_calendar(2026, theme="botanical", paper="12x12", out_path="wall.pdf")
```

Configurable: year, `Sunday`/`Monday` week start, portrait/landscape, 11 named paper sizes plus any
custom trim, bleed and crop marks, holidays (US/UK/none), moon phases, notes column, cover page,
year-at-a-glance page, adjacent-month days, and per-month artwork panels.

### Why the dates are right

Every grid comes from `calendar.Calendar(firstweekday).monthdatescalendar()`, so leap years and
century rules are handled by the standard library rather than by hand-written tables. On top of
that each build runs two independent checks:

1. **Grid maths** - weeks are exactly 7 aligned days, dates are contiguous, in-month days are
   exactly `1..n` in order, February matches `isleap`, the year totals 365/366 and month
   boundaries chain correctly.
2. **Rendered output** - the finished PDF is re-opened, the text of every month page is extracted,
   and the month name plus every day number is confirmed present on the correct page.

The result lands in `manifest.json` and in `BuildResult.verification`. A maths failure aborts the
build (`strict_dates=True`); rendering anomalies are surfaced as warnings.

```bash
python -m artisan_forge --verify 1900-2200     # both week starts, all months
python -m pytest                               # 55 checks: dates, accounts, bundle, UI pages
```

The UI suite drives the real app through Streamlit's `AppTest`, signing up an account and rendering
every page, so a broken studio fails the build rather than the browser.

## Themes

`minimalist`, `watercolor_floral`, `botanical`, `boho`, `japandi`, `dark_luxe`, `scandi`,
`coastal`, `vintage`, `kids` (`python -m artisan_forge --list-themes`).

A theme drives the PDF palette and typography, the AI prompt, and the procedural fallback motif at
once, so the pages, the art and the mockups always match.

## Bundle Studio (ChatGPT)

Give it a topic and an audience. ChatGPT returns a JSON plan - title, intro, section titles,
prompts, checklist items, tracker columns, affirmations, plus Etsy copy - and the engine renders it
as a printable bundle: cover, welcome page, prompt pages with writing lines, checklist pages,
tracker grids, full-page affirmation prints, dot-grid notes and a closing page.

Model ids change often, so `artisan_forge/ai/text_client.py` tries a chain and uses the first model
that answers (`AF_TEXT_MODEL` goes to the front). Every model response is passed through
`normalise_plan`, which drops unknown sections, fills missing fields from templates and clamps
lengths - a malformed response degrades instead of breaking the build. With no API key the whole
studio runs on templates.

## AI art

Set `OPENAI_API_KEY` and optionally `AF_IMAGE_MODEL` (`gpt-image-1.5` by default) and
`AF_IMAGE_QUALITY` (`low`/`medium`/`high`). Cost is controlled by how many interior images a build
requests:

| `--month-art` | Images generated | Use for |
|---|---|---|
| `seasonal` (default) | 5 (cover + 4 quarters) | best value |
| `unique` | 13 (cover + 12 months) | premium listings |
| `single` | 2 (cover + one interior) | cheapest |

Prompts never ask for text and always reserve negative space for the grid or title block. Any API
error falls back to procedural art and records a warning instead of failing the build.

## Listing images

Ten 2000x2000 JPEGs, all composited from the real PDF pages: hero, framed wall art, 12-month
bundle grid, desk lifestyle scene, three-frame gallery wall, detail crop, "what you get" card,
print stack, gift bundle, and a size chart. Frames are drawn with gradient mouldings, mats, glass
sheen and soft shadows; the desk scene uses a real perspective warp.

## Canva (optional)

Add `CANVA_ACCESS_TOKEN` (scopes `asset:write`, `design:content:write`) and pass `--canva`. The
cover art is uploaded and an editable design is created; the edit URL comes back in
`manifest.json`. Without a token the step reports `skipped` and the build continues.

## Python API

```python
from artisan_forge import build_product, parse_brief

spec = parse_brief("2027 dark luxe 12x12 calendar, monday start, moon phases")
spec.month_art_mode = "unique"

result = build_product(spec, progress=lambda msg, pct: print(f"{pct:.0%} {msg}"))
print(result.pdf_path, result.zip_path, result.verification["ok"])
```

Batch a whole shop with `artisan_forge.pipeline.build_many([...])`.

## Extending

- **New theme**: add a `Theme` entry in `artisan_forge/themes.py` (palette, prompt, motif). Nothing
  else needs to change.
- **New listing image**: add a `scene_*` method to `MockupStudio` and its key to `ALL_SCENES`.
- **New product type**: build pages from `pdf/blocks.py`, describe the product to the compositor
  with a `MockupContext`, then flip its catalog entry from `soon` to `live` and add a `ui/` page.
  `products/bundle.py` is the reference implementation.
- **Custom fonts**: drop TTFs into `assets/fonts/`; files named `*bold*` / `*italic*` /
  `*display*` are picked up automatically by both the PDF and the mockups.

## Deploy to Railway

The repo ships with what Railway's builder needs: `Procfile` (binds Streamlit to `$PORT` on
`0.0.0.0`), `.python-version` (3.12) and `.streamlit/config.toml`.

1. Push to `main` and point a Railway service at the repo.
2. Set variables: `OPENAI_API_KEY` (optional), `AF_IMAGE_MODEL`, `AF_TEXT_MODEL` (optional) and
   **`AF_SIGNUP_CODE`**.
3. Attach a volume mounted at `/data`, then set `AF_DATA_DIR=/data` and
   `AF_OUTPUT_DIR=/data/output`.
4. Generate a domain under Settings -> Networking.

Two things to keep in mind:

- **Signups are open until you close them.** The first account becomes the admin; set
  `AF_SIGNUP_CODE` straight after creating it so strangers cannot register and spend your OpenAI
  credits.
- **The filesystem is ephemeral without a volume.** Both the accounts database and `output/` are
  wiped on redeploy unless `AF_DATA_DIR` and `AF_OUTPUT_DIR` point at a mounted volume. Downloading
  the ZIP from the Files tab is still the safest habit.

## Notes

- Moon phases use the mean synodic cycle (decorative accuracy, about +/- 1 day).
- Holiday sets cover common US and UK dates; add regions in `artisan_forge/pdf/dates.py`.
- Generated PDFs keep text as vectors, so printing is resolution-independent.

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
| \U0001f9f6 Crochet Studio | live | Graded crochet patterns from uploaded PDFs, Etsy data, a brief or photos, with technical diagrams |
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
| Orchestration | plain Python | `artisan_forge/pipeline.py`, `products/bundle.py`, `products/crochet.py` |
| Crochet engine | pypdfium2 + matplotlib + reportlab | `artisan_forge/crochet/` |
| Technical diagrams | matplotlib (optional) | `artisan_forge/crochet/diagrams.py` |
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
| Etsy drafts | Open API v3 + OAuth PKCE | `artisan_forge/etsy/` |
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
python -m pytest                               # 145 checks: dates, accounts, bundle, crochet, Etsy, UI
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

## Crochet Studio

One dropdown, five ways in. Every mode then runs the same pipeline, so the output is always a
complete, graded, branded pattern document.

| Mode | Input | What it does |
|---|---|---|
| Rebuild from uploaded patterns | up to 10 PDFs | parses every row, stitch count, gauge and abbreviation out of patterns you own, then rewrites complete graded patterns from what they contain. Choose how many patterns to build and how many uploads feed each one |
| From my Etsy product data | CSV / JSON / pasted text + a product number | reads the whole catalogue, builds the pattern for the product you numbered, and writes that pattern's Etsy listing using your existing tags as signal |
| From a written brief | one sentence | a full pattern from a description |
| From photos | up to 6 photos | ChatGPT reads the stitch pattern, construction and gauge back off the images |
| Diagrams and tech pack | a description | diagrams only, no API calls, free to run |

The five stages, in order:

1. **Content extraction** - `crochet/extract.py` reads each upload with pypdfium2 and pulls out the
   title, hooks, yarn weight, gauge, abbreviations, every numbered row with its stitch count,
   measurements, sizes and the assembly steps. Deterministic, free, and it gives the model a compact
   structured brief instead of tens of thousands of raw characters.
2. **Content expansion** - `crochet/expand.py` asks ChatGPT for everything a self-published pattern
   usually lacks: stitch counts, troubleshooting, yarn guide with yardage per size, care
   instructions, seaming methods, graded sizing tables, blocking guide, skill requirements and
   project time estimates. `normalise_pattern` makes any response safe to render.
3. **Diagram generation** - `crochet/diagrams.py` draws the technical plates with matplotlib from
   the pattern's own numbers: construction schematic with dimension arrows, stitch chart with a
   symbol legend, foundation-row illustration, one seam diagram per method used, gauge swatch, body
   measurement guide and a yardage chart.
4. **Image prompts, artwork and Canva** - ChatGPT writes the photographic prompts, they are
   rendered, and the renders are pushed to Canva as editable designs. Canva's Connect API has no
   text-to-image endpoint, so the flow is a round trip: prompt -> render -> Canva design -> optional
   export back into the PDF.
5. **Layout and packaging** - `crochet/pdf.py` assembles roughly 26 pages: cover, credits and
   licence, contents, about, materials, yarn guide, gauge, sizing, abbreviations, construction,
   foundation, chart, the instructions, stitch count reference, assembly, a page per seaming method,
   blocking, troubleshooting, care, gallery and a branded thank-you page. Then listing images, Etsy
   copy and the buyer ZIP.

Branding is threaded through every page: shop name, designer credit, support email, website,
Instagram, Ravelry, tagline, logo, accent colour, and a personal-use or small-business licence.

Long sections paginate themselves - `page_plan()` measures wrapped text against the available
height, so instructions flow onto as many pages as they need. After rendering, the PDF is re-opened
and every section title and step label is confirmed present, so a layout bug cannot silently drop
instructions.

Cost is explicit. `lean` uses the cheap model tier (`AF_TEXT_MODEL_CHEAP`, `AF_IMAGE_MODEL_CHEAP`)
and renders 2 images; `standard` and `max` render 5. The tech pack mode makes no API calls at all.
With no keys the studio still produces the full document from templates and procedural art.

### Batch: many patterns from one upload set

Upload four patterns and you can ask for four products, not one. "Patterns to create" sets how many
separate builds to run; "source files per pattern" decides which uploads feed each one (0 splits
them evenly). Every pattern gets its own run folder, PDF filename, mockups, listing and ZIP, and a
failure in one does not abandon the rest.

### Market research: listings written from real demand

Upload an Etsy competitor scrape - **JSON**, **JSONL**, **CSV/TSV** or **Excel** - and the listing
stops being guesswork. `artisan_forge/crochet/market.py` reads it and works out:

| From the data | Used for |
|---|---|
| `tagVolumes` | ranking tags by real monthly search volume |
| `tags` | candidate tags, and how contested each one is |
| `price`, `originalPrice`, `ehuntDiscountPercent` | what to charge, and whether to list high and discount |
| `ehuntEstimatedSales`, `favoritesCount`, `reviewCount` | which competitors are actually winning |
| `title` | the title conventions buyers in this niche already click |
| `demandScore`, `opportunityScore` | niche health, reported in the results panel |
| `imageCount` | how many listing images competitors use |

Field names are matched loosely, so `estimatedSales`, `numFavorers` or `keywords` land in the right
place too. Three corrections are applied because raw scrape numbers mislead:

* **Volume is scaled logarithmically.** A scrape will list a tag claiming 55M searches beside a real
  one claiming 260K; linear scaling lets the outlier swamp everything.
* **Keyword-stuffed junk is dropped.** Tags like `24in1pokemon crochet` never reach the listing.
* **Off-niche tags are excluded.** A broad "crochet" search returns cardigans next to amigurumi
  keychains. Tags are re-scored against the item you are actually making, so a cardigan does not get
  tagged `crochet keychain` - a generic `instant download` is strictly better than wrong-niche
  traffic. Same-category neighbours (`crochet top` for a cardigan) are kept.

Pricing follows the *winners*, not the field average, then mirrors the market's sale pattern: if 67%
of competitors run permanent discounts, the report proposes a list price and a sale price so the
listing shows a strikethrough like the rest.

With an OpenAI key, one focused call then writes the title, tags and description from that brief -
SEO is worth its own pass rather than being bolted onto the end of the pattern prompt. Without a
key, the research still sets the tags and the price locally. Everything is capped to Etsy's limits
(140-character title, 13 tags, 20 characters each) and the full report lands in `manifest.json`.

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

## Etsy auto-listing (drafts only)

Connect your shop on the Account page, then use the **Publish to Etsy** tab on any build. Artisan
Forge creates the listing with `createDraftListing`, attaches up to 10 mockups and the buyer files,
and stops there. Nothing in `artisan_forge/etsy/` can set a listing to `active` - you review and
publish inside Etsy.

Setup:

1. Create an app at etsy.com/developers -> Your Apps and copy the keystring.
2. Register the callback URL, then set `ETSY_KEYSTRING` and `ETSY_REDIRECT_URI` to the same URL.
3. Set `AF_SECRET_KEY` to any random string.
4. Account -> Etsy shop -> **Connect Etsy shop**.

How it works:

- OAuth 2.0 authorization code flow with PKCE (`S256`), requesting `listings_r listings_w shops_r`.
  The `state` parameter is checked against the session before the code is exchanged.
- Tokens are encrypted with Fernet before they touch SQLite, keyed from `AF_SECRET_KEY`. A 401
  triggers one silent refresh and a retry; the new token is re-encrypted and saved.
- Requests are paced to ~4.8/second to stay inside the 5 QPS personal-access budget. A full listing
  costs roughly 13-15 calls out of the 5,000 daily allowance.
- Titles and tags are sanitised for Etsy's rules (140 chars, 13 tags, 20 chars each, no punctuation
  in tags), and files over Etsy's 20 MB limit are skipped with a warning rather than failing.
- Draft ids are recorded per build, so the Publish tab shows what was already created and links
  straight to the Etsy editor.

Personal access covers your own shop. Publishing to other people's shops needs Etsy's commercial
access review.

## Canva (optional)

Add `CANVA_ACCESS_TOKEN` (scopes `asset:write`, `design:content:write`, plus
`design:content:read` if you want to export designs back out) and pass `--canva`. The cover art is
uploaded and an editable design is created; the edit URL comes back in `manifest.json`. Without a
token the step reports `skipped` and the build continues.

Canva does **not** expose a text-to-image endpoint - the Connect API covers assets, designs,
exports, brand templates and autofill. So "let Canva make the picture" is really a round trip:
ChatGPT writes the prompt, the image is rendered, the render becomes an editable Canva design, and
Crochet Studio can optionally export it straight back and place the Canva version in the pattern
PDF. See `artisan_forge/canva/client.py` (`round_trip`, `send_plates_to_canva`).

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
2. Set variables: `OPENAI_API_KEY` (optional), `AF_IMAGE_MODEL`, `AF_TEXT_MODEL` (optional),
   `ETSY_KEYSTRING`, `ETSY_REDIRECT_URI`, `AF_SECRET_KEY` and **`AF_SIGNUP_CODE`**.
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

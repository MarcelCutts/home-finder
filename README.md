# Home Finder

![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)
![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)
![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)

Multi-platform London rental property scraper with commute filtering, AI quality analysis, web dashboard, and Telegram notifications.

## Table of Contents

- [Features](#features)
- [Quick Start](#quick-start)
- [Web Dashboard](#web-dashboard)
- [Configuration](#configuration)
- [Getting API Keys](#getting-api-keys)
- [Running Tests](#running-tests)
- [Architecture](#architecture)
- [Project Structure](#project-structure)
- [Deployment](#deployment)

## Features

- **Multi-platform scraping**: OpenRent, Rightmove, Zoopla, OnTheMarket
- **Cross-platform deduplication**: Graduated multi-signal scoring merges the same property listed on different platforms (two-phase: pre- and post-enrichment)
- **Commute guidance**: TravelTime measurements distinguish reachable, outside-limit and unknown outcomes; API failures never become zero-minute commutes
- **AI quality analysis**: Two-phase Claude vision analysis — Phase 1 observes images (kitchen, condition, space, light), Phase 2 evaluates value, generates viewing notes, and curates highlights
- **Web dashboard**: FastAPI-powered dashboard with HTMX live filtering, map view with MarkerCluster, property detail pages with area context, and lightbox gallery
- **Rich Telegram notifications**: Property cards with photos, star ratings, commute times, quality summaries, and direct links (including web dashboard deep links)
- **Detail enrichment**: Fetches gallery images, floorplans, and descriptions from property detail pages
- **Disk-based image caching**: Images cached locally for reliable display in dashboard and quality analysis
- **Proxy support**: HTTP/SOCKS5 proxy for accessing geo-restricted sites from outside the UK
- **SQLite storage**: Track seen properties to only notify about new listings, with notification retry on failure

## Quick Start

1. **Install dependencies**:

   ```bash
   uv sync --locked --all-extras
   ```

2. **Configure environment**:

   ```bash
   cp .env.example .env
   # Edit .env with your settings
   ```

3. **Run**:
   ```bash
   # One-shot pipeline (scrape + filter + notify)
   uv run home-finder

   # Inspect the owner dashboard before enabling collection
   uv run home-finder --serve --no-pipeline
   ```

### Run Modes

```bash
uv run home-finder                      # Full pipeline with Telegram notifications
uv run home-finder --dry-run            # Full pipeline, save to DB but no notifications
uv run home-finder --scrape-only        # Just scrape and print (no filtering/storage)
uv run home-finder --max-per-scraper 5  # Limit results per scraper (for testing)
uv run home-finder --scrapers openrent,zoopla  # Only run specific scrapers
uv run home-finder --full-scrape        # Compatibility flag; normal collection already passes known pages
uv run home-finder --serve              # Web dashboard only
uv run home-finder --serve --with-pipeline  # Explicitly enable recurring collection
uv run home-finder --serve --no-pipeline  # Dashboard only (compatible with existing commands)
uv run home-finder --reanalyze          # Re-run quality analysis on flagged properties
uv run home-finder --reanalyze --all    # Flag ALL analyzed properties for re-analysis
uv run home-finder --reanalyze --outcode E2,E8  # Flag specific outcodes for re-analysis
uv run home-finder --reanalyze --request-only   # Only flag for re-analysis, don't run it
uv run home-finder --dedup-existing     # Retroactively merge duplicates already in the DB
uv run home-finder --backfill-commute   # Backfill commute data for properties missing it
uv run home-finder --generate-thumbnails   # Generate missing thumbnails for cached images
uv run home-finder --check-off-market             # Check active properties for off-market signals
uv run home-finder --check-off-market --scrapers zoopla  # Check only specific platforms
uv run home-finder --debug              # Enable debug-level logging
```

Dry runs save listings, analyses, pipeline runs, scraper metrics and property
events, and can incur API charges. They leave delivery intents pending without
constructing a Telegram notifier or recovering/sending notifications. Diagnostics
report the latest terminal run, including dry runs; the database does not store
the run mode. Logs identify it with `run_id` and `dry_run`. Add `--debug` for phase
outcomes and timings. A completed phase means execution returned; individual
properties may still be deferred or unsuccessful.

Event pruning runs only in live mode, but dry runs count toward the retained run
window when the next live cleanup occurs. Run token/cost totals cover returned
main-analysis usage and may be incomplete after interruption; they exclude
reanalysis usage. The paid spend ledger is authoritative for reconciled spending.

## Web Dashboard

The `--serve` flag starts a FastAPI web server on port 8000 without collection. Add `--with-pipeline` to explicitly enable the background scheduler, which waits at least 55 minutes after each run (configurable via `HOME_FINDER_PIPELINE_INTERVAL_MINUTES`). CLI modes are mutually exclusive; reanalysis and scheduler modifiers require their corresponding mode.

### Dashboard Features

- **Property grid**: Filterable, sortable card grid with pagination
- **HTMX live filtering**: Filters update without full-page reload (progressive enhancement — works without JS too)
- **Map view**: Toggle between grid and map. MarkerCluster groups nearby properties; markers are color-coded by quality rating (green 4-5, amber 3, red 1-2, grey unrated)
- **Property detail pages**: Full gallery with lightbox (keyboard + touch/swipe navigation), floorplan, quality analysis breakdown, area context (rental benchmarks, council tax, crime rates, rent trends), and source links
- **Quality summary on cards**: Condition severity badge and one-line AI summary on each card
- **Map marker interaction**: Clicking a marker scrolls to the card if visible, or fetches and pins it at the top of results with a dismiss button
- **Property status workflow**: Pill-based status controls (triage → progress → outcome) with quick-action buttons (star/skip) on new property cards
- **Thumbnails**: Gallery images served as 480px thumbnails for faster card loading
- **Telegram integration**: When `HOME_FINDER_WEB_BASE_URL` is set, Telegram notifications include a "Details" button linking to the web dashboard

### Dashboard Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `HOME_FINDER_WEB_BASE_URL` | `""` | Public URL for the dashboard (enables Telegram deep links) |
| `HOME_FINDER_WEB_OWNER_USERNAME` | `owner` | Owner HTTP Basic login |
| `HOME_FINDER_WEB_OWNER_PASSWORD` | `""` | Required when serving; random ASCII password of at least 24 characters |
| `HOME_FINDER_WEB_PORT` | `8000` | Web server port |
| `HOME_FINDER_WEB_HOST` | `0.0.0.0` | Web server bind address |
| `HOME_FINDER_PIPELINE_INTERVAL_MINUTES` | `55` | Minutes between pipeline runs in serve mode |

### Security

The dashboard writes status changes and queues reanalysis, which can incur API charges. Pages, images, static assets and diagnostics require the owner's HTTP Basic login. Missing, short or non-ASCII passwords fail closed with HTTP 503. `/health` exposes liveness only; `--no-pipeline` stops collection but keeps owner actions available.

Generate a password with `python -c 'import secrets; print(secrets.token_urlsafe(32))'` and set `HOME_FINDER_WEB_OWNER_PASSWORD` in your local configuration or hosting secret store. Hosted access requires HTTPS and `HOME_FINDER_WEB_BASE_URL` set to its canonical origin. For local-only use, bind to `127.0.0.1`. Never put credentials in URLs; Basic authentication uses browser-managed credentials, so close the private browsing session and rotate the password to revoke access.

Mutations require a same-origin `Origin` or `Referer` header, including requests from scripts. Bodies are limited to 64 KiB and 10 seconds. Per-process limits allow 30 mutations/minute separately for the owner and Telegram, and throttle failed credential attempts after 10 attempts/minute per connection peer. Anonymous browser requests retain their HTTP Basic challenge so a fresh owner browser can log in. Valid credentials remain accepted when anonymous traffic exhausts that peer's limit. HTTP 429 includes `Retry-After`; repeated reanalysis requests coalesce. Use one app worker with trusted ingress connection limits.

Telegram callbacks require the webhook secret, configured chat and owner user. `HOME_FINDER_TELEGRAM_OWNER_USER_ID` defaults to a positive private chat ID; set it explicitly for group chats. Update IDs are stored atomically with status history to prevent replay across restarts. Schema migration 7 adds one nullable column and unique index. The core also adds migrations 8–9 for listing text and spend accounting; [back up and rehearse the upgrade](docs/operations.md#back-up-and-migrate) before activation because older binaries refuse newer schemas.

Database operations serialize access to the shared SQLite connection through all
reads, writes and transaction cleanup. This prevents a pipeline commit from
committing an unfinished owner action. Nested helpers explicitly join their
caller's transaction; network requests run outside database ownership. This adds
no schema migration or connection pool. SQLite does not isolate operations on the
[same connection](https://www.sqlite.org/isolation.html).

Cancellation before commit rolls back the operation. Once commit starts, cleanup
finishes before the connection is reused and the whole operation may have committed;
do not automatically replay a cancelled write. Keep one application worker during
initial operation. Merging code does not deploy it or resume collection.

Additional protections include:

- **XSS prevention**: All user-controlled content is escaped. Scraped descriptions use `| e | safe` in Jinja2. Leaflet popups use `textContent` instead of HTML injection.
- **Security headers**: `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin`
- **Input validation**: Query parameters are clamped to valid ranges; sort options are whitelisted
- **Error handling**: Database errors render a user-friendly error page instead of raw tracebacks

### Endpoints

| Path | Description |
|------|-------------|
| `GET /` | Dashboard with filters (supports HTMX partial rendering) |
| `GET /property/{unique_id}` | Property detail page |
| `GET /images/{unique_id}/{filename}` | Cached property image (immutable cache headers) |
| `GET /count` | Property count JSON (for badge/polling) |
| `GET /property/{unique_id}/card` | Single property card partial (for map marker clicks) |
| `GET /area/{outcode}` | Area context page (rent benchmarks, council tax, crime) |
| `GET /health` | Health check (`{"status": "ok"}`) |
| `GET /diagnostics` | Owner-only pipeline state and latest run information |
| `GET /static/...` | Static assets (CSS, JS) |
| `PATCH /property/{unique_id}/status` | Update property status (returns card or badge partial) |
| `POST /property/{unique_id}/reanalyze` | Request quality re-analysis |

## Configuration

Create a `.env` file with these settings (all use `HOME_FINDER_` prefix):

### Required

- `HOME_FINDER_TELEGRAM_BOT_TOKEN`: Your Telegram bot token (from @BotFather)
- `HOME_FINDER_TELEGRAM_CHAT_ID`: Your Telegram chat ID
- `HOME_FINDER_WEB_OWNER_PASSWORD`: Owner dashboard password (24+ ASCII characters), when serving

### Optional APIs

- `HOME_FINDER_TRAVELTIME_APP_ID`: TravelTime API app ID (enables commute filtering)
- `HOME_FINDER_TRAVELTIME_API_KEY`: TravelTime API key
- `HOME_FINDER_ANTHROPIC_API_KEY`: Anthropic API key; paid analysis also requires explicit `HOME_FINDER_ANALYSIS_SPEND_POLICY` caps (see [operations](docs/operations.md))

### Feature Flags

- `HOME_FINDER_ENABLE_QUALITY_FILTER`: Enable Claude vision property analysis (default: true)
- `HOME_FINDER_REQUIRE_FLOORPLAN`: Drop properties without floorplans (default: true)
- `HOME_FINDER_QUALITY_FILTER_MAX_IMAGES`: Max gallery images to analyze per property (default: 20, max: 20)
- `HOME_FINDER_MIN_GALLERY_FOR_PHOTO_INFERENCE`: Minimum gallery images to bypass floorplan gate via photo inference; 0 disables (default: 5)
- `HOME_FINDER_ENABLE_EXTENDED_THINKING`: Enable extended thinking for deeper quality analysis (default: true)
- `HOME_FINDER_ANALYSIS_CONFIG`: Optional phase overrides merged onto the bounded Sonnet 5.5 runtime profile: low effort, 8,192 output tokens and no SDK retries. `{}` retains those defaults; top-level `null` is rejected. Stored historical profiles remain unchanged. See [paid analysis](docs/operations.md#runtime-model-defaults). Spend caps remain required.
- `HOME_FINDER_ENABLE_IMAGE_HASH_MATCHING`: Enable perceptual image hashing for deduplication (default: true)
- `HOME_FINDER_MAX_ENRICHMENT_ATTEMPTS`: Max enrichment retry attempts before giving up on a property, 1-10 (default: 3)
- `HOME_FINDER_MAX_ENRICHMENT_PER_RUN`: Maximum homes to enrich per run, 1-500 (default: 75). Recovery and discovery share the slots; excess homes remain queued without consuming attempts.

### Search Criteria

- `HOME_FINDER_MIN_PRICE`: Minimum monthly rent (default: 1800)
- `HOME_FINDER_MAX_PRICE`: Maximum monthly rent (default: 2500)
- `HOME_FINDER_MIN_BEDROOMS`: Minimum bedrooms (default: 0)
- `HOME_FINDER_MAX_BEDROOMS`: Maximum bedrooms (default: 2)
- `HOME_FINDER_DESTINATION_POSTCODE`: Your destination postcode (default: N1 5AA)
- `HOME_FINDER_MAX_COMMUTE_MINUTES`: Maximum commute time in minutes (default: 30)
- `HOME_FINDER_SEARCH_AREAS`: Comma-separated outcodes/boroughs (default: e2,e3,e5,e9,e10,e15,e17,n15,n16,n17)

### Scraper Filters

- `HOME_FINDER_FURNISH_TYPES`: Comma-separated furnishing filter (default: unfurnished,part_furnished)
- `HOME_FINDER_MIN_BATHROOMS`: Minimum bathrooms (default: 1)
- `HOME_FINDER_INCLUDE_LET_AGREED`: Include already-let properties (default: false)

### Scraper Tuning

- `HOME_FINDER_ZOOPLA_MAX_AREAS_PER_RUN`: Max search areas per Zoopla run to avoid Cloudflare blocks; rotates a subset each run (default: 4)

### Other

- `HOME_FINDER_PROXY_URL`: HTTP/SOCKS5 proxy URL for geo-restricted sites (e.g., `socks5://user:pass@host:port`)
- `HOME_FINDER_DATABASE_PATH`: SQLite database path (default: data/properties.db)

## Getting API Keys

### Telegram Bot

1. Message @BotFather on Telegram
2. Send `/newbot` and follow instructions
3. Copy the bot token to your `.env`
4. Send a message to your bot
5. Get your chat ID from `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`

### TravelTime API (Optional)

1. Sign up at https://traveltime.com/
2. Get your App ID and API Key from the dashboard
3. Add to your `.env`

### Anthropic API (Optional)

1. Get an API key at https://console.anthropic.com/
2. Add to your `.env` as `HOME_FINDER_ANTHROPIC_API_KEY`
3. Enables two-phase AI property quality analysis using Claude vision (~$0.05-0.07/property)

## Running Tests

```bash
uv run pytest                         # Run all tests (slow tests excluded by default)
uv run pytest tests/test_models.py    # Run specific test file
uv run pytest -k "test_openrent"      # Run tests matching pattern
uv run pytest --cov=src               # Run with coverage
uv run pytest -m slow                 # Run slow tests (real scraping)
uv run pytest -m browser              # Run browser E2E tests (requires Playwright)
```

> **Note:** Don't pass `--timeout` on the command line — it's already configured in `pyproject.toml` (30s default).

## Architecture

### Pipeline Flow

The full pipeline (`uv run home-finder`) executes these steps in order:

1. **Retry Unsent** — Resend failed notifications from previous runs
2. **Scrape** — All scrapers run against configured search areas
3. **Criteria Filter** — Apply price/bedroom filters
4. **Location Filter** — Validate postcodes match search areas (catches scraper leakage)
5. **Wrap as MergedProperty** — Wrap each property as a single-source `MergedProperty`
6. **Persist & Recover** — Retain fetched listings before filtering and recover pending enrichment without waiting for another search receipt
7. **Commute Guidance** — Preserve measured results and explicit unknown outcomes (if configured)
8. **Detail Enrichment** — Fetch gallery images, floorplans, descriptions; cache images to disk
9. **Post-Enrichment Dedup** — Cross-platform deduplication using enriched data (images, postcodes, coordinates)
10. **Floorplan Gate** — Drop properties without floorplans (if enabled)
11. **Quality Analysis** — Two-phase Claude vision analysis: Phase 1 (visual observations from images), Phase 2 (evaluation using Phase 1 output + listing text)
12. **Save & Notify** — Store validated results and send Telegram notifications; provider failures leave work pending, and uncertain charges remain held for reconciliation

### Deduplication

Cross-platform deduplication uses weighted multi-signal scoring:

| Signal | Points |
|--------|--------|
| Image hash match | +40 |
| Full postcode match | +40 |
| Coordinates within 50m | +40 (graduated) |
| Street name match | +20 |
| Outcode match | +10 |
| Price within 3% | +15 (graduated) |

A match requires **60+ points** from **2+ signals**. Coordinates and price use graduated scoring (partial credit for near-matches). Cross-platform matching requires full postcodes to prevent false positives.

## Project Structure

```
src/home_finder/
├── scrapers/              # Platform scrapers
│   ├── base.py            # Abstract BaseScraper interface
│   ├── openrent.py        # OpenRent (crawlee)
│   ├── rightmove.py       # Rightmove (HTTPX + typeahead API)
│   ├── zoopla.py          # Zoopla (curl_cffi for Cloudflare bypass)
│   ├── onthemarket.py     # OnTheMarket (curl_cffi)
│   ├── zoopla_models.py   # Pydantic models for Zoopla JSON parsing
│   ├── detail_fetcher.py  # Gallery/floorplan extraction from detail pages
│   └── location_utils.py  # Outcode detection utilities
├── filters/               # Filtering and analysis
│   ├── criteria.py        # Price/bedroom filtering
│   ├── commute.py         # TravelTime API commute filtering
│   ├── deduplication.py   # Weighted multi-signal cross-platform deduplication
│   ├── location.py        # Location validation (catches scraper leakage)
│   ├── detail_enrichment.py  # Enriches merged properties with images/descriptions
│   ├── floorplan.py       # Floorplan analysis (legacy)
│   ├── quality.py         # Two-phase Claude vision quality analysis (schemas, API calls, merging)
│   └── quality_prompts.py # System prompts and user prompt builders for quality analysis
├── web/                   # Web dashboard (FastAPI)
│   ├── app.py             # Application factory, lifespan, security middleware
│   ├── routes.py          # Dashboard, detail, health check routes
│   ├── templates/         # Jinja2 templates
│   │   ├── base.html      # Base layout (Pico CSS + Leaflet + HTMX)
│   │   ├── dashboard.html # Main dashboard page with filters + map toggle
│   │   ├── detail.html    # Property detail page
│   │   ├── error.html     # Error/404 page
│   │   ├── _results.html  # HTMX partial (card grid + pagination)
│   │   ├── _property_card.html  # Single property card component
│   │   ├── _quality_card.html   # Quality analysis breakdown component
│   │   ├── _status_controls.html  # Status pill workflow component
│   │   └── _macros.html           # Shared Jinja2 macros
│   └── static/
│       ├── app.js         # Lightbox, detail map, dashboard map, lazy loading
│       └── style.css      # All custom styles
├── notifiers/
│   ├── telegram.py        # Telegram formatting and notifier interface
│   └── delivery.py        # Saved plans and per-request receipt recovery
├── db/
│   ├── storage.py         # SQLite domain storage and owned transactions
│   ├── spend.py           # Paid-attempt accounting and visual checkpoints
│   └── delivery.py        # Bounded durable notification state
├── utils/
│   ├── address.py         # Address normalization for deduplication
│   ├── image_cache.py     # Disk-based image caching for gallery/floorplan images
│   ├── image_hash.py      # Perceptual image hashing
│   ├── epc_detector.py    # EPC chart detection (PIL heuristics)
│   └── floorplan_detector.py  # Offline floorplan heuristic experiments
├── models/                # Typed property, analysis, spending and delivery models
├── config.py              # Settings management (pydantic-settings)
├── logging.py             # Structured logging (structlog)
└── main.py                # Pipeline orchestration and CLI

tests/
├── test_db/               # Database storage tests
│   ├── test_storage.py    # Core CRUD, notification tracking
│   └── test_storage_quality.py  # Quality analysis, paginated queries, detail
├── test_web/              # Web dashboard tests
│   ├── test_app.py        # App factory, security headers, middleware
│   └── test_routes.py     # Routes, filters, HTMX, XSS prevention
├── test_filters/          # Filter tests (criteria, commute, dedup, quality, etc.)
├── test_notifiers/
│   ├── test_telegram.py   # Core notification formatting and sending
│   └── test_telegram_web.py  # Web dashboard integration (inline keyboard, deep links)
├── test_scrapers/         # Scraper tests per platform
├── test_utils/            # Utility tests (image cache, address normalization)
└── integration/           # Integration tests (pipeline, real scraping)
```

## Deployment

Use one authoritative SQLite database for collection, dashboard decisions and
notifications. File replacement between a local collector and a running dashboard
is disabled because it can erase owner decisions or omit WAL writes.

The Fly configuration builds the `dashboard` target of `Dockerfile` and starts
`--serve --no-pipeline`. Set a strong owner password, canonical HTTPS origin and
Telegram owner settings before activation. `/health` is a liveness check; it does
not establish that owner access or the pipeline is correctly configured.

The GitHub **Release dashboard** workflow is manual, runs all offline checks,
and uses the main-only `production` environment and its
`FLY_PRODUCTION_API_TOKEN` secret. Merging a PR does not deploy or start scraping.

See [operations](docs/operations.md) for backup, migration, isolated restore,
spend reconciliation, and the single-runner collection configuration. Source
access and model quality require separate qualification before live activation.

```bash
docker build --target dashboard -t home-finder .
docker run --env-file .env -p 127.0.0.1:8000:8000 -v ./data:/app/data home-finder
```

## License

MIT

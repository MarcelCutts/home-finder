# Home Finder

![Python 3.11–3.14](https://img.shields.io/badge/python-3.11–3.14-blue.svg)
![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)
![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)

Multi-platform London rental property scraper with commute filtering, AI quality analysis, web dashboard, and Telegram notifications.

The current application serves one owner's search. The agreed target is a small hosted service for tens to hundreds of users, introduced gradually after reliability and access-control repairs. Multi-user isolation is not implemented yet. Follow the [delivery plan](docs/plans/2026-09-28-research-synthesis-and-delivery-plan.md) in its execution order; keep normal collection paused until its rollout gates pass.

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

- **Multi-platform scraping**: Adapters for OpenRent, Rightmove, Zoopla and OnTheMarket; current source contracts and access need qualification before collection resumes
- **Cross-platform deduplication**: Graduated multi-signal scoring merges the same property listed on different platforms (two-phase: pre- and post-enrichment)
- **Commute information**: Advisory TravelTime estimates against a configured destination and limit. Unknown/error results remain unknown and never exclude a home or become a zero-minute success. Saved route context remains unverified pending PR16b.
- **AI quality analysis**: Two-phase Claude vision analysis — Phase 1 observes images (kitchen, condition, space, light), Phase 2 evaluates value, generates viewing notes, and curates highlights
- **Web dashboard**: FastAPI-powered dashboard with HTMX live filtering, map view with MarkerCluster, property detail pages with area context, and lightbox gallery
- **Rent and bills**: Separate advertised rent and explicit named bill claims from unpriced costs; show source conflicts and fee questions. Historical research and unsupported earlier EPC/council-tax claims are labelled unverified. England tenancy guidance is dated and scoped; the dashboard does not recommend fixed offer discounts.
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
   # Local dashboard, with background collection disabled
   HOME_FINDER_WEB_HOST=127.0.0.1 uv run home-finder --serve --no-pipeline
   ```

This still provides writable status and reanalysis controls; see [Security](#security). Use a development database, never the frozen research export. The pipeline commands below require the relevant collection/release gates. `--dry-run` suppresses Telegram notifications but still writes data, fetches listings and can incur analysis/commute charges.

### Run Modes

```bash
uv run home-finder                      # Full pipeline with Telegram notifications
uv run home-finder --dry-run            # Full pipeline, save to DB but no notifications
uv run home-finder --scrape-only        # Just scrape and print (no filtering/storage)
uv run home-finder --max-per-scraper 5  # Limit results per scraper (for testing)
uv run home-finder --scrapers openrent,zoopla  # Only run specific scrapers
uv run home-finder --full-scrape        # Scrape all pages (disable early-stop on seen properties)
uv run home-finder --serve              # Web dashboard + recurring pipeline scheduler
uv run home-finder --serve --no-pipeline  # Web dashboard only, no background pipeline
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

Availability checks need evidence for the requested listing. Generic redirects, bare 404s, challenges and unrecognised pages remain unknown. Selecting one source cannot establish that every listing for a home is off market. Existing unchecked records are historical candidates, not confirmed available homes; source activation still requires the delivery plan's access and fixture checks.

The [PR13a spending ledger](docs/plans/2026-09-28-pr13a-spend-ledger.md) can be inspected with `uv run python -m home_finder.pipeline.spend_report --database /path/to/properties.db`. This command is read-only and does not load credentials or start processing. Missing historical ledgers report unknown costs. PR13b still needs to connect reservations and accounting to every analysis request; the foundation alone does not enforce a live budget.

## Web Dashboard

The `--serve` flag starts a FastAPI web server on port 8000. Unless `--no-pipeline` is supplied, it starts a background pipeline after 30 seconds, then waits the configured interval (55 minutes by default) plus zero to five minutes of jitter **after each run finishes**. Long runs do not accumulate missed starts. CLI, maintenance and serve entry points share a nonblocking process lock beside the resolved database path. An overlapping invocation logs `runner_skipped` with `already_running` and performs no collection or analysis. Fly uses `--serve --no-pipeline`.

Runs with no eligible new listings still recover pending enrichment, initial analysis, reanalysis and price-drop delivery. Missing cached images leave analysis pending with a deferred reason; incomplete model output is not delivery-ready. Initial analysis exceptions, incomplete results and timeouts stop automatic retries after three attempts. Exhausted rows remain retained. Reanalysis preserves the existing result until a valid replacement arrives, and recovery of an already-notified fallback cannot create a second discovery alert. The initial-analysis batch alternates older retries and new work. Enrichment recovery and reanalysis are each capped at `MAX_ANALYSIS_PER_RUN` as well; these are separate bounded drains, **not a global paid-call or dollar budget**. PR13 supplies that admission/ledger contract, and PR12b adds durable due times, per-request attempts and explicit operator retry controls.

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
| `HOME_FINDER_WEB_OWNER_USERNAME` | `owner` | Single owner HTTP Basic login |
| `HOME_FINDER_WEB_OWNER_PASSWORD` | `""` | Required for dashboard access; random password of at least 24 characters |
| `HOME_FINDER_WEB_PORT` | `8000` | Web server port |
| `HOME_FINDER_WEB_HOST` | `0.0.0.0` | Web server bind address |
| `HOME_FINDER_CARTO_BASEMAP_API_KEY` | `""` | Browser tile key for both maps; absent/failed maps fall back to listings or a location link |
| `HOME_FINDER_PIPELINE_INTERVAL_MINUTES` | `55` | Minimum delay after a serve-mode run, plus 0–5 minutes of jitter |

Maps use a shared CARTO raster configuration with visible OpenStreetMap/CARTO attribution. Request a key from [CARTO](https://www.carto.com/basemaps/apikey/), check the [current terms](https://www.carto.com/legal/basemap-terms/), and restrict the key to the dashboard's actual origins in the provider console. This key is intentionally visible in the browser; it must be a dedicated basemap key, not a private backend credential. Keep other keys private. Monitor usage across all project keys; the September 25, 2026 terms allow 5 million monthly requests for non-commercial use or 1 million for commercial use. A business-operated service can count as commercial even without a user subscription. Do not enable a paid plan automatically. The tested application falls back cleanly without a key or when tiles fail; an actual provisioned key and origin restrictions still need verification before production map activation.

### Security

The dashboard **writes data**: status updates modify the database, and reanalysis requests queue work that can later incur API charges. Pages, HTMX fragments, cached images, static assets and diagnostics require the configured owner HTTP Basic login. An empty or short password fails closed with HTTP 503. `/health` exposes only liveness; `--no-pipeline` disables collection but keeps owner actions available.

Before starting the dashboard, generate a password with `python -c 'import secrets; print(secrets.token_urlsafe(32))'` and store it as `HOME_FINDER_WEB_OWNER_PASSWORD` in your local secret configuration or hosting secret store. Use HTTPS for hosted access and set `HOME_FINDER_WEB_BASE_URL` to its canonical HTTPS origin. The browser prompts for the username (`owner` by default) and password. Local development can use `http://127.0.0.1:8000`; bind to `127.0.0.1` if the service is only needed locally. Never place credentials in a URL. HTTP Basic has browser-managed credentials rather than an application logout/session screen; close the private browsing session and rotate the password when access must be revoked. Multi-user accounts are a later delivery segment.

Mutations require an exact same-origin `Origin` or `Referer`, reject cross-site fetches, and accept at most 64 KiB within 10 seconds. Authenticated scripts must send `Origin` too. Per-process limits allow 30 mutations/minute for the owner and separately for Telegram; failed authentication is limited to 10/minute per connection peer. A 429 response includes `Retry-After`. Deploy one app worker at this stage; these bounded in-memory limits are not distributed quotas. A trusted ingress must enforce HTTPS, connection limits and its own abuse controls. Repeated reanalysis clicks coalesce into the existing pending request.

Telegram callbacks require the webhook secret, the configured chat and the configured owner user. `HOME_FINDER_TELEGRAM_OWNER_USER_ID` defaults to a positive private chat ID; set it explicitly for a group chat. Inline callbacks without an authorised message/chat are ignored. Accepted update IDs are stored atomically with status history to prevent replay across restarts. No bot callback authentication grants dashboard access.

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
- `HOME_FINDER_WEB_OWNER_PASSWORD`: Owner dashboard password (24+ characters), when serving the web app

### Optional APIs

- `HOME_FINDER_TRAVELTIME_APP_ID`: TravelTime API app ID (enables advisory commute estimates)
- `HOME_FINDER_TRAVELTIME_API_KEY`: TravelTime API key
- `HOME_FINDER_ANTHROPIC_API_KEY`: Anthropic API key (enables AI quality analysis)

### Feature Flags

- `HOME_FINDER_ENABLE_QUALITY_FILTER`: Enable Claude vision property analysis (default: true)
- `HOME_FINDER_REQUIRE_FLOORPLAN`: Drop properties without floorplans (default: true)
- `HOME_FINDER_QUALITY_FILTER_MAX_IMAGES`: Max gallery images to analyze per property (default: 20, max: 20)
- `HOME_FINDER_MIN_GALLERY_FOR_PHOTO_INFERENCE`: Minimum gallery images to bypass floorplan gate via photo inference; 0 disables (default: 5)
- `HOME_FINDER_ENABLE_EXTENDED_THINKING`: Enable extended thinking for deeper quality analysis (default: true)
- `HOME_FINDER_ENABLE_IMAGE_HASH_MATCHING`: Enable perceptual image hashing for deduplication (default: true)
- `HOME_FINDER_MAX_ENRICHMENT_ATTEMPTS`: Max enrichment retry attempts before giving up on a property, 1-10 (default: 3)

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
3. Enables two-phase AI property quality analysis using Claude vision

The retained February 13–28, 2026 snapshot contains 15 metered runs with 200 counted analyses and $32.1247557 of estimated model cost: approximately $0.1606 per counted analysis in that cohort. This is incomplete historical telemetry, not an invoice, a cost per unique home, or a future price guarantee. It excludes unrecorded calls and other service costs. See the [production-data review](docs/reviews/production-data-2026-09-27.md) and the planned complete spend ledger in PR13.

The SDK is locked to Anthropic 1.8.0 with its native `httpx2` transport. The migration preserves the Claude Sonnet 4.6 model, prompts and request JSON for both analysis phases; native transport tests cover retries, timeouts, cancellation and cache accounting. See the [PR20a compatibility record](docs/plans/2026-09-28-pr20a-sdk-transport.md). This does not change API pricing or establish a new model-quality result.

`HOME_FINDER_ANALYSIS_CONFIG` optionally supplies versioned JSON settings for each analysis phase. Omitted fields retain the baseline defaults; supplying this setting takes precedence over `HOME_FINDER_ENABLE_EXTENDED_THINKING`. New assessments record their effective settings, SDK and contract hashes. Only the currently priced Sonnet 4.6 model is admitted until the spend ledger and model evaluation qualify alternatives. See the [settings, defaults and provenance contract](docs/plans/2026-09-28-pr20b-analysis-configuration.md).

## Running Tests

```bash
uv run pytest                         # Default suite; excludes slow and browser, blocks network sockets
uv run pytest tests/test_models.py    # Run specific test file
uv run pytest -k "test_openrent"      # Run tests matching pattern
uv run pytest --cov=src               # Run with coverage
uv run pytest -m slow                 # Opt-in live portal tests; qualify source access first
uv run playwright install chromium  # Install browser for dashboard tests
uv run pytest -m 'browser and not slow' # Dashboard browser tests; excludes live portal sort tests
```

Tests have a 30-second default timeout; slow-marked tests receive 120 seconds. The default coverage gate is 75%, including branches.

### CI and runtime baseline

CI retains separate Ruff/format, strict mypy and default test jobs. A separate build job installs `uv.lock` with no package cache and builds the wheel and source distribution. The source archive explicitly includes application code, tests, README, licence and dependency files, keeping unrelated local logs and research out. Coverage XML and distributions are downloadable workflow artifacts; CI no longer needs permission to post pull-request comments. Pull requests, pushes and scheduled CI have no deployment job.

The supported interpreter range is Python 3.11–3.14 (`>=3.11,<3.15`); CI tests 3.11.16 and 3.14.7. Local development and both container targets select 3.14.7 with uv 0.12.19. The shared Dockerfile uses uv's checksum-verified managed Python build because the official Python/bookworm image still links SQLite 3.40.1 without the WAL reset fix. The selected build reports SQLite 3.53.1 and OpenSSL 3.5.8; the container build rejects SQLite below 3.51.3. New runtime patches still require test/audit review; a Python version alone does not establish its linked SQLite version.

CI also builds the dashboard and collector container targets and runs a dependency advisory audit with pip-audit 2.10.1. Security floors cover current affected dependencies. Crawlee's existing 1.3.2b4 wheel is temporarily pinned by its original URL/hash because that prerelease disappeared from the index; transport qualification/replacement remains PR11. Its known sitemap SSRF advisory is deferred because this application does not invoke sitemap loading, and collection remains gated. Container scanning also identifies residual Debian advisories; see the [PR03 audit record](docs/plans/2026-09-28-pr03-runtime-and-maps.md). Anthropic uses the verified native SDK 1.8.0 transport; explicit phase contracts remain in PR20. Do not infer live source compatibility or complete vulnerability coverage from one scanner.

Weekly CI includes dashboard browser tests, excluding tests marked `slow`. Manual CI has separate `run_browser_tests` and `run_live_tests` switches, both off by default. Live portal tests require explicit opt-in and qualified source access. The remote CI workflow was `disabled_inactivity` when inspected on September 28, 2026; changing local YAML does not re-enable it. Re-enable CI after the release split is reviewed and merged, then confirm a hosted run.

## Architecture

### Pipeline Flow

The full pipeline (`uv run home-finder`) executes these steps in order:

1. **Retry Unsent** — Resend failed notifications from previous runs
2. **Scrape and record source listings** — Configured adapters search selected areas; persist the listings returned by the scraping stage
3. **Criteria and location filters** — Apply price/bedroom criteria and validate search-area postcodes
4. **Wrap and detect price changes** — Wrap single-source `MergedProperty` objects; detect price changes on known properties
5. **Select new properties and enrichment retries** — Listings already linked to a canonical record are considered seen; load eligible enrichment retries
6. **Detail enrichment** — Fetch gallery, floorplans, descriptions and features; retain text by source, cache and classify images, then generate thumbnails
7. **Geocode and compute commute** — Runs after enrichment so detail coordinates are available; compares seconds to the limit, rounds displayed minutes up, and retains all homes. Measured over-limit journeys, bounded-search unreachable results, missing inputs and provider errors remain distinct. The within-limit count is measured matches, not a funnel/admission count.
8. **Post-enrichment deduplication and floorplan gate** — Merge within/across runs using enriched evidence; apply floorplan exemptions and configured photo fallback
9. **Analyze, save and notify** — Phase 1 observes images and listing text using a tool response; phase 2 evaluates phase 1 output and listing text using native JSON schema `output_config`. Save results and deliver notifications in live mode
10. **Drain reanalysis and price-drop work** — Process queued reanalysis, then send pending price-drop notifications in live mode; finish run accounting

This describes current code in `main.py` and `pipeline/stages.py`, including its limitations. Early returns before analysis can skip later recovery, price-drop delivery and accounting work (PR09a/PR12a). Source descriptions/features now survive save, reload and sparse refreshes; both model phases receive bounded, source-labelled inputs, recorded with each completed analysis. Missing text preserves prior evidence, while dated corrections can explicitly clear it. Historical missing inputs are not reconstructed from model summaries. Full immutable source revisions and replay remain PR06b/14.

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
│   ├── rightmove.py       # Rightmove (crawlee + typeahead API)
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
│   └── telegram.py        # Rich Telegram notifications with quality cards
├── db/
│   └── storage.py         # SQLite storage with notification tracking
├── utils/
│   ├── address.py         # Address normalization for deduplication
│   ├── image_cache.py     # Disk-based image caching for gallery/floorplan images
│   ├── image_hash.py      # Perceptual image hashing
│   ├── epc_detector.py    # EPC chart detection (PIL heuristics)
│   └── floorplan_detector.py  # Floorplan detection (PIL heuristics)
├── models.py              # Pydantic models (Property, MergedProperty, SOURCE_NAMES, etc.)
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

### Architecture

The deployment is split into two parts:

- **Fly.io** hosts the **web dashboard only** (`--serve --no-pipeline`). It serves the property database and cached images but does not run scrapers.
- **Pipeline runs locally** on your machine (cron, systemd timer, or manual `uv run home-finder`). It scrapes, filters, enriches, analyzes, and writes to the local SQLite DB + image cache.
- **Legacy `scripts/sync-to-fly.sh`** replaces Fly's database and syncs images. It is not safe for preserving dashboard-written history; replacement is pending the authority/restore work in PR05.

### Manual release

Production deployment is a separate **Release dashboard** workflow (`.github/workflows/release.yml`), invoked manually against `main`. It calls the same CI workflow at that revision, with no secrets passed to validation, and deploys only if validation succeeds. Only the deployment step maps the environment secret `FLY_PRODUCTION_API_TOKEN` to Fly's `FLY_API_TOKEN`. The Fly CLI binary is version-pinned and checked against its upstream SHA-256 digest.

Before activating the revised workflows, configure a GitHub environment named `production` with deployment branches restricted to `main` and an app-scoped `FLY_PRODUCTION_API_TOKEN` secret. Retire the old repository-wide `FLY_API_TOKEN`; a repository secret can be referenced by edited workflows in same-repository PRs even if the current CI file never uses it. On September 28 the old repository secret existed and the required environment was not configured. PR01 makes the workflow changes locally; it does not claim that this credential migration or branch policy is already in place. The new release deliberately has no fallback to the old secret.

After the access/release prerequisites are met, select **Actions → Release dashboard → Run workflow → main**. This releases the `dashboard` target of the shared `Dockerfile`; it does not enable pipeline collection. Ordinary CI, Dependabot updates and merging repair PRs have no automatic deployment step. Keep a previously verified Fly image/release with a compatible database for operational rollback; do not restore automatic push deployment as a rollback shortcut.

### Fly.io setup

The existing configuration targets London (`lhr`). These are provisioning commands, not the procedure for restarting collection; establish private access and a consistent backup before releasing:

```bash
fly apps create home-finder
fly volumes create home_finder_data --region lhr --size 1
fly secrets set \
  HOME_FINDER_TELEGRAM_BOT_TOKEN=xxx \
  HOME_FINDER_TELEGRAM_CHAT_ID=xxx \
  HOME_FINDER_WEB_OWNER_PASSWORD=your-random-24-or-more-character-password \
  HOME_FINDER_ANTHROPIC_API_KEY=xxx \
  HOME_FINDER_TELEGRAM_WEBHOOK_SECRET=xxx
# Optional:
# fly secrets set HOME_FINDER_TRAVELTIME_APP_ID=xxx HOME_FINDER_TRAVELTIME_API_KEY=xxx
# fly secrets set HOME_FINDER_PROXY_URL=socks5://user:pass@host:port
# Use the manual Release dashboard workflow after validation and access setup.
```

The `fly.toml` includes a health check on `/health` with a 30-second grace period. It explicitly builds the `dashboard` target, which runs `home-finder --serve --no-pipeline`.

### Syncing data to Fly.io

The legacy sync script checkpoints the local WAL, removes the remote DB/WAL/SHM files, uploads a replacement database, syncs images, restarts the machine and checks health. Both its normal and `--full` modes replace the database; “incremental” only describes image transfer. This can overwrite status/history written on the dashboard and is not a consistent backup of the live remote database. Keep it inactive while PR05 establishes one authority and proves backup/restore. Do not point application startup at a frozen research export.

### Docker (local)

```bash
docker build --target dashboard -t home-finder .
docker run --env-file .env -p 127.0.0.1:8000:8000 -v ./data:/app/data home-finder
```

The `collector` target uses the same runtime and locked packages, with the CLI as its command. It does not install a browser or qualify live scraping; retain the collection gates below. Both targets run as UID 1000, so the mounted data directory must be writable by that user. The default target is the dashboard.

### Local pipeline scheduling

Use **one scheduler** on the chosen local SQLite authority: the systemd timer below, or the serve loop. Both use a completion-relative delay of at least 55 minutes plus up to five minutes of jitter by default. Do not enable both. Keep scheduled collection and paid processing inactive until the delivery plan's source, authority and spend gates pass; implementing run exclusion does not activate collection.

The advisory lock uses Unix `flock` on a local filesystem. Relative paths and symlinks resolve to the same authority; hard-linked database aliases are rejected. The lock covers participating application entry points, including standalone reanalysis and maintenance. It is not a distributed lease and cannot coordinate independent database copies, network filesystems or scripts that bypass the application. Keep `<database>.runner.lock` in place; process exit/crash releases its descriptor lock. Deleting a live lock file can defeat exclusion. Dashboard mutations continue under the existing SQLite transaction ownership rules.

**systemd timer:**

Create `/etc/systemd/system/home-finder.service`:

```ini
[Unit]
Description=Home Finder Property Scraper
After=network.target

[Service]
Type=oneshot
WorkingDirectory=/path/to/home-finder
ExecStart=/path/to/uv run home-finder
User=your-user
TimeoutStartSec=0
```

Create `/etc/systemd/system/home-finder.timer`:

```ini
[Unit]
Description=Run Home Finder after its previous run finishes

[Timer]
OnBootSec=1min
OnUnitInactiveSec=55min
RandomizedDelaySec=5min
AccuracySec=1s

[Install]
WantedBy=timers.target
```

After the collection gates pass, a reviewed timer can be enabled with:

```bash
sudo systemctl enable --now home-finder.timer
```

The repository's `crontab` is intentionally inactive. Remove any previously installed `*/55` entry when selecting this timer: it ran at :00 and :55, giving alternating 55- and 5-minute gaps. No scheduler or service is installed/enabled by these repository changes. To stop new scheduled work, stop the selected timer or use `--no-pipeline`; do not delete pending work or the lock file. Versioned active/paused/dormant search episodes remain PR12b.

## License

MIT

# Home Finder

![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)
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
- **Commute information**: TravelTime estimates against a configured destination and limit; advisory/error semantics need the PR16 repair described in the delivery plan
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

## Web Dashboard

The `--serve` flag starts a FastAPI web server on port 8000. Unless `--no-pipeline` is supplied, it starts a background pipeline after an initial delay, then waits the configured interval (55 minutes by default) plus or minus five minutes **after each run finishes**. This is not a fixed 55-minute start-to-start schedule. The lock covers only this process; it does not exclude another CLI/cron process. Fly uses `--serve --no-pipeline`.

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
| `HOME_FINDER_PIPELINE_INTERVAL_MINUTES` | `55` | Delay after a serve-mode pipeline run, with ±5 minutes of jitter |

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

- `HOME_FINDER_TRAVELTIME_APP_ID`: TravelTime API app ID (enables commute filtering)
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

The SDK remains locked to Anthropic 0.81.0, with a temporary `>=0.81.0,<1` constraint so routine dependency resolution does not perform the separate PR20 SDK/transport migration. This does not change the model, prompts or API pricing.

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

The workflow uses Python 3.14 and uv 0.9.29 (the existing Docker build tool). Package metadata still advertises Python 3.11+, and the generic `Dockerfile` uses 3.11, while `Dockerfile.fly` and CI use 3.14. That is a declared/tested-runtime mismatch, not proof that all versions work. PR03 will align the supported versions and patch the runtime; until then, use Python 3.14 for the measured baseline.

Weekly CI includes dashboard browser tests, excluding tests marked `slow`. Manual CI has separate `run_browser_tests` and `run_live_tests` switches, both off by default. Live portal tests require explicit opt-in and qualified source access. The remote CI workflow was `disabled_inactivity` when inspected on September 28, 2026; changing local YAML does not re-enable it. Re-enable CI after the release split is reviewed and merged, then confirm a hosted run.

## Architecture

### Pipeline Flow

The full pipeline (`uv run home-finder`) executes these steps in order:

1. **Retry Unsent** — Resend failed notifications from previous runs
2. **Scrape and record source listings** — Configured adapters search selected areas; persist the listings returned by the scraping stage
3. **Criteria and location filters** — Apply price/bedroom criteria and validate search-area postcodes
4. **Wrap and detect price changes** — Wrap single-source `MergedProperty` objects; detect price changes on known properties
5. **Select new properties and enrichment retries** — Listings already linked to a canonical record are considered seen; load eligible enrichment retries
6. **Detail enrichment** — Fetch gallery, floorplans and descriptions; cache and classify images, then generate thumbnails
7. **Geocode and compute commute** — Runs after enrichment so detail coordinates are available; current mixed reachable/unreachable results are advisory, but an all-unreachable batch can still abort
8. **Post-enrichment deduplication and floorplan gate** — Merge within/across runs using enriched evidence; apply floorplan exemptions and configured photo fallback
9. **Analyze, save and notify** — Phase 1 observes images and listing text using a tool response; phase 2 evaluates phase 1 output and listing text using native JSON schema `output_config`. Save results and deliver notifications in live mode
10. **Drain reanalysis and price-drop work** — Process queued reanalysis, then send pending price-drop notifications in live mode; finish run accounting

This describes current code in `main.py` and `pipeline/stages.py`, including its limitations. Early returns before analysis can skip later recovery, price-drop delivery and accounting work (PR09a/PR12a). Description/feature propagation needs PR06a; a fetch succeeding does not prove that its text reaches the model.

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

After the access/release prerequisites are met, select **Actions → Release dashboard → Run workflow → main**. This releases the dashboard configured in `Dockerfile.fly`; it does not enable pipeline collection. Ordinary CI, Dependabot updates and merging repair PRs have no automatic deployment step. Keep a previously verified Fly image/release for operational rollback; do not restore automatic push deployment as a rollback shortcut.

### Fly.io setup

The existing configuration targets London (`lhr`). These are provisioning commands, not the procedure for restarting collection; establish private access and a consistent backup before releasing:

```bash
fly apps create home-finder
fly volumes create home_finder_data --region lhr --size 1
fly secrets set \
  HOME_FINDER_TELEGRAM_BOT_TOKEN=xxx \
  HOME_FINDER_TELEGRAM_CHAT_ID=xxx \
  HOME_FINDER_ANTHROPIC_API_KEY=xxx \
  HOME_FINDER_TELEGRAM_WEBHOOK_SECRET=xxx
# Optional:
# fly secrets set HOME_FINDER_TRAVELTIME_APP_ID=xxx HOME_FINDER_TRAVELTIME_API_KEY=xxx
# fly secrets set HOME_FINDER_PROXY_URL=socks5://user:pass@host:port
# Use the manual Release dashboard workflow after validation and access setup.
```

The `fly.toml` includes a health check on `/health` with a 30-second grace period. The `Dockerfile.fly` runs `home-finder --serve --no-pipeline` — dashboard only, no scraping.

### Syncing data to Fly.io

The legacy sync script checkpoints the local WAL, removes the remote DB/WAL/SHM files, uploads a replacement database, syncs images, restarts the machine and checks health. Both its normal and `--full` modes replace the database; “incremental” only describes image transfer. This can overwrite status/history written on the dashboard and is not a consistent backup of the live remote database. Keep it inactive while PR05 establishes one authority and proves backup/restore. Do not point application startup at a frozen research export.

### Docker (local)

```bash
docker build -f Dockerfile.fly -t home-finder .
docker run --env-file .env -p 8000:8000 -v ./data:/app/data home-finder
```

### Local pipeline (cron/systemd)

The following are historical deployment examples, kept to explain existing scheduling. Keep scheduled collection disabled during the repair series. PR12a must establish a reliable single runner and exclusion across CLI/cron processes before resumption.

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

[Install]
WantedBy=multi-user.target
```

Create `/etc/systemd/system/home-finder.timer`:

```ini
[Unit]
Description=Run Home Finder every 55 minutes

[Timer]
OnBootSec=1min
OnUnitActiveSec=55min

[Install]
WantedBy=timers.target
```

After the collection gates pass, a reviewed timer can be enabled with:

```bash
sudo systemctl enable --now home-finder.timer
```

**Existing cron example (not a 55-minute interval):**

```bash
*/55 * * * * cd /path/to/home-finder && /path/to/uv run home-finder >> /var/log/home-finder.log 2>&1
```

This runs at minute 00 and 55 of every hour, producing alternating 55-minute and 5-minute gaps. It has no cross-process overlap protection. The systemd example measures from activation (`OnUnitActiveSec`); the serve-mode loop measures from completion. Their semantics differ; PR12a will replace the ambiguous scheduling guidance alongside the implementation.

## License

MIT

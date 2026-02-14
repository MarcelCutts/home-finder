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
- **Commute filtering**: Filter properties within X minutes of your destination using TravelTime API
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
   uv sync --all-extras
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

   # Web dashboard with background pipeline
   uv run home-finder --serve
   ```

### Run Modes

```bash
uv run home-finder                      # Full pipeline with Telegram notifications
uv run home-finder --dry-run            # Full pipeline, save to DB but no notifications
uv run home-finder --scrape-only        # Just scrape and print (no filtering/storage)
uv run home-finder --max-per-scraper 5  # Limit results per scraper (for testing)
uv run home-finder --serve              # Web dashboard + recurring pipeline scheduler
uv run home-finder --debug              # Enable debug-level logging
```

## Web Dashboard

The `--serve` flag starts a FastAPI web server on port 8000 with a background pipeline scheduler that runs every 55 minutes (configurable via `HOME_FINDER_PIPELINE_INTERVAL_MINUTES`).

### Dashboard Features

- **Property grid**: Filterable, sortable card grid with pagination
- **HTMX live filtering**: Filters update without full-page reload (progressive enhancement — works without JS too)
- **Map view**: Toggle between grid and map. MarkerCluster groups nearby properties; markers are color-coded by quality rating (green 4-5, amber 3, red 1-2, grey unrated)
- **Property detail pages**: Full gallery with lightbox (keyboard + touch/swipe navigation), floorplan, quality analysis breakdown, area context (rental benchmarks, council tax, crime rates, rent trends), and source links
- **Quality summary on cards**: Condition severity badge and one-line AI summary on each card
- **Telegram integration**: When `HOME_FINDER_WEB_BASE_URL` is set, Telegram notifications include a "Details" button linking to the web dashboard

### Dashboard Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `HOME_FINDER_WEB_BASE_URL` | `""` | Public URL for the dashboard (enables Telegram deep links) |
| `HOME_FINDER_WEB_PORT` | `8000` | Web server port |
| `HOME_FINDER_WEB_HOST` | `0.0.0.0` | Web server bind address |
| `HOME_FINDER_PIPELINE_INTERVAL_MINUTES` | `55` | Minutes between pipeline runs in serve mode |

### Security

The web dashboard is read-only (no forms that mutate state). Security measures include:

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
| `GET /health` | Health check (`{"status": "ok"}`) |
| `GET /static/...` | Static assets (CSS, JS) |

## Configuration

Create a `.env` file with these settings (all use `HOME_FINDER_` prefix):

### Required

- `HOME_FINDER_TELEGRAM_BOT_TOKEN`: Your Telegram bot token (from @BotFather)
- `HOME_FINDER_TELEGRAM_CHAT_ID`: Your Telegram chat ID

### Optional APIs

- `HOME_FINDER_TRAVELTIME_APP_ID`: TravelTime API app ID (enables commute filtering)
- `HOME_FINDER_TRAVELTIME_API_KEY`: TravelTime API key
- `HOME_FINDER_ANTHROPIC_API_KEY`: Anthropic API key (enables AI quality analysis)

### Feature Flags

- `HOME_FINDER_ENABLE_QUALITY_FILTER`: Enable Claude vision property analysis (default: true)
- `HOME_FINDER_REQUIRE_FLOORPLAN`: Drop properties without floorplans (default: true)
- `HOME_FINDER_QUALITY_FILTER_MAX_IMAGES`: Max gallery images to analyze per property (default: 10, max: 20)
- `HOME_FINDER_ENABLE_IMAGE_HASH_MATCHING`: Enable perceptual image hashing for deduplication (default: false)

### Search Criteria

- `HOME_FINDER_MIN_PRICE`: Minimum monthly rent (default: 1800)
- `HOME_FINDER_MAX_PRICE`: Maximum monthly rent (default: 2200)
- `HOME_FINDER_MIN_BEDROOMS`: Minimum bedrooms (default: 1)
- `HOME_FINDER_MAX_BEDROOMS`: Maximum bedrooms (default: 2)
- `HOME_FINDER_DESTINATION_POSTCODE`: Your destination postcode (default: N1 5AA)
- `HOME_FINDER_MAX_COMMUTE_MINUTES`: Maximum commute time in minutes (default: 30)
- `HOME_FINDER_SEARCH_AREAS`: Comma-separated outcodes/boroughs (default: e3,e5,e9,e10,e15,e17,n15,n16,n17)

### Scraper Filters

- `HOME_FINDER_FURNISH_TYPES`: Comma-separated furnishing filter (default: unfurnished,part_furnished)
- `HOME_FINDER_MIN_BATHROOMS`: Minimum bathrooms (default: 1)
- `HOME_FINDER_INCLUDE_LET_AGREED`: Include already-let properties (default: false)

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
6. **New Property Filter** — Check SQLite DB to only process unseen properties
7. **Commute Filter** — TravelTime API filters by travel time (if configured)
8. **Detail Enrichment** — Fetch gallery images, floorplans, descriptions; cache images to disk
9. **Post-Enrichment Dedup** — Cross-platform deduplication using enriched data (images, postcodes, coordinates)
10. **Floorplan Gate** — Drop properties without floorplans (if enabled)
11. **Quality Analysis** — Two-phase Claude vision analysis: Phase 1 (visual observations from images), Phase 2 (evaluation using Phase 1 output + listing text)
12. **Save & Notify** — Store in DB, send Telegram notifications

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
│   │   └── _quality_card.html   # Quality analysis breakdown component
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
│   └── image_hash.py      # Perceptual image hashing
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

### Fly.io (recommended)

Deploys to London (`lhr`) for UK IP. The `--serve` flag runs the web dashboard with a background pipeline scheduler:

```bash
fly apps create home-finder
fly volumes create home_finder_data --region lhr --size 1
fly secrets set HOME_FINDER_TELEGRAM_BOT_TOKEN=xxx HOME_FINDER_TELEGRAM_CHAT_ID=xxx ...
fly deploy
```

The `fly.toml` includes a health check on `/health` with a 30-second grace period (matching the pipeline initial delay).

### Docker (local)

```bash
docker build -f Dockerfile.fly -t home-finder .
docker run --env-file .env -p 8000:8000 -v ./data:/app/data home-finder
```

### One-shot mode (cron/systemd)

For running the pipeline without the web server:

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

Enable:

```bash
sudo systemctl enable --now home-finder.timer
```

**cron:**

```bash
*/55 * * * * cd /path/to/home-finder && /path/to/uv run home-finder >> /var/log/home-finder.log 2>&1
```

## License

MIT

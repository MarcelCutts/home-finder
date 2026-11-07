# Home Finder

![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)
![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)
![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)

Multi-platform London rental property scraper with commute guidance, AI quality analysis, web dashboard, and Telegram notifications.

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
- **Cross-platform deduplication**: Evidence scoring matches listings after enrichment; consolidation preserves stored property identity and history
- **Commute guidance**: TravelTime measurements distinguish reachable, outside-limit and unknown outcomes; API failures never become zero-minute commutes
- **AI quality analysis**: Two-phase Claude vision analysis — Phase 1 observes images (kitchen, condition, space, light), Phase 2 evaluates value, generates viewing notes, and curates highlights
- **Web dashboard**: FastAPI-powered dashboard with HTMX live filtering, map view with MarkerCluster, property detail pages with area context, and lightbox gallery
- **Rich Telegram notifications**: Property cards with photos, fit scores, commute times, quality summaries, and direct links (including web dashboard deep links)
- **Detail enrichment**: Fetches gallery images, floorplans, and descriptions from property detail pages
- **Disk-based image caching**: Images cached locally for reliable display in dashboard and quality analysis
- **Proxy support**: HTTP/SOCKS5 proxy for accessing geo-restricted sites from outside the UK
- **SQLite storage**: Track seen properties to only notify about new listings; alerts held by Telegram's rate limit resume automatically, while blocked or uncertain ones wait for an operator

## Quick Start

1. **Install dependencies**:

   ```bash
   uv sync --locked --all-extras
   ```

2. **Configure environment**:

   ```bash
   cp .env.example .env
   chmod 600 .env
   # Set an owner password for the dashboard; add only the integrations you need.
   uv run home-finder --check-config
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
uv run home-finder --check-config       # Offline settings/prerequisite check
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
uv run home-finder --dedup-existing     # Fold unused staging rows into matching stored homes; never merges established homes
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
- **Map view**: Toggle between grid and map. MarkerCluster groups nearby properties; markers are price pills in ink and greys (off-market homes faded), and each popup shows the fit tier
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
| `HOME_FINDER_CARTO_BASEMAP_API_KEY` | `""` | Browser-visible, origin-restricted tile key; maps are unavailable without it |
| `HOME_FINDER_PIPELINE_INTERVAL_MINUTES` | `55` | Delay after completion plus 0–5 minutes jitter; only with `--serve --with-pipeline` |

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

Use [`.env.example`](.env.example) as the complete annotated reference. All 45
application settings have defaults; configure only your preferences and the
features you use. Advanced queue, retry and model tuning can stay commented out.

| Feature | Configuration needed |
|---|---|
| Owner dashboard | `HOME_FINDER_WEB_OWNER_PASSWORD` (24+ ASCII characters); canonical HTTPS `HOME_FINDER_WEB_BASE_URL` when hosted |
| Map tiles | `HOME_FINDER_CARTO_BASEMAP_API_KEY`; this browser-visible key must be restricted to your origins |
| Telegram delivery | `HOME_FINDER_TELEGRAM_BOT_TOKEN` and nonzero numeric `HOME_FINDER_TELEGRAM_CHAT_ID` |
| Telegram callbacks | Delivery settings, HTTPS base URL and `HOME_FINDER_TELEGRAM_WEBHOOK_SECRET`; groups also need `HOME_FINDER_TELEGRAM_OWNER_USER_ID` |
| Advisory commute measurements | Both `HOME_FINDER_TRAVELTIME_APP_ID` and `HOME_FINDER_TRAVELTIME_API_KEY` |
| Paid assessments | `HOME_FINDER_ANTHROPIC_API_KEY`, enabled `HOME_FINDER_ENABLE_QUALITY_FILTER`, and explicit `HOME_FINDER_ANALYSIS_SPEND_POLICY` caps |

Discovery alerts require a rated assessment meeting `HOME_FINDER_NOTIFICATION_POLICY`.
A Telegram token alone does not make unassessed homes eligible. An Anthropic key
without spend caps leaves paid analysis deferred. See [operations](docs/operations.md)
for the spending, delivery and activation contracts.

```bash
uv run home-finder --check-config
uv run home-finder --env-file /absolute/path/to/.env --check-config
```

The check parses settings and reports feature prerequisites without opening a
database, registering a webhook, scraping, sending messages or spending money.
Exit 0 means the configuration is valid, **not** that every optional feature is
configured or any credential works remotely. Invalid settings or malformed dotenv
syntax exit 1. Syntax errors identify the file and line without printing values.
CLI usage errors or missing prerequisites for the selected run mode exit 2. `--env-file`
also works with the normal run modes. The normal notifying pipeline and
`--serve --with-pipeline` require both Telegram credentials before starting work.
Use `--dry-run` to collect without sending (it can still spend on configured
analysis), `--scrape-only` to inspect source results, or `--serve` for just the dashboard.

Settings load from the current directory's `.env`; parent directories and other
Git worktrees are not searched. Exported `HOME_FINDER_` variables override the file,
and omitted values use code defaults. Programmatic `Settings(...)` arguments take
precedence over both. Relative database paths still resolve from the working
directory, not from the selected env file. Use an absolute database path when needed.

Keep one assignment per name: dotenv uses the last duplicate. Names are case
insensitive. Unknown `HOME_FINDER_` names fail at startup; unrelated tools' keys
are ignored. Empty strings are explicit values: comment out an optional numeric
or JSON setting to restore its default. Boolean settings accept `true`/`false`;
policies use JSON with double-quoted keys and exact decimal strings for USD caps.
Do not `source .env`: it is dotenv data, and unquoted JSON/postcodes are not shell
assignments. The example's single-line `KEY=value` format also works with
`docker run --env-file .env`; avoid shell-style outer quotes or interpolation when
sharing a file with Docker.

`HOME_FINDER_MAX_ANALYSIS_PER_RUN` remains a compatibility fallback for **each**
unset initial/reanalysis cap. Prefer the two explicit queue settings.
`HOME_FINDER_SCRAPE_INTERVAL_MINUTES` is unsupported; recurring serve-mode runs
use `HOME_FINDER_PIPELINE_INTERVAL_MINUTES` only with `--serve --with-pipeline`.
`HOME_FINDER_BRIEF_MUST_HAVES` controls the dashboard brief, not assessment weights
or alert eligibility. Floorplan admission also accepts exempt sources or enough
photos; `HOME_FINDER_REQUIRE_FLOORPLAN` does not mean every home needs a floorplan.

**Source-filter limitations:** `FURNISH_TYPES`, `MIN_BATHROOMS` and
`INCLUDE_LET_AGREED` are search hints, not uniform local constraints. Rightmove
forwards all three. Zoopla deliberately omits all three, and has no local fallback
enforcement. OpenRent forwards browser filter state, but its server response is
not filtered locally for these fields. OnTheMarket forwards let-agreed and a
single furnishing value, but ignores bathroom count and multiple furnishing
choices. Price and bedroom limits are checked locally after scraping. The current
property model does not retain normalized furnishing/bathroom values for universal
filtering. These limitations require a separate evidence/model change to fix;
setting extra environment variables cannot make them guaranteed constraints.

The runtime uses the prefixed Anthropic key. Unprefixed `ANTHROPIC_API_KEY` is used
by the separate layout experiment and opt-in live evaluation tests; OpenAI,
Gemini, Typesafe and TfL keys are not read by the application. Fly secrets are
injected as process variables, independently of your local `.env`. The release
workflow's `FLY_PRODUCTION_API_TOKEN` belongs in GitHub's production environment.

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
3. Configure an explicit `HOME_FINDER_ANALYSIS_SPEND_POLICY` as described in the
   [operations guide](docs/operations.md). Paid analysis remains deferred without
   that policy; actual charges depend on the configured models and evidence.

## Running Tests

See the [test reading guide](tests/README.md) for suite boundaries, fixtures, and naming conventions.

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

Start with [`pipeline/flow.py`](src/home_finder/pipeline/flow.py), then follow each
stage into its domain implementation. The full workflow runs in this order:

1. **Set up and recover** — Acquire the run lock, reconcile abandoned spend, create
   the run record, repair image caches, and resume eligible queued deliveries.
2. **Collect and retain** — Scrape configured sources and persist observations
   before selecting candidates.
3. **Select and admit enrichment** — Apply criteria and location rules, refresh
   known prices, and interleave discovery with pending enrichment under its cap.
4. **Enrich and assess commutes** — Acquire detail/media evidence, then measure
   commutes using the available coordinates. Commute results are advisory.
5. **Consolidate and admit assessment** — Match against stored anchors, preserve
   established identity/history, and apply floorplan/photo admission. Source
   exemptions and sufficient gallery photos can satisfy that policy.
6. **Process initial assessments** — Persist candidates before provider work,
   interleave them with pending assessments, save results and notification
   decisions, then attempt immediate delivery when eligible.
7. **Process reanalysis** — Refresh requested assessments through the same queue
   processor used by `--reanalyze`, preserving existing notification status.
8. **Drain and finalize** — Attempt remaining queued deliveries, flush diagnostics,
   clean up resources, and finalize the run record.

Empty discovery still allows recovery and pending queues to advance. Dry runs
follow the same data-processing sequence and skip notification transport.
The [pipeline guide](docs/pipeline.md) explains the contracts, terminology,
queue limits, reading order, and metric definitions.

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

A score-based match requires **60+ points** from **2+ signals**. Coordinates and
price use graduated scoring. Additional postcode, image-evidence, grouping, and
persistence checks can refuse a merge. Confidence tiers describe a heuristic;
they are not calibrated probabilities. See
[`matching/`](src/home_finder/matching/) for scoring and grouping, and
[`pipeline/matching.py`](src/home_finder/pipeline/matching.py) for consolidation
against stored anchors.

## Project Structure

```text
src/home_finder/
├── main.py                # CLI parsing and dispatch
├── pipeline/              # Workflow sequencing and durable queue coordination
│   ├── flow.py            # Full run lifecycle and execution order
│   ├── contracts.py       # Stage results, partial progress, outcome summaries
│   ├── collection.py      # Scraper coordination and observation retention
│   ├── candidates.py      # Selection, enrichment admission, commute, media admission
│   ├── matching.py        # Coordinate matching and persistent consolidation
│   ├── assessment.py      # Initial assessment and shared execution helpers
│   ├── reanalysis.py      # Requested reassessment and CLI entry point
│   ├── image_recovery.py  # Restore analysis images from stored URLs
│   ├── run_lock.py        # Single-host run exclusion and spend recovery
│   └── commands.py        # Maintenance entry points
├── scrapers/              # Source adapters and detail-page fetching
├── enrichment/            # Detail/media augmentation and advisory commute assessment
├── matching/              # Match scoring and aggregate construction
├── analysis/              # Provider analysis, prompts, evidence and response contracts
├── filters/               # Criteria, location, media admission, fit and alert selection
├── models/                # Domain, analysis, spending, and delivery contracts
├── db/
│   ├── storage.py         # Connection/transaction ownership and domain storage
│   ├── processing_repo.py # Enrichment/assessment transitions and recovery queues
│   ├── runs.py            # Run metrics and diagnostic events
│   ├── spend.py           # Paid-attempt accounting and visual checkpoints
│   └── delivery.py        # Durable notification intent and receipt state
├── notifiers/             # Message rendering, transport, delivery recovery
├── web/                   # Routes, templates, and static assets
├── utils/                 # Address, image cache, and other supporting operations
└── config.py              # Typed configuration
```

Tests live under `tests/`, with subsystem suites, complete workflow scenarios in
`integration/`, and saved provider responses in `fixtures/`.

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

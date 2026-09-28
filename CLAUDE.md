# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Why This Exists

Home Finder began as Marcel's personal rental search in London. The confirmed target is a small hosted service for tens to hundreds of users, introduced gradually after reliability and access-control repairs. Current implementation and stored preferences still belong to one owner's search; do not treat them as a multi-user design.

Follow the execution queue in [the delivery plan](docs/plans/2026-09-28-research-synthesis-and-delivery-plan.md), including its operational and rollout gates. Keep normal production collection paused. After reviewing PR01, the user instructed agents to commit each completed segment and continue through the tasks using goals. This supersedes the earlier pause after every segment: verify, document and commit each step, then continue to the next eligible item. External rollout, source-access and elapsed-evidence gates still apply.

Marcel runs a small remote software consultancy (WFH full-time) and music events. Key preferences that should inform quality analysis, scoring, and UX decisions:

- **Space layout:** 2-bed (one as office) ideal, or 1-bed with a large common area. Needs separation between sleep and work, plus room to host people for music/social events.
- **Sound & neighbours:** Good sound insulation, artist-friendly/warehouse areas are a plus — wants to host without being antisocial.
- **Practical needs:** High-speed internet, gas or induction hob (likes to cook).
- **Vibe:** Creative/cool spaces appreciated (lofts, conversions, interesting architecture) but subjective.
- **Reality:** All preferences, not dealbreakers. The London rental market is brutal — compromise expected.

## Commands

```bash
# Install dependencies (includes dev deps)
uv sync --locked --all-extras

# Run the application
uv run home-finder                      # Full pipeline with Telegram notifications
uv run home-finder --dry-run            # Full pipeline, save to DB but no notifications
uv run home-finder --scrape-only        # Just scrape and print (no filtering/storage)
uv run home-finder --max-per-scraper 5  # Limit results per scraper (for testing)
uv run home-finder --serve              # Web dashboard + recurring pipeline scheduler
uv run home-finder --serve --no-pipeline # Dashboard only; status/reanalysis routes still write
uv run home-finder --debug              # Enable debug-level logging
uv run home-finder --generate-thumbnails   # Generate missing thumbnails for cached images
uv run home-finder --check-off-market                     # Spot-check URLs for removed/let-agreed signals
uv run home-finder --check-off-market --scrapers zoopla   # Check specific platforms only

# Testing — runs in parallel by default via pytest-xdist (-n auto in addopts).
# Timeout is already configured in pyproject.toml addopts (30s default).
# Slow-marked tests receive a 120s timeout; override only for a justified diagnostic.
# PYTHONUNBUFFERED=1 prevents hanging when stdout is piped (e.g. Claude Code).
PYTHONUNBUFFERED=1 uv run pytest                         # Default suite; excludes slow/browser and blocks network sockets
PYTHONUNBUFFERED=1 uv run pytest tests/test_models.py    # Run specific test file
PYTHONUNBUFFERED=1 uv run pytest -k "test_openrent"      # Run tests matching pattern
PYTHONUNBUFFERED=1 uv run pytest -n0 tests/test_foo.py   # Run single-threaded (useful for debugging)
PYTHONUNBUFFERED=1 uv run pytest --cov=src               # Run with coverage
PYTHONUNBUFFERED=1 uv run pytest -m slow                 # Run slow tests (real network scraping)

# Browser E2E tests (excluded from normal test runs, requires Chromium)
# xdist loadgroup scheduling keeps all browser tests on one worker automatically
PYTHONUNBUFFERED=1 uv run pytest -m browser tests/test_web/test_browser_e2e.py -v
PYTHONUNBUFFERED=1 uv run pytest -m browser tests/test_web/test_browser_e2e.py -v --headed  # See the browser
PYTHONUNBUFFERED=1 uv run pytest -m browser tests/test_web/test_browser_e2e.py -v --tracing=retain-on-failure  # Save traces on failure

# Linting and type checking
uv run ruff check src tests           # Check for issues
uv run ruff check --fix src tests     # Auto-fix issues
uv run ruff format src tests          # Format code
uv run mypy src                       # Type check (strict mode)
```

## Environment Setup

See README.md for full configuration reference. All settings use `HOME_FINDER_` prefix. See `config.py` for field definitions. Use a development database, never a frozen research export. `--dry-run` still writes data and can incur model/commute costs; it suppresses notifications only.

Python support is `>=3.11,<3.15`; CI tests 3.11.16 and 3.14.7. Local/default container Python is 3.14.7, installed by pinned uv 0.12.19. One Dockerfile provides `dashboard` (default/Fly) and `collector` targets, using a managed Python build with SQLite 3.53.1 and an explicit WAL-fix version check. The old official Python/bookworm image's Debian SQLite lacked that fix. CI includes package/container builds and a dependency audit; release remains manual. Anthropic is temporarily constrained to `<1` pending PR20. Crawlee 1.3.2b4 retains its original wheel URL/hash until PR11; no live source qualification is implied.

## Pipeline Flow (main.py and pipeline/stages.py)

1. **Retry Unsent** — Resend failed notifications from previous runs
2. **Scrape and record source listings** — Selected scrapers run against configured areas; upsert returned listings
3. **Criteria and location filters** — Apply `SearchCriteria` and validate search-area postcodes
4. **Wrap and detect price changes** — `properties_to_merged()` wraps single-source properties; update known price observations
5. **Select new properties and retries** — A source listing is seen when it has a `merged_id`, not merely because it was scraped; load eligible unenriched properties for retry
6. **Detail enrichment** — Fetch media/text, cache images, classify EPCs/floorplans and generate thumbnails
7. **Geocode and compute commute** — Runs after enrichment; detail coordinates are available. Mixed reachable/unreachable results are advisory, but an all-unreachable batch can currently abort
8. **Post-enrichment deduplication and floorplan gate** — Merge across sources/runs and update existing anchors; apply floorplan exemptions and configured photo fallback
9. **Analyze, save and notify** — Two-phase analysis (if enabled); persist each result and send notifications in live mode
10. **Drain reanalysis, deliver price drops, finish accounting** — Later work only runs if the pre-analysis stage did not return early

Known repairs are still pending: description/features can be lost between fetch and analysis (PR06a); known-page/early-return accounting is incomplete (PR09a); discovery-independent recovery and process exclusion need PR12a. Do not describe the current commute implementation as a reliable hard filter (PR16a).

## Gotchas & Constraints

### HTTP Client Selection

These are the transports implemented in the current adapters, based on historical source behaviour. They do not establish present access rights or current portal compatibility. Qualify source access and preserve fixtures before live use; parser/transport repairs are PR10/PR11.

| Component | HTTP Client | Reason |
|-----------|-------------|--------|
| Zoopla scraper | `curl_cffi` with per-area browser-profile rotation | Historical TLS/challenge handling; requests can still be blocked |
| OnTheMarket scraper | `curl_cffi` with `impersonate="chrome"` | TLS fingerprinting detection |
| Rightmove scraper | `crawlee.BeautifulSoupCrawler` | Current search implementation; parser contract needs PR10 qualification |
| OpenRent scraper | `curl_cffi` with `impersonate="chrome"` | TLS fingerprinting detection |
| DetailFetcher (Zoopla/OTM/OpenRent) | `curl_cffi` with `impersonate="chrome"` | TLS fingerprinting on detail pages |
| DetailFetcher (Rightmove) | `httpx.AsyncClient` | Current implementation parses `PAGE_MODEL`; live contract may differ |

```python
# Example: curl_cffi usage for anti-bot sites
from curl_cffi.requests import AsyncSession

async with AsyncSession() as session:
    response = await session.get(url, impersonate="chrome", headers=HEADERS, timeout=30)
```

### Deduplication

- **Match threshold:** 60 points AND 2+ signals minimum (`MATCH_THRESHOLD`, `MINIMUM_SIGNALS`)
- **Graduated scoring:** Coordinates and price use linear interpolation (full credit at exact match, 0.5x at threshold boundary, 0x at 2x threshold). See `graduated_coordinate_score()` and `graduated_price_score()`.
- **Scoring weights:** image hash +40, full postcode +40, coordinates +40, street +20, outcode +10, price +15
- **Conservative matching:** Cross-platform matching requires full postcodes (e.g., "E8 3RH" not just "E8"). Rightmove only provides outcodes, so won't cross-match until after enrichment.
- **Two-phase dedup:** Pre-enrichment wraps as single-source (`properties_to_merged`), post-enrichment `deduplicate_merged_async()` merges cross-platform dupes with image/floorplan data.

### Image Caching

- Disk cache at `{data_dir}/image_cache/{safe_id}/` (see `utils/image_cache.py`)
- Detail enrichment downloads and caches images before geocoding/commute computation
- Quality analysis reads cached images from disk (avoids re-downloading)
- Web dashboard serves cached images via `GET /images/{unique_id}/{filename}` with immutable cache headers
- `image_url_map` in detail template maps original CDN URLs to local `/images/` URLs for cached images
- **Thumbnails**: `thumb_` prefix, max 480px (2x retina for 240px cards). Generated during enrichment *after* EPC/floorplan detection (avoids orphaned thumbnails for reclassified images). `backfill_thumbnails()` generates missing thumbnails for existing caches (`--generate-thumbnails` CLI). Web dashboard prefers `thumb_` files when available, falls back to originals.
- **EPC detection**: Gallery images identified as EPC charts are renamed from `gallery_*` to `epc_*` on disk and excluded from gallery/quality analysis.

### Quality Analysis

- Uses claude-sonnet-4-6 (`claude-sonnet-4-6`) via Anthropic API
- **Two-phase chained analysis** (perception → evaluation):
  - **Phase 1 (Visual)**: Images + listing text → structured observations (kitchen, condition, space, etc.). Uses adaptive thinking (`type: adaptive`), `tool_choice: auto`, `max_tokens: 21000` (total cap for thinking + response; SDK enforces ~21333 non-streaming ceiling). Non-strict (schema exceeds Anthropic's grammar complexity limit).
  - **Phase 2 (Evaluation)**: Phase 1 JSON + listing text (no images) → value assessment, viewing notes, highlights, one-liner. Calls `messages.create()` with native `output_config.format.type = "json_schema"` and `max_tokens: 4096`; it does not force a tool call.
- Phase 1's tool schema comes from `_VisualAnalysisResponse` through `_build_tool_schema()`; phase 2's native schema comes from `_EvaluationResponse` through `_eval_json_schema()`. Both are in `quality.py`; prompts are in `quality_prompts.py`
- Phase 1 output feeds Phase 2 via `build_evaluation_prompt()` (wraps Phase 1 JSON in `<visual_analysis>` XML tags)
- Graceful degradation: Phase 1 fails → `None`; Phase 2 fails → partial analysis with visual data only
- Post-processing strips `{"..."}` artifacts from string fields (Claude sometimes wraps text in JSON-like braces even with strict mode)
- Rate limited: 0.2s delay between calls (Tier 2: 1,000 RPM)
- Requests ephemeral prompt caching; realized savings depend on cache writes/hits and the actual request mix
- Historical February 13–28, 2026 cohort: 15 metered runs, 200 counted analyses, $32.1247557 estimated model cost (~$0.1606 per counted analysis). This incomplete ledger is not cost per unique home, a full invoice or a future price guarantee; see [the data review](docs/reviews/production-data-2026-09-27.md). PR13 adds complete accounting
- All images read from disk cache (populated during detail enrichment) and sent as base64; no CDN re-downloads during analysis

### Proxy Support

- `proxy_url` config (e.g., `socks5://user:pass@host:port`) for geo-restricted sites
- Passed to `OnTheMarketScraper` and `DetailFetcher`

## Architecture Pointers

- **Scrapers** → `src/home_finder/scrapers/` — each implements `BaseScraper.scrape()`, see `base.py` for interface
- **Filters** → `src/home_finder/filters/` — `CriteriaFilter`, `LocationFilter`, `CommuteFilter`, `Deduplicator`, `PropertyQualityFilter` (two-phase: `quality.py` + `quality_prompts.py`)
- **Detail Fetcher** → `scrapers/detail_fetcher.py` — per-platform extraction (Rightmove: `PAGE_MODEL` JSON, Zoopla: RSC payload, OpenRent: PhotoSwipe, OTM: Redux state)
- **Models** → `models.py` — `Property` (frozen), `MergedProperty`, `PropertyImage`, `SearchCriteria`, `SOURCE_NAMES`
- **Config** → `config.py` — `pydantic-settings` with `HOME_FINDER_` prefix, key methods: `get_search_areas()`, `get_furnish_types()`, `get_search_criteria()`
- **Database** → `db/storage.py` — async SQLite, `properties` + `property_images` tables, paginated queries with filters
- **Notifier** → `notifiers/telegram.py` — aiogram 3.x, photo cards, quality display, venue pins, web dashboard deep links
- **Image Cache** → `utils/image_cache.py` — disk-based, deterministic filenames from URL hash, thumbnail generation, EPC/floorplan file renaming, cache coverage checks
- **Address Utils** → `utils/address.py` — `normalize_street_name`, `extract_outcode`
- **Source Knowledge** → `docs/sources/` — per-platform decisions, filter quirks, known bugs, pipeline implications. Update the relevant source doc when changing a scraper's HTTP client, filter params, extraction approach, or anti-bot strategy.

## Web Dashboard Notes

- **Access and writes:** All dashboard routes and mounted assets require owner HTTP Basic authentication. Set `HOME_FINDER_WEB_OWNER_PASSWORD` (random, 24+ characters) and the canonical `WEB_BASE_URL`; missing/short passwords fail closed. Mutations require an exact same-origin Origin/Referer, with body/time/rate bounds. `/health` is minimal/public; `/diagnostics` is protected. Telegram callbacks require secret + chat + owner and deduplicate update IDs in status history (schema 7). This local implementation does not establish that production ingress is protected. `--no-pipeline` disables collection but does not make the dashboard read-only. Do not use `sync-to-fly.sh` to overwrite dashboard-written history; authority/restore work is PR05.
- **Scheduling:** Serve mode waits 55 minutes by default ±5 minutes after completion, using an in-process lock only. The old cron `*/55` runs at :00 and :55 (55/5-minute gaps), not every 55 minutes. PR12a will establish process exclusion and a reliable schedule.
- **HTMX pattern:** Dashboard `GET /` checks `HX-Request` header — returns `_results.html` partial or full `dashboard.html`. Filter form uses `hx-get="/" hx-target="#results" hx-push-url="true"`.
- **Maps:** Both modules use a shared CARTO basemap helper with `HOME_FINDER_CARTO_BASEMAP_API_KEY`. This is a browser-visible key: restrict origins/quotas at the provider and retain attribution. No key or tile failure keeps a usable grid/location-link fallback. Browser tests intercept tiles; a real key/terms/ingress check is still required before activation.
- **Image serving:** `GET /images/{unique_id}/{filename}` serves cached images with directory traversal protection and immutable cache headers.
- **Security:** XSS: descriptions use `| e | replace("\n", "<br>") | safe` (escape first). Leaflet popups use `textContent`. All query params clamped; sort whitelisted against `VALID_SORT_OPTIONS`.
- **Template variables:**
  - Dashboard: `properties`, `properties_json`, `search_areas`, `source_names`, `total`, `page`, `total_pages`, `sort`, `min_price`, `max_price`, `bedrooms`, `min_rating`, `area`
  - Detail: `prop`, `outcode`, `area_context`, `best_description`, `source_names`, `image_url_map`
  - Property card `prop` dict: `unique_id`, `title`, `price_pcm`, `bedrooms`, `postcode`, `image_url`, `quality_rating`, `quality_concerns`, `quality_severity`, `quality_summary`, `sources_list`, `commute_minutes`, `transport_mode`, `min_price`, `max_price`, `latitude`, `longitude`, `status`, `highlights`, `lowlights`, `value_rating`
- **Templates:**
  - `_status_controls.html` — Status pill groups (triage/progress/outcome) for detail page
  - `_macros.html` — Shared Jinja2 macros

## Testing Patterns

- `pytest-asyncio` with `asyncio_mode = "auto"`
- Slow and browser tests are excluded by default (`-m 'not slow and not browser'`); default network sockets are blocked. Live `pytest -m slow` requires qualified source access. Dashboard-only browser selection is `-m 'browser and not slow'`
- Mock HTTP with `pytest-httpx` and `AsyncMock` for curl_cffi/TravelTime
- Common fixtures in `tests/conftest.py`: `sample_property`, `sample_property_no_coords`, `default_search_criteria`
- Use `Property.model_copy()` for variations; `hypothesis` for property-based testing
- Integration tests in `tests/integration/` reset Crawlee global state with autouse fixtures
- Tests use in-memory or temporary SQLite databases and files. Parallel agents should use separate checkouts/output paths to avoid collisions in coverage, browser artifacts and other generated files

**Test structure:**
- `tests/test_scrapers/` — per-platform scraper tests
- `tests/test_filters/` — criteria, commute, dedup, quality, location, floorplan
- `tests/test_notifiers/` — core notifications + web dashboard integration
- `tests/test_db/` — CRUD, notification tracking, quality analysis, paginated queries
- `tests/test_web/` — app factory, routes, HTMX partials, XSS prevention
- `tests/test_utils/` — image cache, address utils
- `tests/integration/` — pipeline, real scraping (slow)

**Web test patterns:** FastAPI `TestClient` with in-memory SQLite. HTMX tests send `HX-Request: true` header. XSS tests save `<script>` in description, assert `&lt;script&gt;` in response.

## Feature Flags

| Flag | Default | Description |
|------|---------|-------------|
| `enable_quality_filter` | True | Enable Claude vision property analysis |
| `require_floorplan` | True | Drop properties without floorplans |
| `quality_filter_max_images` | 20 | Max gallery images to analyze (1-20) |
| `enable_image_hash_matching` | True | Perceptual image hash for dedup |
| `proxy_url` | `""` | HTTP/SOCKS5 proxy for geo-restricted sites |

## ADR Writing Guide

Architectural Decision Records live in `docs/adr/`. Number sequentially (`001-`, `002-`, ...). Based on Nygard's original format enhanced with select MADR 4.0 sections.

**Required sections** (in order):

| Section | Guidance |
|---------|----------|
| **Title** | `# ADR NNN: Short Noun Phrase` |
| **Status** | `Proposed`, `Accepted`, `Deprecated`, or `Superseded by ADR NNN` |
| **Date** | YYYY-MM-DD of last status change |
| **Context** | Value-neutral facts only. Describe the forces at play — don't justify the decision here. |
| **Decision Drivers** | Bullet list of forces/constraints that shaped the choice (from MADR) |
| **Decision** | Active voice: "We add/use/choose..." Full sentences, not fragments. |
| **Alternatives Considered** | H3 per alternative. 2-3 sentences: what it is, why rejected. |
| **Consequences** | Split into Positive / Negative / Risks subsections. |
| **Confirmation** | How to verify the decision holds: test commands, validation steps, what to monitor. |

**Style rules:**
- **1-2 pages max.** If it's longer, you're writing a design doc, not a decision record.
- **Decision ≠ design doc.** State *what* we decided and *why*. Don't include implementation-level details (exact thresholds, kernel coefficients, API parameters) — those belong in source code. Reference the source file and line range instead.
- **Context is neutral.** Describe facts and forces. The justification lives in the Decision section.
- **Active voice in Decision.** "We will..." / "We add..." — not passive or imperative.
- **Full sentences.** Bullets are for visual structure, not an excuse for fragments.
- **Immutable.** Don't edit accepted ADRs. To reverse a decision, write a new ADR that supersedes it.
- **One decision per ADR.** If a decision has multiple phases, write separate ADRs.

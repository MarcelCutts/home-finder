# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Why This Exists

Personal rental property finder for Marcel, based in Stoke Newington, London. Scrapes listings, filters, analyzes quality with AI vision, and sends Telegram notifications for good matches.

Marcel runs a small remote software consultancy (WFH full-time) and music events. Key preferences that should inform quality analysis, scoring, and UX decisions:

- **Space layout:** 2-bed (one as office) ideal, or 1-bed with a large common area. Needs separation between sleep and work, plus room to host people for music/social events.
- **Sound & neighbours:** Good sound insulation, artist-friendly/warehouse areas are a plus — wants to host without being antisocial.
- **Practical needs:** High-speed internet, gas or induction hob (likes to cook).
- **Vibe:** Creative/cool spaces appreciated (lofts, conversions, interesting architecture) but subjective.
- **Reality:** All preferences, not dealbreakers. The London rental market is brutal — compromise expected.

## Commands

```bash
# Install dependencies (includes dev deps)
uv sync --all-extras

# Run the application
uv run home-finder                      # Full pipeline with Telegram notifications
uv run home-finder --dry-run            # Full pipeline, save to DB but no notifications
uv run home-finder --scrape-only        # Just scrape and print (no filtering/storage)
uv run home-finder --max-per-scraper 5  # Limit results per scraper (for testing)
uv run home-finder --serve              # Web dashboard + recurring pipeline scheduler
uv run home-finder --debug              # Enable debug-level logging

# Testing — timeout is already configured in pyproject.toml addopts (30s default).
# Do NOT pass --timeout on the command line; it's handled automatically.
# PYTHONUNBUFFERED=1 prevents hanging when stdout is piped (e.g. Claude Code).
PYTHONUNBUFFERED=1 uv run pytest                         # Run all tests (slow tests excluded by default)
PYTHONUNBUFFERED=1 uv run pytest tests/test_models.py    # Run specific test file
PYTHONUNBUFFERED=1 uv run pytest -k "test_openrent"      # Run tests matching pattern
PYTHONUNBUFFERED=1 uv run pytest --cov=src               # Run with coverage
PYTHONUNBUFFERED=1 uv run pytest -m slow                 # Run slow tests (real network scraping)

# Linting and type checking
uv run ruff check src tests           # Check for issues
uv run ruff check --fix src tests     # Auto-fix issues
uv run ruff format src tests          # Format code
uv run mypy src                       # Type check (strict mode)
```

## Environment Setup

See README.md for full configuration reference. All settings use `HOME_FINDER_` prefix. See `config.py` for field definitions.

## Pipeline Flow (main.py)

1. **Retry Unsent** — Resend failed notifications from previous runs
2. **Scrape** — All scrapers run against configured `SEARCH_AREAS`
3. **Criteria Filter** — Price/bedroom filters from `SearchCriteria`
4. **Location Filter** — Validate postcodes match search areas (catches scraper leakage)
5. **Wrap as MergedProperty** — `Deduplicator.properties_to_merged()` wraps each Property as single-source
6. **New Property Filter** — Check SQLite DB to only process unseen properties
7. **Commute Filter** — TravelTime API (if configured); geocodes missing coordinates
8. **Detail Enrichment** — Fetch gallery, floorplans, descriptions; cache images to disk
9. **Post-Enrichment Dedup** — `deduplicate_merged_async()` merges cross-platform duplicates using enriched data
10. **Floorplan Gate** — Drop properties without floorplans (if `require_floorplan=True`)
11. **Quality Analysis** — Claude vision analyzes images (if configured)
12. **Save & Notify** — Store in DB, send Telegram notifications

## Gotchas & Constraints

### HTTP Client Selection

**Scrapers MUST use the correct HTTP client to avoid 403 blocks:**

| Component | HTTP Client | Reason |
|-----------|-------------|--------|
| Zoopla scraper | `curl_cffi` with `impersonate="chrome"` | TLS fingerprinting bypasses Cloudflare |
| OnTheMarket scraper | `curl_cffi` with `impersonate="chrome"` | TLS fingerprinting detection |
| Rightmove scraper | `crawlee.BeautifulSoupCrawler` | Standard requests work |
| OpenRent scraper | `crawlee.BeautifulSoupCrawler` | Standard requests work |
| DetailFetcher (Zoopla/OTM) | `curl_cffi` with `impersonate="chrome"` | TLS fingerprinting on detail pages |
| DetailFetcher (others) | `httpx.AsyncClient` | Standard requests work |
| QualityFilter (Zoopla images) | `curl_cffi` with `impersonate="chrome"` | Anti-bot protection on CDN images |

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
- Detail enrichment downloads and caches images during pipeline step 8
- Quality analysis reads cached images from disk (avoids re-downloading)
- Web dashboard serves cached images via `GET /images/{unique_id}/{filename}` with immutable cache headers
- `image_url_map` in detail template maps original CDN URLs to local `/images/` URLs for cached images

### Quality Analysis

- Uses claude-sonnet-4-5 (`claude-sonnet-4-5-20250929`) via Anthropic API
- **Two-phase chained analysis** (perception → evaluation):
  - **Phase 1 (Visual)**: Images + listing text → structured observations (kitchen, condition, space, etc.). Uses extended thinking, `tool_choice: auto`. Non-strict (schema exceeds Anthropic's grammar complexity limit).
  - **Phase 2 (Evaluation)**: Phase 1 JSON + listing text (no images) → value assessment, viewing notes, highlights, one-liner. Uses forced tool choice, `strict: true`.
- Tool schemas auto-generated from Pydantic models (`_VisualAnalysisResponse`, `_EvaluationResponse`) via `_build_tool_schema()` in `quality.py`; prompts in `quality_prompts.py`
- Phase 1 output feeds Phase 2 via `build_evaluation_prompt()` (wraps Phase 1 JSON in `<visual_analysis>` XML tags)
- Graceful degradation: Phase 1 fails → `None`; Phase 2 fails → partial analysis with visual data only
- Post-processing strips `{"..."}` artifacts from string fields (Claude sometimes wraps text in JSON-like braces even with strict mode)
- Rate limited: 0.2s delay between calls (Tier 2: 1,000 RPM)
- Uses prompt caching (`cache_control: ephemeral`) for ~90% cost savings on system prompt
- ~$0.05-0.07/property (Phase 1 ~$0.04-0.06 multimodal + thinking, Phase 2 ~$0.005-0.01 text-only)
- Zoopla CDN images downloaded via curl_cffi and sent as base64 (anti-bot); others sent as URL references

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
- **Image Cache** → `utils/image_cache.py` — disk-based, deterministic filenames from URL hash
- **Address Utils** → `utils/address.py` — `normalize_street_name`, `extract_outcode`

## Web Dashboard Notes

- **HTMX pattern:** Dashboard `GET /` checks `HX-Request` header — returns `_results.html` partial or full `dashboard.html`. Filter form uses `hx-get="/" hx-target="#results" hx-push-url="true"`.
- **Image serving:** `GET /images/{unique_id}/{filename}` serves cached images with directory traversal protection and immutable cache headers.
- **Security:** XSS: descriptions use `| e | replace("\n", "<br>") | safe` (escape first). Leaflet popups use `textContent`. All query params clamped; sort whitelisted against `VALID_SORT_OPTIONS`.
- **Template variables:**
  - Dashboard: `properties`, `properties_json`, `search_areas`, `source_names`, `total`, `page`, `total_pages`, `sort`, `min_price`, `max_price`, `bedrooms`, `min_rating`, `area`
  - Detail: `prop`, `outcode`, `area_context`, `best_description`, `source_names`, `image_url_map`
  - Property card `prop` dict: `unique_id`, `title`, `price_pcm`, `bedrooms`, `postcode`, `image_url`, `quality_rating`, `quality_concerns`, `quality_severity`, `quality_summary`, `sources_list`, `commute_minutes`, `transport_mode`, `min_price`, `max_price`, `latitude`, `longitude`

## Testing Patterns

- `pytest-asyncio` with `asyncio_mode = "auto"`
- Slow tests excluded by default (`-m 'not slow'`); run with `pytest -m slow`
- Mock HTTP with `pytest-httpx` and `AsyncMock` for curl_cffi/TravelTime
- Common fixtures in `tests/conftest.py`: `sample_property`, `sample_property_no_coords`, `default_search_criteria`
- Use `Property.model_copy()` for variations; `hypothesis` for property-based testing
- Integration tests in `tests/integration/` reset Crawlee global state with autouse fixtures
- **Concurrent-safe:** All tests use in-memory SQLite, no shared state — multiple agents/processes can run `pytest` simultaneously without interference

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
| `enable_image_hash_matching` | False | Perceptual image hash for dedup |
| `proxy_url` | `""` | HTTP/SOCKS5 proxy for geo-restricted sites |

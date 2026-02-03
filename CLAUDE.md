# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies (includes dev deps)
uv sync --all-extras

# Run the application
uv run home-finder                    # Full pipeline with notifications
uv run home-finder --dry-run          # Full pipeline, save to DB but no Telegram notifications
uv run home-finder --scrape-only      # Just scrape and print (no filtering/storage/notifications)
uv run home-finder --max-per-scraper 5  # Limit results per scraper (for testing)

# Testing
uv run pytest                         # Run all tests (slow tests excluded by default)
uv run pytest tests/test_models.py    # Run specific test file
uv run pytest -k "test_openrent"      # Run tests matching pattern
uv run pytest --cov=src               # Run with coverage
uv run pytest -m slow                 # Run slow tests (real network scraping)

# Linting and type checking
uv run ruff check src tests           # Check for issues
uv run ruff check --fix src tests     # Auto-fix issues
uv run ruff format src tests          # Format code
uv run mypy src                       # Type check (strict mode)
```

## Environment Setup

Copy `.env.example` to `.env` and configure:

| Variable | Required | Description |
|----------|----------|-------------|
| `HOME_FINDER_TELEGRAM_BOT_TOKEN` | Yes | Bot token from @BotFather |
| `HOME_FINDER_TELEGRAM_CHAT_ID` | Yes | Chat ID for notifications |
| `HOME_FINDER_TRAVELTIME_APP_ID` | No | TravelTime API - enables commute filtering |
| `HOME_FINDER_TRAVELTIME_API_KEY` | No | TravelTime API key |
| `HOME_FINDER_ANTHROPIC_API_KEY` | No | Enables Claude vision quality analysis of property images |

All settings use `HOME_FINDER_` prefix. See `config.py` for full list.

## Architecture

This is an async Python application that scrapes London rental properties from multiple platforms, filters them by criteria and commute time, deduplicates across platforms, optionally analyzes quality with Claude vision, and sends Telegram notifications for new matches.

### Pipeline Flow (main.py)

1. **Retry Unsent** - Resend failed notifications from previous runs
2. **Scrape** - All scrapers run against configured `SEARCH_AREAS` (outcodes: e3, e5, e9, e10, e15, e17, n15, n16, n17)
3. **Criteria Filter** - Apply price/bedroom filters from `SearchCriteria`
4. **Location Filter** - Validate postcodes match search areas (catches scraper leakage)
5. **Deduplicate & Merge** - Weighted multi-signal matching across platforms, outputs `MergedProperty` list
6. **New Property Filter** - Check SQLite DB to only process unseen properties
7. **Commute Filter** - TravelTime API filters by travel time to destination (if configured). Geocodes properties missing coordinates.
8. **Detail Enrichment** - Fetch gallery images, floorplans, and descriptions from property detail pages
9. **Floorplan Gate** - Drop properties without floorplans (if `require_floorplan=True`)
10. **Quality Analysis** - Claude vision analyzes images for condition, kitchen, space, and value (if Anthropic API key configured and `enable_quality_filter=True`)
11. **Save & Notify** - Store in DB, send Telegram notifications with quality cards

### Key Abstractions

**Scrapers** (`src/home_finder/scrapers/`): Each platform implements `BaseScraper` with:
- `source` property returning `PropertySource` enum
- `scrape()` async method returning `list[Property]`
- Parameters: price range, bedrooms, area, furnish_types, min_bathrooms, include_let_agreed, max_results

**Filters** (`src/home_finder/filters/`):
- `CriteriaFilter` - Price/bedroom filtering
- `LocationFilter` - Borough/outcode validation with comprehensive London borough→outcode mapping
- `CommuteFilter` - TravelTime API integration with class-level geocoding cache
- `Deduplicator` - Weighted multi-signal cross-platform deduplication (see below)
- `detail_enrichment.enrich_merged_properties()` - Fetches detail pages for gallery/floorplan/description
- `PropertyQualityFilter` - Claude vision analysis of property images (see below)

**Detail Fetcher** (`src/home_finder/scrapers/detail_fetcher.py`):
- Fetches property detail pages for gallery/floorplan extraction
- Uses `curl_cffi` for Zoopla/OnTheMarket (TLS fingerprinting), `httpx` for others
- Per-platform extraction: Rightmove (`window.PAGE_MODEL` JSON), Zoopla (`__NEXT_DATA__` + regex fallback), OpenRent (PhotoSwipe lightbox), OnTheMarket (Redux state)
- Returns `DetailPageData` with floorplan_url, gallery_urls, description, features

**Utilities** (`src/home_finder/utils/`):
- `address.py` - Address normalization (`normalize_street_name`, `extract_outcode`)
- `image_hash.py` - Perceptual hashing for cross-platform image matching (pHash, Hamming distance threshold: 8)

**Models** (`src/home_finder/models.py`): Pydantic models with validation:
- `Property` - Immutable (frozen), validates coordinates are both present or both absent, postcode normalization
- `MergedProperty` - Aggregates same property from multiple platforms with combined images, floorplan, descriptions, price range
- `PropertyImage` - Image from listing (gallery or floorplan)
- `SearchCriteria` - Validates price/bedroom ranges, transport modes
- `CommuteResult`, `TrackedProperty` - Pipeline data structures

**Config** (`src/home_finder/config.py`): `pydantic-settings` with `HOME_FINDER_` env prefix.

**Database** (`src/home_finder/db/storage.py`): Async SQLite via aiosqlite:
- `properties` table - Tracks all seen properties with notification status (pending/sent/failed), commute data, multi-source JSON fields
- `property_images` table - Gallery and floorplan images per property (unique constraint on property+url)
- Key methods: `save_merged_property()`, `filter_new_merged()`, `get_unsent_notifications()`, `save_property_images()`

**Notifier** (`src/home_finder/notifiers/telegram.py`): Rich Telegram notifications via aiogram 3.x:
- Photo cards with inline keyboard (source links + map button)
- Quality analysis display: star rating, condition, kitchen, space, value assessment
- Venue pins with coordinates
- Fallback from photo to text if image send fails

### HTTP Client Selection (IMPORTANT)

**Scrapers MUST use the correct HTTP client to avoid 403 blocks:**

| Component | HTTP Client | Reason |
|-----------|-------------|--------|
| Zoopla scraper | `curl_cffi` with `impersonate="chrome"` | TLS fingerprinting detection |
| OnTheMarket scraper | `curl_cffi` with `impersonate="chrome"` | TLS fingerprinting detection |
| Rightmove scraper | `crawlee.BeautifulSoupCrawler` | Standard requests work |
| OpenRent scraper | `crawlee.BeautifulSoupCrawler` | Standard requests work |
| DetailFetcher (Zoopla/OTM) | `curl_cffi` with `impersonate="chrome"` | TLS fingerprinting on detail pages |
| DetailFetcher (others) | `httpx.AsyncClient` | Standard requests work |
| QualityFilter (Zoopla images) | `curl_cffi` with `impersonate="chrome"` | Anti-bot protection on CDN images |

**Why curl_cffi?** Sites like Zoopla and OnTheMarket use TLS fingerprinting to detect bots. Standard Python HTTP clients (httpx, aiohttp, requests) have distinctive TLS fingerprints that get blocked with 403. `curl_cffi` impersonates Chrome's TLS fingerprint.

```python
# Example: curl_cffi usage for anti-bot sites
from curl_cffi.requests import AsyncSession

async with AsyncSession() as session:
    response = await session.get(url, impersonate="chrome", headers=HEADERS, timeout=30)
```

### Deduplication System

The deduplicator uses weighted multi-signal scoring to match properties across platforms:

**Scoring (constants in `filters/deduplication.py`):**
- Image hash match: +40 points
- Full postcode match: +40 points
- Coordinates within 50m: +40 points
- Street name match: +20 points
- Outcode match: +10 points
- Price within 3%: +15 points

**Match threshold:** 55 points AND 2+ signals minimum

**Match confidence levels:** HIGH (≥80 points, 3+ signals), MEDIUM (55-79), LOW (40-54), NONE

**Key methods:**
- `deduplicate()` - Legacy behavior, discards duplicates
- `deduplicate_and_merge()` - Sync, groups by full postcode + bedrooms
- `deduplicate_and_merge_async()` - Async with image hashing support (used in main pipeline)

**Conservative matching:** Cross-platform matching requires full postcodes (e.g., "E8 3RH" not just "E8") to prevent false positives. This is important because Rightmove only provides outcodes.

### Quality Analysis System

`PropertyQualityFilter` in `filters/quality.py` uses Claude vision (claude-sonnet-4-5-20250929) to analyze property images:

**Analyzes:**
- Kitchen: hob type, dishwasher, washing machine, overall quality
- Condition: damp, mold, worn fixtures, maintenance concerns with severity
- Light/space: natural light, window sizes, spaciousness, ceiling height
- Space: living room sqm estimate (2+ bed auto-passes — office in spare room)
- Value: comparison against embedded rental benchmarks per outcode, quality-adjusted rating
- Overall: 1-5 star rating and 2-3 sentence summary

**Area context:** Embedded benchmarks and context for East London outcodes (E2, E3, E5, E8, E9, E10, E15, E17, N1, N15, N16, N17). Includes average rents by bedroom count, neighborhood character, transport links, council tax, crime rates, rent trends.

**Rate limiting:** 1.5s delay between calls (Tier 1: 50 RPM). Uses prompt caching (ephemeral cache_control) for ~90% cost savings on the system prompt.

**Image handling:** Downloads Zoopla CDN images via curl_cffi and sends as base64 (anti-bot protection). Other sites send as URL references.

### Scraper Implementation Notes

- Scrapers support both borough names (e.g., "hackney") and outcodes (e.g., "e8")
- Use `location_utils.is_outcode()` to detect postcode vs borough
- Rightmove uses region codes mapped via hardcoded dicts (`RIGHTMOVE_LOCATIONS`, `RIGHTMOVE_OUTCODES`) with async typeahead API fallback for unknown outcodes
- All scrapers handle rate limiting with configurable page delays (0.5-2s)
- All scrapers deduplicate within their own results (track seen IDs/URLs)
- **New scrapers for anti-bot sites MUST use curl_cffi** (see HTTP Client Selection above)

**Data extraction patterns:**
- Zoopla: Next.js — `__NEXT_DATA__` JSON or RSC format via `self.__next_f.push()` calls. Parsed with `zoopla_models.py` Pydantic models.
- OnTheMarket: Next.js — `__NEXT_DATA__` with Redux state at `data.props.initialReduxState.results.list`
- Rightmove: Crawlee + typeahead API for outcode → region code. Detail pages use `window.PAGE_MODEL` JSON.
- OpenRent: JavaScript arrays (`PROPERTYIDS`, `prices`, `bedrooms`, `PROPERTYLISTLATITUDES`, `PROPERTYLISTLONGITUDES`) extracted via regex

### Testing Patterns

- Tests use `pytest-asyncio` with `asyncio_mode = "auto"`
- Slow tests excluded by default (`-m 'not slow'`); run with `pytest -m slow`
- Mock HTTP with `pytest-httpx` and `AsyncMock` for curl_cffi/TravelTime
- Common fixtures in `tests/conftest.py`: `sample_property`, `sample_property_no_coords`, `default_search_criteria`
- Use `Property.model_copy()` to create variations with specific field changes
- Property-based testing with `hypothesis` (profiles: "fast" 10 examples, "ci" 200 examples)
- Integration tests in `tests/integration/` have autouse fixtures to reset Crawlee global state and use temp storage dirs
- Test structure mirrors source: `tests/test_scrapers/`, `tests/test_filters/`, `tests/test_notifiers/`, `tests/test_db/`

### Configuration Flags

Key feature flags in `config.py`:

| Flag | Default | Description |
|------|---------|-------------|
| `enable_quality_filter` | True | Enable Claude vision analysis of property images |
| `require_floorplan` | True | Drop properties without floorplans before quality analysis |
| `quality_filter_max_images` | 10 | Max gallery images to analyze per property (1-20) |
| `enable_image_hash_matching` | False | Enable async perceptual image hash comparison for deduplication |

### Deployment

- **Docker**: `Dockerfile` uses python:3.11-slim with uv 0.6.6, multi-stage build
- **Railway**: `railway.toml` configured for cron schedule (`*/55 * * * *`) with restart-on-failure (3 retries)
- SQLite database stored at `data/properties.db` (mount as volume in Docker)

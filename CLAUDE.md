## Current delivery scope

Prioritize dependable collection, retained listing text, conservative costs and
availability, owner decisions, bounded paid analysis and reliable notifications.
The large implementation PR queue is being resolved, not extended. Do not resume
its former automatic delivery plan. Source-observation ledgers, replay/evaluation
platforms, reversible home grouping and multi-user infrastructure need a concrete
product requirement before implementation. Keep collection and deployment manual
until their operational configuration has been verified. See `docs/operations.md`
and the review disposition record for current decisions.

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
uv run home-finder --serve              # Web dashboard (add --with-pipeline for the recurring scheduler)
uv run home-finder --debug              # Enable debug-level logging
uv run home-finder --generate-thumbnails   # Generate missing thumbnails for cached images
uv run home-finder --check-off-market                     # Spot-check URLs for removed/let-agreed signals
uv run home-finder --check-off-market --scrapers zoopla   # Check specific platforms only

# Testing — runs in parallel by default via pytest-xdist (-n auto; no repository worker cap).
# Timeout is already configured in pyproject.toml addopts (30s default).
# Do NOT pass --timeout on the command line; it's handled automatically.
# PYTHONUNBUFFERED=1 prevents hanging when stdout is piped (e.g. Claude Code).
# Agent shells that set PYTHONDONTWRITEBYTECODE=1 also stop pytest writing its
# assertion-rewrite cache; until one run with it cleared (PYTHONDONTWRITEBYTECODE=)
# writes the cache, every worker recompiles every test module (~2x collection time).
PYTHONUNBUFFERED=1 uv run pytest                         # Run all tests in parallel (slow and browser tests excluded)
PYTHONUNBUFFERED=1 uv run pytest tests/test_models.py    # Run specific test file
PYTHONUNBUFFERED=1 uv run pytest -k "test_openrent"      # Run tests matching pattern
PYTHONUNBUFFERED=1 uv run pytest -n0 tests/test_foo.py   # Run single-threaded (useful for debugging)
PYTHONUNBUFFERED=1 uv run pytest --cov=src               # Run with coverage
PYTHONUNBUFFERED=1 uv run pytest --test-profile=test-results/local.json  # Where the run's time went
PYTHONUNBUFFERED=1 uv run pytest -m slow                 # Run slow tests (real network scraping)

# Browser E2E tests (excluded from normal test runs, requires Chromium)
# xdist loadgroup scheduling keeps all browser tests on one worker automatically
PYTHONUNBUFFERED=1 uv run pytest -m browser tests/test_web/browser -v
PYTHONUNBUFFERED=1 uv run pytest -m browser tests/test_web/browser -v --headed  # See the browser
PYTHONUNBUFFERED=1 uv run pytest -m browser tests/test_web/browser -v --tracing=retain-on-failure  # Save traces on failure

# Linting and type checking
uv run ruff check src tests           # Check for issues
uv run ruff check --fix src tests     # Auto-fix issues
uv run ruff format src tests          # Format code
uv run mypy src                       # Type check (strict mode)
```

## Environment Setup

See README.md for full configuration reference. All settings use `HOME_FINDER_` prefix. See `config.py` for field definitions.

## Pipeline Flow (pipeline/flow.py)

[`docs/pipeline.md`](docs/pipeline.md) is the authoritative sequence and vocabulary. In
outline, a run advances durable queues even when it discovers nothing new:

1. **Setup** — Acquire run ownership, recover abandoned paid attempts, repair image caches
2. **Delivery recovery** — Resume saved notification plans that were not fully delivered
3. **Candidate preparation** — Collect listings from every scraper; apply criteria and
   location filters; select listings not yet seen (a listing is "seen" once it belongs to a
   stored home, not merely because it was scraped); refresh known prices; acquire detail
   and media evidence (gallery, floorplans, descriptions, cached images, EPC and floorplan
   detection, thumbnails); assess commutes (advisory, after enrichment so detail pages can
   supply coordinates); match and consolidate with stored anchors; apply media admission
   (the floorplan gate)
4. **Initial assessment** — Claude vision analysis of new and pending homes, then
   notification selection and delivery
5. **Reanalysis** — Process requested reassessments (e.g. a home gained a source)
6. **Delivery drain** and **finalization** — Send queued deliveries, flush diagnostics, close the run

## Gotchas & Constraints

### HTTP Client Selection

**Scrapers MUST use the correct HTTP client to avoid 403 blocks:**

| Component | HTTP Client | Reason |
|-----------|-------------|--------|
| Zoopla scraper | `curl_cffi` with `impersonate="chrome"` | TLS fingerprinting bypasses Cloudflare |
| OnTheMarket scraper | `curl_cffi` with `impersonate="chrome"` | TLS fingerprinting detection |
| Rightmove scraper | `httpx.AsyncClient` + BeautifulSoup | Standard requests work |
| OpenRent scraper | `curl_cffi` with `impersonate="chrome"` | TLS fingerprinting detection |
| DetailFetcher (Zoopla/OTM/OpenRent) | `curl_cffi` with `impersonate="chrome"` | TLS fingerprinting on detail pages |
| DetailFetcher (Rightmove) | `httpx.AsyncClient` | Standard requests work |

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
- Detail enrichment downloads and caches images during candidate preparation
- Quality analysis reads cached images from disk (avoids re-downloading)
- Web dashboard serves cached images via `GET /images/{unique_id}/{filename}` with immutable cache headers
- `image_url_map` in detail template maps original CDN URLs to local `/images/` URLs for cached images
- **Thumbnails**: `thumb_` prefix, max 480px (2x retina for 240px cards). Generated during enrichment *after* EPC/floorplan detection (avoids orphaned thumbnails for reclassified images). `backfill_thumbnails()` generates missing thumbnails for existing caches (`--generate-thumbnails` CLI). Web dashboard prefers `thumb_` files when available, falls back to originals.
- **EPC detection**: Gallery images identified as EPC charts are renamed from `gallery_*` to `epc_*` on disk and excluded from gallery/quality analysis.

### Quality Analysis

- The experimental `opus_visual_weighted_2026_10_21_v1` rating is available only through
  `python -m home_finder.pipeline.rating_shadow_report --database PATH`. It reports candidate
  ratings, evidence coverage and counterfactual selection; it never replaces live ratings,
  enters either model prompt, writes a database or creates deliveries. Its sparse-evidence
  failures remain visible for review. See the 2026-10-22 follow-up in the model bake-off review.
- Models and limits come from `new_runtime_analysis_config()` in `config.py` (currently `claude-sonnet-5-5`
  for both phases, `effort="low"`, 8,192 output tokens each); the `analysis_config` setting overrides them,
  and historical model identities stay readable
- **Two-phase chained analysis** (perception → evaluation):
  - **Phase 1 (Visual)**: Images + listing text → structured observations (kitchen, condition, space, etc.). Uses adaptive thinking with `tool_choice: auto` when reasoning is enabled, otherwise a forced tool call. Non-strict (schema exceeds Anthropic's grammar complexity limit).
  - **Phase 2 (Evaluation)**: Phase 1 JSON + listing text (no images) → value assessment, viewing notes, highlights, one-liner. Uses structured JSON output (`output_config` with the evaluation schema) rather than a tool; token caps come from the evaluation phase settings.
- Schemas are generated from Pydantic models in `analysis/analyzer.py`: the visual tool from `_VisualAnalysisResponse` via `_build_tool_schema()`, the evaluation output from `_EvaluationResponse` via `_eval_json_schema()`; prompts in `analysis/prompts.py`
- Phase 1 output feeds Phase 2 via `build_evaluation_prompt()` (wraps Phase 1 JSON in `<visual_analysis>` XML tags)
- Failures produce a failed assessment, never a partial one: a Phase 2 failure after a successful Phase 1 records `analysis_failure` (phase `evaluation`) with no rating. A completed Phase 1 result is kept as a visual checkpoint so a retry can skip it
- Both response contracts are validated before merging; tolerant repairs for historical stored analyses live in the storage model's reader
- Pacing comes from the run's bounded assessment executor; `DELAY_BETWEEN_CALLS` (0.2s) applies only to the batch helper `analyze_merged_properties`, which the pipeline does not call
- Uses prompt caching (`cache_control: ephemeral`) for ~90% cost savings on system prompt
- ~$0.05-0.07/property (Phase 1 ~$0.04-0.06 multimodal + thinking, Phase 2 ~$0.005-0.01 text-only)
- All images read from disk cache (populated during detail enrichment) and sent as base64; no CDN re-downloads during analysis

### Proxy Support

- `proxy_url` config (e.g., `socks5://user:pass@host:port`) for geo-restricted sites
- Passed to `OnTheMarketScraper` and `DetailFetcher`

## Architecture Pointers

- **Scrapers** → `src/home_finder/scrapers/` — each implements `BaseScraper.scrape()`, see `base.py` for interface
- **Filters** → `src/home_finder/filters/` — `CriteriaFilter`, `LocationFilter`, media admission, fit score, availability, off-market and notification policies
- **Enrichment** → `src/home_finder/enrichment/` — detail/media acquisition (`details.py`) and the advisory `CommuteAssessor`
- **Matching** → `src/home_finder/matching/` — `Deduplicator` and match scoring
- **Analysis** → `src/home_finder/analysis/` — `PropertyQualityAnalyzer` (two-phase: `analyzer.py` + `prompts.py`), spend metering, badge reconciliation
- **Detail Fetcher** → `scrapers/detail_fetcher.py` — per-platform extraction (Rightmove: `PAGE_MODEL` JSON, Zoopla: RSC payload, OpenRent: PhotoSwipe, OTM: Redux state)
- **Models** → `models/` — `Property` (frozen), `MergedProperty`, `PropertyImage`, `SearchCriteria`, `SOURCE_NAMES`
- **Config** → `config.py` — `pydantic-settings` with `HOME_FINDER_` prefix, key methods: `get_search_areas()`, `get_furnish_types()`, `get_search_criteria()`
- **Database** → `db/storage.py` — async SQLite, `properties` + `property_images` tables, paginated queries with filters
- **Notifier** → `notifiers/telegram.py` — aiogram 3.x, photo cards, quality display, venue pins, web dashboard deep links
- **Image Cache** → `utils/image_cache.py` — disk-based, deterministic filenames from URL hash, thumbnail generation, EPC/floorplan file renaming, cache coverage checks
- **Address Utils** → `utils/address.py` — `normalize_street_name`, `extract_outcode`
- **Source Knowledge** → `docs/sources/` — per-platform decisions, filter quirks, known bugs, pipeline implications. Update the relevant source doc when changing a scraper's HTTP client, filter params, extraction approach, or anti-bot strategy.

## Web Dashboard Notes

- **HTMX pattern:** Dashboard `GET /` checks `HX-Request` header — returns `_results.html` partial or full `dashboard.html`. Filter form uses `hx-get="/" hx-target="#results" hx-push-url="true"`.
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

[`tests/README.md`](tests/README.md) describes suite boundaries, naming and shared support.

- `pytest-asyncio` with `asyncio_mode = "auto"`; no `@pytest.mark.asyncio` needed
- Default runs exclude `slow` (live network) and `browser` (Playwright) tests, block sockets
  (`pytest-socket`) and reject unknown markers (`--strict-markers`). The root conftest gives
  `slow` and `browser` tests real sockets, so `pytest -m slow` can reach the portals
- Mock HTTP with `pytest-httpx`, `httpx2.MockTransport` for the Anthropic SDK, and `AsyncMock` for
  curl_cffi/TravelTime. Fake only the
  Telegram Bot API (`tests/support/delivery.py`), keeping real delivery planning and persistence
- Builders in `tests/conftest.py`: `make_property`, `make_merged_property`,
  `make_quality_analysis`, `sample_property`, `enriched_merged_property`. Reusable fakes live in
  `tests/support/`; directory `conftest.py` files hold resource fixtures such as `storage`
- Never wait out production pacing. Telegram's `DELIVERY_PAUSE_SECONDS` is zeroed for every
  test; set a scraper's `PAGE_DELAY_SECONDS` to 0, or record the off-market checker's pauses. When
  the pause is the contract, record `asyncio.sleep` and assert its argument, as
  `test_delivery_recovery.py` and `test_off_market.py` do
- Use `Property.model_copy()` for variations; `hypothesis` for property-based testing
- **Concurrent-safe:** in-memory or `tmp_path` SQLite and `tmp_path` data dirs, so several agents
  can run `pytest` at once. Fresh in-memory databases start from a migrated schema template
  (`tests/plugins/schema_template.py`); file databases, tests that patch `MIGRATIONS` and
  direct `run_migrations` calls still run the real migrations
- `--test-profile=PATH` (`tests/support/pytest_profile.py`; `--timing-report` is an alias)
  records startup, phase and fixture timings, worker balance, CPU/RSS and host load.
  `scripts/summarize_test_profile.py PATH` prints a summary; CI uploads profiles and
  JUnit as `test-observability`. See `tests/README.md` for measurement limits.

**Test structure:**
- `tests/test_scrapers/` — search-page parsing and transport per portal, detail fetcher, shared parsing
- `tests/test_filters/` — criteria, location, media gate, fit score, availability, off-market checker
- `tests/test_enrichment/` — detail and media acquisition, commute assessment
- `tests/test_matching/` — match scoring, grouping invariants, canonical selection, image evidence
- `tests/test_analysis/` — provider requests, prompts, response validation, spend, assessment models
- `tests/test_db/` — repositories and queues, migrations, transactions, backups
- `tests/test_pipeline/` — run coordination, consolidation, recovery queues, run accounting
- `tests/test_notifiers/` — message formatting, keyboards, delivery selection and recovery
- `tests/test_web/` — Dashboard, detail page, status changes, images, access; `browser/` holds Playwright tests
- `tests/test_utils/`, `tests/test_data/`, `tests/test_scripts/` — supporting modules, area data, scripts
- `tests/integration/` — contracts across several stages; live scraping tests are marked `slow`
- `tests/plugins/` — suite infrastructure (SQLite leak detection, schema template)

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

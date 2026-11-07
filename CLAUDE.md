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
uv run home-finder --serve              # Web dashboard only
uv run home-finder --serve --with-pipeline  # Web dashboard + recurring pipeline scheduler
uv run home-finder --debug              # Enable debug-level logging
uv run home-finder --generate-thumbnails   # Generate missing thumbnails for cached images
uv run home-finder --check-off-market                     # Spot-check URLs for removed/let-agreed signals
uv run home-finder --check-off-market --scrapers zoopla   # Check specific platforms only

# Testing — runs in parallel by default via pytest-xdist (-n auto in addopts).
# Timeout is already configured in pyproject.toml addopts (30s default).
# Do NOT pass --timeout on the command line; it's handled automatically.
# PYTHONUNBUFFERED=1 prevents hanging when stdout is piped (e.g. Claude Code).
PYTHONUNBUFFERED=1 uv run pytest                         # Run all tests in parallel (slow tests excluded by default)
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

`.env.example` is the complete annotated settings reference; README.md's configuration table lists what each feature needs. All settings use the `HOME_FINDER_` prefix, and unknown `HOME_FINDER_` names fail at startup. `uv run home-finder --check-config` checks the configuration without network or database access. See `config.py` for field definitions.

## Pipeline Flow (pipeline/flow.py)

`run_pipeline` in `pipeline/flow.py` runs these stages in order; `main.py` only
parses CLI options, and steps 3–8 live in `pipeline/candidates.py`. Each run also
advances durable queues, so it does work even when nothing new is found. Stage
contracts, vocabulary and run metrics are in `docs/pipeline.md`.

1. **Setup** — Runner lock, abandoned-spend recovery, run row, image-cache repair.
2. **Delivery recovery** — Resume `queued`, `ready` and due `waiting` alerts; blocked, uncertain and review ones wait for an operator. Live runs only.
3. **Collect and select** — Scrape `SEARCH_AREAS` and store every card, then apply the price, bedroom and outcode rules.
4. **Find new and admit** — Save each unseen listing as a staging row; it shares `max_enrichment_per_run` with the backlog.
5. **Detail enrichment** — Gallery, source-declared floorplans, descriptions, image cache, EPC removal, thumbnails.
6. **Commute** — Advisory TravelTime measurement (`CommuteAssessor`); never removes a home.
7. **Match and consolidate** — Join listings to stored homes first seen in the last 30 days (`pipeline/matching.py`).
8. **Media rule** (`require_floorplan`) — Admit a source-declared floorplan, an OpenRent-only home or enough photos; save the rest as dropped.
9. **Initial assessment** — Two Claude calls (see Quality Analysis); the assessment and alert decision commit together, then live runs try to send.
10. **Reanalysis** — Refresh flagged assessments; never sends an alert.
11. **Delivery drain** — Send `queued`, `ready` and due `waiting` alerts. Live runs only.
12. **Finalize** — Prune old events (live runs), record the run's status.

Homes are saved at each checkpoint (staging row, dropped, `pending_analysis`,
assessment), not in a final save step.

## Gotchas & Constraints

### HTTP Client Selection

**Scrapers MUST use the correct HTTP client to avoid 403 blocks:**

| Component | HTTP Client | Reason |
|-----------|-------------|--------|
| Zoopla scraper | `curl_cffi`, one of four impersonation profiles (`chrome`, `safari`, `chrome131`, `safari184`) picked at random per area | TLS fingerprinting bypasses Cloudflare |
| OnTheMarket scraper | `curl_cffi` with `impersonate="chrome"` | TLS fingerprinting detection |
| Rightmove scraper | `httpx.AsyncClient` | Standard requests work |
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
- **Outcode blocking:** Matching compares listings only within an outcode + bedroom-count block. A full postcode (e.g., "E8 3RH") is one signal, not a requirement: outcode-only listings can still match on coordinates, street, price and image hashes. A listing with no outcode never cross-matches.
- **Two-phase dedup:** Pre-enrichment wraps as single-source (`properties_to_merged`), post-enrichment `deduplicate_merged_async()` merges cross-platform dupes with image/floorplan data.

### Image Caching

- Disk cache at `{data_dir}/image_cache/{safe_id}/` (see `utils/image_cache.py`)
- Detail enrichment downloads and caches images during pipeline step 5
- Quality analysis reads cached images from disk (avoids re-downloading)
- Web dashboard serves cached images via `GET /images/{unique_id}/{filename}` with immutable cache headers
- `image_url_map` in detail template maps original CDN URLs to local `/images/` URLs for cached images
- **Thumbnails**: `thumb_` prefix, max 480px (2x retina for 240px cards). Generated during enrichment *after* EPC detection (avoids orphaned thumbnails for reclassified images). `backfill_thumbnails()` generates missing thumbnails for existing caches (`--generate-thumbnails` CLI). Web dashboard prefers `thumb_` files when available, falls back to originals.
- **EPC detection**: Gallery images identified as EPC charts are renamed from `gallery_*` to `epc_*` on disk and excluded from gallery/quality analysis.

### Quality Analysis

- The experimental `opus_visual_weighted_2026_10_21_v1` rating is available only through
  `python -m home_finder.pipeline.rating_shadow_report --database PATH`. It reports candidate
  ratings, evidence coverage and counterfactual selection; it never replaces live ratings,
  enters either model prompt, writes a database or creates deliveries. Its sparse-evidence
  failures remain visible for review. See the 2026-10-22 follow-up in the model bake-off review.
- Both calls default to `claude-sonnet-5-5` with low effort, `max_tokens: 8192` and no SDK retries (`new_runtime_analysis_config()` in `config.py`). `HOME_FINDER_ANALYSIS_CONFIG` overrides individual phase fields.
- **Two-phase chained analysis** (perception → evaluation), in `analysis/analyzer.py` (`PropertyQualityAnalyzer`):
  - **Phase 1 (Visual)**: Images + listing text → structured observations (kitchen, condition, space, etc.), a 1–5 rating and a summary. Returned through one tool, `property_visual_analysis`, with adaptive thinking and `tool_choice: auto`. The tool is not strict: the provider rejected the strict schema in the 2026-10-10 trial.
  - **Phase 2 (Evaluation)**: Phase 1 JSON + listing text (no images) → listing facts, value assessment, viewing notes, highlights, one-liner. No tools: the answer is JSON schema output (`output_config.format`). Reasoning is `disabled`, which Sonnet 5.5 sends as its lowest thinking mode, `between_tools`.
- Response schemas come from Pydantic models: `_VisualAnalysisResponse` (visual tool, via `_build_tool_schema()`) and `_EvaluationResponse` (via `_eval_json_schema()`), both in `analysis/analyzer.py`; prompts are in `analysis/prompts.py`
- Phase 1 output feeds Phase 2 via `build_evaluation_prompt()` (wraps Phase 1 JSON in `<visual_analysis>` XML tags)
- No partial results: a malformed visual answer (including a missing or out-of-range rating) is sent once more as a second paid attempt; if it is still invalid, or the evaluation answer is invalid, the attempt fails and nothing is saved. A paid visual result is kept as a visual checkpoint, so retrying a failed evaluation call does not pay for vision again. Missing cached images defer the home without spending.
- There is no blanket brace-stripping pass. Two narrow repairs exist. `validate_output` (`analysis/contracts.py`) decodes nested objects or arrays sent as JSON text, in visual answers only (live or from a visual checkpoint). The `PropertyQualityAnalysis` validators in `models/quality.py` (e.g. `unwrap_one_line`) unwrap JSON-wrapped strings in both live and stored analyses.
- Concurrency: at most `quality_concurrency` (15) homes at once, or the spend policy's `max_in_flight` if lower, with `property_analysis_timeout` (600 s) per home for both calls. There is no delay between calls; `DELAY_BETWEEN_CALLS` is used only by `analyze_merged_properties()`, which the pipeline does not call.
- **Spend policy**: every Anthropic request goes through `AnalysisSpendMeter` (`analysis/spend.py`), which reserves the request's worst-case cost in the `paid_attempts` ledger before sending and settles it from reported usage. Without `HOME_FINDER_ANALYSIS_SPEND_POLICY` every paid request is deferred (`spending_not_configured`), so nothing is assessed. At 20 images each attempt reserves $0.64817 (visual) and $0.43192 (evaluation); these are caps, not predicted charges. See the paid-analysis section of `docs/operations.md`.
- Uses prompt caching (`cache_control: ephemeral`) on both system prompts and the visual tool; cache hits never reduce a reservation
- All images read from disk cache (populated during detail enrichment) and sent as base64; no CDN re-downloads during analysis

### Proxy Support

- `proxy_url` config (e.g., `socks5://user:pass@host:port`) for geo-restricted sites
- Passed to the OpenRent, Zoopla and OnTheMarket scrapers, to `DetailFetcher` (its `curl_cffi` requests) and to the off-market checker; Rightmove does not use it

## Architecture Pointers

- **Scrapers** → `src/home_finder/scrapers/` — each implements `BaseScraper.scrape()`, see `base.py` for interface
- **Pipeline** → `src/home_finder/pipeline/` — `flow.py` (stage order), `candidates.py` (candidate preparation), `matching.py`, `assessment.py`, `reanalysis.py`; see `docs/pipeline.md`
- **Filters** → `src/home_finder/filters/` — `CriteriaFilter`, `LocationFilter`, the media rule (`media.py`), fit score and notification policy
- **Commute** → `enrichment/commute.py` — `CommuteAssessor` (advisory; never removes a home)
- **Matching** → `matching/` — `Deduplicator` (`deduplication.py`) and pair scoring (`scoring.py`)
- **Analysis** → `analysis/` — `PropertyQualityAnalyzer` (two-phase: `analyzer.py` + `prompts.py`), spend meter (`spend.py`)
- **Detail Fetcher** → `scrapers/detail_fetcher.py` — per-platform extraction (Rightmove: `PAGE_MODEL` JSON, Zoopla: RSC payload, OpenRent: PhotoSwipe, OTM: Redux state)
- **Models** → `models/` package (import from `home_finder.models`) — `core.py` has `Property` (frozen), `MergedProperty`, `PropertyImage`, `SearchCriteria`, `SOURCE_NAMES`; sibling modules hold quality, spend, delivery and commute models
- **Config** → `config.py` — `pydantic-settings` with `HOME_FINDER_` prefix, key methods: `get_search_areas()`, `get_furnish_types()`, `get_search_criteria()`
- **Database** → `db/storage.py` — `PropertyStorage`, async SQLite with 16 tables (schema in `db/migrations.py`); repositories `processing`, `runs`, `spend` and `deliveries`; paginated dashboard queries in `db/web_queries.py`
- **Notifier** → `notifiers/telegram.py` — aiogram 3.x, photo cards, quality display, venue pins, web dashboard deep links
- **Image Cache** → `utils/image_cache.py` — disk-based, deterministic filenames from URL hash, thumbnail generation, cache coverage checks (EPC renaming happens in `enrichment/details.py`)
- **Address Utils** → `utils/address.py` — `normalize_street_name`, `extract_outcode`
- **Source Knowledge** → `docs/sources/` — per-platform decisions, filter quirks, known bugs, pipeline implications. Update the relevant source doc when changing a scraper's HTTP client, filter params, extraction approach, or anti-bot strategy.

## Web Dashboard Notes

- **HTMX pattern:** Dashboard `GET /` checks `HX-Request` header — returns `_results.html` partial or full `dashboard.html`. Filter form uses `hx-get="/" hx-target="#results" hx-push-url="true"`.
- **Image serving:** `GET /images/{unique_id}/{filename}` serves cached images with directory traversal protection and immutable cache headers.
- **Security:** XSS: the listing page shows one advert cleaned by `clean_advert_text` (`web/annotate.py`), which turns the portal's markup into plain text and drops script and style contents. It is still escaped when rendered: descriptions use `| e | replace("\n", "<br>" | safe)`: escape the text first, then mark only the inserted `<br>` safe. Replacing with a plain string escapes the `<br>` itself, so it renders literally. Leaflet popups use `textContent`. All query params clamped; sort whitelisted against `VALID_SORT_OPTIONS`.
- **Template variables:**
  - Dashboard: `properties`, `properties_json`, `search_areas`, `source_names`, `total`, `page`, `total_pages`, `sort`, `min_price`, `max_price`, `bedrooms`, `min_rating`, `area`
  - Detail: `prop`, `outcode`, `area_context`, `best_description`, `source_names`, `image_url_map`
  - Property card `prop` dict: `unique_id`, `title`, `price_pcm`, `bedrooms`, `postcode`, `image_url`, `quality_rating`, `quality_concerns`, `quality_severity`, `quality_summary`, `sources_list`, `commute_minutes`, `transport_mode`, `min_price`, `max_price`, `latitude`, `longitude`, `status`, `highlights`, `lowlights`, `value_rating`
- **Templates:**
  - `_status_controls.html` — Status pill groups (triage/progress/outcome) for detail page
  - `_macros.html` — Shared Jinja2 macros

## Testing Patterns

- `pytest-asyncio` with `asyncio_mode = "auto"`
- Slow tests excluded by default (`-m 'not slow'`); run with `pytest -m slow`
- Mock HTTP with `pytest-httpx` and `AsyncMock` for curl_cffi/TravelTime
- Common fixtures in `tests/conftest.py`: `sample_property`, `sample_property_no_coords`, `default_search_criteria`
- Use `Property.model_copy()` for variations; `hypothesis` for property-based testing
- Tests that drive the legacy Crawlee scrapers opt in with `@pytest.mark.usefixtures("reset_crawlee_state", "set_crawlee_storage_dir")`; the fixtures are not autouse, and `src/` no longer uses Crawlee
- **Concurrent-safe:** All tests use in-memory SQLite, no shared state — multiple agents/processes can run `pytest` simultaneously without interference

**Test structure:**
- `tests/test_scrapers/` — per-platform scraper tests, pagination, detail parsing and identity
- `tests/test_filters/` — criteria, location, fit score, availability, off-market, shadow rating
- `tests/test_enrichment/` — detail enrichment, commute
- `tests/test_matching/` — dedup scoring, grouping, canonical selection, image evidence
- `tests/test_analysis/` — analyzer, prompts, output validation, spend, visual checkpoints
- `tests/test_pipeline/` — stage orchestration, admission, recovery, run lock, spend admin
- `tests/test_notifiers/` — Telegram messages, notification selection, delivery recovery
- `tests/test_db/` — storage, migrations, spend ledger, transactions, pipeline runs
- `tests/test_web/` — app factory, access, routes, HTMX partials, XSS prevention
- `tests/test_utils/` — image cache, address utils, EPC and floorplan detectors, image hashing
- `tests/test_data/`, `tests/test_scripts/` — area data, maintenance scripts
- `tests/integration/` — pipeline, real scraping (slow)

**Web test patterns:** FastAPI `TestClient` with in-memory SQLite. HTMX tests send `HX-Request: true` header. XSS tests save a raw `<script>` in the description and assert it is gone (the cleaner drops it with its contents), and save an encoded `&lt;script&gt;` and assert it comes back escaped.

## Feature Flags

| Flag | Default | Description |
|------|---------|-------------|
| `enable_quality_filter` | True | Enable Claude vision property analysis |
| `require_floorplan` | True | Media rule before assessment: admit a source-declared floorplan, an OpenRent-only home, or one with at least `min_gallery_for_photo_inference` (5) photos; save the rest as dropped |
| `quality_filter_max_images` | 20 | Max gallery images to analyze (1-20) |
| `enable_image_hash_matching` | True | Perceptual image hashing for `--dedup-existing`; pipeline matching always hashes, whatever this is set to |
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

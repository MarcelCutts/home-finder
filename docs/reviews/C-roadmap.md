# C: Refactoring Roadmap — Prioritized Ticket List

**Date:** 2026-02-16
**Sources:** A1-A5 (vertical layer reviews), B1-B4 (cross-cutting reviews)
**Codebase:** ~14,400 LOC source, ~26,900 LOC tests, 42 source files

## Executive Summary

Across 9 review sessions, we identified **82 findings** (0 critical, 19 major, 36 minor, 27 suggestions). After deduplication and cross-referencing, these consolidate into **36 actionable refactoring tickets** organized into 3 tiers.

The codebase is in **good shape** — frozen Pydantic models, strict mypy, 82% coverage, clear pipeline architecture, enterprise-grade testing patterns (mutant-killing, Hypothesis, prompt snapshots, Playwright). The refactoring targets are about reducing complexity and duplication, not fixing fundamental design flaws.

**The 5 highest-value changes are:**
1. `PropertyFilter` dataclass — eliminates ~400 lines of 18-param forwarding across routes + storage
2. Persist `fit_score` in DB — eliminates full-table Python sort on every page load
3. Extract `_row_to_merged_property` helper — eliminates 160 lines of identical reconstruction code (4x duplication)
4. Extract pagination loop into `BaseScraper` — eliminates 120 lines of identical scraper logic
5. Split `_analyze_property` into 3 methods — makes the 425-line function testable and modifiable

---

## Tier 1: Quick Wins (< 1 hour each)

These require minimal effort, have no dependencies, and can be done in any order. Ideal for warming up or filling gaps between larger tasks.

### T1.1 — Kill bedroom extraction duplicate in zoopla.py
**Source:** A2 | **Effort:** S (5 min) | **Files:** `zoopla.py`

Replace `ZooplaListing._extract_bedrooms_from_text()` (line 169) with a call to the existing `parsing.extract_bedrooms()`. Delete the static method.

---

### T1.2 — Add `exc_info=True` to ~15 error-level log statements
**Source:** B1 | **Effort:** S (30 min) | **Files:** `main.py`, `telegram.py`, `quality.py`, `commute.py`

Systematic pass: add `exc_info=True` to every `logger.error()` or `logger.warning()` inside an `except Exception` block that doesn't already have it. Key locations: main.py:190, telegram.py:718/862/955, quality.py:1037, commute.py:171/262.

---

### T1.3 — Set explicit PIL decompression bomb limit
**Source:** B2 | **Effort:** S (5 min) | **Files:** `image_processing.py`, `floorplan_detector.py`, `image_hash.py`

Add `Image.MAX_IMAGE_PIXELS = 50_000_000` at module level in all 3 files that call `Image.open()` on untrusted image bytes.

---

### T1.4 — Fix Rightmove Hackney fallback
**Source:** A2, B1 | **Effort:** S (15 min) | **Files:** `rightmove.py`

Replace the silent Hackney fallback (lines 299-301, 304) with an `error`-level log + return empty results for the area. The location filter would catch wrong-area results, but the current behavior wastes API calls and creates confusing logs.

---

### T1.5 — Move fit_score dict constants to module level
**Source:** A3, B3 | **Effort:** S (10 min) | **Files:** `fit_score.py`

Promote `_HIGHLIGHT_SCORES` and `_LOWLIGHT_SCORES` (lines 409, 427) from inside `_score_vibe()` to module-level constants. They're recreated on every call despite being static.

---

### T1.6 — Import BROWSER_HEADERS in detail_fetcher.py
**Source:** A2 | **Effort:** S (5 min) | **Files:** `detail_fetcher.py`

Replace the inline headers dict (lines 152-156) with `from home_finder.scrapers.constants import BROWSER_HEADERS`.

---

### T1.7 — Add error handling to module-level JSON loading
**Source:** A1, A3, B1 | **Effort:** S (15 min) | **Files:** `area_context.py`, `location.py`

Wrap the `json.loads(_DATA_PATH.read_text())` calls in try/except with clear error messages:
```python
try:
    _DATA = json.loads(_DATA_PATH.read_text())
except (FileNotFoundError, json.JSONDecodeError) as e:
    raise RuntimeError(f"Failed to load {_DATA_PATH}: {e}") from e
```

---

### T1.8 — Tighten `suppress(Exception)` in storage.py migrations
**Source:** A4, B1 | **Effort:** S (15 min) | **Files:** `storage.py`

Replace 4 `contextlib.suppress(Exception)` blocks (lines 219, 253, 266, 274) with targeted exception handling:
```python
try:
    await conn.execute(...)
except aiosqlite.OperationalError as e:
    if "duplicate column" not in str(e):
        raise
```

---

### T1.9 — Fix CLAUDE.md quality_filter_max_images default
**Source:** A1 | **Effort:** S (2 min) | **Files:** `CLAUDE.md`

CLAUDE.md says default is 10; code says 20. Update CLAUDE.md.

---

### T1.10 — Type OpenRent `link` parameter as `Tag`
**Source:** A2 | **Effort:** S (10 min) | **Files:** `openrent.py`

Change `link` parameter in `_parse_link_text`, `_extract_price_from_html`, `_extract_bedrooms_from_html` (lines 364, 426, 436) from untyped to `Tag`. Remove the `# type: ignore[no-untyped-def]` comments.

---

### T1.11 — Add debug logging to silent utility catches
**Source:** B1 | **Effort:** S (10 min) | **Files:** `image_hash.py`, `image_processing.py`, `floorplan_detector.py`

Add `logger.debug("..._failed", exc_info=True)` to the 3 silent `except Exception` blocks that currently return defaults with no logging.

---

### T1.12 — Add defense-in-depth traversal check on `unique_id`
**Source:** B2 | **Effort:** S (5 min) | **Files:** `routes.py`

Add the same `..` / `/` / `\` check on `unique_id` that already exists for `filename` in `serve_cached_image()` (line 632), or add a comment documenting that `safe_dir_name()` provides the security guarantee.

---

### T1.13 — Validate config CSV fields eagerly at startup
**Source:** A1 | **Effort:** S (10 min) | **Files:** `config.py`

Add a `@model_validator(mode="after")` to `Settings` that calls `get_furnish_types()` and `get_search_areas()` to catch invalid CSV values at startup rather than mid-pipeline.

---

### T1.14 — Move asyncio imports to module level in scrapers
**Source:** A2 | **Effort:** S (5 min) | **Files:** `rightmove.py`, `openrent.py`, `onthemarket.py`

Move `import asyncio` from inside `scrape()` methods to module-level imports for consistency with `zoopla.py` and `base.py`.

---

**Tier 1 Total: 14 tickets, ~2-3 hours combined effort**

---

## Tier 2: Medium Effort (1-4 hours each)

These are the core refactoring tickets — each eliminates significant duplication or complexity.

### T2.1 — Introduce `PropertyFilter` dataclass
**Source:** A4, A5 | **Effort:** M (2-3 hrs) | **Files:** `storage.py`, `routes.py`
**Blocks:** T3.6 (storage splitting)

The 18-parameter filter signature is repeated 6 times across routes + storage (~400 lines of parameter forwarding). Define a `PropertyFilter` Pydantic model:
```python
class PropertyFilter(BaseModel):
    min_price: int | None = None
    max_price: int | None = None
    bedrooms: int | None = None
    # ... all 18 fields with validation
```

This model:
- Replaces `_validate_filters()` in routes.py (validation moves to model validators)
- Replaces 18-param signatures in `get_properties_paginated`, `get_filter_count`, `get_map_markers`
- Can use FastAPI `Depends` for automatic query param binding
- Makes adding a new filter a single-model change

---

### T2.2 — Extract `_row_to_merged_property` helper in storage.py
**Source:** A4 | **Effort:** S-M (45 min) | **Files:** `storage.py`
**Blocks:** T3.6 (storage splitting)

The same ~40-line JSON parsing + MergedProperty construction is duplicated 4 times (lines 607-645, 749-793, 1412-1455, 1678-1721). Extract into a single `_row_to_merged_property(self, row, *, load_images=True) -> MergedProperty` method.

---

### T2.3 — Extract `_build_property_insert` helper in storage.py
**Source:** A4 | **Effort:** M (1 hr) | **Files:** `storage.py`
**Blocks:** T3.6 (storage splitting)

Three INSERT methods (`save_property`, `save_unenriched_property`, `save_pre_analysis_properties`) have near-identical 20+ column INSERT statements. Extract a helper that builds the SQL + params from a Property/MergedProperty, with callers providing only the varying parts (notification_status, enrichment_status).

---

### T2.4 — Extract pagination loop into `BaseScraper`
**Source:** A2 | **Effort:** M (1-2 hrs) | **Files:** `base.py`, `zoopla.py`, `rightmove.py`, `openrent.py`, `onthemarket.py`

All 4 scrapers implement identical pagination + dedup + early-stop + max-results logic (~30 lines each, 120 lines total). Extract a `_paginate(fetch_page_fn, *, known_source_ids, max_results) -> list[Property]` method on `BaseScraper` that each scraper calls with its page-fetching logic.

---

### T2.5 — Split `_analyze_property` into 3 methods
**Source:** A3 | **Effort:** M (1-2 hrs) | **Files:** `quality.py`
**Blocks:** T3.4 (dual response model unification)

The 425-line method handles Phase 1 API call, Phase 2 API call, data cleaning, and model validation. Split into:
- `_run_visual_analysis(content, property_id) -> dict | None`
- `_run_evaluation(visual_data, ...) -> dict`
- `_merge_analysis_results(visual_data, eval_data, bedrooms) -> PropertyQualityAnalysis | None`

Also move `_clean_value`, `_clean_dict`, `_clean_list` to module level (they're pure functions defined inside the method body).

---

### T2.6 — Extract concurrent analysis helper
**Source:** A3 | **Effort:** M (1 hr) | **Files:** `main.py`

`_run_quality_and_save` (lines 672-712) and `run_reanalysis` (lines 1042-1079) share identical semaphore + circuit breaker + as_completed logic (~40 lines). Extract `_run_concurrent_analysis(quality_filter, items, on_result) -> int`.

---

### T2.7 — Extract cross-run dedup from `_run_pre_analysis_pipeline`
**Source:** A3 | **Effort:** S-M (45 min) | **Files:** `main.py`

The 70-line anchor matching + genuinely-new splitting block (lines 440-512) is the most complex logic in the 320-line function. Extract into `_cross_run_deduplicate(deduplicator, merged, storage, re_enrichment_ids) -> list[MergedProperty]`.

---

### T2.8 — Use `Annotated` types for tri-state validators
**Source:** A1 | **Effort:** S-M (45 min) | **Files:** `models.py`
**Blocks:** T3.2 (models splitting)

Replace 15 identical validator methods across 9 models with `Annotated` type aliases:
```python
TriState = Annotated[Literal["yes", "no", "unknown"], BeforeValidator(_coerce_bool_to_tristate)]
```
Fields become `has_dishwasher: TriState = "unknown"` — zero per-model boilerplate, ~90 lines eliminated.

---

### T2.9 — Reuse HTTP clients across calls
**Source:** B3 | **Effort:** M (2 hrs) | **Files:** `postcode_lookup.py`, `quality.py`, `image_hash.py`, `rightmove.py`

Four modules create a new `httpx.AsyncClient` or `curl_cffi.AsyncSession` per individual request. Apply the `DetailFetcher` pattern (create once, reuse, close explicitly):
- `postcode_lookup.py`: Accept optional shared client parameter
- `quality.py:_download_image_as_base64`: Create session in `__init__`, reuse across batch
- `image_hash.py`: Accept optional shared client (behind feature flag, lower priority)
- `rightmove.py:_resolve_outcode_id`: Minor (cached), lowest priority

---

### T2.10 — Wrap CPU-bound PIL operations in `asyncio.to_thread()`
**Source:** B3 | **Effort:** S (30 min) | **Files:** `detail_enrichment.py`, `quality.py`, `image_hash.py`

Wrap synchronous PIL operations in `asyncio.to_thread()`:
```python
# Before:
is_fp, confidence = detect_floorplan(image_bytes)
# After:
is_fp, confidence = await asyncio.to_thread(detect_floorplan, image_bytes)
```
Priority: `_resize_image_bytes` (per-image in quality) > `detect_floorplan` (per gallery image) > `phash` (behind feature flag).

---

### T2.11 — Persist `fit_score` as DB column
**Source:** A4, B3 | **Effort:** M (2-3 hrs) | **Files:** `storage.py`, `main.py`

When `sort=fit_desc`, `get_properties_paginated` loads ALL rows, computes fit_score/breakdown/icons for every row, sorts in Python, then paginates. Fix by:
1. Add `fit_score INTEGER` column to `properties` table
2. Compute and store on `save_quality_analysis` and `complete_reanalysis`
3. Use `ORDER BY fit_score DESC LIMIT ? OFFSET ?` in SQL
4. Only compute `fit_breakdown` and `lifestyle_icons` for the page-sized result set
5. One-time migration to backfill existing rows

---

### T2.12 — Unify Telegram keyboard building
**Source:** A5 | **Effort:** S (15 min) | **Files:** `telegram.py`

`send_property_notification` (lines 684-702) manually builds the same keyboard that `_build_inline_keyboard` already handles for `MergedProperty`. Make `_build_inline_keyboard` accept `Property | MergedProperty` or wrap the single property before calling it.

---

### T2.13 — Extract area context builder
**Source:** A5 | **Effort:** S (30 min) | **Files:** `routes.py`, possibly `area_context.py`

Both `property_detail` (lines 692-735) and `area_detail` (lines 837-862) assemble area context dicts with overlapping logic. Extract `build_area_context(outcode: str) -> dict` for the common fields (description, benchmarks, borough, council_tax, rent_trend). Each route extends with its specific additions.

---

### T2.14 — Extract `_validate_enum_param` helper
**Source:** A5 | **Effort:** S (30 min) | **Files:** `routes.py`

12 string filter parameters follow the identical 3-line strip/validate/whitelist pattern (~50 lines). Extract:
```python
def _validate_enum_param(value: str | None, valid: set[str]) -> str | None:
    if not value: return None
    cleaned = value.strip().lower()
    return cleaned if cleaned in valid else None
```
Note: This becomes unnecessary if T2.1 (PropertyFilter) puts validation in the model. If doing T2.1, skip this.

---

### T2.15 — Standardize HTML escaping in Telegram formatters
**Source:** B2 | **Effort:** S (30 min) | **Files:** `telegram.py`

Apply `html.escape()` consistently to all free-text fields in Telegram formatters. Currently `_format_viewing_notes` escapes but `_format_kitchen_info`, `_format_bathroom_info`, and `_format_space_info` do not escape `notes` fields. Also escape URLs with `html.escape(str(url), quote=True)` in href attributes.

---

### T2.16 — Consolidate test property helpers to conftest factories
**Source:** B4 | **Effort:** M (1-2 hrs) | **Files:** ~10 test files

~10 test files re-implement `_make_property()` / `_make_merged()` locally instead of using the conftest `make_property` and `make_merged_property` factory fixtures (which have auto-incrementing IDs and sensible defaults). Migrate local helpers to factory fixture usage, adding keyword overrides where domain-specific defaults differ.

---

### T2.17 — Combine `compute_fit_score` and `compute_fit_breakdown`
**Source:** A3, B3 | **Effort:** S (30 min) | **Files:** `fit_score.py`

Both functions iterate all 6 scorers independently. When called back-to-back (as in storage.py), every property is scored 12 times instead of 6. Extract `_compute_dimension_results(analysis, bedrooms) -> dict[str, _DimensionResult]` that both functions consume, or have `compute_fit_breakdown` return both breakdown and aggregate score.

---

### T2.18 — Batch ward updates with `executemany`
**Source:** A4, B3 | **Effort:** S (15 min) | **Files:** `storage.py`

Replace the per-row UPDATE loop in `update_wards` (lines 982-1001) with `conn.executemany()`.

---

### T2.19 — Add `run_pipeline()` integration test
**Source:** B4 | **Effort:** M (3-4 hrs) | **Files:** new test file

`run_pipeline()` is the most critical function (1,228 lines, ~50% coverage) with no direct tests. Add an integration test that mocks all external boundaries (scrapers, TravelTime, Anthropic, Telegram) and verifies the full pipeline sequence including retry-unsent, scrape, filter, enrich, dedup, analyze, save, notify.

---

**Tier 2 Total: 19 tickets, ~20-30 hours combined effort**

---

## Tier 3: Strategic Improvements (4+ hours each)

These are larger refactoring efforts that pay off over time. They should be done after related Tier 2 prerequisites.

### T3.1 — Extract `PropertyContextLookup` from quality.py
**Source:** A3 | **Effort:** M-L (2-3 hrs) | **Files:** `quality.py`, `area_context.py`

`analyze_single_merged` spends ~100 lines (lines 621-698) doing area context lookups (borough mapping, council tax, energy costs, crime rates, rent trends, hosting tolerance). Extract into a `build_property_context(outcode, bedrooms, price_pcm)` factory in `area_context.py`. This also consolidates with T2.13 (area context builder in routes).

---

### T3.2 — Split models.py into core + quality models
**Source:** A1 | **Effort:** M (2-3 hrs) | **Files:** `models.py`, new `models/quality.py` or `quality_models.py`
**Depends on:** T2.8 (Annotated types)

Quality analysis models (13 sub-models, 390 lines, 63% of file) are a cohesive unit used only by `quality.py`, `storage.py`, `telegram.py`, and `routes.py`. Extract to a separate module with re-exports from `models/__init__.py` for backward compatibility.

---

### T3.3 — Break up Rightmove card parser and Zoopla detail fetcher
**Source:** A2 | **Effort:** M (2-3 hrs) | **Files:** `rightmove.py`, `detail_fetcher.py`

Two 170-line functions with high cyclomatic complexity:
- `rightmove._parse_property_card()`: Split into per-field extractors (`_extract_id`, `_extract_bedrooms`, etc.)
- `detail_fetcher._fetch_zoopla()`: Split into per-strategy helpers (`_zoopla_images_from_next_data`, `_zoopla_images_from_rsc`, `_zoopla_images_from_html`)

---

### T3.4 — Unify dual response models in quality.py
**Source:** A3 | **Effort:** L (4+ hrs) | **Files:** `quality.py`, `models.py`
**Depends on:** T2.5 (_analyze_property split), T3.2 (models split)

`_VisualAnalysisResponse` and `_EvaluationResponse` structurally mirror models.py quality models but diverge in `extra` mode, validators, and nullable fields. When a field is added, it must be updated in both places. Long-term: generate API schema from storage models with field overrides. Short-term: add cross-referencing tests.

---

### T3.5 — Extract `_run_pre_analysis_pipeline` sub-functions
**Source:** A3 | **Effort:** M (2 hrs) | **Files:** `main.py`
**Depends on:** T2.7 (cross-run dedup extraction)

After T2.7, the remaining 250 lines can be further decomposed:
- `_run_commute_filter(merged, criteria, settings) -> list[MergedProperty]`
- The main function becomes a clean sequence of named steps, each ~20-40 lines.

---

### T3.6 — Split `PropertyStorage` god class by concern
**Source:** A4 | **Effort:** L (4+ hrs) | **Files:** `storage.py`
**Depends on:** T2.1 (PropertyFilter), T2.2 (_row_to_merged), T2.3 (_build_property_insert)

The 2,335-line, ~45-method class spans 8 concerns. Split incrementally:
1. Start with `WebQueryService` (read-only: `get_properties_paginated`, `get_property_detail`, `get_filter_count`, `get_map_markers`) — most easily separable
2. Then `PipelineRepository` (pipeline runs, enrichment retry)
3. A facade `PropertyStorage` delegates for backward compatibility

This is the largest single refactoring. Do it incrementally over multiple sessions after the Tier 2 storage tickets reduce duplication.

---

### T3.7 — Move fit_score computation out of storage layer
**Source:** A4 | **Effort:** M (1 hr) | **Files:** `storage.py`, `routes.py`

`PropertyStorage` imports `fit_score` and `HOSTING_TOLERANCE` — a layer violation. Move fit score computation and hosting tolerance injection to the web route layer (the caller). The storage layer returns raw data; the route enriches it.

Note: T2.11 (persist fit_score) partially addresses this by computing on save rather than on read, but the layer violation for `compute_fit_breakdown` and `compute_lifestyle_icons` remains for the paginated results.

---

### T3.8 — Add circuit breaker half-open state
**Source:** B1 | **Effort:** M (2 hrs) | **Files:** `quality.py`

The quality.py circuit breaker is one-way — once tripped, it stays open until the `PropertyQualityFilter` instance is garbage collected. In `--serve` mode, a transient API outage permanently disables quality analysis until server restart. Add a half-open state with configurable cooldown (e.g., 5 minutes) that allows recovery.

---

### T3.9 — Shared location mappings data file
**Source:** A2 | **Effort:** M-L (2-3 hrs) | **Files:** `zoopla.py`, `rightmove.py`, new `data/location_mappings.py`

`BOROUGH_AREAS` (Zoopla), `RIGHTMOVE_LOCATIONS`/`RIGHTMOVE_OUTCODES` (Rightmove), and `OUTCODE_BOROUGH` (area_context.py) duplicate geographic mapping data in different formats. Consolidate into a shared data source so adding a new search area is a single-file change.

---

### T3.10 — Add Content-Security-Policy header
**Source:** B2 | **Effort:** S-M (1-2 hrs) | **Files:** `app.py`

The security middleware sets X-Content-Type-Options, X-Frame-Options, and Referrer-Policy but no CSP. Requires auditing CDN dependencies (htmx, leaflet, markercluster), inline scripts, and font/image sources. Template inline scripts use `| safe` JSON injection which is safe (json.dumps escapes `</script>`) but should be verified.

---

**Tier 3 Total: 10 tickets, ~25-35 hours combined effort**

---

## Dependency Graph

```
T2.8 (Annotated types) ──────────────────────────────> T3.2 (Split models.py)
                                                            |
T2.5 (Split _analyze_property) ──> T3.4 (Unify response models)

T2.7 (Extract cross-run dedup) ──> T3.5 (Split _run_pre_analysis)

T2.1 (PropertyFilter) ───────────┐
T2.2 (_row_to_merged_property) ──┼─> T3.6 (Split storage.py)
T2.3 (_build_property_insert) ───┘
                                      T3.7 (Move fit_score out of storage)

T2.14 (Filter validation) ──[superseded by]──> T2.1 (PropertyFilter)
```

Most Tier 1 tickets have no dependencies and can be done in any order.
Most Tier 2 tickets are independent of each other (except T2.14 → T2.1).
Tier 3 tickets depend on specific Tier 2 prerequisites as shown.

---

## Suggested Implementation Order

### Sprint 1: Quick Wins + Foundation (1 day)
Pick up all Tier 1 tickets (T1.1-T1.14). These are low-risk, independent, and immediately improve code quality. Combined effort: ~2-3 hours.

### Sprint 2: Storage Deduplication (half day)
1. T2.2 — Extract `_row_to_merged_property` (45 min)
2. T2.3 — Extract `_build_property_insert` (1 hr)
3. T2.18 — Batch ward updates (15 min)

These remove the most duplicated code in the largest file. Combined effort: ~2 hours.

### Sprint 3: PropertyFilter (half day)
1. T2.1 — Introduce `PropertyFilter` dataclass (2-3 hrs)
   - Subsumes T2.14 (filter validation helper)

This is the single highest-value refactoring across the entire codebase.

### Sprint 4: Pipeline Clarity (1 day)
1. T2.7 — Extract cross-run dedup (45 min)
2. T2.6 — Extract concurrent analysis helper (1 hr)
3. T2.5 — Split `_analyze_property` (1-2 hrs)
4. T2.17 — Combine fit_score functions (30 min)

These make the two most complex files (main.py, quality.py) navigable.

### Sprint 5: Scraper + Performance (1 day)
1. T2.4 — Extract pagination loop (1-2 hrs)
2. T2.9 — Reuse HTTP clients (2 hrs)
3. T2.10 — asyncio.to_thread for PIL (30 min)
4. T2.11 — Persist fit_score in DB (2-3 hrs)

These address the most impactful performance issues.

### Sprint 6: Testing + Polish (half day)
1. T2.16 — Consolidate test helpers (1-2 hrs)
2. T2.12 — Unify Telegram keyboard (15 min)
3. T2.13 — Extract area context builder (30 min)
4. T2.15 — HTML escaping in Telegram (30 min)

### Sprint 7+: Strategic (ongoing)
Tier 3 tickets as time allows, in dependency order. T3.6 (split storage) is the largest but most impactful long-term change — do it incrementally after Sprint 2-3 lay the groundwork.

---

## Risk Register

| Scenario | Likelihood | Impact | Current Mitigation | Recommended |
|----------|------------|--------|-------------------|-------------|
| Scraper returns wrong-area results | Medium | Low (confusing data) | Location filter catches most | T1.4: Fix Hackney fallback |
| Quality API outage in --serve mode | Low | Medium (analysis stops permanently) | Circuit breaker, but no recovery | T3.8: Half-open state |
| Malicious image causes memory exhaustion | Low | Medium (process crash) | Pillow default 178M pixel limit | T1.3: Explicit lower limit |
| Storage migration silently fails | Low | High (missing columns) | suppress(Exception) swallows errors | T1.8: Tighten to OperationalError |
| New filter requires 6-file change | Certain (every filter addition) | Low (dev friction) | Manual coordination | T2.1: PropertyFilter model |
| Production debugging hindered | Medium | Medium (slow incident response) | Some exc_info present | T1.2: Add exc_info everywhere |
| fit_sort page load slows with data growth | Medium | Low (personal use) | Works for hundreds | T2.11: Persist fit_score |
| Event loop blocked during image batch | Medium | Low (slight latency) | Fast for small batches | T2.10: asyncio.to_thread |
| run_pipeline() breaks silently | Low | High (no notifications) | Manual verification | T2.19: Integration test |

---

## Metrics Summary

| Category | Tier 1 | Tier 2 | Tier 3 | Total |
|----------|--------|--------|--------|-------|
| Tickets | 14 | 19 | 10 | 43 |
| Estimated effort | 2-3 hrs | 20-30 hrs | 25-35 hrs | 47-68 hrs |
| Lines eliminated (est.) | ~50 | ~800 | ~500 | ~1,350 |
| Files touched | ~15 | ~20 | ~15 | ~30 (unique) |

### Finding Distribution by Review Session

| Session | Findings | Contributed to tickets |
|---------|----------|----------------------|
| A1: Models & Config | 13 | T1.9, T1.13, T2.8, T3.2 |
| A2: Scrapers | 15 | T1.1, T1.4, T1.6, T1.10, T1.14, T2.4, T3.3, T3.9 |
| A3: Filters & Pipeline | 18 | T1.5, T2.5, T2.6, T2.7, T2.17, T3.1, T3.4, T3.5 |
| A4: Database | 15 | T1.8, T2.1, T2.2, T2.3, T2.11, T2.18, T3.6, T3.7 |
| A5: Web + Notifications | 15 | T2.12, T2.13, T2.14, T3.10 |
| B1: Error Handling | 15 | T1.2, T1.7, T1.11, T3.8 |
| B2: Security | 14 | T1.3, T1.12, T2.15, T3.10 |
| B3: Performance | 16 | T1.5, T2.9, T2.10, T2.11, T2.17, T2.18 |
| B4: Testing | 16 | T2.16, T2.19 |

### Cross-Session Deduplication

Several issues were independently identified by multiple sessions, confirming their significance:
- **suppress(Exception) in migrations** — A4 + B1
- **Hackney fallback** — A2 + B1
- **Module-level JSON loading** — A1 + A3 + B1
- **fit_sort full table scan** — A4 + B3
- **fit_score double computation** — A3 + B3
- **fit_score constants inside function** — A3 + B3
- **HTTP client per-call pattern** — B3 (4 modules)
- **18-parameter filter duplication** — A4 + A5
- **ward update batching** — A4 + B3

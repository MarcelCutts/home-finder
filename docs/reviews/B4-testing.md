# B4: Testing Quality -- Cross-Cutting Review

**Scope:** 66 test files, ~26,900 LOC of tests against ~14,400 LOC source (81.92% reported coverage)
**Date:** 2026-02-16

## Executive Summary

The test suite is **exceptionally well-structured for a personal project**, exhibiting professional-grade patterns including mutant-killing tests with explicit annotations, property-based testing via Hypothesis, prompt snapshot regression with inline-snapshot, multi-phase E2E dedup tests with realistic London rental data, and Playwright browser tests covering responsive layout, HTMX partials, and filter modal lifecycle. Test infrastructure is strong: autouse fixtures isolate `.env` leakage and detect leaked aiosqlite threads, factory fixtures with auto-incrementing IDs reduce boilerplate, and in-memory SQLite ensures test parallelism.

The main areas of concern are: (1) **helper duplication** -- `_make_property` / `_make_merged` are re-implemented in ~10 test files rather than using the conftest factory fixtures, (2) **coverage gaps in orchestration** -- `run_pipeline()` and `--serve` mode in main.py (~1,228 LOC, ~50% coverage) have no direct tests, (3) **soft assertions in leakage tests** with a commented-out `pytest.fail` that silently passes even when leakage is detected, (4) **module-level cache pollution risk** -- `_outcode_cache` and `CommuteFilter._geocoding_cache` are cleared in limited scopes, and (5) **Crawlee state reset is not autouse** -- integration tests that forget `@pytest.mark.usefixtures("reset_crawlee_state")` will get mysterious event-loop errors.

## Coverage Analysis

| Module | LOC (approx.) | Test Files | Estimated Coverage | Notes |
|--------|----------:|-----------|-------------------|-------|
| `models.py` | ~750 | `test_models.py`, `test_property_based.py` | ~95% | Hypothesis invariants, frozen immutability, tri-state validators |
| `config.py` | ~200 | `test_config.py` | ~90% | Settings parsing, search areas, furnish types |
| `db/storage.py` | ~2,334 | 6 test files in `test_db/` | ~90% | Comprehensive: CRUD, pagination, enrichment retry, reanalysis, pipeline runs |
| `filters/quality.py` | ~1,234 | `test_quality.py`, `test_quality_eval.py`, `test_quality_prompts.py`, `test_quality_respx.py` | ~85% | Multi-layer: SDK mock, HTTP-level (respx), prompt snapshots, live API |
| `filters/deduplication.py` | ~600 | `test_deduplication.py`, `test_deduplication_weighted.py`, `test_deduplication_e2e.py`, `test_cross_platform_rerun.py` | ~95% | Mutant-killing, Hypothesis invariants, realistic E2E, cross-platform rerun |
| `scrapers/zoopla.py` | ~800 | `test_zoopla.py`, `test_zoopla_models.py` | ~80% | Cloudflare detection, retry logic, profile rotation; curl_cffi mocked via SimpleNamespace |
| `scrapers/rightmove.py` | ~531 | `test_rightmove.py` | ~65% | URL building, HTML parsing, outcode resolver; Crawlee integration untested |
| `scrapers/detail_fetcher.py` | ~684 | `test_detail_fetcher_parsing.py` | ~80% | All 4 platform parsers; download routing; EPC filtering |
| `scrapers/openrent.py` | ~400 | `test_openrent.py` | ~80% | JS array extraction, dedup, pagination |
| `scrapers/onthemarket.py` | ~300 | `test_onthemarket.py` | ~75% | `__NEXT_DATA__` JSON parsing, curl_cffi usage |
| `main.py` | ~1,228 | `test_pipeline_orchestration.py`, `test_reanalysis_orchestration.py` | ~50% | Pre-analysis pipeline tested; `run_pipeline()` and `--serve` untested |
| `web/routes.py` | ~700 | `test_routes.py`, `test_browser_e2e.py` | ~90% | HTMX partials, XSS prevention, filters, Playwright browser tests |
| `notifiers/telegram.py` | ~1,000 | `test_telegram.py`, `test_telegram_web.py` | ~85% | Message formatting, album sending, web dashboard buttons |
| `utils/` | ~800 (all utils) | 6 test files | ~85% | Image cache, address, cost calculator, floorplan, image hash, postcode lookup |
| `data/area_context.py` | ~200 | `test_area_context.py` | ~90% | Data integrity, ward mapping, micro-area matching |
| `filters/fit_score.py` | ~500 | `test_fit_score.py` | ~90% | Marcel Fit Score dimensions, direct unit tests with exact point values |

## Findings

### Finding 1: Helper Duplication Across Test Files
**Severity:** Minor | **Theme:** Maintainability | **Effort:** Low

`_make_property()` and `_make_merged()` helper functions are independently re-implemented in approximately 10 test files:
- `tests/test_reanalysis_orchestration.py` (lines 35-63)
- `tests/test_pipeline_orchestration.py`
- `tests/test_filters/test_deduplication_e2e.py`
- `tests/test_filters/test_cross_platform_rerun.py`
- `tests/test_filters/test_detail_enrichment.py`
- `tests/test_db/test_storage.py`
- `tests/test_db/test_storage_quality.py`
- `tests/test_db/test_storage_enrichment.py`
- `tests/test_db/test_storage_analysis_retry.py`
- `tests/test_db/test_storage_reanalysis.py`

Meanwhile, `tests/conftest.py` already provides `make_property` and `make_merged_property` factory fixtures with auto-incrementing IDs and sensible defaults. These local re-implementations create slightly different Property instances (different URLs, prices, postcodes) making it harder to reason about test data consistency.

**Recommendation:** Migrate local helpers to use the conftest factory fixtures, adding keyword overrides where domain-specific defaults differ. For the reanalysis tests that require file-backed SQLite, the fixtures can still be used -- only the storage fixture differs.

---

### Finding 2: No Tests for `run_pipeline()` or `--serve` Mode
**Severity:** Major | **Theme:** Coverage gap | **Effort:** Medium

`main.py` is the largest source file at 1,228 lines, yet only ~50% is covered by tests. The tested functions are mid-level orchestration (`_save_one`, `_run_quality_and_save`, `scrape_all_platforms`, `_run_pre_analysis_pipeline`, `run_reanalysis`). Critically, the top-level `run_pipeline()` function and the `--serve` mode (web dashboard + recurring pipeline scheduler) have zero test coverage.

`run_pipeline()` contains significant orchestration logic:
- Retry unsent notifications
- Scrape -> filter -> enrich -> dedup -> analyze -> save -> notify sequence
- Error handling for the full pipeline
- Pipeline run tracking (create/update/complete)

The `--serve` mode starts uvicorn with a background pipeline scheduler.

**Recommendation:** Add an integration test for `run_pipeline()` that mocks all external boundaries (scrapers, TravelTime, Anthropic, Telegram) and verifies the full pipeline sequence. For `--serve`, a smoke test that starts the server, verifies `/health`, and checks the pipeline scheduler was registered would close the gap.

---

### Finding 3: Soft Assertions in Location Leakage Tests
**Severity:** Minor | **Theme:** Test design | **Effort:** Low

`tests/test_scrapers/test_scraper_location_leakage.py` contains live integration tests that detect when scrapers return properties from outside the search area. However, the assertion that would fail the test is commented out (line 451):

```python
# Soft assertion - document the issue but don't fail the test
# Comment out the pytest.fail if you just want to gather data
# pytest.fail(
#     f"Location leakage detected: {leakage_count}/{total} properties "
#     f"({leakage_pct}%) are from outside {search_area}"
# )
```

This means these tests pass even when leakage is detected, providing a false sense of security. The tests also duplicate `extract_outcode()` locally (lines 115-128) instead of importing `home_finder.utils.address.extract_outcode`.

**Recommendation:** Either (a) enable the assertion with a threshold (e.g., fail if >20% leakage) so the test catches regressions, or (b) convert to a data-collection script rather than a test, since tests that always pass regardless of outcome are misleading. Also import `extract_outcode` from `utils.address` instead of duplicating it.

---

### Finding 4: Module-Level Cache Pollution Risk
**Severity:** Minor | **Theme:** Test isolation | **Effort:** Low

Several modules maintain module-level or class-level caches that can leak between tests:

1. **`_outcode_cache`** in `rightmove.py`: Cleared by an autouse fixture, but that fixture is scoped to `TestRightmoveOutcodeResolver` class only (in `test_rightmove.py`). If any other test file triggers Rightmove outcode resolution, stale cache entries could affect results.

2. **`CommuteFilter._geocoding_cache`**: A class-level dict manually cleared in `test_commute.py` via explicit `CommuteFilter._geocoding_cache.clear()` calls. No autouse fixture ensures this.

3. **`_AREA_CONTEXT`** and related module-level dicts in `data/area_context.py`: These are read-only so not a mutation risk, but they are loaded at import time.

**Recommendation:** For `_outcode_cache`, add a session-scoped or module-scoped autouse fixture in the root conftest that clears it. For `_geocoding_cache`, add an autouse fixture in `test_commute.py` (or root conftest) that clears it before each test.

---

### Finding 5: Crawlee State Reset Fixture Is Not Autouse
**Severity:** Minor | **Theme:** Test isolation | **Effort:** Low

The `reset_crawlee_state` fixture in `tests/integration/conftest.py` is explicitly documented as "Not autouse -- only tests that exercise real Crawlee scrapers need this." Tests must opt-in via `@pytest.mark.usefixtures("reset_crawlee_state")`.

This is a deliberate design choice (avoids unnecessary imports for non-Crawlee tests), but it means any new integration test that uses Crawlee scrapers but forgets the fixture will get cryptic "attached to a different event loop" errors. The error message does not point to the missing fixture.

**Recommendation:** Add a comment at the top of the integration conftest explaining the symptom ("event loop" errors) and the fix. Alternatively, make it autouse within the integration directory only (it already is scoped there), since all integration tests in that directory are Crawlee-adjacent.

---

### Finding 6: Duplicate `sample_property` Fixture With Different Values
**Severity:** Minor | **Theme:** Test isolation | **Effort:** Low

`tests/conftest.py` defines `sample_property` with:
- `url=HttpUrl("https://www.openrent.com/property/12345")`
- `title="Spacious 1-bed flat in Hackney"`
- `price_pcm=1850`

`tests/test_notifiers/conftest.py` overrides `sample_property` with:
- `url=HttpUrl("https://openrent.com/property/12345")` (different domain format -- no `www`)
- `title="1 Bed Flat, Mare Street"`
- `price_pcm=1900`

Both fixtures use the same `source_id="12345"`, so they produce the same `unique_id`. This creates a subtle trap: any test outside `test_notifiers/` that uses `sample_property` gets the root conftest version, but tests inside `test_notifiers/` get a different property with different price and title. If someone copies a test between directories, the data silently changes.

**Recommendation:** Rename the notifier-specific fixture to `notifier_sample_property` or use the `make_property` factory with explicit overrides to make the difference intentional and visible.

---

### Finding 7: Zoopla Scraper Tests Use Fragile Instance Variable Injection
**Severity:** Minor | **Theme:** Mock quality | **Effort:** Low

Several tests in `test_zoopla.py` directly set private instance variables on the scraper to bypass initialization:

```python
scraper._session = mock_session
scraper._warmed_up = True
```

This couples tests to the internal implementation. If the attribute names change (e.g., refactoring to use a session manager), tests break without the production code being wrong.

**Recommendation:** Consider adding a `create_for_testing()` classmethod or making the session injectable via the constructor, so tests can provide a mock session without reaching into private state.

---

### Finding 8: Browser E2E Tests Have Conditional Assertions
**Severity:** Minor | **Theme:** Test design | **Effort:** Low

Several Playwright tests in `test_browser_e2e.py` wrap assertions in conditionals:

```python
# test_gallery_lightbox (line 339-344)
gallery_imgs = page.locator(".gallery img, .gallery-image, [data-lightbox] img")
if gallery_imgs.count() > 0:
    gallery_imgs.first.click()
    lightbox = page.locator(".lightbox, .lightbox-overlay, [role='dialog']")
    if lightbox.count() > 0:
        assert lightbox.first.is_visible()

# test_map_markers (line 397-399)
markers = page.locator(".leaflet-marker-icon")
if markers.count() > 0:
    assert markers.count() >= 1
```

These patterns silently pass if the selectors find nothing (e.g., due to template changes), defeating the purpose of E2E verification. The `if count > 0: assert count >= 1` pattern is tautological.

**Recommendation:** Use Playwright's `expect()` with timeouts instead: `expect(page.locator(".leaflet-marker-icon")).to_have_count(at_least=1)`. If the element is genuinely optional, document why.

---

### Finding 9: Postcode Lookup Tests Have Verbose Mock Setup
**Severity:** Suggestion | **Theme:** Mock quality | **Effort:** Low

`test_postcode_lookup.py` repeats the same 5-line AsyncClient mock pattern in every test:

```python
with patch("home_finder.utils.postcode_lookup.httpx.AsyncClient") as mock_client:
    instance = AsyncMock()
    instance.get = AsyncMock(return_value=mock_resp)
    mock_client.return_value.__aenter__ = AsyncMock(return_value=instance)
    mock_client.return_value.__aexit__ = AsyncMock(return_value=None)
```

This is repeated 7 times across 3 test classes.

**Recommendation:** Extract into a fixture or use `pytest-httpx` (already a project dependency) which handles the mocking automatically and more cleanly.

---

### Finding 10: Reanalysis Tests Use File-Backed SQLite While Others Use In-Memory
**Severity:** Suggestion | **Theme:** Test infrastructure | **Effort:** None (informational)

`test_reanalysis_orchestration.py` uses file-backed SQLite (`tmp_path / "reanalysis_test.db"`) rather than `:memory:`, which is different from every other test file in the project. The docstring explains this is for "realistic DB interaction."

This is a valid design choice but creates an asymmetry. File-backed SQLite is slightly slower and creates temporary files, but it also catches WAL/journal issues that in-memory databases hide.

**Recommendation:** No action needed -- this is a reasonable trade-off. Documenting the rationale (already done in the docstring) is sufficient.

---

### Finding 11: Property-Based Tests May Mask Regressions Under `fast` Profile
**Severity:** Minor | **Theme:** Test design | **Effort:** Low

The Hypothesis `fast` profile (default in development) runs only 10 examples:

```python
settings.register_profile("fast", max_examples=10)
```

For property-based tests in `test_property_based.py` that verify symmetry, monotonic decay, and triangle inequality of the scoring system, 10 examples may not be sufficient to catch boundary violations. The `ci` profile (200 examples) provides better coverage but only runs in CI.

**Recommendation:** Consider bumping the `fast` profile to 25-50 examples, which is still fast but provides meaningfully better coverage for the scoring invariants.

---

### Finding 12: Cost Calculator Tests Pin Reference Values
**Severity:** Suggestion (positive) | **Theme:** Test design | **Effort:** None

`test_cost_calculator.py` pins exact reference values from `area_context.json` at the top of the file:

```python
HACKNEY_BAND_C = 146
HACKNEY_BAND_D = 164
ENERGY_D_1BED = 106
```

This is an excellent pattern -- tests break loudly when source data changes, preventing silent drift. This pattern should be replicated in other tests that depend on static data.

---

### Finding 13: Mutant-Killing Test Annotations Are Exemplary
**Severity:** Suggestion (positive) | **Theme:** Test design | **Effort:** None

Tests in `test_deduplication.py` and `test_deduplication_weighted.py` include explicit mutant-killing annotations:

```python
def test_exact_threshold_matches(self):
    """Score == threshold passes. Kills 'MATCH_THRESHOLD > 60' mutant."""
```

This practice documents which specific mutation each boundary test targets, making it clear that the test exists for a reason beyond simple coverage. This is a rare and valuable pattern.

---

### Finding 14: Quality Filter Tests Provide Multi-Layer Coverage
**Severity:** Suggestion (positive) | **Theme:** Test design | **Effort:** None

The quality filter has four complementary test files providing defense in depth:

1. **`test_quality.py`** (~1,900 LOC): SDK-level mocking -- tool schema validation, two-phase pipeline, circuit breaker, backward compatibility validators
2. **`test_quality_respx.py`**: HTTP-level mocking via `respx` -- golden response payloads, request verification, prompt caching headers
3. **`test_quality_prompts.py`**: Prompt snapshot regression via `inline-snapshot` -- detects accidental prompt drift
4. **`test_quality_eval.py`**: Live Claude API tests (marked slow, skipped without API key)

This layered approach means a change to the quality analysis must satisfy structural, behavioral, textual, and integration constraints simultaneously.

---

### Finding 15: E2E Dedup Tests Model Real London Rental Data
**Severity:** Suggestion (positive) | **Theme:** Test design | **Effort:** None

`test_deduplication_e2e.py` creates a realistic scenario with 7 properties across 4 platforms, modeling actual cross-platform duplication patterns observed in London rental listings. The test verifies:
- Transitive merge chains (A matches B, B matches C => all three merge)
- DB anchor mixing (previously-seen properties merged with new discoveries)
- Hypothesis invariants (dedup is idempotent, never increases property count)

This provides much higher confidence than isolated unit tests of individual scoring functions.

---

### Finding 16: Browser E2E Tests Cover Filter Modal Lifecycle
**Severity:** Suggestion (positive) | **Theme:** Test design | **Effort:** None

`test_browser_e2e.py` includes a comprehensive `TestFilterBehavior` class (lines 419-640) that tests:
- No auto-apply on select change (radio or dropdown)
- Modal opens, stays open during filter changes, closes on Apply
- Modal count updates live via HTMX `/count` endpoint
- Reset clears both modal and primary filters
- Close without Apply preserves previous results
- Empty state shows "No properties found" with reset link
- Filter chips appear and can be removed

This is unusually thorough browser-level testing for a personal project and catches real UI regressions that unit tests cannot.

## Test Infrastructure Assessment

### Fixtures (conftest.py)

| Fixture | Scope | Autouse | Purpose | Assessment |
|---------|-------|---------|---------|------------|
| `_isolate_settings_from_dotenv` | function | Yes | Prevents `.env` leakage into test Settings | **Excellent** -- critical for CI reproducibility |
| `_cleanup_aiosqlite_threads` | function | Yes | Detects leaked DB connections | **Excellent** -- catches resource leaks early |
| `make_property` | function | No | Factory with auto-incrementing IDs | **Good** -- underutilized by test files |
| `make_merged_property` | function | No | Factory composing `make_property` | **Good** -- underutilized |
| `sample_property` | function | No | Static test property | **Adequate** -- shadowed in `test_notifiers/conftest.py` |
| `enriched_merged_property` | function | No | Multi-source with images/floorplan | **Good** -- used by enrichment tests |
| `sample_quality_analysis` | function | No | Complete analysis object | **Good** -- duplicated in notifier conftest |
| `reset_crawlee_state` | function | No | Clears Crawlee singletons | **Good but risky** -- easy to forget |
| `set_crawlee_storage_dir` | function | No | Isolates Crawlee file storage | **Good** |
| Hypothesis profiles | session | N/A | fast/ci/mutmut with different `max_examples` | **Good** -- `fast` may be too low at 10 |

### Markers

| Marker | Purpose | Usage |
|--------|---------|-------|
| `slow` | Excluded by default; real network tests | Scraper integration tests |
| `integration` | Integration-level tests | Used alongside `slow` |
| `browser` | Playwright browser tests | `test_browser_e2e.py` |
| `e2e` | End-to-end pipeline tests | `test_full_pipeline_e2e.py` |

## Mock Strategy Analysis

### Mock Patterns by Module

| Module | Mock Technique | Assessment |
|--------|---------------|------------|
| **Scrapers (Zoopla, OTM)** | `SimpleNamespace` for curl_cffi response objects | **Adequate** -- lightweight but coupled to response attribute names |
| **Scrapers (Rightmove, OpenRent)** | Mock HTML fixtures loaded from strings | **Good** -- tests parse logic without network |
| **Quality filter** | `AsyncMock` for SDK, `respx` for HTTP, `inline-snapshot` for prompts | **Excellent** -- multi-layer |
| **Telegram** | `_get_bot()` mocked to return `AsyncMock` bot | **Good** -- clean boundary |
| **Database** | Real in-memory SQLite | **Excellent** -- tests real SQL, no mock drift |
| **TravelTime API** | `pytest-httpx` for HTTP mocking | **Good** |
| **Postcode lookup** | Manual `patch("httpx.AsyncClient")` | **Verbose** -- could use pytest-httpx |
| **Image processing** | PIL `Image.new()` creates synthetic images | **Good** -- deterministic |
| **curl_cffi** | `SimpleNamespace(status_code=200, text="...")` | **Adequate** -- type-unsafe |

### Anti-Patterns Identified

1. **Instance variable injection** in Zoopla tests (`scraper._session = mock_session`) -- fragile coupling
2. **Manual AsyncClient mock boilerplate** in postcode tests -- 7 repetitions of 5-line setup
3. **`SimpleNamespace` as response stand-in** -- no type checking, will not fail if response API changes

## Summary by Severity

| Severity | Count | Findings |
|----------|------:|----------|
| Critical | 0 | -- |
| Major | 1 | #2 (no tests for `run_pipeline()` / `--serve`) |
| Minor | 8 | #1 (helper duplication), #3 (soft assertions), #4 (cache pollution), #5 (Crawlee reset not autouse), #6 (duplicate fixture), #7 (fragile injection), #8 (conditional assertions), #11 (low Hypothesis examples) |
| Suggestion | 3 | #9 (verbose mock), #10 (file-backed SQLite), #12-16 (positive patterns) |

## Top 3 Takeaways

1. **Test the top-level pipeline orchestration.** `run_pipeline()` is the most critical function in the codebase -- it coordinates scraping, filtering, enrichment, dedup, analysis, and notification. Testing it end-to-end with mocked boundaries would catch integration errors that unit tests of individual stages miss. This is the single highest-value improvement.

2. **Consolidate property/merged helpers into conftest factories.** The `make_property` and `make_merged_property` factory fixtures in conftest already do what the 10+ local `_make_property` helpers do, but with auto-incrementing IDs and consistent defaults. Migrating to these factories would reduce ~200 lines of duplicated test code and ensure data consistency across the suite.

3. **The test suite's quality patterns are exemplary.** Mutant-killing annotations, multi-layer quality filter testing (SDK + HTTP + prompt snapshot + live API), Hypothesis invariants for scoring algorithms, Playwright browser tests for filter modals, and prompt regression via inline-snapshot collectively set a high bar. These patterns should be preserved and extended as the codebase evolves.

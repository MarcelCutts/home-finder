# A3: Filters & Pipeline Review

**Scope:** `main.py` (1228L), `quality.py` (1234L), `fit_score.py` (748L), `quality_prompts.py` (375L), `deduplication.py` (340L), `detail_enrichment.py` (338L), `scoring.py` (320L), `commute.py` (315L), `location.py` (133L), `criteria.py` (41L)
**Total:** 5,072 LOC | **Date:** 2026-02-16

## Executive Summary

The filter and pipeline layer is **well-architected at the macro level** — clear pipeline stages, good separation between scraping/filtering/analysis, and solid resilience patterns (circuit breaker, crash recovery, enrichment retry). The main issues are:

1. **`_run_pre_analysis_pipeline` is 320 lines** — a single function orchestrating 7+ pipeline steps with complex cross-run dedup logic
2. **`_analyze_property` is 425 lines** — the largest method in the codebase, doing Phase 1 + Phase 2 API calls, error handling, data cleaning, and model validation
3. **Dual response model maintenance burden** — `quality.py` defines `_VisualAnalysisResponse` and `_EvaluationResponse` which mirror but diverge from the `models.py` quality models
4. **Quality analysis concurrent task pattern duplicated** — `_run_quality_and_save` and `run_reanalysis` share identical semaphore+circuit breaker+as_completed logic
5. **fit_score.py has high cyclomatic complexity** — 6 scorers + 6 icon functions with repetitive dict-access boilerplate

The pipeline's resilience design is impressive: crash recovery via `pending_analysis` status, enrichment retry with max attempts, circuit breaker for API outages, and anchor-based cross-run dedup.

---

## Findings

### [MAJOR] main.py:218-540 — _run_pre_analysis_pipeline is 320 lines with 7+ steps

**Theme:** Complexity | **Effort:** M

`_run_pre_analysis_pipeline` orchestrates the entire pre-analysis pipeline in a single function: criteria filtering, location filtering, merging, new-property filtering, unenriched retry loading, commute filtering, enrichment, cross-run dedup with anchor matching (lines 440-512), consumed retry cleanup, and floorplan gating. Each step has its own logging and early-return pattern.

The cross-run dedup block (lines 440-512) is particularly complex — it loads DB anchors, builds URL-to-ID mappings, runs dedup against combined sets, then splits results into genuinely-new vs anchor-updated properties, and cleans up consumed retry rows.

**Recommendation:** Extract the cross-run dedup logic (lines 440-512) into a dedicated function like `_cross_run_deduplicate(deduplicator, merged, storage, re_enrichment_ids) -> list[MergedProperty]`. Consider also extracting the commute filtering block (lines 332-387) into `_run_commute_filter()`. The main function becomes a clean sequence of named steps, each ~20-40 lines.

---

### [MAJOR] quality.py:802-1228 — _analyze_property is 425 lines

**Theme:** Complexity | **Effort:** M

`_analyze_property` is the largest method in the codebase. It handles:
1. Circuit breaker check (line 864)
2. Image block construction (lines 871-897)
3. User prompt building (lines 902-917)
4. Phase 1 API call with error handling (lines 928-1044)
5. Acoustic context mapping (lines 1057-1079)
6. Phase 2 API call with error handling (lines 1081-1138)
7. Response data cleaning (lines 1146-1185)
8. Model validation and space override (lines 1187-1228)

Each section is well-commented, but the total size makes confident modification difficult.

**Recommendation:** Extract into 3-4 methods:
- `_run_visual_analysis(content, property_id) -> dict | None` — Phase 1 API call + error handling
- `_run_evaluation(visual_data, ...) -> dict` — Phase 2 API call + error handling
- `_merge_analysis_results(visual_data, eval_data, bedrooms) -> PropertyQualityAnalysis | None` — cleaning + validation + override
The main `_analyze_property` becomes a ~50-line orchestrator.

---

### [MAJOR] quality.py:130-354 — Dual response models diverge from storage models

**Theme:** Duplication / Coupling | **Effort:** L

`_VisualAnalysisResponse` and `_EvaluationResponse` (225 lines) define the Anthropic API tool schemas. They structurally mirror the storage models in `models.py` (`KitchenAnalysis`, `ConditionAnalysis`, etc.) but differ in:
- `extra="forbid"` vs `extra="ignore"`
- No backward-compat validators (no `_coerce_bool_to_tristate`)
- `feels_spacious: bool` vs `feels_spacious: bool | None`
- Enum literals inline vs imported from models

When a field is added/renamed, it must be updated in **both** places — the API schema here and the storage model in `models.py`. The `analyze_single_merged` method (lines 725-746) manually copies every field from the combined analysis into `PropertyQualityAnalysis`, which is another sync point.

**Recommendation:** Long-term: generate the API schema from the storage models with field overrides (e.g., `_VisualFields = models.KitchenAnalysis.model_json_schema()` with `extra` set to `"forbid"`). Short-term: add a comment cross-referencing the models, and consider a test that validates both schemas have the same fields.

---

### [MAJOR] main.py:672-712 + 1042-1079 — Concurrent analysis pattern duplicated

**Theme:** Duplication | **Effort:** M

`_run_quality_and_save` (lines 672-712) and `run_reanalysis` (lines 1042-1079) share the same pattern:

```python
semaphore = asyncio.Semaphore(_QUALITY_CONCURRENCY)
async def _analyze_one(merged):
    async with semaphore:
        return await quality_filter.analyze_single_merged(merged, ...)
tasks = [asyncio.create_task(_analyze_one(m)) for m in queue]
for coro in asyncio.as_completed(tasks):
    try:
        merged, quality_analysis = await coro
    except APIUnavailableError:
        # Cancel remaining tasks
        ...
    except Exception:
        logger.error(...)
        continue
```

This is ~40 lines duplicated. Bug fixes to the circuit breaker handling or task cancellation logic must be applied in both places.

**Recommendation:** Extract a `_run_concurrent_analysis(quality_filter, items, on_result) -> int` helper that encapsulates the semaphore, task creation, circuit breaker handling, and error recovery. Both `_run_quality_and_save` and `run_reanalysis` call it with different callbacks.

---

### [MINOR] quality.py:123-128 — Mid-file import between function and class

**Theme:** Style | **Effort:** S

```python
# Line 121: end of assess_value() function
# Line 123: import statement
from home_finder.filters.quality_prompts import (  # noqa: E402
    EVALUATION_SYSTEM_PROMPT,
    VISUAL_ANALYSIS_SYSTEM_PROMPT,
    build_evaluation_prompt,
    build_user_prompt,
)
# Line 130: _VisualAnalysisResponse class definition
```

This import sits between `assess_value()` and the response model classes. The `# noqa: E402` confirms it's intentionally out of order. Likely placed here to avoid a circular import, but `quality_prompts.py` has no imports from `quality.py`, so the circular dependency concern may be outdated.

**Recommendation:** Try moving to top-level imports. If there's a genuine circular dependency, add a comment explaining it. If not, move up.

---

### [MINOR] quality.py:1149 — json imported inside method body

**Theme:** Style | **Effort:** S

```python
# Line 1149, inside _analyze_property:
import json as _json
```

`json` is a stdlib module with no import cost. Importing it inside a method body (aliased as `_json` to avoid shadowing) adds confusion. The module already imports `json` indirectly via other modules.

**Recommendation:** Import `json` at module level.

---

### [MINOR] quality.py:1151-1182 — Clean functions defined inside _analyze_property

**Theme:** Testability | **Effort:** S

`_clean_value`, `_clean_dict`, and `_clean_list` are defined as inner functions inside `_analyze_property`. They're pure functions with no closure over local state — they could be module-level functions.

```python
# Inside _analyze_property:
def _clean_value(val: Any) -> Any: ...
def _clean_list(lst: list[Any]) -> list[Any]: ...
def _clean_dict(d: dict[str, Any]) -> dict[str, Any]: ...
```

**Recommendation:** Move to module level. This makes them independently testable and reduces the cognitive load of the already-425-line method.

---

### [MINOR] fit_score.py:409-435 — Constants defined inside function body

**Theme:** Style / Performance | **Effort:** S

`_HIGHLIGHT_SCORES` and `_LOWLIGHT_SCORES` dicts are defined inside `_score_vibe()` (lines 409 and 427). They're recreated on every call despite being constant.

```python
def _score_vibe(analysis: dict[str, Any], _bedrooms: int) -> _DimensionResult:
    # ... 120 lines of code ...
    _HIGHLIGHT_SCORES: dict[str, float] = {
        "Period features": 10,
        "Open-plan layout": 6,
        ...
    }
```

**Recommendation:** Move to module level. These are constants that don't depend on any runtime state.

---

### [MINOR] fit_score.py — Repetitive dict-access boilerplate across all scorers

**Theme:** Duplication | **Effort:** M

All 6 dimension scorers follow the same pattern of defensively accessing nested dicts:

```python
kitchen = analysis.get("kitchen") or {}
hob = kitchen.get("hob_type")
if hob in ("gas", "induction"):
    score += 35
    signals += 1
elif hob and hob not in ("unknown", None):
    signals += 1
```

The `analysis.get("section") or {}` → `.get("field")` → check against known values → accumulate score/signals pattern repeats ~40 times across the file (748 lines total).

**Recommendation:** Consider a small helper that encapsulates the "extract field, score if matches, count signal" pattern:

```python
def _score_field(data: dict, key: str, values: dict[str, float], signals: list[int]) -> float: ...
```

This would reduce each scorer from ~40 lines to ~15, making the weights and logic clearer. Medium effort because the scoring logic varies (some fields have penalty scores, some have special conditions like `bedrooms <= 1`).

---

### [MINOR] commute.py:36 — Class-level mutable geocoding cache

**Theme:** Coupling / Concurrency | **Effort:** S

```python
class CommuteFilter:
    _geocoding_cache: ClassVar[dict[str, tuple[float, float]]] = {}
```

Same issue as the Rightmove outcode cache (A2 finding): class-level mutable state that persists across instances and test runs. In production single-process usage this is fine, but it means test isolation requires manual cache clearing.

**Recommendation:** Either move to instance variable or use `functools.lru_cache`. If keeping class-level, add a `@classmethod` `clear_cache()` for test cleanup. Low priority.

---

### [MINOR] scoring.py:150-172, 175-192 — coordinates_match and prices_match appear unused

**Theme:** Dead code | **Effort:** S

`coordinates_match()` and `prices_match()` are binary match functions that have been superseded by the graduated versions (`graduated_coordinate_score()` and `graduated_price_score()`). The only caller is `calculate_match_score()` which uses the graduated versions.

```python
def coordinates_match(prop1, prop2, max_meters=50) -> bool:  # line 150
def prices_match(price1, price2, tolerance=0.03) -> bool:     # line 175
```

**Recommendation:** Check if these are used in tests or external code. If not, remove them — they're dead code that adds maintenance surface.

---

### [MINOR] main.py:570-612 — _lookup_wards has inline import and mixed async patterns

**Theme:** Coupling | **Effort:** S

`_lookup_wards` imports `postcode_lookup` inside the function body (line 572) and mixes two async patterns: bulk batch for coordinates (line 599) and sequential loop for postcodes (lines 605-608).

```python
async def _lookup_wards(storage: PropertyStorage) -> None:
    from home_finder.utils.postcode_lookup import (
        bulk_reverse_lookup_wards,
        lookup_ward,
    )
    # ... bulk batch for coords, sequential for postcodes
```

The sequential loop (`for p in postcode_props: ward = await lookup_ward(...)`) doesn't batch postcode lookups, which could be slow for many properties.

**Recommendation:** Consider batching postcode lookups similar to the coordinate batch. The inline import is fine if it's to avoid circular imports — add a comment if so.

---

### [MINOR] location.py:21 — JSON loaded at module level with no error handling

**Theme:** Resilience | **Effort:** S

```python
_DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "borough_outcodes.json"
_DATA = json.loads(_DATA_PATH.read_text())
```

Same pattern as `area_context.py` (flagged in A1): module-level JSON load with no error handling. A missing or corrupt `borough_outcodes.json` crashes the entire application at import time with an opaque traceback.

**Recommendation:** Wrap in try/except with a clear error message, same as the A1 recommendation.

---

### [MINOR] detail_enrichment.py:88-230 — _enrich_single mixes orchestration, caching, and backfilling

**Theme:** Complexity | **Effort:** M

`_enrich_single` (142 lines) handles:
1. Iterating sources and fetching detail pages
2. Building `PropertyImage` objects from gallery URLs
3. Downloading and caching image bytes to disk
4. Floorplan detection (structural extraction)
5. Description selection (longest wins)
6. Coordinate/postcode backfilling from detail pages
7. PIL-based floorplan detection in gallery images
8. Copying detected floorplan to cache path

The image download+cache block (lines 130-137) is repeated for both gallery images and floorplans.

**Recommendation:** Extract the image download+cache pattern into a helper: `_cache_image(detail_fetcher, data_dir, unique_id, url, image_type, idx)`. The floorplan detection + cache copy (lines 189-207) could also be a named helper.

---

### [SUGGESTION] quality.py:599-700 — analyze_single_merged has 100 lines of context lookup

**Theme:** Separation of concerns | **Effort:** M

`analyze_single_merged` spends lines 621-698 looking up area context: outcode extraction, borough mapping, council tax, energy costs, crime rates, rent trends, hosting tolerance. This is domain knowledge about London rental data, not quality analysis logic.

```python
borough = OUTCODE_BOROUGH.get(outcode) if outcode else None
council_tax_c = COUNCIL_TAX_MONTHLY.get(borough, {}).get("C") if borough else None
bed_key = f"{min(max(prop.bedrooms, 1), 2)}_bed"
energy_estimate = ENERGY_COSTS_MONTHLY.get("D", {}).get(bed_key)
# ... 15 more lines of similar lookups
```

**Recommendation:** Extract into a `PropertyContextLookup` dataclass + factory function in `area_context.py`:
```python
ctx = build_property_context(outcode, bedrooms, price_pcm)
# ctx.council_tax_c, ctx.crime_summary, ctx.rent_trend, etc.
```
This makes the context assembly testable independently and keeps `quality.py` focused on API interaction.

---

### [SUGGESTION] fit_score.py:512-531 + 534-563 — compute_fit_score and compute_fit_breakdown duplicate iteration

**Theme:** Duplication | **Effort:** S

Both `compute_fit_score` and `compute_fit_breakdown` iterate all dimensions and call all scorers independently:

```python
def compute_fit_score(analysis, bedrooms) -> int | None:
    for dim, weight in WEIGHTS.items():
        result = _SCORERS[dim](analysis, bedrooms)  # Called once here
        ...

def compute_fit_breakdown(analysis, bedrooms) -> list[FitDimension] | None:
    for dim, weight in WEIGHTS.items():
        result = _SCORERS[dim](analysis, bedrooms)  # Called again here
        ...
```

If both are called for the same property, all 6 scorers run twice.

**Recommendation:** Extract a `_compute_dimension_results(analysis, bedrooms) -> dict[str, _DimensionResult]` that both functions consume. Or have `compute_fit_breakdown` return both the breakdown and the aggregate score.

---

### [SUGGESTION] fit_score.py:117,188,240,281 — Unused _bedrooms parameter in 5 of 6 scorers

**Theme:** Interface | **Effort:** S

Five of six dimension scorers accept a `_bedrooms: int` parameter they don't use (prefixed with `_` to suppress lint warnings). Only `_score_workspace` uses it.

```python
def _score_hosting(analysis: dict[str, Any], _bedrooms: int) -> _DimensionResult:
def _score_sound(analysis: dict[str, Any], _bedrooms: int) -> _DimensionResult:
def _score_kitchen(analysis: dict[str, Any], _bedrooms: int) -> _DimensionResult:
def _score_vibe(analysis: dict[str, Any], _bedrooms: int) -> _DimensionResult:
def _score_condition(analysis: dict[str, Any], _bedrooms: int) -> _DimensionResult:
```

This is driven by the `_SCORERS` registry which expects a uniform `(analysis, bedrooms)` signature.

**Recommendation:** This is fine as-is — uniform signatures enable the registry pattern. But if the registry is refactored, consider `**kwargs` or a context dataclass instead.

---

### [SUGGESTION] deduplication.py:58-99 — deduplicate_and_merge_async appears unused in main pipeline

**Theme:** Dead code | **Effort:** S

`deduplicate_and_merge_async` wraps raw `Property` objects into `MergedProperty` and then deduplicates. However, the main pipeline (in `main.py`) uses the two-step approach instead:
1. `properties_to_merged()` — wraps as single-source
2. Later: `deduplicate_merged_async()` — merges after enrichment

The combined `deduplicate_and_merge_async` may only be used in tests.

**Recommendation:** Check usage. If only used in tests, consider marking it as test-only or removing it to reduce the public API surface.

---

### [SUGGESTION] main.py:58-204 — scrape_all_platforms could delegate area iteration to BaseScraper

**Theme:** Coupling | **Effort:** L

`scrape_all_platforms` (146 lines) handles per-area iteration, cross-area dedup, outcode backfilling, area limits, and skip-remaining-areas logic for every scraper. This is orchestration that knows about scraper internals (e.g., `scraper.max_areas_per_run`, `scraper.should_skip_remaining_areas`).

Combined with the A2 finding about pagination loop duplication, both the area-level and page-level loops live in the wrong place — they're outside the scraper classes but contain scraper-specific logic.

**Recommendation:** Consider a `BaseScraper.scrape_all_areas(areas, ...)` method that handles area iteration, cross-area dedup, and area limits internally. `scrape_all_platforms` becomes a simple loop creating scrapers and calling `scrape_all_areas`. This is a larger refactor that should be combined with the A2 pagination extraction. Effort: L.

---

### [SUGGESTION] quality.py:67-120 — assess_value is a standalone utility in the filter module

**Theme:** Coupling | **Effort:** S

`assess_value()` is a pure function that computes value-for-money based on rental benchmarks. It has no dependency on the filter class or Anthropic API — it only uses `area_context` data. It's called from `analyze_single_merged` and also imported by the web routes.

**Recommendation:** Consider moving to `area_context.py` (where the benchmark data lives) or a dedicated `valuation.py` utility module. This would reduce `quality.py`'s responsibilities and make `assess_value` easier to find for non-AI-related callers.

---

## Summary by Severity

| Severity | Count | Key themes |
|----------|-------|-----------|
| Critical | 0 | — |
| Major | 4 | Pipeline function size, analyze_property size, dual response models, concurrent analysis duplication |
| Minor | 9 | Mid-file import, json import, inner functions, constant placement, dict-access boilerplate, geocoding cache, dead code, ward lookup, JSON loading, enrichment complexity |
| Suggestion | 5 | Context lookup extraction, fit_score iteration duplication, unused _bedrooms param, dead dedup method, scraper orchestration |

## Top 3 Takeaways

1. **Extract `_analyze_property` into 3 methods** — At 425 lines, this is the single hardest function to modify confidently. Splitting into `_run_visual_analysis`, `_run_evaluation`, and `_merge_analysis_results` makes each phase independently testable and reduces cognitive load. Estimated 1-2 hours.

2. **Extract concurrent analysis helper** — The semaphore + circuit breaker + as_completed pattern is duplicated between `_run_quality_and_save` and `run_reanalysis`. A shared `_run_concurrent_analysis` helper eliminates ~40 lines of duplication and ensures bug fixes apply to both code paths. Estimated 1 hour.

3. **Extract cross-run dedup from `_run_pre_analysis_pipeline`** — The 70-line anchor matching + genuinely-new splitting block (lines 440-512) is the most complex logic in `main.py` and would benefit from being a named function with its own tests. Estimated 30-45 min.

# A2: Scrapers Review

**Scope:** `base.py` (68L), `constants.py` (9L), `parsing.py` (87L), `zoopla.py` (828L), `rightmove.py` (531L), `openrent.py` (450L), `onthemarket.py` (313L), `detail_fetcher.py` (684L), `__init__.py` (41L)
**Total:** 3,011 LOC | **Date:** 2026-02-16

## Executive Summary

The scraper layer is **functional and battle-tested** with solid anti-bot handling (Zoopla's Cloudflare strategy is particularly well-engineered). The main issues are:

1. **Massive pagination loop duplication** — all 4 scrapers implement near-identical pagination+dedup+early-stop logic (~30 lines each, 120 lines total)
2. **Bedroom extraction duplicated** — `ZooplaListing._extract_bedrooms_from_text()` is a copy-paste of `parsing.extract_bedrooms()`
3. **Rightmove HTML parsing complexity** — `_parse_property_card()` is ~170 lines with 5 cascading fallback strategies for each field, high cyclomatic complexity
4. **detail_fetcher.py duplicates BROWSER_HEADERS** — inlines the same headers dict that `constants.py` exists to share
5. **Broad exception catches** — 13 `except Exception` blocks across the scraper layer with no distinction between expected/unexpected failures

The scraper architecture (BaseScraper ABC + per-platform implementations + shared parsing) is sound. The lazy imports in `__init__.py` are a nice touch for startup perf.

---

## Findings

### [MAJOR] All scrapers — Pagination loop duplicated 4 times
**Theme:** Duplication | **Effort:** M

All four scrapers implement the same pagination loop pattern (~30 lines each):

```python
# In every scraper's scrape() method:
all_properties: list[Property] = []
seen_ids: set[str] = set()

for page in range(MAX_PAGES):
    # ... fetch page ...
    # ... parse properties ...
    if not properties: break

    # Early-stop (identical in all 4)
    if known_source_ids is not None and all(
        p.source_id in known_source_ids for p in properties
    ):
        logger.info("early_stop_all_known", ...)
        break

    # Dedup (identical in all 4)
    new_properties = [p for p in properties if p.source_id not in seen_ids]
    for p in new_properties:
        seen_ids.add(p.source_id)
    if not new_properties: break
    all_properties.extend(new_properties)

    # Max results cap (identical in all 4)
    if max_results is not None and len(all_properties) >= max_results:
        all_properties = all_properties[:max_results]
        break
```

**Recommendation:** Extract a `PaginationLoop` helper (or a `_paginate()` method on `BaseScraper`) that takes a `fetch_page(page_num) -> list[Property]` callback and handles dedup, early-stop, max_results, and inter-page delays. Each scraper only provides the page-fetching logic. This eliminates ~120 lines of near-identical code and ensures bug fixes (e.g., to early-stop logic) apply everywhere.

---

### [MAJOR] zoopla.py:169-183 — Bedroom extraction duplicated from parsing.py
**Theme:** Duplication | **Effort:** S

`ZooplaListing._extract_bedrooms_from_text()` is a line-for-line duplicate of `parsing.extract_bedrooms()`. Both handle "studio" and the `r"(\d+)\s*bed(?:room)?s?"` regex identically. The `ZooplaListing` class already imports from `parsing` via the module, but uses its own copy instead.

```python
# zoopla.py:169 — static method on ZooplaListing
@staticmethod
def _extract_bedrooms_from_text(text: str) -> int | None:
    if not text: return None
    text_lower = text.lower()
    if "studio" in text_lower: return 0
    match = re.search(r"(\d+)\s*bed(?:room)?s?", text_lower)
    return int(match.group(1)) if match else None

# parsing.py:42 — shared module function (identical logic)
def extract_bedrooms(text: str) -> int | None:
    # ... same code ...
```

**Recommendation:** Replace `self._extract_bedrooms_from_text(self.title)` at line 149 with `extract_bedrooms(self.title)` (already imported at module level) and delete the static method.

---

### [MAJOR] rightmove.py:358-526 — _parse_property_card is 170 lines with high complexity
**Theme:** Complexity | **Effort:** M

`_parse_property_card()` has ~5 cascading fallback strategies for each field (property ID, link, address, bedrooms, price, image URL) to handle both old and new Rightmove HTML structures. This results in deeply nested conditional logic and a cyclomatic complexity around 40.

Example — bedroom extraction alone has 4 strategies spanning lines 441-474:
1. New structure: `span.bedroomsCount` class
2. Title parsing: `extract_bedrooms(title)`
3. Old structure: `property-details-lozenge` data-testid
4. Old structure: `propertyCard-tag` li elements

**Recommendation:** Split into `_extract_field_X()` helpers that each try all strategies for one field. This doesn't reduce total code but makes each function single-responsibility and testable in isolation. Alternatively, consider whether the old HTML structures are still encountered — if Rightmove has fully migrated to data-testid, the old class-based selectors are dead code.

---

### [MINOR] detail_fetcher.py:152-156 — BROWSER_HEADERS duplicated inline
**Theme:** Duplication | **Effort:** S

`detail_fetcher.py` inlines the same browser headers that `constants.py` exists to centralize:

```python
# detail_fetcher.py:152-156
"headers": {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
},

# constants.py:5-9 (identical)
BROWSER_HEADERS: Final[dict[str, str]] = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    ...
}
```

**Recommendation:** Import and use `BROWSER_HEADERS` from `constants.py`. One-line fix.

---

### [MINOR] detail_fetcher.py:122-147 — Throttle selection by interval value is fragile
**Theme:** Abstraction | **Effort:** S

`_curl_get_with_retry()` selects the correct throttle (Zoopla/OTM/image) by comparing the `min_interval` float parameter against known constants:

```python
if min_interval >= _ZOOPLA_MIN_INTERVAL:
    lock, attr = self._zoopla_lock, "_zoopla_next_time"
elif min_interval >= _OTM_MIN_INTERVAL:
    lock, attr = self._otm_lock, "_otm_next_time"
else:
    lock, attr = self._image_lock, "_image_next_time"
```

This couples throttle identity to a magic float comparison. If `_ZOOPLA_MIN_INTERVAL` is changed to be less than `_OTM_MIN_INTERVAL`, the logic silently breaks.

**Recommendation:** Accept a throttle name enum/string parameter instead:
```python
async def _curl_get_with_retry(self, url: str, *, throttle: str = "zoopla") -> Any:
```
Or better: create named `_Throttle` dataclass instances that bundle the lock + next_time + interval together.

---

### [MINOR] rightmove.py:182, openrent.py:61, onthemarket.py:63 — asyncio imported inside method
**Theme:** Style / Consistency | **Effort:** S

Three scrapers do `import asyncio` inside `scrape()` instead of at module level. Rightmove and OpenRent use `asyncio.sleep()` for page delays. This is an unusual pattern — `zoopla.py` and `base.py` import asyncio at the top level.

```python
async def scrape(self, ...) -> list[Property]:
    import asyncio  # why not at module level?
```

**Recommendation:** Move to module-level imports for consistency. The likely reason was to avoid importing asyncio when the module is loaded for type-checking only, but `base.py` already imports it globally, so all scrapers already transitively depend on it.

---

### [MINOR] rightmove.py:21 — Module-level mutable cache dict
**Theme:** Coupling / Concurrency | **Effort:** S

```python
_outcode_cache: dict[str, str] = {}
```

This module-level mutable dict caches Rightmove outcode lookups across scraper instances. It's not thread-safe and persists for the process lifetime. In the current single-process usage this is fine, but it's global mutable state that could cause subtle issues in tests or if the scraper is ever used concurrently.

**Recommendation:** Move the cache into `RightmoveScraper` as an instance variable, or use `functools.lru_cache` on `get_rightmove_outcode_id()` (which also handles the "cache forever" semantics more explicitly). Low priority.

---

### [MINOR] zoopla.py:192-224, rightmove.py:75-121 — Large hardcoded location dictionaries
**Theme:** Maintainability | **Effort:** M

`BOROUGH_AREAS` (Zoopla, 24 entries) and `RIGHTMOVE_LOCATIONS` + `RIGHTMOVE_OUTCODES` (Rightmove, 48+24 entries) are large hardcoded dicts mapping borough/outcode names to platform-specific identifiers. They're duplicating the same geographic data in different formats.

**Recommendation:** Consider a shared `data/location_mappings.json` (or Python module) that maps outcodes/boroughs to all platform identifiers in one place. Currently, adding a new search area means updating up to 3 different files. Medium effort because the mappings have different structures per platform.

---

### [MINOR] detail_fetcher.py:299-469 — _fetch_zoopla is 170 lines with 4 fallback strategies
**Theme:** Complexity | **Effort:** M

`_fetch_zoopla()` has cascading fallbacks for images and descriptions:
1. `__NEXT_DATA__` JSON (lines 321-355)
2. RSC taxonomy payload for description (lines 358-381)
3. HTML `<p id="detailed-desc">` fallback (lines 383-394)
4. RSC caption/filename pairs for gallery (lines 399-418)
5. Full URL regex scan for gallery (lines 422-447)
6. `lc.zoocdn.com` regex for floorplan (lines 451-458)

Each fallback is well-commented, but the total complexity makes this hard to modify confidently. The image extraction alone has 3 distinct strategies.

**Recommendation:** Extract each fallback into a named helper: `_zoopla_images_from_next_data()`, `_zoopla_images_from_rsc()`, `_zoopla_images_from_html()`. The main method becomes a clear pipeline of attempts. This also makes each strategy independently testable.

---

### [MINOR] openrent.py:364, 426, 436 — Untyped method parameters
**Theme:** Type safety | **Effort:** S

Three OpenRent methods use `# type: ignore[no-untyped-def]` with untyped `link` parameter:

```python
def _parse_link_text(self, link) -> tuple[str, str | None, str | None]:  # type: ignore[no-untyped-def]
def _extract_price_from_html(self, link) -> int | None:  # type: ignore[no-untyped-def]
def _extract_bedrooms_from_html(self, link) -> int | None:  # type: ignore[no-untyped-def]
```

**Recommendation:** Type `link` as `Tag` (from `bs4`). The module already imports from `bs4` at the top level. This removes the type ignores and enables mypy checking of the method bodies.

---

### [SUGGESTION] All scrapers — _build_search_url could be a standalone function
**Theme:** Testability | **Effort:** S

Each scraper's `_build_search_url()` method is a pure function that doesn't use `self` (except Zoopla which reads `self.BASE_URL`). Making them `@staticmethod` or standalone functions would make them trivially unit-testable without instantiating the scraper.

**Recommendation:** Low priority since they work fine as methods, but worth noting for testability.

---

### [SUGGESTION] openrent.py:216 — Type annotation string with noqa
**Theme:** Style | **Effort:** S

```python
def _parse_search_results(self, soup: "BeautifulSoup", base_url: str) -> list[Property]:  # type: ignore[name-defined]  # noqa: F821
```

The forward reference string `"BeautifulSoup"` with both `type: ignore` and `noqa` suggests an import issue. `BeautifulSoup` is imported at the top of the file (line 7), so the string forward reference is unnecessary. Same pattern at line 328.

**Recommendation:** Change `"BeautifulSoup"` to just `BeautifulSoup` and remove the ignore/noqa comments.

---

### [SUGGESTION] detail_fetcher.py:53-64 — DetailPageData is a mutable dataclass
**Theme:** Consistency | **Effort:** S

`DetailPageData` is a `@dataclass` while every other data structure in the codebase is a frozen Pydantic model. It's mutable and has no validation.

**Recommendation:** Either make it `@dataclass(frozen=True)` for consistency, or convert to a Pydantic `BaseModel` with `frozen=True`. Low priority since it's an internal data transfer object, but the inconsistency is worth noting.

---

### [SUGGESTION] rightmove.py:300-301 — Silent fallback to "hackney" on outcode lookup failure
**Theme:** Resilience | **Effort:** S

When an outcode lookup fails, the scraper silently falls back to the Hackney region:
```python
logger.warning("rightmove_outcode_lookup_failed", outcode=area)
# Fallback to hackney
location_id = RIGHTMOVE_LOCATIONS.get("hackney", "REGION%5E93965")
```

This means a typo in the search areas config (e.g., "EE8" instead of "E8") would silently search Hackney instead of failing visibly. The same pattern appears at line 304.

**Recommendation:** Consider raising an error or returning an empty list instead of silently searching the wrong area. At minimum, log at `error` level rather than `warning`.

---

### [SUGGESTION] zoopla.py:568-602 — Recursive JSON traversal with no max-size guard
**Theme:** Resilience | **Effort:** S

`_extract_listings_from_parsed()` recursively traverses the full RSC JSON structure with a depth limit of 15, but no guard against extremely wide structures (e.g., a list with 100,000 elements). In practice, Zoopla pages are reasonable size, but a malformed response could cause excessive processing.

**Recommendation:** Add a maximum number of listings to collect (e.g., bail after 500 found). Very low priority since the depth limit and page size provide practical bounds.

---

## Summary by Severity

| Severity | Count | Key themes |
|----------|-------|-----------|
| Critical | 0 | — |
| Major | 3 | Pagination loop duplication, bedroom extraction dupe, Rightmove parsing complexity |
| Minor | 7 | Header duplication, throttle selection, imports, mutable cache, location dicts, Zoopla detail complexity, untyped params |
| Suggestion | 5 | Static URL builders, forward ref types, DetailPageData consistency, Hackney fallback, recursive traversal |

## Top 3 Takeaways

1. **Extract pagination loop into BaseScraper** — The dedup+early-stop+max-results pattern is identical across all 4 scrapers (~120 lines of copy-paste). A shared `_paginate()` method with a page-fetch callback eliminates this duplication and ensures consistent behavior. Estimated 1-2 hours.

2. **Kill the bedroom extraction duplicate** — `ZooplaListing._extract_bedrooms_from_text()` is a copy of `parsing.extract_bedrooms()`. One-line fix: call the shared function. 5 minutes.

3. **Break up Rightmove's 170-line card parser and Zoopla's 170-line detail fetcher** — Both are doing too many things with too many fallback strategies. Extracting per-field or per-strategy helpers would make them testable and navigable.

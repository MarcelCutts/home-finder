# B3: Performance & Concurrency -- Cross-Cutting Review

**Reviewer:** Claude (automated)
**Date:** 2026-02-16
**Scope:** All 23 source files across scrapers, filters, database, web, notifiers, and utilities
**LOC reviewed:** ~14,400

---

## Executive Summary

The codebase makes competent use of asyncio overall -- semaphores throttle concurrent work, HTTP clients are reused where it matters most (detail_fetcher, zoopla), and the pipeline orchestration in main.py is well-structured. However, three systemic patterns account for the majority of performance risk:

1. **Fit-score sorting loads the entire result set into memory** and recomputes three expensive functions per row on every page load.
2. **Short-lived HTTP clients are created per-call** in several modules (postcode_lookup, image_hash, rightmove outcode resolution, quality image downloads), wasting TCP+TLS setup and preventing connection reuse.
3. **CPU-bound PIL operations run synchronously on the event loop**, blocking all other async tasks during image resizing, floorplan detection, and perceptual hashing.

None of these are catastrophic for the current dataset size (~hundreds of properties per run), but they will become painful at scale and the fixes are mostly straightforward.

---

## Findings

### F1. `fit_sort` fetches ALL rows then sorts in Python

**Severity:** Major
**Theme:** N+1 / Database
**Effort:** Medium
**File:** `/Users/marcel/projects/labs/home-finder/src/home_finder/db/storage.py` lines 2101-2216

When `sort=fit_desc`, the paginated query skips SQL `LIMIT/OFFSET` and fetches every matching row:

```python
if is_fit_sort:
    # Fit sort: fetch all matching rows, sort in Python by computed score
    cursor = await conn.execute(
        f"""SELECT p.*, q.overall_rating ..., {gallery_subquery}
        FROM properties p
        LEFT JOIN quality_analyses q ON p.unique_id = q.property_unique_id
        WHERE {where_sql}""",
        params,
    )
# ...
rows = await cursor.fetchall()
# ... compute fit_score for EVERY row ...
properties.sort(key=lambda p: (...), reverse=True)
properties = properties[offset : offset + per_page]
```

For each row, it calls `compute_fit_score()`, `compute_fit_breakdown()`, and `compute_lifestyle_icons()` -- three independent traversals of the scorer registry. With 500 properties, that is 1,500 scorer invocations just to display 20 results.

**Recommendation:**
- Short-term: Precompute and persist `fit_score` as an integer column (on save or via a background job after quality analysis). This allows `ORDER BY fit_score DESC LIMIT ? OFFSET ?` in SQL.
- If the score formula changes, a one-time migration recomputes all stored scores.
- `fit_breakdown` and `lifestyle_icons` only need computing for the page-sized result set, not the full query.

---

### F2. `compute_fit_score` and `compute_fit_breakdown` duplicate all scorer calls

**Severity:** Minor
**Theme:** Algorithmic Complexity
**Effort:** Low
**File:** `/Users/marcel/projects/labs/home-finder/src/home_finder/filters/fit_score.py` lines 512-565

`compute_fit_score()` iterates all six `_SCORERS` to produce a single integer. Then `compute_fit_breakdown()` iterates the same six scorers again to produce per-dimension detail. Both are called back-to-back in `storage.py` line 2181-2182, so every property's analysis dict is scored 12 times (6 scorers x 2 calls) instead of 6.

```python
prop_dict["fit_score"] = compute_fit_score(analysis, bedrooms)        # 6 scorer calls
prop_dict["fit_breakdown"] = compute_fit_breakdown(analysis, bedrooms) # 6 scorer calls (same)
prop_dict["lifestyle_icons"] = compute_lifestyle_icons(analysis, bedrooms)
```

**Recommendation:**
Introduce a single `compute_fit_result()` that returns `(score, breakdown)` from one pass through the scorers. The callers at line 2181-2183 collapse to one call.

---

### F3. Dict constants recreated inside function body on every call

**Severity:** Minor
**Theme:** Algorithmic Complexity
**Effort:** Low
**File:** `/Users/marcel/projects/labs/home-finder/src/home_finder/filters/fit_score.py` lines 409-431

`_HIGHLIGHT_SCORES` and `_LOWLIGHT_SCORES` are defined as local variables inside `_score_vibe()`, which means Python rebuilds these dicts on every invocation:

```python
_HIGHLIGHT_SCORES: dict[str, float] = {
    "Period features": 10,
    "Open-plan layout": 6,
    # ... 8 entries
}
```

This is called once per property per scorer invocation (and twice due to F2).

**Recommendation:**
Promote to module-level constants (prefixed with `_` to keep them private). Zero-risk change, marginal but free speedup.

---

### F4. `update_wards` iterates individual UPDATEs instead of batching

**Severity:** Minor
**Theme:** N+1 / Database
**Effort:** Low
**File:** `/Users/marcel/projects/labs/home-finder/src/home_finder/db/storage.py` lines 982-1002

```python
for unique_id, ward in ward_map.items():
    cursor = await conn.execute(
        "UPDATE properties SET ward = ? WHERE unique_id = ?",
        (ward, unique_id),
    )
```

Each iteration is a separate round-trip to the SQLite WAL. With 50 properties, that is 50 individual UPDATE statements where one `executemany()` would suffice.

**Recommendation:**
Replace with `await conn.executemany("UPDATE properties SET ward = ? WHERE unique_id = ?", [(ward, uid) for uid, ward in ward_map.items()])` followed by a single `await conn.commit()`. The existing single commit at the end is correct but the per-row execute is unnecessary overhead.

---

### F5. New `httpx.AsyncClient` created per call in postcode_lookup

**Severity:** Major
**Theme:** Connection Management
**Effort:** Low
**File:** `/Users/marcel/projects/labs/home-finder/src/home_finder/utils/postcode_lookup.py` lines 22, 43

Both `lookup_ward()` and `reverse_lookup_ward()` create a fresh `httpx.AsyncClient` per invocation:

```python
async def lookup_ward(postcode: str) -> str | None:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.get(f"{_BASE_URL}/postcodes/{postcode}")
```

Each client establishes a new TCP connection and TLS handshake to postcodes.io. When called sequentially in `main.py` `_lookup_wards()` (line 605-607) for `postcode_props`, this creates N connections for N postcodes.

**Recommendation:**
Accept an optional `client: httpx.AsyncClient | None` parameter and create a module-level shared client, or restructure to pass a client from the caller. The `bulk_reverse_lookup_wards()` function already shows the better pattern (though it also creates per-call).

---

### F6. Sequential ward lookups in `_lookup_wards`

**Severity:** Minor
**Theme:** Async Patterns
**Effort:** Low
**File:** `/Users/marcel/projects/labs/home-finder/src/home_finder/main.py` lines 604-608

```python
for p in postcode_props:
    ward = await lookup_ward(str(p["postcode"]))
    if ward:
        ward_map[str(p["unique_id"])] = ward
```

This is sequential -- each postcode waits for the previous lookup to complete. Combined with F5 (new client per call), N postcodes take N x (connection setup + request latency).

**Recommendation:**
Use `asyncio.gather()` or `asyncio.TaskGroup` with a semaphore to batch concurrent lookups. The postcodes.io API has no documented rate limit for individual lookups. Alternatively, use the `bulk_reverse_lookup_wards` pattern with forward-lookup batching (postcodes.io supports `POST /postcodes` for bulk forward lookups of up to 100 postcodes).

---

### F7. New `curl_cffi.AsyncSession` per image download in quality.py

**Severity:** Major
**Theme:** Connection Management
**Effort:** Medium
**File:** `/Users/marcel/projects/labs/home-finder/src/home_finder/filters/quality.py` lines 504-516

```python
async def _download_image_as_base64(self, url: str) -> ...:
    from curl_cffi.requests import AsyncSession
    async with AsyncSession() as session:
        response = await session.get(url, impersonate="chrome", ...)
```

Every Zoopla CDN image download creates and tears down a fresh curl_cffi session. For a property with 10 images, that is 10 TLS handshakes to the same CDN host. The `DetailFetcher` class already demonstrates the correct pattern -- creating a session once and reusing it across calls.

**Recommendation:**
Create the `AsyncSession` once (in `__init__` or as a context manager) and reuse it across image downloads within an `analyze_merged_properties` batch. Requires adding session lifecycle management (similar to `DetailFetcher.close()`).

---

### F8. New `httpx.AsyncClient` per image in `fetch_and_hash_image`

**Severity:** Minor
**Theme:** Connection Management
**Effort:** Low
**File:** `/Users/marcel/projects/labs/home-finder/src/home_finder/utils/image_hash.py` lines 35-47

```python
async with httpx.AsyncClient() as client:
    response = await client.get(url, timeout=timeout, follow_redirects=True)
```

Same pattern as F5. Each perceptual hash computation creates a fresh HTTP client. This is behind the `enable_image_hash_matching` flag (currently `False` by default), so impact is gated.

**Recommendation:**
Accept an optional shared client parameter, or restructure the caller to batch downloads through a single client.

---

### F9. New `httpx.AsyncClient` per outcode lookup in rightmove.py

**Severity:** Minor
**Theme:** Connection Management
**Effort:** Low
**File:** `/Users/marcel/projects/labs/home-finder/src/home_finder/scrapers/rightmove.py` lines 44-62

```python
async with httpx.AsyncClient() as client:
    resp = await client.get(url, timeout=10)
```

The `_resolve_outcode_id()` function creates a client for each outcode resolution. Mitigated by the `_outcode_cache` (subsequent lookups for the same outcode skip the request), but the first call per outcode still wastes a client setup.

**Recommendation:**
Minor. Could accept a shared client but since this is cached and called infrequently (once per unique outcode per scraper run), the practical impact is minimal.

---

### F10. New `BeautifulSoupCrawler` per search page in rightmove/openrent

**Severity:** Minor
**Theme:** Connection Management
**Effort:** Medium
**File:** `/Users/marcel/projects/labs/home-finder/src/home_finder/scrapers/rightmove.py` lines 212-218
**File:** `/Users/marcel/projects/labs/home-finder/src/home_finder/scrapers/openrent.py` (similar pattern)

```python
crawler = BeautifulSoupCrawler(
    max_requests_per_crawl=1,
    storage_client=MemoryStorageClient(),
)
crawler.router.default_handler(handle_page)
await crawler.run([url])
```

Each page creates a new Crawlee crawler instance with `max_requests_per_crawl=1`. Crawlee is designed for multi-URL crawls, so instantiating it per-page loses its connection pooling and session management benefits.

**Recommendation:**
This is a conscious design choice (one page at a time with explicit control flow). Restructuring to use multi-URL crawls would change the scraper architecture significantly. The overhead is mostly object allocation, not TCP connections (Crawlee uses its own session pool internally). Keep as-is unless scraping becomes a bottleneck.

---

### F11. CPU-bound PIL operations on the async event loop

**Severity:** Major
**Theme:** Async Patterns (event loop blocking)
**Effort:** Medium

Multiple modules perform synchronous PIL/Pillow image processing directly in the async call chain:

| Location | Operation | Typical latency |
|----------|-----------|-----------------|
| `floorplan_detector.py` `_analyze()` | Thumbnail, HSV convert, quantize, edge filter | 5-15ms |
| `quality.py` `_resize_image_bytes()` | PIL open + resize + JPEG re-encode | 10-30ms |
| `image_hash.py` `fetch_and_hash_image()` | PIL open + `imagehash.phash()` (DCT) | 5-20ms |
| `image_cache.py` `save_image_bytes()` / `read_image_bytes()` | Sync file I/O (`path.write_bytes` / `path.read_bytes`) | 1-5ms |

While each individual call is fast, they are invoked per-image and multiply across a batch. With 10 properties x 10 images, the event loop is blocked for cumulative hundreds of milliseconds, delaying all concurrent async I/O (API calls, database queries, HTTP requests).

**Recommendation:**
Wrap CPU-bound operations in `asyncio.to_thread()` or use `loop.run_in_executor()`:

```python
# Before (blocks event loop):
is_fp, confidence = detect_floorplan(image_bytes)

# After (runs in thread pool):
is_fp, confidence = await asyncio.to_thread(detect_floorplan, image_bytes)
```

Priority order: `_resize_image_bytes` (called per image in quality analysis) > `detect_floorplan` (per gallery image) > `phash` (behind feature flag).

For sync file I/O in `image_cache.py`, consider `aiofiles` or `asyncio.to_thread()` wrappers, though the impact is lower since file operations on local SSD are typically sub-millisecond.

---

### F12. 4x duplicated row-to-dict reconstruction in storage.py

**Severity:** Minor
**Theme:** Algorithmic Complexity (code maintainability, not runtime)
**Effort:** Medium
**File:** `/Users/marcel/projects/labs/home-finder/src/home_finder/db/storage.py` lines ~609, ~751, ~1414, ~1680, ~2138

The pattern of `dict(row)` -> `_parse_json_fields()` -> extract quality fields from `analysis_json` -> compute fit_score/breakdown/icons appears in at least four methods: `get_properties_paginated`, `get_map_markers`, `get_property_detail`, and internally in `fit_sort`. Each duplicates the JSON parsing, field extraction, and null-case boilerplate.

**Recommendation:**
Extract a `_row_to_property_dict(row)` helper that centralizes the common parsing logic. This is more of a maintainability issue than a performance one, but it would make F1's fix (persisting fit_score) easier to implement consistently.

---

### F13. Module-level mutable caches without bounded size

**Severity:** Minor
**Theme:** Memory / Concurrency Safety
**Effort:** Low

Two module-level dict caches grow unboundedly:

| Cache | Location | Type |
|-------|----------|------|
| `_outcode_cache` | `rightmove.py` line 21 | `dict[str, str]` |
| `_geocoding_cache` | `commute.py` line 36 | `ClassVar[dict[str, tuple[float, float]]]` |

Both are populated during pipeline runs and never evicted. In practice, the London outcode set is finite (~150 outcodes) and geocoding results are bounded by the property count, so unbounded growth is not a real risk here. However:

- `_geocoding_cache` is a `ClassVar` shared across all `CommuteFilter` instances, which is the intended design (cross-run cache) but could surprise someone expecting instance isolation.
- Neither is thread-safe, though since the app uses asyncio (single-threaded event loop), this is not a practical issue.

**Recommendation:**
No urgent action needed. If concerned, use `functools.lru_cache` or `cachetools.TTLCache` with a reasonable maxsize. Document the ClassVar behavior in `CommuteFilter`.

---

### F14. Module-level `asyncio.Lock` in app.py

**Severity:** Suggestion
**Theme:** Concurrency Safety
**Effort:** Low
**File:** `/Users/marcel/projects/labs/home-finder/src/home_finder/web/app.py` line 26

```python
_pipeline_lock = asyncio.Lock()
```

This lock is created at module import time. `asyncio.Lock` is bound to the running event loop. If the module is imported before the event loop starts (which is normal), modern Python (3.10+) handles this correctly -- the lock binds lazily. However, if the module were ever reloaded (e.g., in a test framework), the lock would be recreated and lose its state.

**Recommendation:**
No action required for production use. If test isolation becomes an issue, move the lock into the app factory or attach it to the FastAPI app state.

---

### F15. Single aiosqlite connection (no pooling)

**Severity:** Suggestion
**Theme:** Connection Management
**Effort:** High
**File:** `/Users/marcel/projects/labs/home-finder/src/home_finder/db/storage.py`

`PropertyStorage` uses a single `aiosqlite.Connection` for all operations. SQLite's WAL mode allows concurrent readers, but with one connection, all reads and writes are serialized through a single async cursor.

For the current workload (single pipeline + web dashboard), this is adequate. SQLite is the right choice for this application's scale. Connection pooling would only matter if:
- Multiple concurrent web requests need to query the database simultaneously
- Pipeline writes block dashboard reads

**Recommendation:**
No action required at current scale. If the web dashboard sees concurrent traffic, consider a read-replica pattern (separate connection for read-only queries) or migrate to an async pool like `aiosqlite` with multiple connections. Do not switch to PostgreSQL for this application -- SQLite is the correct database for a personal tool.

---

### F16. Dashboard makes two sequential DB queries per page load

**Severity:** Suggestion
**Theme:** N+1 / Database
**Effort:** Low
**File:** `/Users/marcel/projects/labs/home-finder/src/home_finder/web/routes.py`

The dashboard handler calls `get_properties_paginated()` then `get_map_markers()` sequentially. Both hit the same SQLite database with overlapping WHERE clauses.

**Recommendation:**
Could be parallelized with `asyncio.gather()`, but since both go through the same single aiosqlite connection (F15), they would still serialize at the connection level. No practical benefit until connection pooling is added. Keep as-is.

---

## Connection Lifecycle Summary

| Component | Client Type | Lifecycle | Reuse? | Issue? |
|-----------|-------------|-----------|--------|--------|
| `DetailFetcher` (httpx) | `httpx.AsyncClient` | Instance-level | Yes | OK |
| `DetailFetcher` (curl) | `curl_cffi.AsyncSession` | Instance-level | Yes | OK |
| `ZooplaScraper` | `curl_cffi.AsyncSession` | Instance-level | Yes | OK |
| `OnTheMarketScraper` | `curl_cffi.AsyncSession` | Instance-level | Yes | OK |
| `RightmoveScraper` (pages) | `BeautifulSoupCrawler` | Per-page | No | F10 (minor) |
| `OpenRentScraper` (pages) | `BeautifulSoupCrawler` | Per-page | No | F10 (minor) |
| `RightmoveScraper` (outcode) | `httpx.AsyncClient` | Per-call | No | F9 (minor, cached) |
| `PropertyQualityFilter` (images) | `curl_cffi.AsyncSession` | Per-image | No | **F7 (major)** |
| `postcode_lookup` (ward) | `httpx.AsyncClient` | Per-call | No | **F5 (major)** |
| `postcode_lookup` (bulk) | `httpx.AsyncClient` | Per-call | No | F5 (less impactful) |
| `image_hash` | `httpx.AsyncClient` | Per-call | No | F8 (gated by flag) |
| `CommuteFilter` (TravelTime) | `traveltimepy.AsyncClient` | Per-batch | Yes | OK |
| `PropertyStorage` (SQLite) | `aiosqlite.Connection` | App-level singleton | Yes | F15 (suggestion) |

---

## Concurrency Model

```
main.py pipeline
  |
  |-- Scrapers: Sequential (one platform at a time)
  |     |-- Zoopla: curl_cffi, adaptive delays, session reuse
  |     |-- Rightmove: crawlee per-page, outcode cache
  |     |-- OpenRent: crawlee per-page
  |     |-- OnTheMarket: curl_cffi, session reuse
  |
  |-- Detail Enrichment: asyncio.Semaphore(5)
  |     |-- Per-property: sequential source iteration
  |     |-- Per-image: sequential download within semaphore slot
  |     |-- Floorplan detection: sync PIL on event loop [F11]
  |
  |-- Quality Analysis: asyncio.Semaphore(15) via create_task
  |     |-- Per-property: Phase 1 (vision) -> Phase 2 (eval)
  |     |-- Image downloads: new curl_cffi session per image [F7]
  |     |-- Image resize: sync PIL on event loop [F11]
  |
  |-- Ward Lookup:
  |     |-- Coordinates: bulk_reverse_lookup (batched, good)
  |     |-- Postcodes: sequential per-postcode [F6], new client per call [F5]
  |
  |-- Commute Filter: TravelTime client per batch (OK)
  |     |-- Geocoding: sequential per-postcode [F6 pattern], cached [F13]
  |
  |-- DB Operations: single aiosqlite connection [F15]
  |     |-- update_wards: per-row UPDATE [F4]
  |     |-- fit_sort: full table scan + Python sort [F1]

web dashboard (concurrent with pipeline via _pipeline_lock)
  |-- GET /: two sequential DB queries [F16]
  |-- fit_score computed per row on every page load [F1, F2]
  |-- GET /images/: sync file read from disk [F11, minor]
```

---

## Summary by Severity

| Severity | Count | Findings |
|----------|-------|----------|
| Critical | 0 | -- |
| Major | 4 | F1 (fit_sort full scan), F5 (postcode client-per-call), F7 (quality image client-per-call), F11 (PIL on event loop) |
| Minor | 8 | F2 (duplicate scorers), F3 (dict constants in function), F4 (update_wards N+1), F6 (sequential ward lookups), F8 (image_hash client), F9 (rightmove outcode client), F10 (crawler-per-page), F12 (duplicated reconstruction), F13 (unbounded caches) |
| Suggestion | 3 | F14 (module-level lock), F15 (single SQLite connection), F16 (sequential dashboard queries) |

---

## Top 3 Takeaways

1. **Persist `fit_score` in the database** (fixes F1, simplifies F2, F12). This is the single highest-impact change: it eliminates the full-table Python sort, removes redundant scorer computation on every page load, and enables proper SQL pagination for the default dashboard sort order. Effort: medium (add column, compute on save, one-time migration).

2. **Reuse HTTP clients across calls** (fixes F5, F7, F8, F9). The pattern of `async with SomeClient() as c: c.get(url)` per individual request is the most widespread anti-pattern in the codebase. The `DetailFetcher` class already demonstrates the correct approach -- create once, reuse across calls, close explicitly. Apply the same pattern to `postcode_lookup`, `PropertyQualityFilter._download_image_as_base64`, and `image_hash`. Effort: low per module.

3. **Move CPU-bound image work off the event loop** (fixes F11). Wrap `detect_floorplan()`, `_resize_image_bytes()`, and `imagehash.phash()` calls in `asyncio.to_thread()`. This is a one-line change per call site that unblocks the event loop during image processing, improving responsiveness of concurrent API calls and database operations. Effort: low.

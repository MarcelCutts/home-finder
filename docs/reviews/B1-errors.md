# B1: Error Handling & Resilience — Cross-Cutting Review

**Scope:** All 42 source files (~14,400 LOC)
**Date:** 2026-02-16

## Executive Summary

The codebase demonstrates a **mature, layered approach to error handling** with thoughtful patterns like the quality.py circuit breaker, enrichment retry via DB status tracking, and Telegram flood-control retry with exponential backoff. The dominant pattern is `except Exception` + log + continue/return-None, which is appropriate for a scraping pipeline where partial success is preferable to total failure. The main areas of concern are: (1) four `contextlib.suppress(Exception)` blocks in storage.py migrations that could silently mask non-column-exists errors, (2) missing `exc_info=True` on ~15 error-level log statements making production debugging harder, (3) the Rightmove outcode fallback silently defaulting to Hackney which could produce confusing results, and (4) module-level JSON loading in area_context.py and location.py that will crash the entire process at import time with no recovery path.

## Error Handling Inventory

### `except Exception` blocks (37 total)

| File:Line | Caught Type | Behavior | Assessment |
|-----------|-------------|----------|------------|
| main.py:190 | `Exception` | log error + continue to next area | **Justified** — per-area isolation |
| main.py:700 | `Exception` | log error + continue | **Justified** — per-property isolation in analysis loop |
| main.py:825 | `Exception` | record pipeline failure + re-raise | **Good** — top-level with re-raise |
| main.py:1068 | `Exception` | log error + increment failed counter | **Justified** — per-property isolation in reanalysis |
| main.py:1161 | `Exception` | print user-facing message + exit | **Minor** — could be more specific to Pydantic ValidationError |
| quality.py:539 | `Exception` | log warning + return None | **Justified** — image download resilience |
| quality.py:704 | `Exception` | log error with exc_info + return minimal | **Good** — graceful degradation |
| quality.py:1037 | `Exception` | log warning + return None | **Minor** — Phase 1 catch-all after specific catches; logs error_type |
| quality.py:1131 | `Exception` | log warning + continue with partial data | **Justified** — Phase 2 can degrade |
| quality.py:1206 | `Exception` | log warning + return None | **Minor** — Pydantic validation failure; could catch `ValidationError` |
| detail_fetcher.py:291 | `Exception` | log warning + return None | **Minor** — Rightmove detail fetch |
| detail_fetcher.py:467 | `Exception` | log warning + return None | **Minor** — Zoopla detail fetch |
| detail_fetcher.py:568 | `Exception` | log warning + return None | **Minor** — OpenRent detail fetch |
| detail_fetcher.py:647 | `Exception` | log warning + return None | **Minor** — OTM detail fetch |
| detail_fetcher.py:673 | `Exception` | log debug + return None | **Minor** — image download |
| telegram.py:718 | `Exception` | log error + return False | **Missing exc_info** |
| telegram.py:797 | `Exception` | log warning + fall back to text | **Good** — photo→text fallback |
| telegram.py:862 | `Exception` | log error + return False | **Missing exc_info** |
| telegram.py:955 | `Exception` | log error + return False | **Missing exc_info** |
| zoopla.py:323 | `Exception` | log warning + mark warmed up | **Justified** — warm-up is best-effort |
| zoopla.py:488 | `Exception` | log warning/error + retry or return None | **Good** — retries with backoff |
| zoopla.py:736 | `Exception` | log warning | **Minor** — card parse failure |
| rightmove.py:66 | `Exception` | log warning + return None | **Justified** — typeahead API is unreliable |
| rightmove.py:353 | `Exception` | log warning | **Minor** — card parse failure |
| openrent.py:319 | `Exception` | log warning | **Minor** — property creation failure |
| onthemarket.py:159 | `Exception` | log error + return None | **Justified** — fetch failure |
| onthemarket.py:192 | `Exception` | log warning | **Minor** — listing parse failure |
| commute.py:171 | `Exception` | log warning/error + return [] | **Overly broad** — catches non-transient errors like auth failures |
| commute.py:262 | `Exception` | log warning + return original list | **Justified** — geocoding is best-effort |
| commute.py:312 | `Exception` | log warning + return None | **Justified** — single postcode geocode |
| image_hash.py:49 | `Exception` | log debug + return None | **Justified** — image hash is optional |
| image_hash.py:72 | `Exception` | return False | **Silent** — no logging at all |
| image_processing.py:37 | `Exception` | return original bytes | **Silent** — no logging |
| floorplan_detector.py:31 | `Exception` | return (False, 0.0) | **Silent** — no logging |
| web/app.py:59 | `Exception` | log error with exc_info | **Good** — pipeline scheduler |
| web/routes.py:468 | `Exception` | log error with exc_info + return 500 | **Good** — web error page |
| web/routes.py:483 | `Exception` | log error with exc_info + fallback to [] | **Good** — graceful degradation |
| web/routes.py:668 | `Exception` | log error with exc_info + return 500 | **Good** — web error page |

### `contextlib.suppress()` blocks (4 total)

| File:Line | Suppressed Type | Purpose | Assessment |
|-----------|----------------|---------|------------|
| storage.py:219 | `Exception` | ALTER TABLE ADD COLUMN migration | **Major** — see Finding #1 |
| storage.py:253 | `Exception` | ALTER TABLE ADD COLUMN migration | **Major** — see Finding #1 |
| storage.py:266 | `Exception` | ALTER TABLE ADD COLUMN migration | **Major** — see Finding #1 |
| storage.py:274 | `Exception` | UPDATE data migration | **Major** — see Finding #2 |

### `suppress(ValidationError)` (1 total)

| File:Line | Suppressed Type | Purpose | Assessment |
|-----------|----------------|---------|------------|
| zoopla.py:589 | `ValidationError` | Skip invalid listing data | **Good** — properly scoped |

### `suppress(CancelledError)` (1 total)

| File:Line | Suppressed Type | Purpose | Assessment |
|-----------|----------------|---------|------------|
| web/app.py:112 | `asyncio.CancelledError` | Lifespan shutdown cleanup | **Good** — standard pattern |

## Findings

### [Major] storage.py:219,253,266 — `suppress(Exception)` on schema migrations masks non-trivial errors

**Theme:** Error Handling | **Effort:** S

Three `contextlib.suppress(Exception)` blocks around `ALTER TABLE ADD COLUMN` statements are intended to handle the idempotent "column already exists" case. However, `Exception` is far too broad — this also silently swallows:
- `aiosqlite.OperationalError` for disk I/O errors
- `aiosqlite.DatabaseError` for corruption
- Type errors in the SQL string formatting
- Any Python exception in the surrounding code that happens to be in scope

```python
# storage.py:219
with contextlib.suppress(Exception):
    default_clause = f" DEFAULT {default}" if default is not None else ""
    await conn.execute(
        f"ALTER TABLE properties ADD COLUMN {column} {col_type}{default_clause}"
    )
```

**Recommendation:** Catch only the specific SQLite error for duplicate columns. In aiosqlite, this is `aiosqlite.OperationalError` with the message "duplicate column name: X". Use a targeted catch:

```python
try:
    await conn.execute(...)
except aiosqlite.OperationalError as e:
    if "duplicate column" not in str(e):
        raise  # Re-raise unexpected OperationalErrors
```

---

### [Major] storage.py:274 — `suppress(Exception)` on data migration silently eats UPDATE failures

**Theme:** Error Handling | **Effort:** S

The one_line JSON fix migration is wrapped in a blanket suppress:

```python
# storage.py:274
with contextlib.suppress(Exception):
    await conn.execute("""
        UPDATE quality_analyses
        SET analysis_json = json_set(...)
        WHERE json_valid(analysis_json)
          AND json_type(...) = 'object'
    """)
```

The comment explains that `json_extract` can throw on malformed rows, but the `WHERE json_valid(...)` clause already guards against that. If this UPDATE fails for other reasons (disk full, DB locked beyond busy_timeout), the failure is completely invisible.

**Recommendation:** Convert to `try/except aiosqlite.OperationalError` with a warning log, so unexpected failures are visible:

```python
try:
    await conn.execute("""...""")
except aiosqlite.OperationalError as e:
    logger.warning("one_line_migration_failed", error=str(e))
```

---

### [Major] main.py:190 — Scraper `except Exception` swallows errors without traceback

**Theme:** Logging | **Effort:** S

When a scraper fails for an area, the exception is caught and logged, but without `exc_info=True`:

```python
# main.py:190
except Exception as e:
    logger.error(
        "scraping_failed",
        platform=scraper.source.value,
        area=area,
        error=str(e),
    )
```

This loses the full traceback. For transient network errors this is fine, but for programming errors (e.g., AttributeError from a changed page structure), the traceback is essential for debugging.

**Recommendation:** Add `exc_info=True` to the logger call:
```python
logger.error("scraping_failed", ..., error=str(e), exc_info=True)
```

---

### [Major] rightmove.py:299-301 — Silent fallback to Hackney on outcode lookup failure

**Theme:** Failure Modes | **Effort:** S

When Rightmove outcode lookup fails, the code silently falls back to searching Hackney:

```python
# rightmove.py:299-301
logger.warning("rightmove_outcode_lookup_failed", outcode=area)
# Fallback to hackney
location_id = RIGHTMOVE_LOCATIONS.get("hackney", "REGION%5E93965")
```

This means if you search for, say, "SE1", and the API lookup fails, Rightmove will return Hackney results labeled as if they were SE1 results. The location filter (step 2.5) would catch this downstream, but it creates confusing logs and wastes API calls.

**Recommendation:** Return an empty list instead of silently substituting a different area. Log at `error` level since this means Rightmove data for this outcode is lost:

```python
logger.error("rightmove_outcode_lookup_failed_skipping", outcode=area)
return f"{self.BASE_URL}/property-to-rent/find.html?locationIdentifier=SKIP"
# Or better: raise a specific exception that scrape() catches to skip this area
```

---

### [Major] area_context.py:93 and location.py:21 — Module-level JSON loading with no error handling

**Theme:** Resilience | **Effort:** S

Both files load JSON data at module import time with no error handling:

```python
# area_context.py:93
_DATA_PATH = Path(__file__).resolve().parent / "area_context.json"
_DATA = json.loads(_DATA_PATH.read_text())

# location.py:21
_DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "borough_outcodes.json"
_DATA = json.loads(_DATA_PATH.read_text())
```

If either JSON file is missing, corrupted, or has invalid JSON, the entire application crashes at import time with an unhelpful `FileNotFoundError` or `json.JSONDecodeError`. Since these are data files that ship with the package, this is unlikely but not impossible (e.g., incomplete git clone, broken package installation).

**Recommendation:** Wrap in try/except at module level that logs a clear error and provides useful defaults or a clear error message. Alternatively, validate these files in a test.

---

### [Major] telegram.py:718,862,955 — Notification errors logged without tracebacks

**Theme:** Logging | **Effort:** S

Three `except Exception` blocks in the Telegram notifier log the error string but not the traceback:

```python
# telegram.py:718
except Exception as e:
    logger.error(
        "notification_failed",
        property_id=prop.unique_id,
        error=str(e),
    )
```

Network errors, authentication failures, and programming errors all look the same in logs. The `send_merged_property_notification` at line 862 is the primary notification path and particularly needs tracebacks.

**Recommendation:** Add `exc_info=True` to all three logger.error calls.

---

### [Minor] commute.py:171 — TravelTime API catch-all handles rate limits and auth failures identically

**Theme:** Exception Specificity | **Effort:** M

The TravelTime API error handler partially distinguishes rate limits from other errors but then returns `[]` in both cases:

```python
# commute.py:171
except Exception as e:
    error_str = str(e).lower()
    if "rate limit" in error_str or "429" in error_str:
        logger.warning("rate_limit_hit", error=str(e))
    else:
        logger.error("traveltime_api_error", error=str(e))
    return []
```

This conflates authentication errors (invalid API key), programming errors, and transient errors. An invalid API key will silently produce an empty commute filter every run.

**Recommendation:** Catch the TravelTime SDK's specific exception types. At minimum, re-raise on configuration errors (auth, bad request) and only swallow transient errors:

```python
except traveltimepy.errors.ApiError as e:
    if e.status_code == 401:
        raise  # Config error — should be visible
    logger.error(...)
    return []
except Exception as e:
    logger.error("traveltime_unexpected_error", error=str(e), exc_info=True)
    return []
```

---

### [Minor] image_hash.py:72, image_processing.py:37, floorplan_detector.py:31 — Silent exception swallowing

**Theme:** Silent Swallowing | **Effort:** S

Three utility functions catch all exceptions and return default values with no logging:

```python
# image_hash.py:72
except Exception:
    return False

# image_processing.py:37
except Exception:
    return data  # Return original bytes unchanged

# floorplan_detector.py:31
except Exception:
    return False, 0.0
```

While these are defensive patterns for optional features, they make debugging impossible when Pillow or imagehash has a genuine bug. A `logger.debug()` call costs nothing in production (filtered by level) but saves hours of debugging.

**Recommendation:** Add `logger.debug("..._failed", exc_info=True)` to each.

---

### [Minor] quality.py:1037 — Phase 1 visual analysis catch-all after specific exception handlers

**Theme:** Exception Specificity | **Effort:** S

The Phase 1 API call already has specific handlers for `BadRequestError`, `RateLimitError`, `InternalServerError`, `APIConnectionError`, and `APIStatusError`. The final catch-all is:

```python
# quality.py:1037
except Exception as e:
    logger.warning(
        "visual_analysis_failed",
        property_id=property_id,
        error=str(e),
        error_type=type(e).__name__,
    )
    return None
```

This catches any remaining exception (e.g., `TypeError`, `KeyError` from malformed API response parsing) and returns None, which triggers the minimal analysis fallback. The logging includes `error_type` which is helpful, but `exc_info=True` would be better for unexpected errors.

**Recommendation:** Add `exc_info=True` to this handler since it only catches truly unexpected errors.

---

### [Minor] rightmove.py:21 — Module-level mutable cache with no size bound

**Theme:** Resilience | **Effort:** S

```python
# rightmove.py:21
_outcode_cache: dict[str, str] = {}
```

This cache grows unboundedly across pipeline runs. In the `--serve` mode with recurring pipeline execution, this could grow without limit (though in practice the set of UK outcodes is finite and small). More concerning: there is no error recovery if a cache entry is wrong.

**Recommendation:** Consider using `functools.lru_cache` with a maxsize, or document that the cache is bounded by the finite set of UK outcodes (~3000).

---

### [Minor] commute.py:36 — Class-level geocoding cache shared across instances

**Theme:** Resilience | **Effort:** S

```python
# commute.py:36
class CommuteFilter:
    _geocoding_cache: ClassVar[dict[str, tuple[float, float]]] = {}
```

This class-level cache persists across pipeline runs in `--serve` mode. If a geocoding result is wrong (API temporary error returned incorrect data), it is cached permanently. No way to invalidate.

**Recommendation:** Add a TTL or size limit, or make it instance-level if the concern is cross-run poisoning.

---

### [Suggestion] main.py:1161 — Settings error catch could be more specific

**Theme:** Exception Specificity | **Effort:** S

```python
# main.py:1159-1167
try:
    settings = Settings()
except Exception as e:
    logger.error("failed_to_load_settings", error=str(e))
    print(f"Error: Failed to load settings. {e}")
    ...
    sys.exit(1)
```

`Settings()` (pydantic-settings) raises `pydantic.ValidationError` on invalid config. Catching `Exception` here also catches `KeyboardInterrupt`, `SystemExit`, etc. due to the base class hierarchy.

**Recommendation:** Catch `pydantic.ValidationError` specifically. Note: `KeyboardInterrupt` and `SystemExit` derive from `BaseException` not `Exception`, so this is actually safe — but it would be clearer to catch the specific type.

---

### [Suggestion] No custom exception hierarchy

**Theme:** Error Hierarchy | **Effort:** M

The only custom exception is `APIUnavailableError` in quality.py. The codebase could benefit from a small hierarchy:

- `HomeFindError` (base)
  - `ScrapingError` — scraper failures
  - `EnrichmentError` — detail fetch failures
  - `StorageError` — DB failures

This would allow callers to distinguish between error types without relying on `except Exception`. Currently, all failures in the pipeline look the same to the caller.

**Recommendation:** Low priority, but would improve error reporting in the pipeline and make the `except Exception` blocks more intentional.

---

### [Suggestion] detail_fetcher.py — retry on any non-429 HTTP error

**Theme:** Retry Patterns | **Effort:** S

The `_httpx_get_with_retry` method retries on 429 but raises immediately on other errors:

```python
# detail_fetcher.py:112-114
if response.status_code != 429:
    response.raise_for_status()
    return response
```

A 503 or 502 from Rightmove/OpenRent is also transient and worth retrying. The curl_cffi retry method (`_curl_get_with_retry`) only retries on 429 as well.

**Recommendation:** Add 502 and 503 to the retryable status codes in both retry methods.

## Retry & Circuit Breaker Patterns

### Circuit Breaker (quality.py)

The API circuit breaker in `PropertyQualityFilter` is **well-implemented**:

- **Threshold:** 3 consecutive failures (`_CIRCUIT_BREAKER_THRESHOLD`)
- **Triggers:** `RateLimitError`, `InternalServerError`, `APIConnectionError`
- **Excludes:** `BadRequestError`, `APIStatusError` (4xx) — correctly identified as non-outage signals
- **Recovery:** Success resets the counter (`_record_api_success()`)
- **Propagation:** Raises `APIUnavailableError` which the pipeline catches to cancel remaining tasks
- **Thread safety:** Comment notes asyncio is single-threaded, no lock needed (correct)
- **Gap:** Circuit breaker is one-way — once open, it stays open until the PropertyQualityFilter instance is garbage collected. For long-running `--serve` mode, this means a transient API outage permanently disables quality analysis until server restart.

**Recommendation:** Add a half-open state with a configurable cooldown (e.g., 5 minutes) to allow recovery.

### Retry Patterns Summary

| Component | Retry Strategy | Bounded? | Backoff | Issues |
|-----------|---------------|----------|---------|--------|
| detail_fetcher httpx | 2 retries on 429 | Yes | Exponential (2s, 4s) | Only retries 429, not 502/503 |
| detail_fetcher curl_cffi | 2 retries on 429 | Yes | Exponential + jitter | Good implementation |
| zoopla _fetch_page | 4 attempts with CF backoff | Yes | Exponential + jitter (2s base) | Good, resets session after 2 blocks |
| onthemarket _fetch_page | 1 attempt, no retry | N/A | N/A | Could benefit from retry on transient errors |
| Anthropic SDK | 3 retries (configured) | Yes | SDK handles backoff | Good delegation |
| Telegram notifications | 2 retries on flood control | Yes | Server-specified + jitter | Good implementation |
| Enrichment (cross-run) | `enrichment_attempts` DB field, max 3 | Yes | Next pipeline run | Good persistent retry |
| Notification (cross-run) | `notification_status` DB field | Yes | Next pipeline run | Good persistent retry |
| TravelTime API | SDK-level: `retry_attempts=3` | Yes | SDK handles | Good delegation |
| OpenRent adaptive | Backoff factor 2x on slow responses | Yes | Capped at 30s | Creative approach |

### SDK Retry Delegation

The codebase correctly delegates retry logic to SDKs where available:
- Anthropic SDK: `max_retries=3` with built-in exponential backoff
- TravelTime SDK: `retry_attempts=3`
- crawlee: Built-in retry for Rightmove and OpenRent scrapers

## Failure Mode Analysis

| Component | Failure Mode | Current Behavior | Assessment |
|-----------|-------------|------------------|------------|
| **Zoopla scraper** | Cloudflare blocks | Exponential backoff, session reset after 2 blocks, skip areas after 5 blocks | **Good** — adaptive and bounded |
| **Rightmove scraper** | Outcode lookup failure | Falls back to Hackney | **Problematic** — see Finding #4 |
| **OpenRent scraper** | Rate limiting (429) | Adaptive delay escalation (2s → 30s cap) | **Good** |
| **OnTheMarket scraper** | Fetch failure | Log error, return None, skip area | **Adequate** |
| **Detail enrichment** | Single property fails | Tracked in `enrichment_status`/`enrichment_attempts`, retried next run | **Good** — persistent retry |
| **Quality analysis** | API unavailable | Circuit breaker cancels remaining, DB stores `pending_analysis` | **Good** — graceful degrade |
| **Quality analysis** | Per-property failure | Returns minimal analysis, pipeline continues | **Good** |
| **Telegram notification** | Flood control (429) | Sleep for server-specified duration, retry up to 2x | **Good** |
| **Telegram notification** | Photo send failure | Falls back to text message | **Good** |
| **Telegram notification** | Total failure | Stored as `notification_failed` in DB, retried next run | **Good** |
| **TravelTime API** | Rate limit | Caught and logged, returns empty results | **Adequate** |
| **TravelTime API** | Auth failure | Caught by same handler, returns empty results | **Problematic** — silent config error |
| **SQLite database** | Connection failure | Exception propagates up (no explicit handling) | **Adequate** — pipeline crashes and retries on next run |
| **SQLite database** | Disk full | Exception propagates through suppress blocks during migration | **Problematic** — see Finding #1 |
| **JSON data files** | Missing/corrupt | Process crashes at import time | **Problematic** — see Finding #5 |
| **Pipeline crash** | Mid-analysis crash | Properties saved as `pending_analysis`, picked up next run | **Good** — crash recovery |
| **Pipeline crash** | Mid-enrichment crash | Stale image cache detected and cleared on retry | **Good** |
| **Web dashboard** | DB query failure | Returns error.html template with 500 status | **Good** |
| **Web dashboard** | Map markers query failure | Falls back to empty markers, continues rendering | **Good** |

## Logging Consistency

### Log Levels

The codebase uses structured logging via `structlog` with generally appropriate levels:

- **error:** Used for genuine errors (API failures, notification failures, unexpected exceptions)
- **warning:** Used for degraded behavior (rate limits, fallbacks, skipped items)
- **info:** Used for pipeline progress and summaries
- **debug:** Used for cache hits, detailed parsing info

**Issues found:**
1. Some `except Exception` blocks log at `warning` instead of `error` (e.g., quality.py:1037 for unexpected Phase 1 failures)
2. Scraper per-area failures log at `error` without `exc_info` (main.py:190)
3. Notification failures log at `error` without `exc_info` (telegram.py:718, 862, 955)

### Structured Logging Fields

Consistently uses key-value pairs with meaningful field names:
- `property_id` for property context
- `platform` / `source` for scraper context
- `error` for error messages
- `phase` for pipeline step identification
- `exc_info=True` used in 10 locations (but missing from ~15 others that need it)

### exc_info Coverage

| Location | Has exc_info? | Should it? |
|----------|-------------|------------|
| main.py:190 (scraper area failure) | No | **Yes** |
| main.py:701 (property processing) | Yes | Yes |
| main.py:1069 (reanalysis failure) | Yes | Yes |
| quality.py:708 (analyze_single_merged) | Yes | Yes |
| quality.py:1037 (visual analysis catch-all) | No | **Yes** |
| telegram.py:718 (send_property_notification) | No | **Yes** |
| telegram.py:862 (send_merged_notification) | No | **Yes** |
| telegram.py:955 (send_status_message) | No | **Yes** |
| web/routes.py:469 (dashboard query) | Yes | Yes |
| web/routes.py:484 (map markers query) | Yes | Yes |
| web/routes.py:669 (detail query) | Yes | Yes |
| web/app.py:60 (pipeline scheduler) | Yes | Yes |
| postcode_lookup.py (3 locations) | Yes | Yes |
| commute.py:171 (TravelTime API) | No | **Yes** |
| commute.py:262 (geocoding batch) | No | **Yes** |

## Summary by Severity

| Severity | Count | Key Themes |
|----------|-------|------------|
| Critical | 0 | — |
| Major | 6 | suppress(Exception) in migrations, missing tracebacks, silent Hackney fallback, module-level JSON crash |
| Minor | 6 | Overly broad catches, silent swallowing in utils, unbounded caches, retry gaps |
| Suggestion | 3 | Settings catch specificity, custom exception hierarchy, retry on 502/503 |

## Top 3 Takeaways

1. **Tighten the `suppress(Exception)` blocks in storage.py migrations.** These are the highest-risk items because they can mask database corruption or disk errors during schema evolution. Replace with `except aiosqlite.OperationalError` and check the error message for "duplicate column". This is a small, safe change with high diagnostic value.

2. **Add `exc_info=True` to ~15 error-level log statements.** The pipeline's "catch and continue" pattern is correct for resilience, but the current logging loses tracebacks on unexpected errors. This makes production debugging much harder. A systematic pass adding `exc_info=True` to all `logger.error()` calls inside `except Exception` blocks would take under an hour and dramatically improve observability.

3. **Fix the Rightmove Hackney fallback to return empty results instead.** Silently substituting a different geographic area is a data integrity issue that the location filter may or may not catch (depends on postcode presence). The correct behavior for an unknown outcode is to skip it and log an error, not to search the wrong area.

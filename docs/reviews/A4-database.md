# A4: Database Review

**Scope:** `db/storage.py` (2335L), `db/__init__.py` (5L)
**Total:** 2,340 LOC | **Date:** 2026-02-16

## Executive Summary

`PropertyStorage` is a **2335-line god class with ~45 methods** that handles every database concern: schema management, migrations, CRUD for 3 tables, notification tracking, pipeline run tracking, enrichment retry, quality analysis storage, reanalysis queuing, paginated web queries with 18 filter parameters, map markers, and MergedProperty reconstruction. Despite the size, the code is well-organized with clear method docstrings and good defensive patterns (COALESCE, ON CONFLICT, WAL mode).

The main issues are:

1. **Row-to-MergedProperty reconstruction duplicated 4 times** (~160 lines of copy-paste)
2. **INSERT column lists duplicated 3 times** (~60 lines each, must stay in sync)
3. **18-parameter filter signature repeated 3 times** with identical forwarding
4. **`get_properties_paginated` is 200 lines** mixing SQL, JSON parsing, fit score computation, and two pagination modes
5. **Storage layer imports domain logic** (`fit_score`, `HOSTING_TOLERANCE`) — layer violation

The resilience patterns are sound: WAL mode, foreign keys, COALESCE for nullable backfills, ON CONFLICT for idempotent upserts, and `enrichment_status` + `enrichment_attempts` for retry tracking.

---

## Findings

### [MAJOR] storage.py:607-645, 749-793, 1412-1455, 1678-1721 — Row-to-MergedProperty duplicated 4 times

**Theme:** Duplication | **Effort:** M

Four methods reconstruct `MergedProperty` from DB rows using the exact same ~40-line pattern:

```python
# Repeated in: get_unenriched_properties, get_recent_properties_for_dedup,
#              get_pending_analysis_properties, get_reanalysis_queue
sources_list: list[PropertySource] = []
source_urls: dict[PropertySource, HttpUrl] = {}
descriptions: dict[PropertySource, str] = {}

if row["sources"]:
    for s in json.loads(row["sources"]):
        sources_list.append(PropertySource(s))
else:
    sources_list.append(prop.source)

if row["source_urls"]:
    for s, url in json.loads(row["source_urls"]).items():
        source_urls[PropertySource(s)] = HttpUrl(url)
else:
    source_urls[prop.source] = prop.url

if row["descriptions_json"]:
    for s, desc in json.loads(row["descriptions_json"]).items():
        descriptions[PropertySource(s)] = desc

# Load images (in 3 of 4 methods)
images = await self.get_property_images(prop.unique_id)
gallery = tuple(img for img in images if img.image_type == "gallery")
floorplan_img = next((img for img in images if img.image_type == "floorplan"), None)

min_price = row["min_price"] if row["min_price"] is not None else prop.price_pcm
max_price = row["max_price"] if row["max_price"] is not None else prop.price_pcm

results.append(MergedProperty(
    canonical=prop, sources=tuple(sources_list), source_urls=source_urls,
    images=gallery, floorplan=floorplan_img, ...
))
```

This is ~160 lines of identical code. A bug fix (e.g., handling a new JSON field) must be applied 4 times.

**Recommendation:** Extract `_row_to_merged_property(self, row, *, load_images: bool = True) -> MergedProperty` that encapsulates the JSON parsing, image loading, and MergedProperty construction. All 4 methods call this helper. Estimated 30-45 min.

---

### [MAJOR] storage.py:290-342, 517-583, 1282-1376 — INSERT column lists duplicated 3 times

**Theme:** Duplication | **Effort:** M

Three methods (`save_property`, `save_unenriched_property`, `save_pre_analysis_properties`) have nearly identical INSERT statements with 20+ columns. The column lists, parameter tuples, and ON CONFLICT clauses are copy-pasted with minor variations (different `notification_status` defaults, different ON CONFLICT updates).

Adding a new column to the `properties` table requires updating 3+ INSERT statements, and missing one causes silent data loss for that column.

**Recommendation:** Extract a `_build_property_insert(prop, merged, *, notification_status, enrichment_status) -> tuple[str, tuple]` helper that constructs the INSERT SQL and params from a `Property` or `MergedProperty`. Each save method provides only the varying parts (notification_status, enrichment behavior). This also makes it easier to add new columns.

---

### [MAJOR] storage.py:1753-1869 + 1871-1928 + 1930-2014 + 2016-2218 — 18-parameter filter signature repeated 3 times

**Theme:** Duplication | **Effort:** M

`_build_filter_clauses` accepts 18 keyword arguments. The exact same 18 parameters are repeated in `get_filter_count`, `get_map_markers`, and `get_properties_paginated`, each forwarding them verbatim:

```python
async def get_filter_count(self, *, min_price=None, max_price=None, bedrooms=None,
    min_rating=None, area=None, property_type=None, outdoor_space=None,
    natural_light=None, pets=None, value_rating=None, hob_type=None,
    floor_level=None, building_construction=None, office_separation=None,
    hosting_layout=None, hosting_noise_risk=None, broadband_type=None,
    tags=None) -> int:
    where_sql, params = self._build_filter_clauses(
        min_price=min_price, max_price=max_price, bedrooms=bedrooms,
        min_rating=min_rating, area=area, property_type=property_type, ...
    )
```

Each new filter requires adding the parameter to 4 places (the builder + 3 callers). Currently ~400 lines of parameter forwarding.

**Recommendation:** Create a `PropertyFilter` dataclass (or `TypedDict`):
```python
@dataclass(frozen=True)
class PropertyFilter:
    min_price: int | None = None
    max_price: int | None = None
    # ... all 18 fields
```
Methods accept `PropertyFilter` instead of 18 kwargs. The caller constructs one filter object and passes it everywhere. This also makes the web route → storage interface cleaner.

---

### [MAJOR] storage.py:2016-2218 — get_properties_paginated is 200 lines with mixed concerns

**Theme:** Complexity | **Effort:** M

`get_properties_paginated` handles:
1. Filter clause building (delegated, good)
2. Sort order mapping (lines 2069-2076)
3. Total count query (lines 2078-2088)
4. Gallery subquery construction (lines 2090-2098)
5. Two different SQL queries based on sort mode (lines 2101-2134)
6. Row processing with JSON parsing (lines 2136-2206)
7. Analysis JSON extraction (lines 2145-2205) — 60 lines with try/except
8. Fit score computation (lines 2181-2183)
9. Python-side sorting for fit_desc (lines 2208-2216)

The analysis JSON extraction block (lines 2145-2205) duplicates its null-field defaults in both the `except` block (lines 2184-2194) and the `else` block (lines 2196-2205) — 10 identical field assignments.

**Recommendation:**
1. Extract `_process_property_row(row) -> PropertyListItem` to handle JSON parsing, analysis extraction, fit score computation
2. Set null defaults once via a dict update, not duplicated in except/else
3. Consider whether fit_sort should be handled differently (e.g., denormalize fit_score into the DB)

---

### [MINOR] storage.py:12-17 — Storage imports domain logic (layer violation)

**Theme:** Coupling | **Effort:** S

```python
from home_finder.data.area_context import HOSTING_TOLERANCE
from home_finder.filters.fit_score import (
    compute_fit_breakdown,
    compute_fit_score,
    compute_lifestyle_icons,
)
```

The storage layer imports from `data.area_context` and `filters.fit_score` to compute fit scores and inject hosting tolerance during query result processing (line 2177-2183). This couples the DB layer to domain logic — changes to fit_score computation require touching storage.py.

**Recommendation:** Move the fit score computation and hosting tolerance injection to the web route layer (the caller of `get_properties_paginated`). The storage layer returns raw data; the route enriches it. This cleanly separates persistence from business logic.

---

### [MINOR] storage.py:209-223, 250-257, 260-267 — Schema migrations use suppress(Exception)

**Theme:** Resilience | **Effort:** S

```python
for column, col_type, default in [
    ("sources", "TEXT", None),
    ("source_urls", "TEXT", None),
    ...
]:
    with contextlib.suppress(Exception):
        await conn.execute(f"ALTER TABLE properties ADD COLUMN ...")
```

`contextlib.suppress(Exception)` catches all errors, not just "duplicate column name". If the ALTER TABLE fails for a different reason (e.g., disk full, database locked), the error is silently swallowed and the column is missing.

**Recommendation:** Catch `aiosqlite.OperationalError` (or check the error message for "duplicate column name") instead of suppressing all exceptions:
```python
try:
    await conn.execute(f"ALTER TABLE properties ADD COLUMN ...")
except aiosqlite.OperationalError as e:
    if "duplicate column name" not in str(e).lower():
        raise
```

---

### [MINOR] storage.py:126-288 — initialize() is 160 lines mixing schema and migrations

**Theme:** Complexity | **Effort:** M

`initialize()` handles CREATE TABLE (3 tables), CREATE INDEX (5 indexes), ADD COLUMN migrations (8 columns across 2 tables), and a data migration (one_line JSON fix). All in one method.

The migration pattern (suppress + ALTER TABLE) is fragile (see previous finding) and will grow linearly as the schema evolves. There's no tracking of which migrations have been applied.

**Recommendation:** Split into:
- `_create_tables()` — CREATE TABLE IF NOT EXISTS + indexes
- `_run_migrations()` — ADD COLUMN and data fixups

For the future: consider a simple migration tracking table (`schema_version INTEGER`) so migrations only run once and can include more complex operations. Low priority since the current approach works for a personal project.

---

### [MINOR] storage.py:2101-2115 — fit_sort loads all rows into memory

**Theme:** Performance | **Effort:** M

When `sort="fit_desc"`, `get_properties_paginated` fetches ALL matching rows (no LIMIT/OFFSET), computes fit scores in Python for each one, sorts, then paginates:

```python
if is_fit_sort:
    cursor = await conn.execute(f"""
        SELECT p.*, ... FROM properties p
        LEFT JOIN quality_analyses q ON ...
        WHERE {where_sql}
    """, params)  # No LIMIT!
    ...
    properties.sort(key=lambda p: ...)
    properties = properties[offset : offset + per_page]
```

For a dataset of hundreds of properties this is fine, but it scales poorly. Each row also triggers JSON parsing and fit score computation.

**Recommendation:** Consider denormalizing fit_score into a DB column (computed on save/reanalysis). This would allow SQL-level sorting and pagination for all sort modes. Medium effort because it requires updating `save_quality_analysis` and `complete_reanalysis` to also compute and store the score.

---

### [MINOR] storage.py:1206-1222 — update_pipeline_run uses f-string column names from kwargs

**Theme:** Abstraction | **Effort:** S

```python
async def update_pipeline_run(self, run_id: int, **counts: int) -> None:
    set_clauses = ", ".join(f"{k} = ?" for k in counts)
    await conn.execute(f"UPDATE pipeline_runs SET {set_clauses} WHERE id = ?", values)
```

While `**counts` comes from internal callers only, the pattern of injecting kwargs as SQL column names is unusual. A typo like `scraped_cout=42` would generate invalid SQL at runtime rather than failing at the call site.

**Recommendation:** Accept explicit named parameters instead of `**counts`:
```python
async def update_pipeline_run(self, run_id: int, *,
    scraped_count: int | None = None, new_count: int | None = None, ...) -> None:
```
Or define a `PipelineRunCounts` TypedDict. This catches typos at development time.

---

### [MINOR] storage.py:982-1001 — update_wards iterates individually instead of batching

**Theme:** Performance | **Effort:** S

```python
async def update_wards(self, ward_map: dict[str, str]) -> int:
    for unique_id, ward in ward_map.items():
        cursor = await conn.execute(
            "UPDATE properties SET ward = ? WHERE unique_id = ?",
            (ward, unique_id),
        )
        updated += cursor.rowcount
    await conn.commit()
```

Each ward update is a separate SQL execution. For large batches this could be slow (though `commit()` is only called once, which helps).

**Recommendation:** Use `executemany` or a CASE expression for batch updates:
```python
await conn.executemany(
    "UPDATE properties SET ward = ? WHERE unique_id = ?",
    [(ward, uid) for uid, ward in ward_map.items()]
)
```

---

### [MINOR] storage.py:2136-2206 — analysis_json processing duplicates null defaults

**Theme:** Duplication | **Effort:** S

The null-field defaults are set in three places within `get_properties_paginated`:
1. Happy path: lines 2148-2183 (fields extracted from JSON)
2. Except block: lines 2185-2194 (all set to None/"")
3. Else block (no analysis): lines 2196-2205 (all set to None/"")

Blocks 2 and 3 are identical — 10 field assignments each.

**Recommendation:** Set defaults before the try block:
```python
prop_dict.update({"quality_summary": "", "value_rating": None, "highlights": None, ...})
if prop_dict.get("analysis_json"):
    try:
        # overwrite defaults with real data
```
This eliminates one of the duplicated blocks.

---

### [SUGGESTION] storage.py — Class should be split by concern

**Theme:** Abstraction | **Effort:** L

`PropertyStorage` has ~45 methods spanning these concerns:

| Concern | Methods | Approx lines |
|---------|---------|-------------|
| Schema / migrations | `initialize` | 160 |
| Property CRUD | `save_property`, `save_merged_property`, `is_seen`, `get_property`, `get_all_properties`, `filter_new`, `filter_new_merged`, `delete_property`, `get_all_known_source_ids` | ~300 |
| Notification tracking | `get_pending_notifications`, `get_unsent_notifications`, `mark_notified`, `mark_notification_failed` | ~80 |
| Enrichment retry | `save_unenriched_property`, `get_unenriched_properties`, `mark_enriched`, `expire_unenriched` | ~180 |
| Quality analysis | `save_quality_analysis`, `get_quality_analysis`, `complete_analysis`, `reset_failed_analyses` | ~150 |
| Reanalysis | `request_reanalysis`, `request_reanalysis_by_filter`, `get_reanalysis_queue`, `complete_reanalysis` | ~180 |
| Pipeline runs | `create_pipeline_run`, `update_pipeline_run`, `complete_pipeline_run`, `get_last_pipeline_run` | ~100 |
| Cross-run dedup | `get_recent_properties_for_dedup`, `update_merged_sources`, `save_pre_analysis_properties`, `get_pending_analysis_properties` | ~300 |
| Web queries | `get_properties_paginated`, `get_property_detail`, `get_filter_count`, `get_map_markers`, `get_property_count`, `update_wards`, `get_properties_without_ward` | ~500 |

**Recommendation:** Long-term, split into multiple classes sharing a connection:
- `PropertyRepository` — core CRUD, schema
- `NotificationRepository` — notification tracking
- `QualityRepository` — analysis storage, reanalysis
- `PipelineRepository` — pipeline runs, enrichment retry
- `WebQueryService` — paginated queries, map markers, detail views

A facade `PropertyStorage` could delegate to these for backward compatibility. This is a large refactor — consider doing it incrementally, starting with extracting web queries (which are read-only and easily separable).

---

### [SUGGESTION] storage.py:895-980 — save_merged_property shares 90% with save_property

**Theme:** Duplication | **Effort:** S

`save_merged_property` and `save_property` have nearly identical INSERT statements. `save_merged_property` adds `sources`, `source_urls`, `min_price`, `max_price`, `descriptions_json`, and `ward` columns. The base 16 columns and their parameter extraction are identical.

**Recommendation:** This would be resolved by the `_build_property_insert` helper recommended in the INSERT duplication finding.

---

### [SUGGESTION] storage.py:1780-1791 — Default WHERE clauses embed business logic in SQL

**Theme:** Coupling | **Effort:** S

`_build_filter_clauses` always adds 4 default WHERE conditions:

```python
where_clauses = [
    "COALESCE(p.enrichment_status, 'enriched') != 'pending'",
    "p.notification_status != 'pending_analysis'",
    "(q.overall_rating IS NOT NULL OR q.property_unique_id IS NULL)",
    """(p.image_url IS NOT NULL OR EXISTS (...))""",
]
```

These encode business rules ("hide unenriched", "hide pending analysis", "hide fallback analysis", "hide imageless") in SQL. If any web route needs to show these hidden properties (e.g., an admin view), there's no way to bypass these defaults.

**Recommendation:** Consider making the defaults configurable:
```python
def _build_filter_clauses(self, *, include_hidden: bool = False, ...) -> ...:
```
Low priority since all current callers want the same defaults.

---

### [SUGGESTION] storage.py — No explicit typing for aiosqlite.Row access

**Theme:** Type safety | **Effort:** S

Throughout the file, row fields are accessed via string keys: `row["unique_id"]`, `row["sources"]`, etc. These are untyped — a typo like `row["sorces"]` fails at runtime with a KeyError, not at type-check time. The `TypedDict`s (`PropertyListItem`, `PropertyDetailItem`) only type the *output*, not the database row access.

**Recommendation:** This is inherent to raw SQL + aiosqlite.Row. Long-term, an ORM or a typed query builder would help, but for a personal project this is acceptable. Just noting the gap.

---

## Summary by Severity

| Severity | Count | Key themes |
|----------|-------|-----------|
| Critical | 0 | — |
| Major | 4 | MergedProperty reconstruction duplication, INSERT duplication, 18-param filter signature, get_properties_paginated complexity |
| Minor | 7 | Layer violation, suppress(Exception), initialize() size, fit_sort memory, f-string columns, individual ward updates, null default duplication |
| Suggestion | 4 | Class splitting, save_merged_property overlap, hardcoded WHERE defaults, untyped row access |

## Top 3 Takeaways

1. **Extract `_row_to_merged_property` helper** — The same ~40-line JSON parsing + MergedProperty construction pattern is duplicated 4 times (~160 lines total). A single helper eliminates this and ensures consistency. Quick win: 30-45 min.

2. **Introduce `PropertyFilter` dataclass** — The 18-parameter filter signature is repeated 3 times with identical forwarding. A dataclass encapsulates all filter params, eliminating ~200 lines of parameter passing and making it easy to add new filters. 1-2 hours.

3. **Move fit score computation out of storage** — `PropertyStorage` imports `fit_score` and `HOSTING_TOLERANCE` to enrich query results. This domain logic belongs in the web route layer, not the DB layer. Moving it improves separation of concerns and makes `storage.py` a pure persistence module. 1 hour.

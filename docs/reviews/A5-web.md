# A5: Web + Notifications Review

**Scope:** `routes.py` (878L), `app.py` (131L), `telegram.py` (964L), templates (1,260L total), `app.js` (737L), `style.css` (26,159L)
**Total:** ~30,129 LOC (including CSS) | **Date:** 2026-02-16

## Executive Summary

The web layer is **well-built with good UX patterns** — HTMX partial rendering, responsive filter modal with mobile sync, lightbox with focus trapping and touch/swipe, Leaflet map with marker clustering and card-map hover sync. The Telegram notifier is well-decomposed with proper flood control handling and graceful photo→text fallback.

The main issues are:

1. **18-parameter filter signature repeated 3 times** in routes.py — mirrors the same `PropertyFilter` dataclass need identified in A4
2. **Repetitive filter validation** — 12 string parameters follow identical strip/validate/whitelist pattern (~50 lines of near-identical code)
3. **Area context assembly duplicated** — property_detail and area_detail both build area context dicts with overlapping borough/council_tax/rent_trend lookups
4. **Keyboard building duplicated** — `send_property_notification` manually builds inline keyboard that `_build_inline_keyboard` already handles for merged properties

No critical issues. Security is handled well (XSS escaping, directory traversal protection, CSP-compatible headers, `textContent` in JS popups).

---

## Findings

### [MAJOR] routes.py:208-304,307-351,371-417 — 18-parameter filter signature repeated 3 times
**Theme:** Duplication | **Effort:** M

The same 18 query parameters (`min_price`, `max_price`, `bedrooms`, `min_rating`, `area`, `property_type`, `outdoor_space`, `natural_light`, `pets`, `value_rating`, `hob_type`, `floor_level`, `building_construction`, `office_separation`, `hosting_layout`, `hosting_noise_risk`, `broadband_type`, `tag`) are declared in:

1. `_validate_filters()` (lines 208-228) — as function parameters
2. `filter_count()` (lines 307-328) — as FastAPI query params, then forwarded to `_validate_filters`
3. `dashboard()` (lines 371-396) — as FastAPI query params, then forwarded to `_validate_filters`

Adding a new filter requires updating all 3 signatures plus `_validate_filters`'s body, the storage layer (A4), and the `_results.html` pagination macro.

This is the same issue identified in A4 (storage.py's 18-parameter `_build_filter_clauses`). The solution is also the same: a `PropertyFilter` dataclass or Pydantic model that flows from routes through storage.

**Recommendation:** Define a `PropertyFilter` Pydantic model that FastAPI can bind from query params (using `Depends`). All 3 route handlers receive a single `filters: PropertyFilter` parameter. The validation logic moves into the model's validators. This eliminates ~200 lines of parameter forwarding across routes + storage.

---

### [MAJOR] routes.py:230-283 — Repetitive strip/validate pattern for 12 string filters
**Theme:** Duplication | **Effort:** S

Each string filter follows the exact same 3-line pattern:

```python
property_type_val = property_type.strip() if property_type else None
if property_type_val and property_type_val not in VALID_PROPERTY_TYPES:
    property_type_val = None
```

This repeats 12 times (lines 243-280), totalling ~50 lines of near-identical code. The only variation is the parameter name and valid set.

**Recommendation:** Extract a `_validate_enum_param(value: str | None, valid: set[str]) -> str | None` helper:
```python
def _validate_enum_param(value: str | None, valid: set[str]) -> str | None:
    if not value: return None
    cleaned = value.strip().lower()
    return cleaned if cleaned in valid else None
```

This reduces the 12 blocks to 12 one-liners. Even better, this validation moves into the `PropertyFilter` model recommended above.

---

### [MINOR] routes.py:488-538 — Active filter chip building is ~50 lines of repetitive if-checks
**Theme:** Duplication | **Effort:** S

Each filter parameter gets an identical pattern to build a display chip:

```python
if property_type_val:
    pt_label = property_type_val.replace("_", " ").title()
    active_filters.append({"key": "property_type", "label": pt_label})
if outdoor_space_val:
    active_filters.append({"key": "outdoor_space", "label": f"Outdoor: {outdoor_space_val}"})
# ... 14 more times
```

**Recommendation:** Define a filter metadata list:
```python
FILTER_CHIPS = [
    ("property_type", lambda v: v.replace("_", " ").title()),
    ("outdoor_space", lambda v: f"Outdoor: {v}"),
    ...
]
```
Then iterate: `for key, fmt in FILTER_CHIPS: if f[key]: active_filters.append(...)`. This also ensures chip formatting stays in sync with filter additions.

---

### [MINOR] routes.py:419-436 — Dashboard unpacks validated dict back to individual variables
**Theme:** Complexity | **Effort:** S

After calling `_validate_filters()` which returns a dict, `dashboard()` immediately unpacks all 18 values back into individual variables (lines 419-436):

```python
min_price_val = f["min_price"]
max_price_val = f["max_price"]
bedrooms_val = f["bedrooms"]
# ... 15 more lines
```

These individual variables are then passed to the template context (lines 580-614) one by one. The unpacking adds 18 lines of pure boilerplate.

**Recommendation:** Pass `f` (the validated dict) directly into the template context with `**f`, or use the `PropertyFilter` model recommended above. The template already accesses these by key name.

---

### [MINOR] routes.py:692-735, 837-862 — Area context assembly duplicated between property_detail and area_detail
**Theme:** Duplication | **Effort:** S

Both `property_detail()` (lines 692-735) and `area_detail()` (lines 837-862) assemble an `area_context` dict with overlapping logic:

```python
# Both do this:
area_context["description"] = get_area_overview(outcode)
area_context["benchmarks"] = RENTAL_BENCHMARKS.get(outcode)
borough = OUTCODE_BOROUGH.get(outcode)
if borough:
    area_context["borough"] = borough
    area_context["council_tax"] = COUNCIL_TAX_MONTHLY.get(borough)
    area_context["rent_trend"] = RENT_TRENDS.get(borough)
```

`property_detail` adds additional fields (micro_area matching, acoustic profile, noise enforcement, hosting tolerance, creative scene) while `area_detail` adds micro_areas listing.

**Recommendation:** Extract a `build_area_context(outcode: str) -> dict` helper that handles the common fields. Each route extends the base context with its specific additions.

---

### [MINOR] routes.py:776-801 — Mid-function import and mixed concerns in property_detail
**Theme:** Complexity / Style | **Effort:** S

`property_detail()` is 160 lines (lines 658-818) that handles 6 distinct responsibilities:
1. Fetch property from DB (666-674)
2. Validate has images (684-690)
3. Build area context (692-735)
4. Build image URL map (738-749)
5. Find best description (752-759)
6. Compute fit score and cost breakdown (762-801)

The cost breakdown section has a mid-function import:
```python
from home_finder.utils.cost_calculator import estimate_true_monthly_cost
```

**Recommendation:** Move the import to module level. Consider extracting the cost breakdown computation into a helper function to reduce the route handler's complexity.

---

### [MINOR] routes.py:69-145 — TAG_CATEGORIES is a 76-line constant coupling routes to all highlight/lowlight variants
**Theme:** Coupling | **Effort:** S

`TAG_CATEGORIES` is a large dict mapping category names to lists of `PropertyHighlight` and `PropertyLowlight` enum values. It's used only for the filter modal's tag chips display (passed to template as `tag_categories`).

Every time a new highlight or lowlight is added to the enum, this mapping must be manually updated or the new tag won't appear in filters.

**Recommendation:** Consider adding a `category` field to the `PropertyHighlight` and `PropertyLowlight` enums, then generating `TAG_CATEGORIES` dynamically:
```python
TAG_CATEGORIES = defaultdict(list)
for h in PropertyHighlight:
    TAG_CATEGORIES[h.category].append(h.value)
```
This ensures new tags automatically appear in the correct category. Lower priority since enum changes are infrequent.

---

### [MINOR] telegram.py:655-724 — send_property_notification duplicates keyboard building
**Theme:** Duplication | **Effort:** S

`send_property_notification()` manually builds an `InlineKeyboardMarkup` with source link + map buttons (lines 684-702):

```python
buttons: list[InlineKeyboardButton] = [
    InlineKeyboardButton(text=SOURCE_NAMES.get(prop.source.value, ...), url=str(prop.url))
]
if prop.latitude is not None and prop.longitude is not None:
    map_url = f"https://www.google.com/maps?q={prop.latitude},{prop.longitude}"
    buttons.append(InlineKeyboardButton(text="Map 📍", url=map_url))
# ... postcode fallback ...
keyboard = InlineKeyboardMarkup(inline_keyboard=[buttons])
```

`_build_inline_keyboard()` (lines 545-594) already implements the same logic (source buttons + map button with coordinate/postcode fallback) for `MergedProperty`. The single-property version is a strict subset.

**Recommendation:** Either make `_build_inline_keyboard` accept `Property | MergedProperty`, or have `send_property_notification` wrap the `Property` in a temporary `MergedProperty` (which is already what the pipeline does before notification). This eliminates the duplicated keyboard building.

---

### [MINOR] telegram.py:92-145 — _format_value_info has 4 cascading return paths
**Theme:** Complexity | **Effort:** S

`_format_value_info()` has 4 different formatting branches, each constructing the output string differently:

1. `brief` mode: rating + optional note (lines 115-123)
2. Both `quality_adjusted_rating` and `note`: combined format (lines 126-131)
3. `quality_adjusted_rating` only: no benchmark data (lines 134-138)
4. Fallback to simple `rating` (lines 141-143)

The emoji lookup is duplicated across branches. The logic is correct but hard to verify at a glance.

**Recommendation:** Restructure to select `rating` and `note` first, then format once:
```python
rating = value.quality_adjusted_rating or value.rating
note = value.quality_adjusted_note or value.note
# ... single formatting path
```
Low priority since the current code works correctly.

---

### [MINOR] _results.html:1 — page_url macro is a single 500+ character line
**Theme:** Maintainability | **Effort:** S

The `page_url` macro is a single line listing every filter parameter:

```jinja
{% macro page_url(target_page) %}?page={{ target_page }}&sort={{ sort }}{% if min_price %}&min_price={{ min_price }}{% endif %}{% if max_price %}&max_price=...
```

Adding a new filter requires finding and extending this already-unreadable line. It's also tightly coupled to every filter parameter name.

**Recommendation:** Either:
- Break across multiple lines for readability
- Or better: build the URL from a filter dict in Python and pass it to the template, avoiding the manual param enumeration entirely. The `_validate_filters` return dict already has all the needed keys.

---

### [MINOR] app.js:380-613 — Dashboard map IIFE is 230+ lines mixing multiple concerns
**Theme:** Complexity | **Effort:** M

The dashboard map IIFE handles:
1. Map initialization and tile layer setup (lines 533-548)
2. Price pill icon creation (lines 393-402)
3. Rich popup DOM building (lines 404-480)
4. Marker-card hover sync (lines 482-510, 550-565)
5. Marker cluster management (lines 512-531)
6. HTMX data sync (lines 567-573)
7. Grid/split/map view toggle (lines 575-613)

**Recommendation:** Split into separate IIFEs or a simple module pattern: map initialization, popup builder, hover sync, view toggle. This doesn't require a build system — just logical separation within the file. Medium effort because the pieces share state (`dashMap`, `cluster`, `markersByPropertyId`).

---

### [MINOR] detail.html:161 — has_listing_data condition is 200+ characters on one line
**Theme:** Readability | **Effort:** S

```jinja
{% set has_listing_data = (le.epc_rating and le.epc_rating != 'unknown') or le.service_charge_pcm or le.deposit_weeks is not none or le.bills_included != 'unknown' or le.pets_allowed != 'unknown' or (le.parking and le.parking != 'unknown') or (le.council_tax_band and le.council_tax_band != 'unknown') or (le.property_type and le.property_type != 'unknown') or (le.furnished_status and le.furnished_status != 'unknown') or (le.broadband_type and le.broadband_type != 'unknown') %}
```

This 9-condition expression on a single line is hard to read and maintain.

**Recommendation:** Compute `has_listing_data` in the route handler (Python) and pass it as a template variable. The route already has access to `qa.listing_extraction`. Alternatively, use a Jinja `set` block with each condition on its own line (Jinja2 supports multi-line set blocks).

---

### [SUGGESTION] telegram.py — Format functions are well-decomposed but could use a shared pattern
**Theme:** Consistency | **Effort:** M

The module has 10 format helper functions (`_format_star_rating`, `_format_kitchen_info`, `_format_light_space_info`, `_format_space_info`, `_format_value_info`, `_format_header_lines`, `_format_bathroom_info`, `_format_outdoor_info`, `_format_listing_extraction_info`, `_format_viewing_notes`) plus 2 top-level formatters and a caption builder. This is good decomposition, but the helpers inconsistently return `str`, `str | None`, or `list[str]`, which forces `_format_quality_block` to check each return type differently.

**Recommendation:** Standardize on `str | None` (where `None` means "skip this section") for single-line helpers and `list[str]` for multi-line helpers. Not urgent — the current code works correctly.

---

### [SUGGESTION] detail.html:298-349 — Three identical viewing prep collapsible blocks
**Theme:** Duplication | **Effort:** S

The Viewing Prep section has three near-identical blocks for check_items, questions_for_agent, and deal_breaker_tests:

```jinja
<div class="viewing-checklist-group">
    <div class="viewing-group-header" onclick="...">
        <h4>Things to Check</h4>
        <span class="viewing-group-toggle">&#9660;</span>
    </div>
    <div class="viewing-group-body open">
        <ul class="viewing-checklist">
        {% for item in vn.check_items %}<li>{{ item }}</li>{% endfor %}
        </ul>
    </div>
</div>
```

**Recommendation:** Extract a Jinja macro: `{% macro viewing_group(title, items) %}...{% endmacro %}`. Reduces 50 lines to 3 macro calls.

---

### [SUGGESTION] app.py — Clean and well-structured
**Theme:** Positive | **Effort:** —

`app.py` at 131 lines is well-designed:
- Security headers middleware is appropriate
- Pipeline scheduling with jitter prevents fixed-offset scraping patterns
- Lifespan management handles clean shutdown
- `_pipeline_lock` prevents overlapping runs

The module-level `_pipeline_lock` is global mutable state, but this is the standard pattern for single-process asyncio servers and is appropriate here.

No findings — this is the cleanest file in the web layer.

---

### [SUGGESTION] app.js — Good accessibility and UX patterns
**Theme:** Positive | **Effort:** —

The JavaScript is well-organized with good practices:
- Lightbox has focus trapping, keyboard navigation, and touch/swipe support
- Map popup uses DOM API (`textContent`, `createElement`) consistently — safe against XSS
- `IntersectionObserver` for section nav highlighting and bar animations
- HTMX event handling is clean
- Card↔Marker hover sync with event delegation

The only concern is the 230-line dashboard map IIFE (noted above), but the code quality within it is good.

---

## Summary by Severity

| Severity | Count | Key themes |
|----------|-------|-----------|
| Critical | 0 | — |
| Major | 2 | Filter signature duplication (routes mirrors storage), repetitive validation |
| Minor | 9 | Active filter chips, dict unpacking, area context duplication, mid-function import, tag categories coupling, keyboard building dupe, value_info complexity, page_url macro, map IIFE size, long template condition |
| Suggestion | 4 | Format function consistency, viewing prep macro, app.py positive, app.js positive |

## Top 3 Takeaways

1. **PropertyFilter dataclass/model solves the 18-parameter problem across routes AND storage** — This is the single highest-value refactoring across A4 and A5. A shared `PropertyFilter` Pydantic model with built-in validation eliminates ~200 lines of parameter forwarding in routes.py, ~100 lines in storage.py, and makes adding new filters a single-file change. Estimated 2-3 hours.

2. **Extract area context builder** — Both `property_detail` and `area_detail` assemble area context dicts with overlapping logic. A shared `build_area_context(outcode)` helper eliminates the duplication and makes the pattern reusable if new pages need area data. 30 minutes.

3. **Unify keyboard building in telegram.py** — `send_property_notification` manually builds what `_build_inline_keyboard` already does for merged properties. Making the helper accept both `Property` and `MergedProperty` (or wrapping single properties) eliminates the duplication and ensures keyboard changes apply everywhere. 15 minutes.

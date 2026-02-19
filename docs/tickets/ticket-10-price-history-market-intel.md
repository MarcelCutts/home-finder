# Ticket 10: Price History & Market Intelligence

**Depends on:** Nothing
**Blocks:** Nothing (but complements Ticket 7 status tracking for a complete dashboard)

## Goal

Track price changes over time, show days-on-market intelligence, compute area rent benchmarks from scraped data, and provide negotiation context — so Marcel knows when to push on price, when to move fast, and which properties represent genuine value.

---

## 1. Price History Tracking

### 1A. Database schema

**File:** `src/home_finder/db/storage.py` (in `initialize()`)

```sql
CREATE TABLE IF NOT EXISTS price_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    property_unique_id TEXT NOT NULL,
    old_price INTEGER NOT NULL,
    new_price INTEGER NOT NULL,
    change_amount INTEGER NOT NULL,
    source TEXT,
    detected_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (property_unique_id) REFERENCES properties(unique_id)
);

CREATE INDEX IF NOT EXISTS idx_price_history_property
    ON price_history(property_unique_id);
CREATE INDEX IF NOT EXISTS idx_price_history_detected
    ON price_history(detected_at);
```

Add column to properties via migration loop (line 173):

```python
("price_drop_notified", "INTEGER", "0"),
```

### 1B. Price change detection

**File:** `src/home_finder/db/storage.py`

```python
async def detect_and_record_price_change(
    self,
    unique_id: str,
    new_price: int,
    source: str | None = None,
) -> int | None:
    """Compare new_price against DB; record change if different.

    Returns change_amount (negative = drop) or None if no change / not found.
    """
```

Implementation:
1. `SELECT price_pcm FROM properties WHERE unique_id = ?`
2. If row not found, return None (property is new, not re-seen)
3. If `price_pcm == new_price`, return None
4. INSERT into `price_history` (old_price, new_price, change_amount, source)
5. UPDATE `properties SET price_pcm = ?, price_drop_notified = 0` (reset notification flag for new drop)
6. Return `new_price - old_price`

```python
async def get_price_history(self, unique_id: str) -> list[dict[str, Any]]:
    """Get price change history for a property, newest first."""

async def get_unsent_price_drops(self) -> list[dict[str, Any]]:
    """Get properties with unnotified price drops for Telegram alerts.

    Returns properties where:
    - price_history has a negative change_amount
    - price_drop_notified = 0
    - notification_status = 'sent' (only alert for properties Marcel has already seen)
    """
```

### 1C. Pipeline integration point

**File:** `src/home_finder/main.py`

The critical insight: price change detection must happen **before** `storage.filter_new_merged()` (line 664) because that function discards already-seen properties. The detection must compare scraped prices against DB prices for re-seen properties.

In `_run_pre_analysis_pipeline()`, after wrapping as MergedProperties (line 656) and **before** the new property filter (line 664), add:

```python
# Step 3.5: Detect price changes for re-seen properties
price_changes = await _detect_price_changes(merged_properties, storage)
if price_changes:
    logger.info("price_changes_detected", count=len(price_changes), ...)
```

New helper function:

```python
async def _detect_price_changes(
    merged: list[MergedProperty],
    storage: PropertyStorage,
) -> list[tuple[str, int]]:
    """Compare scraped prices against DB for re-seen properties.

    Returns list of (unique_id, change_amount) for properties whose price changed.
    """
    changes = []
    for mp in merged:
        result = await storage.detect_and_record_price_change(
            mp.unique_id,
            mp.canonical.price_pcm,
            source=mp.canonical.source.value,
        )
        if result is not None:
            changes.append((mp.unique_id, result))
    return changes
```

**Price drop notifications:** After the price detection phase, in `run_pipeline()`, query `get_unsent_price_drops()` and send Telegram alerts before the main notification loop. Mark `price_drop_notified = 1` after successful send.

### 1D. Handling cross-source price disagreements

When sources disagree (Zoopla: £1800, Rightmove: £1850), the pipeline already tracks `min_price`/`max_price` on `MergedProperty`. Price history should track the **canonical** `price_pcm` (which comes from the first-seen source). If a different source reports a different price for the same merged property, that's captured in `min_price`/`max_price` but doesn't generate a price history event — only changes to the canonical source's price do. This prevents false alerts from cross-source variation.

---

## 2. Days-on-Market Display

This is largely UI-only — `first_seen` already exists in the DB (line 109 of storage.py), and there's already a `listing_age` Jinja2 filter in routes.py.

### 2A. Add `days_since` template filter

**File:** `src/home_finder/web/routes.py`

```python
def days_since_filter(iso_str: str | None) -> int:
    """Return integer days since ISO datetime string, or 0 if invalid."""
    if not iso_str:
        return 0
    try:
        dt = datetime.fromisoformat(iso_str)
        return max(0, (datetime.now(UTC) - dt).days)
    except (ValueError, TypeError):
        return 0

templates.env.filters["days_since"] = days_since_filter
```

### 2B. "Negotiable?" badge on property cards

**File:** `src/home_finder/web/templates/_property_card.html`

Add near the existing `card-age` span:

```html
{% set dom = prop.first_seen | days_since %}
{% if dom >= 14 %}
<span class="badge badge-amber" title="Listed {{ dom }} days — may be negotiable">Negotiable?</span>
{% endif %}
```

### 2C. Add "Longest listed" sort option

**File:** `src/home_finder/web/filters.py` (line 16)

Add to `VALID_SORT_OPTIONS`:

```python
VALID_SORT_OPTIONS: Final = {"newest", "price_asc", "price_desc", "rating_desc", "fit_desc", "longest_listed"}
```

**File:** `src/home_finder/db/web_queries.py` (line 248)

Add to `order_map`:

```python
"longest_listed": "p.first_seen ASC",
```

**File:** `src/home_finder/web/templates/dashboard.html`

Add option to sort dropdown:

```html
<option value="longest_listed" {% if sort == 'longest_listed' %}selected{% endif %}>Longest Listed</option>
```

### 2D. Detail page enhancement

**File:** `src/home_finder/web/templates/detail.html`

Add days-on-market display alongside the commute pill in the header metadata:

```html
{% set dom = prop.first_seen | days_since %}
{% if dom > 0 %}
<span class="listing-age-pill">
    Listed {{ dom }}d ago
    {% if dom >= 14 %} · May be negotiable{% endif %}
</span>
{% endif %}
```

---

## 3. Area Rent Benchmarking

### 3A. Benchmark table

**File:** `src/home_finder/db/storage.py` (in `initialize()`)

```sql
CREATE TABLE IF NOT EXISTS rent_benchmarks (
    outcode TEXT NOT NULL,
    bedrooms INTEGER NOT NULL,
    median_rent INTEGER NOT NULL,
    mean_rent INTEGER NOT NULL,
    sample_count INTEGER NOT NULL,
    computed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (outcode, bedrooms)
);
```

### 3B. Computation method

**File:** `src/home_finder/db/storage.py`

```python
async def compute_rent_benchmarks(self) -> int:
    """Recompute area rent benchmarks from scraped data.

    Groups properties by outcode + bedrooms, calculates median/mean.
    Returns number of benchmark rows written.
    """
```

Implementation:
1. Query all properties with postcodes, grouped by outcode + bedrooms
2. For each group, compute median (Python, since SQLite lacks PERCENTILE_CONT) and mean
3. Filter: only groups with sample_count >= 3 (minimum for meaningful benchmark)
4. DELETE FROM rent_benchmarks, then INSERT new rows (full replace)

SQLite query to get the raw data:

```sql
SELECT
    UPPER(SUBSTR(p.postcode, 1, INSTR(p.postcode || ' ', ' ') - 1)) as outcode,
    p.bedrooms,
    p.price_pcm
FROM properties p
WHERE p.postcode IS NOT NULL
  AND p.notification_status = 'sent'
ORDER BY outcode, bedrooms, price_pcm
```

Compute median in Python (sort prices, pick middle value). Write results.

```python
async def get_rent_benchmark(self, outcode: str, bedrooms: int) -> dict[str, Any] | None:
    """Get benchmark for a specific outcode + bedroom count."""
```

### 3C. Pipeline integration

**File:** `src/home_finder/main.py`

Call `await storage.compute_rent_benchmarks()` at the end of `run_pipeline()`, after all properties are saved. This is cheap (a few SQL aggregates) and ensures benchmarks are up-to-date after each run.

### 3D. Web query integration

**File:** `src/home_finder/db/web_queries.py`

In `get_properties_paginated()` (line 281), add a LEFT JOIN:

```sql
LEFT JOIN rent_benchmarks rb
    ON rb.outcode = UPPER(SUBSTR(p.postcode, 1, INSTR(p.postcode || ' ', ' ') - 1))
    AND rb.bedrooms = p.bedrooms
```

Add to the SELECT:

```sql
rb.median_rent as area_median,
(p.price_pcm - rb.median_rent) as benchmark_diff
```

### 3E. Dashboard display

**File:** `src/home_finder/web/templates/_property_card.html`

Add benchmark chip near the price display:

```html
{% if prop.benchmark_diff is defined and prop.benchmark_diff is not none %}
<span class="benchmark-chip {% if prop.benchmark_diff < 0 %}benchmark-below{% elif prop.benchmark_diff > 100 %}benchmark-above{% else %}benchmark-at{% endif %}">
    {% if prop.benchmark_diff < 0 %}
        £{{ (-prop.benchmark_diff)|int }} below avg
    {% elif prop.benchmark_diff > 0 %}
        £{{ prop.benchmark_diff|int }} above avg
    {% else %}
        At area avg
    {% endif %}
</span>
{% endif %}
```

### 3F. Relationship to existing static benchmarks

The existing `RENTAL_BENCHMARKS` in `src/home_finder/data/area_context.json` are hand-curated values used by `assess_value()` in `quality.py` (line 73) for the AI quality analysis. The new data-driven benchmarks complement these. Use data-driven benchmarks when available (sample_count >= 5) in the dashboard display, falling back to static benchmarks otherwise. Don't replace the AI quality pipeline's value assessment — that uses richer context.

---

## 4. Price Change Display

### 4A. Query enhancement

**File:** `src/home_finder/db/web_queries.py`

Add subqueries to `get_properties_paginated()` SELECT:

```sql
(SELECT ph.change_amount FROM price_history ph
 WHERE ph.property_unique_id = p.unique_id
 ORDER BY ph.detected_at DESC LIMIT 1) as last_price_change,
(SELECT ph.detected_at FROM price_history ph
 WHERE ph.property_unique_id = p.unique_id
 ORDER BY ph.detected_at DESC LIMIT 1) as price_changed_at
```

### 4B. Price badge on cards

**File:** `src/home_finder/web/templates/_property_card.html`

Add near the price overlay:

```html
{% if prop.last_price_change and prop.last_price_change < 0 %}
<span class="badge badge-green price-change-badge">
    Dropped £{{ "{:,}".format((-prop.last_price_change)|int) }}
</span>
{% elif prop.last_price_change and prop.last_price_change > 0 %}
<span class="badge badge-red price-change-badge">
    Up £{{ "{:,}".format(prop.last_price_change|int) }}
</span>
{% endif %}
```

### 4C. Price history on detail page

**File:** `src/home_finder/web/templates/detail.html`

Add a new card in the Overview section (after the existing Value Assessment card):

```html
{% if price_history %}
<div class="overview-card">
    <h4>Price History</h4>
    <div class="price-timeline">
        {% for event in price_history %}
        <div class="price-event {% if event.change_amount < 0 %}price-drop{% else %}price-increase{% endif %}">
            <span class="price-date">{{ event.detected_at | listing_age }}</span>
            <span class="price-change">
                £{{ "{:,}".format(event.old_price) }} → £{{ "{:,}".format(event.new_price) }}
                <span class="price-delta">
                    ({% if event.change_amount < 0 %}−{% else %}+{% endif %}£{{ "{:,}".format(event.change_amount|abs) }})
                </span>
            </span>
        </div>
        {% endfor %}
    </div>
</div>
{% endif %}
```

**File:** `src/home_finder/web/routes.py`

In `property_detail()`, add:

```python
price_history = await storage.get_price_history(unique_id)
benchmark = await storage.get_rent_benchmark(outcode, prop["bedrooms"])
```

Pass both to template context.

### 4D. Row mapper TypedDict updates

**File:** `src/home_finder/db/row_mappers.py`

Add to `PropertyListItem`:

```python
last_price_change: int | None
price_changed_at: str | None
area_median: int | None
benchmark_diff: int | None
```

---

## 5. Telegram Price Drop Alerts

### 5A. New notification method

**File:** `src/home_finder/notifiers/telegram.py`

```python
async def send_price_drop_notification(
    self,
    *,
    unique_id: str,
    title: str,
    old_price: int,
    new_price: int,
    postcode: str | None,
    days_listed: int,
    url: str,
) -> bool:
    """Send a compact price drop alert via Telegram."""
```

Format:

```
📉 Price dropped!
{title}
Was: £{old:,}/mo → Now: £{new:,}/mo (−£{abs(diff):,})
📍 {postcode} · Listed {days}d
```

Inline keyboard: [Details] [View Listing]

### 5B. Pipeline integration

**File:** `src/home_finder/main.py`

In `run_pipeline()`, after the price detection phase (step 3.5) and before the main notification loop:

```python
# Send price drop alerts for previously-seen properties
unsent_drops = await storage.get_unsent_price_drops()
for drop in unsent_drops:
    success = await notifier.send_price_drop_notification(...)
    if success:
        await storage.mark_price_drop_notified(drop["unique_id"])
```

---

## 6. Negotiation Intelligence (Stretch)

### 6A. Brief generator

**New file:** `src/home_finder/utils/negotiation.py`

```python
def generate_negotiation_brief(
    *,
    days_listed: int,
    price_history: list[dict[str, Any]],
    benchmark_diff: int | None,
    area_median: int | None,
    current_price: int,
) -> dict[str, Any] | None:
    """Generate a negotiation intelligence brief for a property.

    Returns dict with: strength, days_context, price_context,
    history_context, suggested_approach. Or None if insufficient data.
    """
```

Logic:
- `strength`: "strong" if 2+ of (days>14, already dropped, above avg); "moderate" if 1; "weak" otherwise
- `days_context`: "Listed 28 days (above 17-day London average)" or "Listed 5 days (fresh listing)"
- `price_context`: "12% above area median for 2-beds" or "5% below — grab it"
- `history_context`: "Already dropped once from £2,200" or "No price changes"
- `seasonal_context`: "February — rents typically 5-8% lower than peak"
- `suggested_approach`: Based on all factors, a 1-2 sentence recommendation

### 6B. Detail page integration

**File:** `src/home_finder/web/templates/detail.html`

Add a collapsible "Negotiation Intel" card:

```html
{% if negotiation %}
<details class="overview-card negotiation-card">
    <summary>
        <h4>Negotiation Intel</h4>
        <span class="badge badge-{{ negotiation.strength }}">{{ negotiation.strength }} position</span>
    </summary>
    <ul>
        <li>{{ negotiation.days_context }}</li>
        <li>{{ negotiation.price_context }}</li>
        {% if negotiation.history_context %}<li>{{ negotiation.history_context }}</li>{% endif %}
        <li>{{ negotiation.seasonal_context }}</li>
    </ul>
    <p><strong>Approach:</strong> {{ negotiation.suggested_approach }}</p>
</details>
{% endif %}
```

---

## 7. CSS

**File:** `src/home_finder/web/static/style.css`

```css
.price-change-badge {
    font-size: 0.7rem;
    font-weight: 600;
}

.benchmark-chip {
    font-size: 0.7rem;
    padding: 1px 6px;
    border-radius: 3px;
}
.benchmark-below { background: rgba(34, 197, 94, 0.15); color: #22c55e; }
.benchmark-above { background: rgba(245, 158, 11, 0.15); color: #f59e0b; }
.benchmark-at { background: rgba(100, 116, 139, 0.15); color: #94a3b8; }

.price-timeline {
    display: flex;
    flex-direction: column;
    gap: 8px;
}
.price-event { display: flex; justify-content: space-between; align-items: center; }
.price-drop .price-delta { color: #22c55e; }
.price-increase .price-delta { color: #ef4444; }

.listing-age-pill {
    font-size: 0.8rem;
    padding: 2px 8px;
    border-radius: 4px;
    background: rgba(100, 116, 139, 0.1);
}

.negotiation-card summary { cursor: pointer; display: flex; align-items: center; gap: 8px; }
```

---

## 8. Testing

### Test files

**`tests/test_db/test_price_history.py`**

- `test_detect_no_change` — same price returns None, no history row
- `test_detect_price_drop` — lower price returns negative change, history row created
- `test_detect_price_increase` — higher price returns positive change
- `test_detect_new_property_no_history` — unknown property returns None
- `test_get_price_history_ordered` — multiple changes, newest first
- `test_compute_benchmarks_basic` — 5 properties in E8, verify median
- `test_compute_benchmarks_min_sample` — <3 properties excluded
- `test_get_rent_benchmark` — round-trip
- `test_unsent_price_drops` — only returns unnotified drops for sent properties

**`tests/test_web/test_routes.py`** (extend)

- `test_property_card_price_drop_badge` — response includes green "Dropped" badge
- `test_property_card_benchmark_chip` — response includes area comparison
- `test_sort_longest_listed` — `?sort=longest_listed` returns oldest first
- `test_detail_price_history_section` — detail page shows history when available
- `test_negotiable_badge_14_days` — 14+ day old listing shows badge

**`tests/test_web/test_filters.py`** (extend)

- `test_longest_listed_sort_valid` — `longest_listed` in VALID_SORT_OPTIONS

**`tests/test_main/test_price_detection.py`** (new)

- `test_price_change_detected_in_pipeline` — mock storage, verify detection called before filter_new_merged
- `test_price_drop_notification_sent` — mock notifier, verify telegram alert for drops

**`tests/test_utils/test_negotiation.py`** (if stretch goal implemented)

- `test_strong_negotiation_position` — 14+ days + price drop + above avg
- `test_weak_negotiation_position` — fresh listing + at avg
- `test_insufficient_data_returns_none` — no benchmark, no history

---

## 9. Implementation Order

Build in this sequence — each sub-feature is independently shippable:

1. **Days-on-market display** (~1-2 hours) — Pure UI, no schema changes, uses existing `first_seen`. Add `days_since` filter, negotiable badge, "Longest listed" sort.
2. **Price history schema + detection** (~2-3 hours) — Table, detection method, pipeline integration.
3. **Price badges on cards + detail** (~1-2 hours) — Query changes, template updates.
4. **Telegram price drop alerts** (~1-2 hours) — New notification method, pipeline hook.
5. **Rent benchmarks** (~2-3 hours) — Table, computation, query JOIN, dashboard chips.
6. **Negotiation intelligence** (~1-2 hours, stretch) — Brief generator, detail page card.

---

## 10. Acceptance Criteria

- [ ] Price changes detected during pipeline scrape runs
- [ ] Price history recorded in `price_history` table
- [ ] Price drop/increase badges on property cards
- [ ] Price history timeline on property detail page
- [ ] "Listed X days ago" displayed on cards
- [ ] "Negotiable?" badge on properties listed 14+ days
- [ ] "Longest listed" sort option works
- [ ] Telegram alert when a previously-seen property drops price
- [ ] Area rent benchmarks computed from scraped data
- [ ] "£X above/below avg" chip on property cards
- [ ] Benchmark comparison on detail page
- [ ] All tests pass, types check, linting clean

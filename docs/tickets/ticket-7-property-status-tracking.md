# Ticket 7: Property Status Tracking

**Depends on:** Nothing
**Blocks:** Ticket 8 (AI Viewing Messages), Ticket 9 (One-Click Enquiry)

## Goal

Add a user-facing property lifecycle so Marcel can manage his rental pipeline from discovery through to signing. Currently all properties end at `notification_status = 'sent'` with no way to track what he's done with them. This ticket adds statuses, dashboard filters, quick-update controls, and Telegram inline buttons — the foundation that the viewing message and enquiry features build on.

---

## 1. Data Model

### 1A. Add `UserStatus` enum

**File:** `src/home_finder/models/core.py` (after `NotificationStatus` at line 144)

```python
class UserStatus(StrEnum):
    """User-facing property lifecycle status."""
    NEW = "new"
    INTERESTED = "interested"
    ENQUIRED = "enquired"
    VIEWING_BOOKED = "viewing_booked"
    VIEWED = "viewed"
    APPLIED = "applied"
    OFFERED = "offered"
    REJECTED = "rejected"
    ARCHIVED = "archived"
```

Add display metadata dict for badge rendering:

```python
USER_STATUS_META: Final[dict[str, dict[str, str]]] = {
    "new": {"label": "New", "color": "#6366f1"},
    "interested": {"label": "Interested", "color": "#8b5cf6"},
    "enquired": {"label": "Enquired", "color": "#3b82f6"},
    "viewing_booked": {"label": "Viewing", "color": "#f59e0b"},
    "viewed": {"label": "Viewed", "color": "#10b981"},
    "applied": {"label": "Applied", "color": "#06b6d4"},
    "offered": {"label": "Offered", "color": "#22c55e"},
    "rejected": {"label": "Rejected", "color": "#ef4444"},
    "archived": {"label": "Archived", "color": "#6b7280"},
}
```

Design note: transitions are not strictly enforced — any status can move to any other. This is a personal tool, not a multi-user workflow. The event log captures full history regardless.

### 1B. Export from models package

**File:** `src/home_finder/models/__init__.py`

Add `UserStatus` and `USER_STATUS_META` to imports and `__all__`.

---

## 2. Database Schema

### 2A. Add `user_status` column to properties

**File:** `src/home_finder/db/storage.py`

Add to the ALTER TABLE migration loop (line 173):

```python
("user_status", "TEXT", "'new'"),
```

This follows the existing migration pattern — `ALTER TABLE ... ADD COLUMN` with `DEFAULT 'new'` applies the default to all existing rows. No separate backfill needed.

Add index:

```sql
CREATE INDEX IF NOT EXISTS idx_user_status ON properties(user_status)
```

### 2B. Create `status_events` table

**File:** `src/home_finder/db/storage.py` (in `initialize()`, after existing table creations)

```sql
CREATE TABLE IF NOT EXISTS status_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    property_unique_id TEXT NOT NULL,
    from_status TEXT,
    to_status TEXT NOT NULL,
    note TEXT,
    source TEXT NOT NULL DEFAULT 'web',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (property_unique_id) REFERENCES properties(unique_id)
);

CREATE INDEX IF NOT EXISTS idx_status_events_property
    ON status_events(property_unique_id);
```

The `source` column tracks where the status change originated: `'web'` (dashboard), `'telegram'` (inline button), or `'api'` (future enquiry automation).

### 2C. New storage methods

**File:** `src/home_finder/db/storage.py`

```python
async def update_user_status(
    self,
    unique_id: str,
    new_status: UserStatus,
    *,
    note: str | None = None,
    source: str = "web",
) -> UserStatus | None:
    """Update property user_status and log the event.

    Returns the previous status, or None if property not found.
    """
```

Implementation: read current `user_status` from properties, UPDATE the column, INSERT into `status_events`, commit.

```python
async def get_status_history(self, unique_id: str) -> list[dict[str, Any]]:
    """Get status change history for a property, ordered chronologically."""
```

---

## 3. Web Dashboard — Filter & Query Changes

### 3A. Add status filter

**File:** `src/home_finder/web/filters.py`

Add valid options set:

```python
VALID_USER_STATUSES: Final = {s.value for s in UserStatus}
```

Add `status` field to `PropertyFilter` (line 100, with other fields):

```python
status: str | None = None
```

Add validator using existing `_validate_enum_field` pattern:

```python
@field_validator("status", mode="before")
@classmethod
def validate_status(cls, v: object) -> str | None:
    return _validate_enum_field(v, VALID_USER_STATUSES)
```

Add chip to `active_filter_chips()`:

```python
if self.status:
    chips.append({"key": "status", "label": USER_STATUS_META.get(self.status, {}).get("label", self.status.title())})
```

Add `status` parameter to `parse_filters()` function (line 362) and include in the dict passed to `PropertyFilter.model_validate()`.

### 3B. Wire into queries

**File:** `src/home_finder/db/web_queries.py`

In `build_filter_clauses()` (after the existing `added` filter, ~line 74), add:

```python
if filters.status:
    where_clauses.append("COALESCE(p.user_status, 'new') = ?")
    params.append(filters.status)
```

In `get_properties_paginated()`, the `p.*` already includes `user_status` since it selects all columns. No SELECT change needed — it's already in `dict(row)`.

In `get_property_detail()`, same — `p.*` already includes it.

### 3C. Add to row mapper TypedDicts

**File:** `src/home_finder/db/row_mappers.py`

Add to `PropertyListItem` (line 24):

```python
user_status: str | None
```

Add to `PropertyDetailItem` if not inherited.

---

## 4. Web Dashboard — Routes & Templates

### 4A. New PATCH endpoint

**File:** `src/home_finder/web/routes.py`

```python
@router.patch("/property/{unique_id}/status")
async def update_property_status(
    request: Request,
    unique_id: str,
    storage: StorageDep,
) -> Response:
    """Update property user status. Returns status badge partial for HTMX swap."""
```

Accept form data with `status` field. Validate against `UserStatus`. Call `storage.update_user_status()`. Return `_status_badge.html` partial for HTMX in-place swap. Return 400 for invalid status, 404 for unknown property.

### 4B. Pass status context to templates

**File:** `src/home_finder/web/routes.py`

In the `dashboard()` handler, add to template context:
- `user_statuses`: list of `UserStatus` values
- `status_meta`: `USER_STATUS_META` dict
- `status`: current filter value (from query params)

In the `property_detail()` handler, add:
- `user_status`: current status of this property
- `status_history`: from `storage.get_status_history()`
- `status_meta`: `USER_STATUS_META` dict
- `user_statuses`: list for the dropdown

### 4C. New template partials

**New file:** `src/home_finder/web/templates/_status_badge.html`

Reusable badge showing the current status with color:

```html
<span class="status-badge" id="status-badge-{{ unique_id }}"
      style="--status-color: {{ status_meta[status].color }}">
    {{ status_meta[status].label }}
</span>
```

**New file:** `src/home_finder/web/templates/_status_controls.html`

Status dropdown that auto-submits via HTMX:

```html
<div class="status-controls">
    <select name="status"
            hx-patch="/property/{{ unique_id }}/status"
            hx-target="#status-badge-{{ unique_id }}"
            hx-swap="outerHTML">
        {% for s in user_statuses %}
        <option value="{{ s.value }}" {% if s.value == current_status %}selected{% endif %}>
            {{ status_meta[s.value].label }}
        </option>
        {% endfor %}
    </select>
</div>
```

### 4D. Modify existing templates

**`src/home_finder/web/templates/_property_card.html`**

Add status badge in card footer area (near source badges). Only show for non-"new" statuses to avoid visual noise:

```html
{% if prop.user_status and prop.user_status != 'new' %}
<span class="status-badge" style="--status-color: {{ status_meta[prop.user_status].color }}">
    {{ status_meta[prop.user_status].label }}
</span>
{% endif %}
```

**`src/home_finder/web/templates/dashboard.html`**

Add status filter `<select>` in the filter controls section (near the existing area/bedrooms/price selects):

```html
<select name="status" aria-label="Status filter"
        hx-get="/" hx-target="#results" hx-push-url="true">
    <option value="">All statuses</option>
    {% for s in user_statuses %}
    <option value="{{ s.value }}" {% if status == s.value %}selected{% endif %}>
        {{ status_meta[s.value].label }}
    </option>
    {% endfor %}
</select>
```

**`src/home_finder/web/templates/detail.html`**

Add status bar in the header section (after price, before section nav). Include the badge + dropdown controls. Optionally add a status timeline section showing `status_history` events chronologically.

**`src/home_finder/web/templates/_results.html`**

Add `{% if status %}&status={{ status }}{% endif %}` to the `page_url` macro (line 1) so pagination preserves the status filter.

### 4E. CSS

**File:** `src/home_finder/web/static/style.css`

```css
.status-badge {
    display: inline-flex;
    align-items: center;
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 0.7rem;
    font-weight: 600;
    letter-spacing: 0.02em;
    background: color-mix(in srgb, var(--status-color) 20%, transparent);
    color: var(--status-color);
    border: 1px solid color-mix(in srgb, var(--status-color) 30%, transparent);
}

.status-controls select {
    font-size: 0.8rem;
    padding: 4px 8px;
    border-radius: 4px;
}
```

The `color-mix()` approach uses CSS custom properties so a single class works for all statuses — no need for per-status CSS classes. Works with the existing dark theme.

---

## 5. Telegram Integration

### 5A. Add inline buttons to notifications

**File:** `src/home_finder/notifiers/telegram.py`

Modify `_build_inline_keyboard()` (line 550) to add status buttons as the first row:

```python
from aiogram.types import InlineKeyboardButton

status_row = [
    InlineKeyboardButton(text="👍 Interested", callback_data=f"status:{merged.unique_id}:interested"),
    InlineKeyboardButton(text="⏭ Skip", callback_data=f"status:{merged.unique_id}:archived"),
]
rows.insert(0, status_row)
```

**Callback data size check:** Telegram limits callback_data to 64 bytes. Format `status:{unique_id}:{status}` — e.g., `status:onthemarket:12345678:interested` = 42 chars. Fits within limit for all current source+id combinations.

### 5B. Add callback handler

**File:** `src/home_finder/notifiers/telegram.py`

Add a new class:

```python
class TelegramCallbackHandler:
    """Handle Telegram inline keyboard callbacks for status updates."""

    def __init__(self, storage: PropertyStorage) -> None:
        self.storage = storage

    async def handle_status_callback(self, callback_query: CallbackQuery) -> None:
        """Parse callback_data 'status:{uid}:{status}' and update DB."""
```

Parse callback data, validate against `UserStatus`, call `storage.update_user_status(source="telegram")`, answer the callback query with a toast message ("Marked as Interested"), and optionally update the inline keyboard to show the selected state.

### 5C. Register webhook

**File:** `src/home_finder/web/routes.py` (or a new `src/home_finder/web/telegram_webhook.py`)

Add `POST /telegram/webhook` endpoint. On startup, register webhook URL via `bot.set_webhook()`.

**File:** `src/home_finder/config.py`

Add optional `telegram_webhook_url: str = ""` setting. When set, enables the callback handler and webhook registration.

**Security:** Validate `X-Telegram-Bot-Api-Secret-Token` header on incoming webhook requests.

---

## 6. Testing

### New test files

**`tests/test_db/test_status.py`**

- `test_update_status_from_new_to_interested` — verify column update + event logged
- `test_update_status_returns_previous` — returns old status
- `test_update_nonexistent_property` — returns None
- `test_status_history_chronological` — multiple transitions, verify order
- `test_default_status_is_new` — newly saved property has `user_status = 'new'`

**`tests/test_web/test_routes.py`** (extend existing)

- `test_patch_status_success` — PATCH returns 200 with badge HTML
- `test_patch_status_htmx_partial` — response is valid HTML fragment
- `test_patch_invalid_status_400` — bad status value returns 400
- `test_patch_unknown_property_404` — unknown unique_id returns 404
- `test_dashboard_status_filter` — `?status=interested` only returns interested properties
- `test_pagination_preserves_status` — page links include `&status=`

**`tests/test_web/test_filters.py`** (extend existing)

- `test_valid_status_filter` — `PropertyFilter(status="interested")` works
- `test_invalid_status_becomes_none` — `PropertyFilter(status="bogus")` → None
- `test_status_chip_display` — `active_filter_chips()` includes status chip

**`tests/test_notifiers/`** (extend or new file)

- `test_inline_keyboard_has_status_buttons` — verify `_build_inline_keyboard` includes Interested/Skip
- `test_callback_data_format` — verify format fits 64-byte limit
- `test_callback_handler_updates_status` — mock storage, verify DB update + answer

---

## 7. Acceptance Criteria

- [ ] Properties default to `user_status = 'new'` (existing properties migrated)
- [ ] Dashboard shows status badge on property cards (non-"new" only)
- [ ] Dashboard has status filter dropdown; pagination preserves it
- [ ] Property detail page has status dropdown that updates via HTMX
- [ ] PATCH `/property/{uid}/status` works and returns badge partial
- [ ] Status changes logged in `status_events` table with timestamp and source
- [ ] Telegram notifications include "Interested" and "Skip" inline buttons
- [ ] Telegram button taps update the DB status
- [ ] All tests pass, types check, linting clean

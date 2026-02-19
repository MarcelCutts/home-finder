# Ticket 8: AI-Drafted Viewing Request Messages

**Depends on:** Ticket 7 (Property Status Tracking — needed for status transitions)
**Blocks:** Ticket 9 (One-Click Enquiry — uses the generated message as enquiry body)

## Goal

After Claude analyses a property's quality, it already knows the place inside-out. Reuse that knowledge to draft a personalised ~100-word viewing request message for each property — referencing specific features, stating Marcel's situation, and proposing viewing times. One button on the dashboard, copy to clipboard, paste into the portal.

---

## 1. Viewing Message Generator

### 1A. New module

**New file:** `src/home_finder/filters/viewing_message.py`

This is a thin wrapper around a Claude API call. Follow the existing pattern in `quality.py` (lines 1-60) for client initialization, retry config, and rate limiting.

```python
async def generate_viewing_message(
    *,
    analysis: PropertyQualityAnalysis,
    title: str,
    price_pcm: int,
    bedrooms: int,
    postcode: str | None,
    source_name: str,
    profile: str,
    api_key: str,
) -> str:
    """Draft a personalised viewing request message using Claude.

    Uses the existing quality analysis (not images) to reference specific
    property features. Text-only call — cheap and fast.

    Returns:
        Plain text message (~100 words) ready to send to an agent.
    """
```

**API call details:**
- Model: `claude-sonnet-4-6` (same as quality analysis Phase 2 — text-only, fast)
- No images — this is a text-generation call using the analysis JSON
- Use `cache_control: {"type": "ephemeral"}` on the system prompt for cost savings
- `max_tokens: 1024` (message is ~100 words, but leave room)
- No tool use needed — just generate text

**System prompt** (hardcode in the module or in a prompts file):

```
You are writing a viewing request message for a London rental property.
Write a warm, professional message (~80-120 words) from a prospective tenant
to the letting agent or landlord. The message should:

1. Open with genuine interest referencing 1-2 specific features from the
   property analysis (e.g., "The gas hob and separate office space are
   exactly what I'm looking for")
2. Briefly state the tenant's situation (provided in the profile)
3. Mention readiness to provide references and move quickly
4. Propose flexible viewing availability
5. Close politely

Do NOT:
- Use generic phrases like "I'm very interested in your property"
- Mention the price or try to negotiate
- Be overly formal or stiff
- Exceed 150 words

Return ONLY the message text, no greeting, no subject line.
```

**User prompt** should include:
- Property title, price, bedrooms, postcode
- The quality analysis highlights, lowlights, and one-liner
- Kitchen hob type, office separation score, any standout features
- Marcel's profile string

### 1B. Profile configuration

**File:** `src/home_finder/config.py`

Add a new setting:

```python
viewing_message_profile: str = (
    "I'm a remote software consultant running a small company from home. "
    "I have excellent references from my current and previous landlords, "
    "and I'm ready to move quickly with all documents prepared."
)
```

This is the self-description included in every viewing message. Configurable so Marcel can tweak it without changing code.

---

## 2. Message Caching

### 2A. Database table

**File:** `src/home_finder/db/storage.py` (in `initialize()`)

```sql
CREATE TABLE IF NOT EXISTS viewing_messages (
    property_unique_id TEXT PRIMARY KEY,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (property_unique_id) REFERENCES properties(unique_id)
);
```

### 2B. Storage methods

**File:** `src/home_finder/db/storage.py`

```python
async def get_viewing_message(self, unique_id: str) -> str | None:
    """Get cached viewing message for a property."""

async def save_viewing_message(self, unique_id: str, message: str) -> None:
    """Save or replace a viewing message for a property."""

async def delete_viewing_message(self, unique_id: str) -> None:
    """Delete cached viewing message (for regeneration)."""
```

---

## 3. Web Dashboard

### 3A. Routes

**File:** `src/home_finder/web/routes.py`

**Generate/retrieve message:**

```python
@router.post("/property/{unique_id}/viewing-message")
async def generate_viewing_message_endpoint(
    request: Request,
    unique_id: str,
    storage: StorageDep,
) -> Response:
    """Generate a viewing message for a property. Returns HTML partial.

    If a cached message exists, returns it immediately.
    If not, generates via Claude API, caches, and returns.
    """
```

Flow:
1. Check cache: `storage.get_viewing_message(unique_id)`
2. If cached, return the partial with the message
3. If not cached, load property detail + quality analysis from DB
4. If no quality analysis exists, return a helpful error partial
5. Call `generate_viewing_message()` with the analysis data
6. Cache the result: `storage.save_viewing_message()`
7. Return `_viewing_message.html` partial

**Regenerate message:**

```python
@router.post("/property/{unique_id}/viewing-message/regenerate")
async def regenerate_viewing_message(
    request: Request,
    unique_id: str,
    storage: StorageDep,
) -> Response:
    """Delete cached message and generate a fresh one."""
```

Delete cache, then call the same generation logic.

### 3B. Template partial

**New file:** `src/home_finder/web/templates/_viewing_message.html`

```html
<div class="viewing-message-card" id="viewing-message-{{ unique_id }}">
    <h4>Viewing Request Draft</h4>
    <textarea id="viewing-msg-text-{{ unique_id }}" rows="6" readonly
              class="viewing-message-textarea">{{ message }}</textarea>
    <div class="viewing-message-actions">
        <button type="button" class="btn-copy"
                onclick="navigator.clipboard.writeText(document.getElementById('viewing-msg-text-{{ unique_id }}').value)">
            Copy to clipboard
        </button>
        <button type="button"
                hx-post="/property/{{ unique_id }}/viewing-message/regenerate"
                hx-target="#viewing-message-{{ unique_id }}"
                hx-swap="outerHTML"
                class="btn-outline btn-sm">
            Regenerate
        </button>
    </div>
    {% if source_urls %}
    <div class="viewing-message-portals">
        <span class="label">Send via:</span>
        {% for source, url in source_urls.items() %}
        <a href="{{ url }}" target="_blank" rel="noopener" class="btn-portal">
            {{ source_names[source] }} ↗
        </a>
        {% endfor %}
    </div>
    {% endif %}
</div>
```

### 3C. Integrate into detail page

**File:** `src/home_finder/web/templates/detail.html`

Add a "Draft Viewing Message" button in the header action area (near the status controls from Ticket 7):

```html
<button type="button"
        hx-post="/property/{{ prop.unique_id }}/viewing-message"
        hx-target="#viewing-message-container"
        hx-swap="innerHTML"
        hx-indicator="#msg-spinner"
        class="btn-primary">
    Draft Viewing Message
</button>
<span id="msg-spinner" class="htmx-indicator">Generating...</span>
<div id="viewing-message-container"></div>
```

The button triggers a POST, which loads the message partial into the container. Subsequent clicks return the cached version instantly.

### 3D. CSS

**File:** `src/home_finder/web/static/style.css`

```css
.viewing-message-textarea {
    width: 100%;
    font-size: 0.9rem;
    line-height: 1.5;
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 12px;
    resize: vertical;
}

.viewing-message-actions {
    display: flex;
    gap: 8px;
    margin-top: 8px;
}

.btn-copy {
    /* Use the primary action style from existing CSS */
}

.viewing-message-portals {
    margin-top: 12px;
    display: flex;
    gap: 8px;
    align-items: center;
    flex-wrap: wrap;
}
```

---

## 4. Error Handling

- If the Anthropic API is unavailable (rate limit, outage), return an error partial: "Couldn't generate message. Try again in a moment." Don't throw — the existing circuit breaker pattern from `quality.py` should be reused if feasible, but for a single on-demand call, just catch the exception and return a user-friendly partial.
- If no quality analysis exists for the property, return a partial suggesting the user wait for analysis or request re-analysis.
- If the API key is not configured, return a partial explaining that the feature requires `HOME_FINDER_ANTHROPIC_API_KEY`.

---

## 5. Cost Estimate

- Text-only call with claude-sonnet-4-6
- Input: ~500-800 tokens (system prompt + analysis summary + profile)
- Output: ~150-200 tokens
- Cost: ~$0.003-0.005 per message (negligible)
- Prompt caching on system prompt reduces cost by ~90% for subsequent calls

---

## 6. Testing

### Test files

**`tests/test_filters/test_viewing_message.py`**

- `test_generate_message_returns_text` — mock Anthropic client, verify non-empty string returned
- `test_message_references_property_features` — verify the prompt includes highlights/analysis data
- `test_message_includes_profile` — verify the profile string is passed to the API
- `test_api_error_handled_gracefully` — mock API exception, verify graceful return

**`tests/test_web/test_routes.py`** (extend)

- `test_viewing_message_endpoint_generates` — POST returns HTML with textarea
- `test_viewing_message_cached` — second POST returns same message without API call
- `test_viewing_message_regenerate` — regenerate endpoint clears cache and generates fresh
- `test_viewing_message_no_analysis_400` — property without quality analysis returns helpful error
- `test_viewing_message_copy_button` — response HTML contains copy button

**`tests/test_db/test_storage.py`** (extend)

- `test_save_and_get_viewing_message` — round-trip cache
- `test_delete_viewing_message` — delete then get returns None

---

## 7. Acceptance Criteria

- [ ] "Draft Viewing Message" button appears on property detail page
- [ ] Clicking generates a personalised ~100-word message via Claude
- [ ] Message references specific property features from quality analysis
- [ ] Message includes Marcel's profile/situation
- [ ] Generated messages are cached in DB (instant on second click)
- [ ] "Regenerate" button clears cache and generates fresh
- [ ] "Copy to clipboard" button works
- [ ] Portal links shown below the message for quick navigation
- [ ] Graceful error handling for missing analysis, API errors
- [ ] All tests pass, types check, linting clean

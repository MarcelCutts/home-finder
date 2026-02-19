# Ticket 9: One-Click Enquiry Submission

**Depends on:** Ticket 7 (Status Tracking), Ticket 8 (AI Viewing Messages)
**Blocks:** Nothing

## Goal

Automate "contact agent" / "book viewing" form submission on rental portals via Playwright browser automation. Marcel reviews the AI-drafted viewing message on the dashboard, confirms, and the system fills and submits the portal's contact form. Human-in-the-loop always — never auto-submits without confirmation.

**Start with OpenRent** (simplest, direct landlord contact), then Zoopla, then Rightmove.

---

## 1. Architecture

### 1A. New package

**New directory:** `src/home_finder/enquiry/`

```
src/home_finder/enquiry/
├── __init__.py          # Exports
├── base.py              # ABC + shared types
├── openrent.py          # OpenRent form automation
├── zoopla.py            # Zoopla form automation
├── rightmove.py         # Rightmove form automation
└── models.py            # EnquiryResult, ContactInfo
```

### 1B. Models

**New file:** `src/home_finder/enquiry/models.py`

```python
from datetime import datetime
from enum import StrEnum
from pydantic import BaseModel

class EnquiryStatus(StrEnum):
    PENDING = "pending"
    SUBMITTED = "submitted"
    FAILED = "failed"

class ContactInfo(BaseModel):
    """Pre-configured contact details for form filling."""
    name: str
    email: str
    phone: str

class EnquiryResult(BaseModel):
    """Result of an enquiry submission attempt."""
    property_unique_id: str
    portal: str  # openrent, zoopla, rightmove, onthemarket
    status: EnquiryStatus
    message_sent: str
    submitted_at: datetime | None = None
    error: str | None = None
    screenshot_path: str | None = None  # For debugging failed submissions
```

### 1C. Base submitter

**New file:** `src/home_finder/enquiry/base.py`

```python
from abc import ABC, abstractmethod

class BaseEnquirySubmitter(ABC):
    """Abstract base for portal-specific enquiry submission."""

    @abstractmethod
    async def submit(
        self,
        *,
        listing_url: str,
        message: str,
        contact: ContactInfo,
    ) -> EnquiryResult:
        """Fill and submit the contact/viewing form on a portal listing page.

        Must be idempotent — if already submitted for this listing, return
        a result indicating duplicate (not an error).
        """
        ...

    @abstractmethod
    async def close(self) -> None:
        """Clean up browser resources."""
        ...
```

---

## 2. OpenRent Implementation (Phase 1)

### 2A. Form analysis

OpenRent's "Book Viewing" flow (based on research):
1. Navigate to listing URL (e.g., `https://www.openrent.com/property-to-rent/london/12345`)
2. Click "Book Viewing" or "Send Enquiry" button
3. Form fields: Name, Email, Phone, Message (optional)
4. Submit button
5. Confirmation page/modal

The form is relatively clean HTML — no heavy anti-bot protection (unlike Zoopla/Rightmove).

### 2B. Implementation

**New file:** `src/home_finder/enquiry/openrent.py`

```python
from playwright.async_api import async_playwright, Browser, Page

class OpenRentSubmitter(BaseEnquirySubmitter):
    def __init__(self, *, headless: bool = True) -> None:
        self._headless = headless
        self._browser: Browser | None = None
        self._playwright = None

    async def _get_browser(self) -> Browser:
        if self._browser is None:
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(headless=self._headless)
        return self._browser

    async def submit(
        self,
        *,
        listing_url: str,
        message: str,
        contact: ContactInfo,
    ) -> EnquiryResult:
        """Submit viewing request on OpenRent."""
        browser = await self._get_browser()
        page = await browser.new_page()
        try:
            await page.goto(listing_url, wait_until="domcontentloaded")

            # Find and click "Book Viewing" or "Enquire" button
            # Fill name, email, phone, message fields
            # Click submit
            # Wait for confirmation
            # Take screenshot for audit trail

            return EnquiryResult(
                property_unique_id=...,
                portal="openrent",
                status=EnquiryStatus.SUBMITTED,
                message_sent=message,
                submitted_at=datetime.now(UTC),
            )
        except Exception as e:
            # Screenshot on failure for debugging
            screenshot_path = ...
            await page.screenshot(path=screenshot_path)
            return EnquiryResult(
                property_unique_id=...,
                portal="openrent",
                status=EnquiryStatus.FAILED,
                message_sent=message,
                error=str(e),
                screenshot_path=screenshot_path,
            )
        finally:
            await page.close()

    async def close(self) -> None:
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
```

**Important implementation notes:**

- Use realistic browser viewport (1280x720) and user agent
- Add small random delays between field fills (100-300ms) to appear human-like
- Take a screenshot before and after submission for audit trail
- Handle common failure modes: listing removed, form changed, CAPTCHA, network error
- The form selectors will need to be reverse-engineered from the actual OpenRent page. Use `page.locator()` with robust selectors (prefer `role`, `label`, `placeholder` over CSS classes)

### 2C. Zoopla implementation (Phase 2)

**New file:** `src/home_finder/enquiry/zoopla.py`

Zoopla's "Email Agent" form. More complex than OpenRent — likely uses React/dynamic rendering. May need to wait for hydration. Zoopla pages require `curl_cffi` in the scraper due to TLS fingerprinting, but Playwright with a real browser should bypass this.

Key difference from OpenRent: Zoopla connects to letting agents, not landlords directly. The form typically has: Name, Email, Phone, Message, "Best time to call" dropdown.

### 2D. Rightmove implementation (Phase 3)

**New file:** `src/home_finder/enquiry/rightmove.py`

Most complex — Rightmove's "Contact Agent" form sometimes requires login or has additional verification. May need cookies/session management.

---

## 3. Database

### 3A. Enquiry log table

**File:** `src/home_finder/db/storage.py` (in `initialize()`)

```sql
CREATE TABLE IF NOT EXISTS enquiry_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    property_unique_id TEXT NOT NULL,
    portal TEXT NOT NULL,
    message TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    submitted_at TEXT,
    error TEXT,
    screenshot_path TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (property_unique_id) REFERENCES properties(unique_id),
    UNIQUE(property_unique_id, portal)
);

CREATE INDEX IF NOT EXISTS idx_enquiry_log_property
    ON enquiry_log(property_unique_id);
```

The `UNIQUE(property_unique_id, portal)` constraint prevents double-submission to the same portal.

### 3B. Storage methods

**File:** `src/home_finder/db/storage.py`

```python
async def log_enquiry(self, result: EnquiryResult) -> None:
    """Record an enquiry attempt (INSERT or UPDATE on conflict)."""

async def get_enquiries_for_property(self, unique_id: str) -> list[dict[str, Any]]:
    """Get all enquiry attempts for a property."""

async def has_enquiry(self, unique_id: str, portal: str) -> bool:
    """Check if an enquiry has already been submitted for this property+portal."""
```

---

## 4. Web Dashboard

### 4A. Routes

**File:** `src/home_finder/web/routes.py`

**Preview endpoint (shows what will be sent):**

```python
@router.post("/property/{unique_id}/enquiry/preview")
async def enquiry_preview(
    request: Request,
    unique_id: str,
    storage: StorageDep,
) -> Response:
    """Show enquiry preview: message + contact info + portal selector."""
```

Flow:
1. Load viewing message from cache (if not generated, prompt user to generate first)
2. Load property detail (source URLs determine which portals are available)
3. Load contact info from config
4. Check which portals already have submissions (`has_enquiry()`)
5. Return `_enquiry_preview.html` partial

**Submit endpoint (actually sends):**

```python
@router.post("/property/{unique_id}/enquiry/submit")
async def submit_enquiry(
    request: Request,
    unique_id: str,
    storage: StorageDep,
) -> Response:
    """Submit enquiry to a specific portal. Accepts form data: portal, message."""
```

Flow:
1. Parse `portal` from form data
2. Validate portal is in enabled list
3. Check for duplicate (`has_enquiry()`)
4. Get appropriate submitter instance
5. Submit via Playwright
6. Log result to `enquiry_log`
7. Update `user_status` to `enquired` (via Ticket 7's `update_user_status()`)
8. Return `_enquiry_result.html` partial (success/failure message)

### 4B. Template partials

**New file:** `src/home_finder/web/templates/_enquiry_preview.html`

```html
<div class="enquiry-preview" id="enquiry-preview-{{ unique_id }}">
    <h4>Send Viewing Request</h4>

    <div class="enquiry-message-preview">
        <label>Message:</label>
        <textarea name="message" rows="5">{{ message }}</textarea>
    </div>

    <div class="enquiry-contact-preview">
        <label>Sending as:</label>
        <p>{{ contact.name }} · {{ contact.email }} · {{ contact.phone }}</p>
    </div>

    <div class="enquiry-portals">
        {% for source, url in available_portals.items() %}
        <div class="enquiry-portal-row">
            <span class="portal-name">{{ source_names[source] }}</span>
            {% if submitted_portals[source] %}
            <span class="badge badge-green">Sent ✓</span>
            {% else %}
            <form hx-post="/property/{{ unique_id }}/enquiry/submit"
                  hx-target="#enquiry-result-{{ source }}"
                  hx-swap="innerHTML"
                  hx-confirm="Send viewing request to {{ source_names[source] }}?">
                <input type="hidden" name="portal" value="{{ source }}">
                <button type="submit" class="btn-primary btn-sm">
                    Send to {{ source_names[source] }}
                </button>
            </form>
            {% endif %}
            <div id="enquiry-result-{{ source }}"></div>
        </div>
        {% endfor %}
    </div>
</div>
```

**New file:** `src/home_finder/web/templates/_enquiry_result.html`

```html
{% if result.status == 'submitted' %}
<span class="badge badge-green">Sent ✓ {{ result.submitted_at | listing_age }}</span>
{% else %}
<span class="badge badge-red">Failed: {{ result.error }}</span>
<button hx-post="/property/{{ unique_id }}/enquiry/submit"
        hx-target="#enquiry-result-{{ portal }}"
        class="btn-sm btn-outline">Retry</button>
{% endif %}
```

### 4C. Integrate into detail page

**File:** `src/home_finder/web/templates/detail.html`

Add a "Send Enquiry" button near the viewing message section (from Ticket 8). This button loads the preview partial:

```html
<button hx-post="/property/{{ prop.unique_id }}/enquiry/preview"
        hx-target="#enquiry-container"
        hx-swap="innerHTML"
        class="btn-secondary">
    Send Enquiry
</button>
<div id="enquiry-container"></div>
```

The flow is: Draft Message → Review → Send Enquiry → Preview → Confirm per portal → Submitted.

---

## 5. Configuration

**File:** `src/home_finder/config.py`

```python
# Enquiry submission settings
enquiry_name: str = ""
enquiry_email: str = ""
enquiry_phone: str = ""
enquiry_enabled_portals: str = "openrent"  # Comma-separated: openrent,zoopla,rightmove
```

All three contact fields must be set for enquiry submission to be available. If any is empty, the "Send Enquiry" button is hidden and the preview endpoint returns an instructional message.

### Dependencies

**File:** `pyproject.toml`

Add `playwright` to dependencies:

```toml
[project.dependencies]
# ... existing deps ...
playwright = ">=1.40"
```

**Post-install note:** Users need to run `playwright install chromium` after installing. Document in README.md.

---

## 6. Safety & Rate Limiting

- **Human-in-the-loop always:** The `hx-confirm` attribute on submit buttons forces a browser confirmation dialog. No auto-submission path exists.
- **Duplicate prevention:** `UNIQUE(property_unique_id, portal)` constraint + `has_enquiry()` check. UI shows "Sent ✓" badge for already-submitted portals.
- **Rate limiting:** Max 1 submission per 30 seconds (use a simple in-memory timestamp check). This prevents accidental rapid-fire clicks.
- **Screenshot audit trail:** Every submission attempt (success or failure) takes a screenshot saved to `{data_dir}/enquiry_screenshots/{unique_id}/{portal}_{timestamp}.png`. Useful for debugging if a portal changes its form.
- **ToS consideration:** This is personal-use, low-volume automation. Portals prohibit automated submissions in their ToS, but at 1-5 enquiries per day from a personal tool, practical risk is minimal. The tool uses a real browser (not API scraping), making it indistinguishable from a human filling forms.

---

## 7. Testing

### Test approach

Playwright form automation is hard to unit test against real portals (sites change, need network). Use a two-layer approach:

1. **Unit tests with mock page:** Mock `playwright.async_api.Page` to verify form fill sequence
2. **Integration tests with local HTML fixtures:** Create a minimal HTML form that mimics OpenRent's structure, serve it locally, run Playwright against it

### Test files

**`tests/test_enquiry/test_openrent.py`**

- `test_submit_fills_all_fields` — mock Page, verify `page.fill()` called for name/email/phone/message
- `test_submit_clicks_submit_button` — verify `page.click()` called
- `test_submit_returns_result` — verify EnquiryResult with SUBMITTED status
- `test_submit_failure_captures_screenshot` — mock exception, verify screenshot taken
- `test_submit_failure_returns_error` — verify EnquiryResult with FAILED status

**`tests/test_enquiry/test_models.py`**

- `test_contact_info_validation` — required fields
- `test_enquiry_result_serialization` — round-trip

**`tests/test_web/test_routes.py`** (extend)

- `test_enquiry_preview_shows_portals` — POST returns HTML with portal options
- `test_enquiry_preview_shows_sent_status` — already-submitted portals show badge
- `test_enquiry_submit_duplicate_prevented` — second submit returns duplicate message
- `test_enquiry_submit_updates_status` — user_status transitions to 'enquired'
- `test_enquiry_disabled_without_contact_info` — button hidden when config incomplete

**`tests/test_db/test_storage.py`** (extend)

- `test_log_enquiry_and_retrieve` — round-trip
- `test_has_enquiry_true_after_log` — boolean check
- `test_duplicate_enquiry_upsert` — UNIQUE constraint handled

---

## 8. Implementation Order

1. **Models + base class + config** — foundation, no external deps
2. **Database schema + storage methods** — can test independently
3. **OpenRent submitter** — reverse-engineer the form, implement, test with fixture HTML
4. **Routes + templates** — wire up the dashboard UI
5. **Integration test** — manual test against a real (or test) OpenRent listing
6. **Zoopla submitter** — second portal
7. **Rightmove submitter** — third portal

---

## 9. Acceptance Criteria

- [ ] "Send Enquiry" button appears on detail page (when contact info configured)
- [ ] Preview shows message, contact details, and available portals
- [ ] Each portal has an independent "Send" button with browser confirmation
- [ ] OpenRent form filled and submitted via Playwright
- [ ] Submission logged in `enquiry_log` table
- [ ] Property `user_status` auto-transitions to `enquired`
- [ ] Already-submitted portals show "Sent ✓" badge
- [ ] Duplicate submissions prevented
- [ ] Failed submissions show error + retry button
- [ ] Screenshots captured for audit trail
- [ ] Contact info configurable via `HOME_FINDER_ENQUIRY_*` env vars
- [ ] All tests pass, types check, linting clean

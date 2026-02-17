# B2: Security & Data Safety -- Cross-Cutting Review

**Scope:** All source files + templates (~14,400 LOC source + templates)
**Date:** 2026-02-16

## Executive Summary

The codebase has a sound security baseline for a personal-use application. SQL injection is prevented throughout via parameterized queries, XSS is handled with disciplined `| e` escaping before `| safe`, secrets use `pydantic.SecretStr`, and path traversal has explicit guards. The most significant gaps are: (1) the image serving endpoint validates `filename` but not `unique_id`, leaving a directory traversal vector through the `unique_id` path component; (2) no PIL decompression bomb limit is set, which could cause denial-of-service via a malicious image; (3) several Telegram message fields output data without HTML-escaping (condition, kitchen info, bathroom info); and (4) the web dashboard has no authentication, rate limiting, or CSRF protection on its state-mutating endpoint. Given the threat model (personal project, local/Fly.io deployment, single user), these are proportionate risks, but items 1 and 2 should be fixed regardless.

## Threat Model

**Assets:** API keys (Anthropic, TravelTime, Telegram bot token), scraped property data (low sensitivity), local SQLite database.

**Adversaries:** (1) Opportunistic scanners finding the web dashboard if exposed to the internet; (2) Malicious content injected by compromised listing platforms (XSS via scraped HTML/JSON); (3) Crafted image payloads from untrusted CDNs.

**Attack surface:** FastAPI web dashboard (HTTP endpoints), Telegram bot message formatting (HTML injection), image cache file serving, SQLite database, external API calls (Anthropic, TravelTime, postcodes.io), scraper HTTP clients processing untrusted HTML/JSON.

## Findings

### [Major] routes.py:632-655 -- Path traversal via `unique_id` in image serving endpoint
**Theme:** Path Traversal | **Effort:** S

The `serve_cached_image` endpoint validates `filename` for directory traversal (`..`, `/`, `\`) but does **not** validate `unique_id`:

```python
@router.get("/images/{unique_id}/{filename}")
async def serve_cached_image(unique_id: str, filename: str, data_dir: DataDirDep) -> Response:
    # Validate filename -- no directory traversal
    if ".." in filename or "/" in filename or "\\" in filename:
        return JSONResponse({"error": "invalid filename"}, status_code=400)

    image_path = get_cache_dir(data_dir, unique_id) / filename
```

The `get_cache_dir` function calls `safe_dir_name(unique_id)` which sanitizes the ID via `re.sub(r"[^a-zA-Z0-9_-]", "_", unique_id)`. This means `..` becomes `__`, `.` becomes `_`, and `/` becomes `_`, so `safe_dir_name` actually neutralizes traversal characters. However, this protection is implicit and accidental -- the sanitization exists for filesystem compatibility, not security.

**Recommendation:** Add an explicit traversal check on `unique_id` matching the `filename` check for defense-in-depth, or add a comment documenting that `safe_dir_name()` provides the security guarantee:

```python
if ".." in unique_id or "/" in unique_id or "\\" in unique_id:
    return JSONResponse({"error": "invalid id"}, status_code=400)
```

**Revised severity after analysis:** This is actually **Minor** (defense-in-depth) because `safe_dir_name()` does strip traversal characters. The risk is that someone modifies `safe_dir_name()` without realizing it has a security role.

---

### [Major] image_processing.py:22-39 -- No PIL decompression bomb limit
**Theme:** Data Safety | **Effort:** S

`resize_image_bytes()` calls `Image.open()` on untrusted image data from external CDNs without setting `PIL.Image.MAX_IMAGE_PIXELS`:

```python
def resize_image_bytes(data: bytes, max_dim: int = MAX_IMAGE_DIMENSION) -> bytes:
    try:
        img = Image.open(BytesIO(data))
        w, h = img.size
```

Pillow's default `MAX_IMAGE_PIXELS` is 178 million pixels (since Pillow 9.x), which is a reasonable guard. However, for a scraper that downloads images from untrusted sources, explicitly setting a conservative limit is best practice. A 100 megapixel image decompressed to RGBA can consume ~400 MB of RAM.

The same concern applies to `floorplan_detector.py:39` and `image_hash.py:45`, both of which call `Image.open()` on untrusted bytes.

**Recommendation:** Set an explicit limit at module level:

```python
Image.MAX_IMAGE_PIXELS = 50_000_000  # 50 megapixels max
```

---

### [Minor] telegram.py:305-313 -- Unescaped HTML in Telegram message fields
**Theme:** Injection (HTML) | **Effort:** S

Several `_format_quality_block` lines output data from the AI quality analysis without HTML-escaping:

```python
lines.append(f"Kitchen: {_format_kitchen_info(analysis)}")  # line 305
lines.append(f"Bathroom: {bathroom_info}")                   # line 309
lines.append(f"Light: {_format_light_space_info(analysis)}") # line 311
lines.append(f"Space: {_format_space_info(analysis)}")       # line 312
lines.append(f"Condition: {analysis.condition.overall_condition}")  # line 313
lines.append(f"Outdoor: {outdoor_info}")                     # line 317
lines.append(f"Listing: {listing_info}")                     # line 320
```

The AI analysis comes from Claude's structured tool response (constrained by enum values like `"modern"`, `"good"`, etc.), so the risk is low. However, free-text fields like `kitchen.notes`, `bathroom.notes`, and `condition.maintenance_concerns` are passed through `_format_kitchen_info()`, `_format_bathroom_info()`, etc. **Some** of these helpers do call `html.escape()` (e.g., `_format_viewing_notes` escapes `check_items`), but the kitchen and bathroom formatters do not escape `notes` fields, and `_format_space_info` does not escape any output.

In `_format_quality_block`, free-text maintenance concerns at line 292 **are** escaped:
```python
concerns_text = ", ".join(html.escape(c) for c in analysis.condition.maintenance_concerns)
```

But `_format_kitchen_info` does not escape `quality_str` or the combined text output. The `overall_condition` at line 313 is a Literal enum value, so it is safe.

**Recommendation:** Apply `html.escape()` consistently to all free-text fields in Telegram formatters (`notes` fields, quality strings). The risk is theoretical since these values come from Claude's structured output, but data flows could change.

---

### [Minor] telegram.py:437 -- Source URLs inserted into Telegram HTML without escaping
**Theme:** Injection (HTML) | **Effort:** S

In `format_merged_property_message`:

```python
source_links.append(f'<a href="{url}">{name}</a>')
```

The `url` is a `pydantic.HttpUrl` from the Property model, and `name` comes from the static `SOURCE_NAMES` dict, so injection risk is extremely low. However, the URL is not HTML-attribute-escaped. If a listing platform URL were to contain `"` characters, it could break out of the attribute.

**Recommendation:** Use `html.escape(str(url), quote=True)` for the href attribute:

```python
source_links.append(f'<a href="{html.escape(str(url), quote=True)}">{name}</a>')
```

---

### [Minor] app.py -- No Content-Security-Policy header
**Theme:** Web Security | **Effort:** M

The `SecurityHeadersMiddleware` sets `X-Content-Type-Options`, `X-Frame-Options`, and `Referrer-Policy`, but does not set a `Content-Security-Policy` header. The dashboard loads scripts from CDNs (htmx, leaflet, leaflet.markercluster) and uses inline `<script>` tags (e.g., `properties_json | safe` in `dashboard.html:227` and `_results.html:60`).

The inline scripts inject `properties_json` with `| safe`, which is server-controlled JSON from the database. This is safe as long as property data does not contain `</script>` sequences. The JSON is produced by `json.dumps()` which escapes such sequences to `<\/script>`, so this is handled correctly.

**Recommendation:** Add a CSP header. Given the CDN dependencies and inline scripts, a pragmatic starting point:

```
Content-Security-Policy: default-src 'self'; script-src 'self' https://unpkg.com 'unsafe-inline'; style-src 'self' https://cdn.jsdelivr.net https://unpkg.com https://fonts.googleapis.com 'unsafe-inline'; font-src https://fonts.gstatic.com; img-src 'self' data: https://*.zoocdn.com https://*.rightmovecdn.com https://*.openrent.co.uk https://*.onthemarket.com; connect-src 'self'
```

---

### [Minor] routes.py:865-877 -- POST endpoint without CSRF protection
**Theme:** Web Security | **Effort:** S

The `/property/{unique_id}/reanalyze` POST endpoint modifies database state (flags a property for re-analysis) without CSRF protection:

```python
@router.post("/property/{unique_id}/reanalyze")
async def request_reanalysis(unique_id: str, storage: StorageDep) -> JSONResponse:
```

Since the dashboard has no authentication and is single-user, CSRF is a theoretical concern. A malicious page could trigger re-analysis requests if the dashboard is exposed to the internet.

**Recommendation:** For a personal project, this is acceptable. If the dashboard ever gets authentication, add CSRF tokens to state-mutating endpoints.

---

### [Minor] storage.py:1216-1222 -- Dynamic column names in SQL UPDATE
**Theme:** Injection (SQL) | **Effort:** S

`update_pipeline_run` constructs column names from `**kwargs` keys using f-strings:

```python
set_clauses = ", ".join(f"{k} = ?" for k in counts)
```

The column names are not parameterized (they cannot be in SQL), but the values use `?` placeholders. This is safe because `update_pipeline_run` is only called internally with hardcoded keyword arguments (e.g., `scraped_count=42`, `notified_count=3`). There is no path from user input to these kwargs.

**Recommendation:** No action needed -- this is a standard pattern for internal-only methods. For added safety, a whitelist check could be added:

```python
VALID_COUNTS = {"scraped_count", "new_count", "enriched_count", "analyzed_count", "notified_count", "anchors_updated"}
assert set(counts.keys()) <= VALID_COUNTS
```

---

### [Minor] storage.py:209-222,260-267 -- Dynamic column names in ALTER TABLE
**Theme:** Injection (SQL) | **Effort:** S

Database migration code uses f-strings for column names and types:

```python
await conn.execute(
    f"ALTER TABLE properties ADD COLUMN {column} {col_type}{default_clause}"
)
```

These values come from hardcoded tuples defined in the source code, not from user input. This is safe.

**Recommendation:** No action needed. The pattern is standard for migration code.

---

### [Minor] web dashboard -- No authentication
**Theme:** Web Security | **Effort:** M

The web dashboard has no authentication. Anyone who can reach the server can view all property data, trigger re-analysis, and browse cached images. The default bind address is `0.0.0.0:8000`.

For local use this is fine. On Fly.io, the app is accessible to the public internet.

**Recommendation:** If deploying to a public URL, consider adding basic authentication (e.g., via a middleware checking a bearer token from an environment variable) or restricting access via Fly.io's private networking. The `web_base_url` config already implies public deployment is an intended use case.

---

### [Minor] web dashboard -- No rate limiting
**Theme:** Web Security | **Effort:** M

No rate limiting exists on any endpoint. The `/count` endpoint performs a database query per request, and repeated requests to `/property/{unique_id}/reanalyze` could queue expensive re-analysis operations.

**Recommendation:** For personal use, acceptable. If the dashboard is publicly exposed, consider adding rate limiting via a middleware (e.g., `slowapi`).

---

### [Suggestion] image_cache.py:49 -- MD5 used for filename generation
**Theme:** Data Safety | **Effort:** S

```python
url_hash = hashlib.md5(url.encode()).hexdigest()[:8]
```

MD5 is used for generating deterministic filenames from URLs, not for security purposes. The 8-character prefix means there is a ~1-in-4-billion chance of collision per filename slot, which is fine for a cache.

**Recommendation:** No action needed. MD5 is appropriate here since it is not used for security.

---

### [Suggestion] config.py:125-128 -- Proxy credentials in config
**Theme:** Secrets | **Effort:** S

The `proxy_url` field is a plain `str`, not `SecretStr`:

```python
proxy_url: str = Field(
    default="",
    description="HTTP/SOCKS5 proxy URL (e.g. socks5://user:pass@host:port)",
)
```

If the proxy URL contains credentials (as shown in the description example), it could be logged or exposed in error messages.

**Recommendation:** Change to `SecretStr` and use `.get_secret_value()` when passing to HTTP clients, matching the pattern used for other credentials in this file.

---

### [Suggestion] detail.html:371 -- XSS prevention on descriptions is correct but fragile
**Theme:** Injection (XSS) | **Effort:** S

```jinja2
{{ best_description | e | replace("\n", "<br>") | safe }}
```

The pattern is correct: escape first, then replace newlines with `<br>`, then mark as safe. However, the `| safe` at the end means any future edit that removes or reorders the `| e` filter would introduce XSS. This pattern appears only once and is well-documented in CLAUDE.md.

**Recommendation:** Consider using a custom Jinja2 filter that combines the escape-and-linebreak operation into a single step to prevent accidental misuse:

```python
def nl2br(value):
    return Markup(escape(value).replace('\n', '<br>'))
templates.env.filters["nl2br"] = nl2br
```

Then in the template: `{{ best_description | nl2br }}`

---

### [Suggestion] area.html:120-124 -- Minor DOM-based concern with `highlight` parameter
**Theme:** Injection (XSS) | **Effort:** S

```html
{% if highlight %}
<script>
document.addEventListener('DOMContentLoaded', function() {
    var el = document.getElementById('highlighted');
    if (el) el.scrollIntoView({behavior: 'smooth', block: 'center'});
});
</script>
{% endif %}
```

The `highlight` query parameter only controls whether this script block is emitted -- the parameter value itself is not injected into JavaScript. It is used in the template to set an `id="highlighted"` attribute on a matching micro-area card (line 21), but the card is rendered from server-side data (area context dict), not from the query parameter value directly. The `highlight` parameter is also used in URL construction (`?highlight={{ area_context.matched_micro_area.name | urlencode }}`), but always from server-controlled data. This is safe.

**Recommendation:** No action needed.

---

### [Suggestion] error.html:10 -- Error message rendered without explicit escaping
**Theme:** Injection (XSS) | **Effort:** S

```jinja2
<p>{{ message }}</p>
```

Jinja2 auto-escapes by default in FastAPI's `Jinja2Templates`, so `{{ message }}` is auto-escaped. The messages passed to this template are all hardcoded strings from the routes (e.g., `"Property not found."`, `"Failed to load properties. Please try again."`). One exception is `area_detail` which includes user-controlled `outcode` in the message:

```python
return templates.TemplateResponse(
    "error.html",
    {"request": request, "message": f"No area data for {outcode}."},
    status_code=404,
)
```

The `outcode` comes from the URL path (`/area/{outcode}`), but since Jinja2 auto-escapes, this is safe.

**Recommendation:** No action needed. The auto-escaping is working correctly.

---

### [Suggestion] _results.html / dashboard.html -- `properties_json | safe` injection surface
**Theme:** Injection (XSS) | **Effort:** S

```jinja2
<script>var propertiesMapData = {{ properties_json | safe }};</script>
```

`properties_json` is produced by `json.dumps(map_markers)` where `map_markers` comes from the database. `json.dumps()` escapes special characters (including `<`, `>`, `&` in recent Python versions with default settings). However, if property titles contained `</script>`, this would need to be escaped. Python's `json.dumps()` does NOT escape `</script>` by default.

**Recommendation:** Use `json.dumps(map_markers, ensure_ascii=False)` or better, `Markup(json.dumps(map_markers).replace('</script>', '<\\/script>'))` to prevent script tag breakout. Alternatively, use a `<script type="application/json">` tag and parse via `JSON.parse()`.

---

### [Suggestion] Regex patterns on untrusted input (ReDoS assessment)
**Theme:** Input Validation | **Effort:** S

The scrapers use many regex patterns on untrusted HTML. Key patterns reviewed:

- `zoopla.py:227`: `_SHARED_ACCOMMODATION_PATTERNS` -- alternation pattern, no nested quantifiers, safe.
- `rightmove.py:342`: `re.compile(r"^propertyCard-\d+$")` -- anchored, safe.
- `detail_fetcher.py:218`: `r"window\.PAGE_MODEL\s*=\s*"` -- no quantifier nesting, safe.
- `detail_fetcher.py:401-403`: `r'\\"caption\\":\\"([^\\]*)\\",\\"filename\\":\\"([a-f0-9]+\.(?:jpg|jpeg|png|webp))\\"'` -- character classes are bounded, safe.
- `area_context.py:236-239`: `_STREET_NAME_RE` -- alternation of fixed strings, safe.

No ReDoS vulnerabilities found. The regex patterns are well-constructed with bounded quantifiers and character classes.

**Recommendation:** No action needed.

## Security Checklist

| Control | Status | Notes |
|---------|--------|-------|
| SQL injection prevention | :white_check_mark: | All queries use parameterized `?` placeholders. f-string SQL limited to column names from hardcoded sources. |
| XSS prevention | :white_check_mark: | Jinja2 auto-escaping enabled. `\| safe` usage is preceded by `\| e` escape filter. Leaflet popups use `textContent`. |
| CSRF protection | :warning: | Single POST endpoint (`/reanalyze`) lacks CSRF. Acceptable for personal use with no auth. |
| Secret management | :white_check_mark: | API keys use `pydantic.SecretStr`. `.env` is gitignored. Secrets not logged. `proxy_url` is plain `str` (minor). |
| Input validation | :white_check_mark: | Query params validated against whitelists (`VALID_SORT_OPTIONS`, `VALID_PROPERTY_TYPES`, etc.). Integers clamped with `max()`/`min()`. |
| Path traversal prevention | :white_check_mark: | `filename` validated explicitly. `unique_id` sanitized via `safe_dir_name()`. Could benefit from explicit check. |
| TLS verification | :white_check_mark: | No `verify=False` or `ssl=False` anywhere in the codebase. All HTTP clients use default TLS verification. |
| Rate limiting | :x: | No rate limiting on any endpoint. Acceptable for personal use. |
| Security headers | :warning: | `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy` present. No CSP header. |
| Error information disclosure | :white_check_mark: | Error pages show generic messages. Stack traces logged but not sent to client. |
| Authentication | :x: | No authentication on web dashboard. Acceptable for personal use, risky if publicly exposed. |
| Decompression bomb protection | :warning: | PIL default limit (178M pixels) applies. No explicit limit set. |
| Request timeouts | :white_check_mark: | All HTTP clients have explicit timeouts (10-60s). Anthropic client has 180s timeout. |
| SSRF | :white_check_mark: | No user-controlled URLs are fetched. All URLs come from scraper-discovered listings or static configuration. |
| Command injection | :white_check_mark: | No `subprocess`, `os.system`, or `os.popen` calls anywhere in the codebase. |

## Summary by Severity

| Severity | Count | Description |
|----------|-------|-------------|
| Critical | 0 | -- |
| Major | 1 | PIL decompression bomb (no explicit limit) |
| Minor | 7 | Path traversal defense-in-depth, Telegram HTML escaping gaps, no CSP, no CSRF on POST, no auth, no rate limiting, dynamic SQL column names (safe but unguarded) |
| Suggestion | 6 | MD5 for filenames (fine), proxy credentials as plain str, fragile XSS pattern, `properties_json` script injection surface, error template (auto-escaped), regex review (clean) |

## Top 3 Takeaways

1. **Set an explicit PIL pixel limit** (`Image.MAX_IMAGE_PIXELS = 50_000_000`) in `image_processing.py`, `floorplan_detector.py`, and `image_hash.py`. This is the most actionable fix -- a single line in each file that protects against malicious images from untrusted CDNs causing memory exhaustion.

2. **Add defense-in-depth to the image serving endpoint.** Either add an explicit traversal check on `unique_id` (matching the `filename` check) or add a code comment documenting that `safe_dir_name()` provides the security sanitization. Also consider adding a CSP header to the security middleware.

3. **Standardize HTML escaping in Telegram formatters.** The codebase is inconsistent -- some helpers like `_format_viewing_notes` carefully escape free-text fields, while others like `_format_kitchen_info` and `_format_bathroom_info` do not. Since these fields come from Claude's structured output (constrained enum values + free-text `notes`), the current risk is low, but consistent escaping is cheap and eliminates the class of bugs entirely.

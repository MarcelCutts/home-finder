# Zoopla 403 Forbidden Investigation

> **For Claude:** This is an investigation/fix task for the Zoopla scraper returning 403 errors.

**Problem:** Zoopla intermittently returns 403 Forbidden on both listing pages and detail pages. Some requests succeed (e.g., e3, e9, e10, n15 work) while others fail (e.g., haringey, e5 blocked).

**Impact:**
- Listing scraper: Partial data - some areas return 0 properties
- Floorplan fetcher: Cannot fetch detail pages for Zoopla properties (always 403)

---

## Investigation Steps

### 1. Understand Current Implementation

**Files to read:**
- `src/home_finder/scrapers/zoopla.py` - main scraper
- `src/home_finder/filters/floorplan.py` - `_fetch_zoopla()` method

**Questions to answer:**
- What headers are being sent?
- Is there rate limiting implemented?
- Are cookies being handled?

### 2. Test Request Patterns

```bash
# Test with curl to see what works
curl -v "https://www.zoopla.co.uk/to-rent/property/e3/" -H "User-Agent: Mozilla/5.0..."

# Compare headers between successful and failed requests
```

### 3. Potential Fixes to Explore

**Option A: Better Headers**
- Add more browser-like headers (Accept, Accept-Language, Accept-Encoding)
- Add Referer header
- Randomize User-Agent

**Option B: Request Delays**
- Add delays between requests to same domain
- Implement exponential backoff on 403

**Option C: Session/Cookies**
- Use a session that persists cookies
- Visit homepage first to get initial cookies

**Option D: Headless Browser**
- Use Playwright for Zoopla (already in deps for some scrapers)
- More resistant to bot detection but slower

### 4. Implementation Priority

1. Try Option A first (simplest)
2. If still failing, try Option B + C together
3. Option D as last resort (significant refactor)

---

## Success Criteria

- [x] Zoopla listing scraper returns properties for all search areas (already using curl_cffi)
- [x] Zoopla detail pages can be fetched for floorplan extraction (fixed: now uses curl_cffi)
- [ ] No increase in 403 errors in logs during dry-run

---

## Resolution (2026-02-02)

**Root cause:** The listing scraper (`zoopla.py`) was already using `curl_cffi` with Chrome TLS impersonation, but the `DetailFetcher` in `floorplan.py` was using plain `httpx`. This exposed Python's native TLS fingerprint to Zoopla's bot detection on detail page requests.

**Fix:** Updated `_fetch_zoopla()` in `floorplan.py` to use `curl_cffi.requests.AsyncSession` with `impersonate="chrome"`, matching the approach used by the listing scraper.

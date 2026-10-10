# Rightmove

> UK's largest property portal. Minimal anti-bot measures; data available in `PAGE_MODEL` JSON on detail pages. Search results parsed from HTML cards.

## Key Decisions

- **HTTP client**: `httpx.AsyncClient` for search and detail pages. Search reuses one client across an area's pages, closes it on completion or cancellation, and leaves signal handling to the application.
- **Search bounds**: Up to 20 pages, 24 results per page, with a 2-second delay between pages. Each fetch permits at most 3 attempts for transport errors, timeouts, HTTP 429 and 5xx, with exponential backoff and jitter. A 30-second total timeout per attempt also bounds responses that keep trickling data. A terminal fetch failure stops the area's pagination and retains earlier observations. HTTP 403 or exhausted retries after a 429 also skip the source's remaining areas for this run; a successful retry permits collection to continue. Unrecognized/challenge HTML counts as a parse error.
- **Cancellation**: HTTP requests, retry waits and page delays propagate cancellation through `ScrapeCancelled`, retaining already received observations. Search must not intercept SIGINT or return success after application cancellation. Crawlee 1.7's queue scheduling and cancellation behavior are unsuitable for this one-page-at-a-time collector.
- **Sort**: "Newest Listed" — prioritizes recent adverts; normal collection continues through known pages within its bounds.
- **Full postcode reconstruction**: Search results only provide outcodes (e.g., `E8`). The detail fetcher combines `outcode` + `incode` from `PAGE_MODEL` to reconstruct the full postcode. This is an architectural decision — without it, Rightmove properties can't cross-platform match.

## Filter Quirks

No significant quirks — Rightmove's server-side filters (bedrooms, price, furnished, bathrooms, let-agreed) all work correctly. This is the only portal where all filters are reliable.

## Detail Page Extraction

`PAGE_MODEL` JSON embedded in page HTML contains all structured data: gallery URLs, floorplan URLs, description, key features, coordinates, and the split outcode/incode for full postcode reconstruction. Extracted via brace-counting (not regex) to handle nested objects.

## Known Quirks

- **Availability envelopes**: The availability reader shares the bounded detail decoder for current indexed `window.__PAGE_MODEL` data and retains legacy `PAGE_MODEL` support. Only explicit status words, tags or flags on the matching main record establish lifecycle status. A malformed or mismatched indexed model remains unknown even if another page element supplies a status. Publication metadata (`published`/`archived`) is not mapped to rental availability; neither HTTP 200 nor a move-in date proves that a listing is available.
- **Outcode-only postcodes at scrape time**: Full postcodes only available from detail pages. Rightmove properties can't cross-platform dedup match until after detail enrichment.
- **Dual price display**: Shows both PCM and PW in a single element (e.g., `£2,400 pcm £554 pw`). The shared `extract_price()` prefers the explicit PCM match to avoid double-conversion.
- **Card format changes**: Rightmove periodically updates card HTML structure. The scraper has multiple fallback selectors for each field.
- **Image lazy loading**: Real image URLs in `data-src` or `srcset`, not `src` (which may be a placeholder).

## Pipeline Implications

- **Early-stop**: Supported (`sortType=6` = "Newest Listed").
- **Dedup**: Only outcodes at scrape time → **cannot cross-match until after enrichment** backfills full postcodes and coordinates from `PAGE_MODEL`.
- **Image priority**: Second highest (3).
- **Quality analysis**: Images sent as URL references (Anthropic fetches directly — no anti-bot on Rightmove CDN).
- **Floorplan gate**: Rightmove has dedicated floorplan data — no exemption needed.
- **Coordinate backfill**: Detail enrichment extracts coordinates from `PAGE_MODEL` — critical for commute filtering.

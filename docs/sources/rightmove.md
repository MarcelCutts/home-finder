# Rightmove

> UK's largest property portal. Minimal anti-bot measures; data available in `PAGE_MODEL` JSON on detail pages. Search results parsed from HTML cards.

## Key Decisions

- **HTTP client**: `crawlee.BeautifulSoupCrawler` for search, `httpx.AsyncClient` for detail pages — no TLS fingerprinting needed anywhere.
- **Sort**: "Newest Listed" — enables early-stop pagination.
- **Full postcode reconstruction**: Search results only provide outcodes (e.g., `E8`). The detail fetcher combines `outcode` + `incode` from `PAGE_MODEL` to reconstruct the full postcode. This is an architectural decision — without it, Rightmove properties can't cross-platform match.

## Filter Quirks

No significant quirks — Rightmove's server-side filters (bedrooms, price, furnished, bathrooms, let-agreed) all work correctly. This is the only portal where all filters are reliable.

## Detail Page Extraction

`PAGE_MODEL` JSON embedded in page HTML contains all structured data: gallery URLs, floorplan URLs, description, key features, coordinates, and the split outcode/incode for full postcode reconstruction. Extracted via brace-counting (not regex) to handle nested objects.

## Known Quirks

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

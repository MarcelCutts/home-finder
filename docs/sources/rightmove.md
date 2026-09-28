# Rightmove

> Current adapter: Crawlee search-card parsing and `PAGE_MODEL` detail extraction. These are historical contracts, awaiting live qualification and parser repairs in PR10.

## Key Decisions

- **HTTP client**: `crawlee.BeautifulSoupCrawler` for search, `httpx.AsyncClient` for detail pages. This describes the implementation, not a guarantee of present portal behaviour.
- **Sort**: "Newest Listed" — enables early-stop pagination.
- **Full postcode reconstruction**: Search results only provide outcodes (e.g., `E8`). The detail fetcher combines `outcode` + `incode` from `PAGE_MODEL` to reconstruct the full postcode. This is an architectural decision — without it, Rightmove properties can't cross-platform match.

## Filter Quirks

Earlier fixtures used server-side bedroom, price, furnishing, bathroom and let-agreed filters. Do not treat those fixtures as proof that today's filters, result ordering or page structures still satisfy the same contract. Requalify them in PR10 using dated source evidence; see the [delivery plan](../plans/2026-09-28-research-synthesis-and-delivery-plan.md).

## Detail Page Extraction

The current detail parser expects `PAGE_MODEL` JSON with gallery URLs, floorplans, description, features, coordinates and split outcode/incode. It extracts the object by brace counting. September research also found newer page contracts; PR10 must support retained examples of the observed variants instead of assuming every page still exposes `PAGE_MODEL`.

## Known Quirks

- **Outcode-only postcodes at scrape time**: Full postcodes only available from detail pages. Rightmove properties can't cross-platform dedup match until after detail enrichment.
- **Dual price display**: Shows both PCM and PW in a single element (e.g., `£2,400 pcm £554 pw`). The shared `extract_price()` prefers the explicit PCM match to avoid double-conversion.
- **Card format changes**: Rightmove periodically updates card HTML structure. The scraper has multiple fallback selectors for each field.
- **Image lazy loading**: Real image URLs in `data-src` or `srcset`, not `src` (which may be a placeholder).

## Pipeline Implications

- **Early-stop**: Supported (`sortType=6` = "Newest Listed").
- **Dedup**: Only outcodes at scrape time → **cannot cross-match until after enrichment** backfills full postcodes and coordinates from `PAGE_MODEL`.
- **Image priority**: Second highest (3).
- **Quality analysis**: The normal pipeline reads locally cached images and sends base64 content, as for the other sources.
- **Floorplan gate**: Rightmove has dedicated floorplan data — no exemption needed.
- **Coordinate backfill**: Detail enrichment extracts coordinates from `PAGE_MODEL` — critical for commute filtering.

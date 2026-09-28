# Rightmove

> Current adapter: Crawlee acquisition with retained `__NEXT_DATA__` search parsing and HTML-card fallback; bounded legacy `PAGE_MODEL` detail extraction. Offline repairs are documented in the [PR10 record](../plans/2026-09-28-pr10-rightmove-contracts.md). Encoded `__PAGE_MODEL` detail support and live qualification remain open.

## Key Decisions

- **HTTP client**: `crawlee.BeautifulSoupCrawler` for search, `httpx.AsyncClient` for detail pages. This describes the implementation, not a guarantee of present portal behaviour.
- **Sort**: "Newest Listed" — enables early-stop pagination.
- **Search facts**: The retained structured search path supplies summaries and portal coordinates, with postcode precision limited to the displayed address. Legacy HTML cards lack those coordinates. Neither implies a verified unit-level position.
- **Full postcode reconstruction**: The legacy detail fetcher combines `outcode` + `incode` from `PAGE_MODEL`. Search addresses often contain only an outcode, so successful current detail qualification still matters for precise cross-platform matching.

## Filter Quirks

Earlier fixtures used server-side bedroom, price, furnishing, bathroom and let-agreed filters. Do not treat those fixtures as proof that today's filters, result ordering or page structures still satisfy the same contract. Requalify them in PR10 using dated source evidence; see the [delivery plan](../plans/2026-09-28-research-synthesis-and-delivery-plan.md).

## Detail Page Extraction

The legacy detail parser expects `PAGE_MODEL` JSON with gallery URLs, floorplans, description, features, coordinates and split outcode/incode. It uses a bounded JSON decoder and checks a supplied target ID. Non-200/challenge responses are rejected before extraction; unsupported/missing model variants produce warning diagnostics. September research reports an encoded `__PAGE_MODEL` contract, but the retained detail bodies available to this implementation are 429s. That new format still needs a successful retained example before a decoder can be qualified.

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
- **Coordinate backfill**: Structured search now retains portal coordinates; legacy detail enrichment can fill missing values. Commute outcomes remain advisory and preserve uncertainty.

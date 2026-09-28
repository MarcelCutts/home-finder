# Rightmove

> Current adapter: Crawlee acquisition with retained `__NEXT_DATA__` search parsing and HTML-card fallback; bounded plain `PAGE_MODEL` and indexed `__PAGE_MODEL` detail extraction. See the [search repair](../plans/2026-09-28-pr10-rightmove-contracts.md) and [captured detail contract](../plans/2026-09-28-pr10-indexed-detail.md). Full production-adapter qualification remains open.

## Key Decisions

- **HTTP client**: `crawlee.BeautifulSoupCrawler` for search, `httpx.AsyncClient` for detail pages. This describes the implementation, not a guarantee of present portal behaviour.
- **Sort**: "Newest Listed" — enables early-stop pagination.
- **Search facts**: The retained structured search path supplies summaries and portal coordinates, with postcode precision limited to the displayed address. Legacy HTML cards lack those coordinates. Neither implies a verified unit-level position.
- **Full postcode reconstruction**: Detail extraction combines `outcode` + `incode` from either supported page model. Search addresses often contain only an outcode. The full postcode improves matching evidence but does not establish unit identity by itself.

## Filter Quirks

Earlier fixtures used server-side bedroom, price, furnishing, bathroom and let-agreed filters. Do not treat those fixtures as proof that today's filters, result ordering or page structures still satisfy the same contract. Requalify them in PR10 using dated source evidence; see the [delivery plan](../plans/2026-09-28-research-synthesis-and-delivery-plan.md).

## Detail Page Extraction

The parser accepts plain `PAGE_MODEL` JSON and `__PAGE_MODEL` with `encoding: "on"` and a JSON-string index table. The latter is evidenced by one owner-requested terminal curl response on 28 September 2026. Container values reference table entries; scalar entries remain literal values. Shared identity and null are preserved. Size/reference/depth limits, cycle checks and required matching target IDs bound the new decoder. Negative references and unknown encodings are rejected. Non-200/challenge responses are rejected before extraction; unsupported/missing variants produce warning diagnostics.

Both forms supply the existing gallery, floorplan, description, feature, coordinate, postcode and sizing extraction path. The successful encoded fixture has nine images and empty floorplan/sizing arrays, so it does not establish encoded floorplan extraction. Terminal curl success does not qualify the production HTTPX client, sessions or retry behaviour.

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
- **Coordinate backfill**: Structured search retains portal coordinates; either supported detail form can fill missing values. Commute outcomes remain advisory and preserve uncertainty.

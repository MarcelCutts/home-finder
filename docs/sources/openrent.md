# OpenRent

> Landlord-direct platform (no letting agents). Property data embedded in JavaScript arrays on search pages. No "newest" sort option — cannot use early-stop pagination.

## Key Decisions

- **HTTP client**: `crawlee.BeautifulSoupCrawler` for search, `httpx.AsyncClient` for detail pages, `curl_cffi` for image downloads — `imagescdn.openrent.co.uk` CDN blocks non-browser requests.
- **No newest sort**: Only distance/price sort available. Early-stop pagination is disabled; must paginate all pages every run.
- **Geocoding override**: OpenRent mis-geocodes certain outcodes (e.g., E10 resolves to Buckinghamshire). `OUTCODE_SLUG_OVERRIDES` maps problem outcodes to correct neighbourhood slugs.

## Filter Quirks

**Client-side filtering only.** OpenRent's server returns all properties within the search radius regardless of URL filter params. The params pre-set client-side filter state in the browser but aren't enforced server-side. The scraper relies on downstream `CriteriaFilter` for accurate filtering.

**Furnished filter**: Only effective for single furnish type selections (not combinations).

## Detail Page Extraction

Gallery extracted via PhotoSwipe lightbox links, with fallbacks for legacy lightbox markup and CDN URL patterns. There is no dedicated floorplan section — landlords upload everything into one gallery, so floorplans are mixed with regular photos. Unavailable properties are detected by redirect to `/properties-to-rent`.

## Known Quirks

- **No dedicated floorplan section**: Floorplans mixed into the general gallery. The PIL heuristic attempts to identify them.
- **Protocol-relative URLs**: All image URLs start with `//` — must prepend `https:`.
- **Property link parsing is fragile**: Multiple text nodes within each property link require careful filtering to extract the title.
- **Geocoding bugs**: Some outcodes resolve to wrong locations. Mitigated by `OUTCODE_SLUG_OVERRIDES`.

## Pipeline Implications

- **Early-stop**: **Disabled** — no newest-first sort available. Passes `known_source_ids=None` to `_paginate()`.
- **Dedup**: Full postcodes and coordinates available at scrape time from JS arrays.
- **Image priority**: Lowest (1) — images typically lower resolution than portal CDNs.
- **Quality analysis**: Images downloaded locally via curl_cffi and sent as base64 (Anthropic can't fetch from `imagescdn.openrent.co.uk`).
- **Floorplan gate**: **Exempt** — OpenRent-only properties pass the floorplan gate even without a detected floorplan (`_FLOORPLAN_EXEMPT_SOURCES`).
- **Search radius**: Fixed at `SEARCH_RADIUS_KM`. May return properties outside the target outcode.

# OnTheMarket

> UK property portal using Next.js with Redux state. Requires `curl_cffi` for TLS fingerprint impersonation on both search and detail pages. Data embedded in `__NEXT_DATA__` JSON.

## Key Decisions

- **HTTP client**: `curl_cffi` with Chrome impersonation — required for both search and detail pages due to TLS fingerprinting detection.
- **Proxy support**: Only scraper that accepts `proxy_url` config for geo-restricted access. Proxy is also passed to the detail fetcher.
- **Sort**: "Recent" by update date — enables early-stop pagination.

## Filter Quirks

- **No bathroom filter**: OnTheMarket's search API has no bathroom count parameter. Must rely on downstream filtering.
- **Furnished filter limitations**: Only works for single furnish type selections (not combinations like "furnished + part-furnished").

## Detail Page Extraction

Same `__NEXT_DATA__` JSON structure as search, but at a different Redux path. Image URLs may have `original`, `largeUrl`, or `url` fields — tried in priority order, with a fallback that constructs URLs from a `prefix` field. Detail page requests are throttled at `_OTM_MIN_INTERVAL`.

## Known Quirks

- **Image URL variations**: Multiple possible URL fields per image with different resolution semantics. The fallback `prefix`-based construction is a safety net for missing fields.
- **Coordinate field naming**: Search results use `location.lon` (not `lng` like other portals).

## Pipeline Implications

- **Early-stop**: Supported (`sort-field=update_date` = "Recent").
- **Dedup**: Full postcodes and coordinates available at scrape time from Redux state.
- **Image priority**: Third (2) — after Zoopla and Rightmove.
- **Quality analysis**: Images sent as URL references (Anthropic fetches directly). Image downloads in the detail fetcher use `curl_cffi` for anti-bot CDN.
- **Floorplan gate**: OnTheMarket has dedicated floorplan data — no exemption needed.
- **Proxy**: Only scraper with proxy support for geo-restricted access.

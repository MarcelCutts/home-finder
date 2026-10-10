# OnTheMarket

> UK property portal using Next.js with Redux state. Requires `curl_cffi` for TLS fingerprint impersonation on both search and detail pages. Data embedded in `__NEXT_DATA__` JSON.

## Key Decisions

- **HTTP client**: `curl_cffi` with Chrome impersonation — required for both search and detail pages due to TLS fingerprinting detection.
- **Proxy support**: Only scraper that accepts `proxy_url` config for geo-restricted access. Proxy is also passed to the detail fetcher.
- **Sort**: "Recent" by update date — prioritizes updated adverts; normal collection continues through known pages within its bounds.

## Filter Quirks

- **No bathroom filter**: OnTheMarket's search API has no bathroom count parameter. Must rely on downstream filtering.
- **Furnished filter limitations**: Only works for single furnish type selections (not combinations like "furnished + part-furnished").

## Detail Page Extraction

Search, detail enrichment, and availability share an explicit selector for the current `props.pageProps.initialReduxState` and legacy `props.initialReduxState` envelopes. Detail data comes from that state's `property` object and still must match the requested advert's identity. Missing or malformed state fails extraction and cannot supply availability status; empty media arrays in a valid, identified property remain valid. Image URLs may have `original`, `largeUrl`, or `url` fields — tried in priority order, with a fallback that constructs URLs from a `prefix` field. Detail page requests are throttled at `_OTM_MIN_INTERVAL`.

## Known Quirks

- **Search envelopes**: Read `props.pageProps.initialReduxState.results.list` on current pages and `props.initialReduxState.results.list` on legacy pages. When `pageProps` is present it takes precedence, including empty or malformed data. Only an explicit list (including `[]`) is valid; missing/malformed data and rejected listings increment parse errors. Valid rows from a partially malformed page remain available, but the scrape is unhealthy. Never substitute `suggestedProperties` for the search results.
- **Request bounds**: Retry 429, 5xx and transport exceptions up to four total attempts per page. A failed page stops that area's pagination. A 403 stops immediately; exhausting attempts after any 429 also stops further areas for that scraper instance, even if later attempts fail with 5xx or transport errors. A recovered request or failures without a source block remain eligible for the next area. Each pipeline run creates a new scraper instance.
- **Image URL variations**: Multiple possible URL fields per image with different resolution semantics. The fallback `prefix`-based construction is a safety net for missing fields.
- **Coordinate field naming**: Search results use `location.lon` (not `lng` like other portals).

## Pipeline Implications

- **Early-stop**: Supported (`sort-field=update_date` = "Recent").
- **Dedup**: Full postcodes and coordinates available at scrape time from Redux state.
- **Image priority**: Third (2) — after Zoopla and Rightmove.
- **Quality analysis**: Images sent as URL references (Anthropic fetches directly). Image downloads in the detail fetcher use `curl_cffi` for anti-bot CDN.
- **Floorplan gate**: OnTheMarket has dedicated floorplan data — no exemption needed.
- **Proxy**: Only scraper with proxy support for geo-restricted access.

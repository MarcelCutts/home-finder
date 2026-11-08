# Zoopla

> UK's second-largest property portal. Heavy Cloudflare protection requiring TLS fingerprint impersonation; data embedded in React Server Components (RSC) payloads.

## Key Decisions

- **HTTP client**: `curl_cffi` with browser impersonation (a Chrome or Safari profile) — required for both search and detail pages due to Cloudflare TLS fingerprinting. Standard HTTP clients get 403s.
- **Browser rotation**: One impersonation profile picked per area (switching mid-session is suspicious). Profile pool defined in scraper.
- **Sort**: `newest_listings` — prioritizes recent adverts; normal collection continues through known pages within its bounds.
- **Shared accommodation**: Listings matching flat share/house share/room patterns are dropped at scrape time (`_SHARED_ACCOMMODATION_PATTERNS`). Each one counts as a parse error, which marks the Zoopla scrape unhealthy and the run partial.

## Filter Quirks

**Furnished filter is broken.** The `furnished_state` URL parameter excludes listings that lack furnishing metadata (the majority), returning "No results" even for areas with hundreds of listings. Intentionally NOT passed. No local furnishing filter replaces it, so Zoopla results include every furnishing type.

**Bathroom filter similarly unreliable** — same metadata gap issue. Not passed as a URL param, and not applied locally either.

## Detail Page Extraction

Three extraction methods tried in order: `__NEXT_DATA__` JSON, RSC taxonomy payload, HTML fallback. Gallery images come from two CDN subdomains — `lid.zoocdn.com` for gallery, `lc.zoocdn.com` for floorplans. Detail page requests are throttled at `_ZOOPLA_MIN_INTERVAL`.

## Known Quirks

- **Availability JSON-LD**: Current detail pages use a `RealEstateListing` with a string `mainEntityOfPage` identifying the advert and an explicit `Offer.availability`. The availability reader accepts that source-bound identity when it matches the requested listing; conflicting main, top-level or offer URLs remain unknown. `listingStatus=to_rent`, move-in dates and HTTP 200 do not establish availability by themselves.
- **RSC format instability**: Zoopla periodically changes RSC payload structure. The scraper uses recursive search rather than fixed paths to handle this.
- **Protocol-relative image URLs**: Image `src` values may lack `https:` prefix — code prepends it.
- **Soft Cloudflare challenges**: 200 status but challenge HTML — treated as a block.

## Pipeline Implications

- **Early-stop**: Not used. The scraper sorts newest first (`results_sort=newest_listings`) and accepts `known_source_ids`, but the pipeline passes none (`pipeline/collection.py`), so collection continues through known pages within its bounds.
- **Dedup**: Full postcodes and coordinates available at scrape time. Matching still runs only after detail enrichment, as it does for every source.
- **Image priority**: Highest (4) — Zoopla CDN images are typically highest resolution.
- **Quality analysis**: Images downloaded locally via curl_cffi and sent as base64 (Anthropic's servers can't fetch from zoocdn.com directly).
- **Floorplan gate**: Zoopla has dedicated floorplan media type — no exemption needed.

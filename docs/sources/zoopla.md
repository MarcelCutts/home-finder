# Zoopla

> UK's second-largest property portal. Heavy Cloudflare protection requiring TLS fingerprint impersonation; data embedded in React Server Components (RSC) payloads.

## Key Decisions

- **HTTP client**: `curl_cffi` with Chrome impersonation — required for both search and detail pages due to Cloudflare TLS fingerprinting. Standard HTTP clients get 403s.
- **Browser rotation**: One impersonation profile picked per area (switching mid-session is suspicious). Profile pool defined in scraper.
- **Sort**: `newest_listings` — enables early-stop pagination.
- **Shared accommodation**: Listings matching flat share/house share/room patterns are silently dropped at scrape time (`_SHARED_ACCOMMODATION_PATTERNS`).

## Filter Quirks

**Furnished filter is broken.** The `furnished_state` URL parameter excludes listings that lack furnishing metadata (the majority), returning "No results" even for areas with hundreds of listings. Intentionally NOT passed — filtered client-side instead.

**Bathroom filter similarly unreliable** — same metadata gap issue. Not passed as a URL param.

## Detail Page Extraction

Three extraction methods tried in order: `__NEXT_DATA__` JSON, RSC taxonomy payload, HTML fallback. Gallery images come from two CDN subdomains — `lid.zoocdn.com` for gallery, `lc.zoocdn.com` for floorplans. Detail page requests are throttled at `_ZOOPLA_MIN_INTERVAL`.

## Known Quirks

- **RSC format instability**: Zoopla periodically changes RSC payload structure. The scraper uses recursive search rather than fixed paths to handle this.
- **Protocol-relative image URLs**: Image `src` values may lack `https:` prefix — code prepends it.
- **Soft Cloudflare challenges**: 200 status but challenge HTML — treated as a block.

## Pipeline Implications

- **Early-stop**: Supported (`results_sort=newest_listings`).
- **Dedup**: Full postcodes and coordinates available at scrape time → can cross-match before enrichment.
- **Image priority**: Highest (4) — Zoopla CDN images are typically highest resolution.
- **Quality analysis**: Images downloaded locally via curl_cffi and sent as base64 (Anthropic's servers can't fetch from zoocdn.com directly).
- **Floorplan gate**: Zoopla has dedicated floorplan media type — no exemption needed.

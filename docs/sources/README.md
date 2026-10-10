# Source Knowledge: Cross-Cutting Concerns

> Reference for behavior shared across all four property portals (Zoopla, Rightmove, OpenRent, OnTheMarket).

See individual source files for platform-specific details:
- [zoopla.md](zoopla.md)
- [rightmove.md](rightmove.md)
- [openrent.md](openrent.md)
- [onthemarket.md](onthemarket.md)

## HTTP Client Routing

See [CLAUDE.md — HTTP Client Selection](../../CLAUDE.md#http-client-selection) for the full routing table. The wrong client gets 403s due to TLS fingerprinting on Zoopla and OnTheMarket.

## CDN Domains and Image Routing

| Source | Gallery CDN | Floorplan CDN | Needs curl_cffi |
|--------|-------------|---------------|-----------------|
| Zoopla | `lid.zoocdn.com` | `lc.zoocdn.com` | Yes |
| Rightmove | `media.rightmove.co.uk` | `media.rightmove.co.uk` | No |
| OpenRent | `imagescdn.openrent.co.uk` | `imagescdn.openrent.co.uk` | Yes |
| OnTheMarket | `media.onthemarket.com` | `media.onthemarket.com` | Yes |

## Image Priority for Deduplication

When cross-platform duplicates are merged, higher-priority source images are kept. Zoopla (4) > Rightmove (3) > OnTheMarket (2) > OpenRent (1). Defined in `SOURCE_IMAGE_PRIORITY`.

## Bounded pagination

Normal collection continues through pages of known listings. A known page does
not prove that an earlier run reached the remaining pages: that run may have
stopped at a processing cap or been interrupted. The pipeline therefore passes
`known_source_ids=None` for every source. Existing page, result and rate limits,
terminal pages and duplicate-page detection still bound each collection.

`BaseScraper._paginate()` retains an optional known-ID stop for direct callers;
the production pipeline does not enable it. The `--full-scrape` flag remains
accepted for command compatibility and does not remove the normal bounds.

## Floorplan Gate

Properties without floorplans are dropped when `require_floorplan=True` (default). Exception: OpenRent-only properties are exempt because OpenRent has no dedicated floorplan section — landlords upload everything into a single gallery. See `_FLOORPLAN_EXEMPT_SOURCES`.

When no structural floorplan is found, a PIL-based heuristic scans cached gallery images to rescue misclassified floorplans.

## Coordinate and Postcode Availability

| Source | Coordinates at Scrape | Full Postcode at Scrape | Backfilled from Detail Page |
|--------|----------------------|------------------------|---------------------------|
| Zoopla | Yes (RSC `pos` field) | Usually full | No (already has data) |
| Rightmove | No | Outcode only (e.g., `E8`) | Yes — full postcode + coords from `PAGE_MODEL` |
| OpenRent | Yes (JS arrays) | Usually full | No (already has data) |
| OnTheMarket | Yes (Redux `location`) | Usually full | No (already has data) |

Rightmove's lack of full postcodes at scrape time means cross-platform dedup won't match Rightmove properties until after detail enrichment backfills the full postcode.

## Rate Limiting Summary

| Source | Aggressiveness | Notes |
|--------|---------------|-------|
| Zoopla | Heavy | Adaptive delays, session reset after consecutive blocks, area skipping |
| Rightmove | Light | Fixed delays, no anti-bot on detail pages |
| OpenRent | Moderate | Adaptive backoff based on response time |
| OnTheMarket | Light | Short fixed delays, uses proxy if configured |

## See Also

- [CLAUDE.md — HTTP Client Selection](../../CLAUDE.md#http-client-selection) — full client routing table with code example
- [CLAUDE.md — Deduplication](../../CLAUDE.md#deduplication) — scoring weights, thresholds, two-phase approach
- [CLAUDE.md — Image Caching](../../CLAUDE.md#image-caching) — disk cache paths, web dashboard serving
- [CLAUDE.md — Quality Analysis](../../CLAUDE.md#quality-analysis) — two-phase Claude vision pipeline

# Source Knowledge: Cross-Cutting Concerns

> Reference for behavior shared across all four property portals (Zoopla, Rightmove, OpenRent, OnTheMarket).

**Status, 2026-09-28:** these notes describe implemented adapters and historical observations, not current live qualification or permission to collect/redisplay content. Follow O2 and PR10/PR11 in the [delivery plan](../plans/2026-09-28-research-synthesis-and-delivery-plan.md). Keep normal collection paused; live tests are now explicit opt-in. Record dated fixtures and source scope when qualifying a source, including separate acquisition, storage, model-processing and hosted-display constraints.

See individual source files for platform-specific details:
- [zoopla.md](zoopla.md)
- [rightmove.md](rightmove.md)
- [openrent.md](openrent.md)
- [onthemarket.md](onthemarket.md)

## HTTP Client Routing

See [CLAUDE.md — HTTP Client Selection](../../CLAUDE.md#http-client-selection) for the implemented routing table. Historical Zoopla/OnTheMarket requests encountered TLS/challenge blocks. A successful response or choice of client is not evidence that access is permitted or that extraction still works.

## CDN Domains and Image Routing

| Source | Gallery CDN | Floorplan CDN | Needs curl_cffi |
|--------|-------------|---------------|-----------------|
| Zoopla | `lid.zoocdn.com` | `lc.zoocdn.com` | Yes |
| Rightmove | `media.rightmove.co.uk` | `media.rightmove.co.uk` | No |
| OpenRent | `imagescdn.openrent.co.uk` | `imagescdn.openrent.co.uk` | Yes |
| OnTheMarket | `media.onthemarket.com` | `media.onthemarket.com` | Yes |

## Image Priority for Deduplication

When cross-platform duplicates are merged, higher-priority source images are kept. Zoopla (4) > Rightmove (3) > OnTheMarket (2) > OpenRent (1). Defined in `SOURCE_IMAGE_PRIORITY`.

## Early-Stop Pagination

The `BaseScraper._paginate()` method supports early-stop: if all results on a page are already in the DB, pagination stops. Only works when results are sorted newest-first.

Current code can return before retaining that fetched known page, losing its price/availability observations (PR09a). The table below describes configured sort assumptions; it is not a guarantee of newest-first ordering on today's sites.

| Source | Supports Early-Stop | Why |
|--------|-------------------|-----|
| Zoopla | Yes | Sorted newest-first |
| Rightmove | Yes | Sorted newest-first |
| OnTheMarket | Yes | Sorted by update date |
| OpenRent | **No** | No newest sort available; `known_source_ids` passed as `None` |

## Floorplan Gate

Properties without floorplans are dropped when `require_floorplan=True` (default). Exception: OpenRent-only properties are exempt because OpenRent has no dedicated floorplan section — landlords upload everything into a single gallery. See `_FLOORPLAN_EXEMPT_SOURCES`.

Enrichment scans cached gallery images with PIL-based heuristics to rescue misclassified floorplans. The gate also allows the configured gallery-photo fallback (`min_gallery_for_photo_inference`, default 5; 0 disables it).

Enrichment runs **before** geocoding/commute calculation and deduplication. Text/features fetched here can still be lost before persistence and analysis; PR06a repairs that handoff. The normal analysis path reads cached images and sends base64 content, including Rightmove images.

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

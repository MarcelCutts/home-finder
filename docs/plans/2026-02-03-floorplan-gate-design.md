# Floorplan Gate: Separate Detail Fetching and Require Floorplans

## Problem

Quality analysis (Claude Vision API) runs on every property with gallery images, even those lacking floorplans. This causes:

1. **Wasted API cost** -- each call sends 5-10 images to Sonnet (~$0.01-0.05/call + 1.5s rate-limit delay)
2. **Useless space analysis** -- `living_room_sqm` returns `null` without a floorplan; `is_spacious_enough` is either `null` or auto-overridden for 2+ beds
3. **Notification noise** -- quality summaries for floorplan-less properties contain "unknown" values
4. **Properties without floorplans aren't worth evaluating** -- if you can't assess space, the listing doesn't provide enough information

## Design

### Pipeline Restructuring

Extract detail fetching from the quality filter into a distinct pipeline step. Add a configurable floorplan gate between enrichment and analysis.

**Before:**

```
Commute filter -> Quality analysis (fetches details + calls Claude) -> Save & Notify
```

**After:**

```
Commute filter
  -> Detail Enrichment (fetch detail pages, populate images/floorplan/descriptions)
  -> Floorplan Gate (drop properties without floorplan, if enabled)
  -> Quality Analysis (Claude Vision only, uses pre-enriched data)
  -> Save & Notify
```

### Config

New setting in `config.py`:

```python
require_floorplan: bool = True
```

When `True`, properties without a valid image-format floorplan are dropped before quality analysis. When `False`, all properties pass through (previous behavior, minus the pipeline separation).

### Multi-Source Floorplan Resolution

A merged property passes the gate if ANY source provides a valid floorplan. The detail enrichment step fetches from all source URLs and keeps the first valid floorplan found (current behavior, extracted).

### Code Changes

#### New: `src/home_finder/filters/detail_enrichment.py`

Extract multi-source detail fetching from `quality.py` lines ~700-780:

```python
async def enrich_merged_properties(
    merged_properties: list[MergedProperty],
    detail_fetcher: DetailFetcher,
) -> list[MergedProperty]:
    """Fetch detail pages and populate images, floorplan, descriptions."""
```

For each `MergedProperty`:
- Iterate source URLs, call `detail_fetcher.fetch_detail_page()` per source
- Collect gallery images as `PropertyImage` objects
- Keep first valid image-format floorplan (skip PDFs)
- Select best description (longest) and best features list (most items)
- Return updated `MergedProperty` with populated fields

#### New: Floorplan gate (inline function or small filter)

```python
def filter_by_floorplan(properties: list[MergedProperty]) -> list[MergedProperty]:
    return [p for p in properties if p.floorplan is not None]
```

#### Modified: `src/home_finder/filters/quality.py`

- Remove detail fetching from `analyze_merged_properties()`
- Remove `_detail_fetcher` field from `PropertyQualityFilter`
- `analyze_merged_properties()` now reads images directly from `MergedProperty.images` and `.floorplan`
- Keep `_create_minimal_analysis()` as fallback for Claude API failures

#### Modified: `src/home_finder/main.py`

Wire the new pipeline steps:

```python
# Detail enrichment (always runs)
enriched = await enrich_merged_properties(merged_to_notify, detail_fetcher)

# Floorplan gate (configurable)
if settings.require_floorplan:
    before = len(enriched)
    enriched = filter_by_floorplan(enriched)
    logger.info("floorplan_filter", before=before, after=len(enriched))

# Quality analysis (if enabled, uses pre-enriched data)
if settings.enable_quality_filter and settings.anthropic_api_key:
    quality_filter = PropertyQualityFilter(api_key=..., max_images=...)
    quality_results = await quality_filter.analyze_merged_properties(enriched)
```

### Tests

| Test | Purpose |
|------|---------|
| `test_enrich_populates_images_and_floorplan` | Mock detail fetcher, verify fields populated |
| `test_enrich_multi_source_collects_all` | Two sources, images from both collected |
| `test_enrich_keeps_first_valid_floorplan` | First source has PDF floorplan, second has JPG -- JPG wins |
| `test_floorplan_gate_drops_without_floorplan` | Property without floorplan excluded |
| `test_floorplan_gate_passes_any_source` | Multi-source, one has floorplan -- passes |
| `test_floorplan_gate_disabled` | `require_floorplan=False` passes all |
| `test_quality_filter_no_detail_fetching` | Verify quality filter uses pre-enriched data only |
| Update existing quality filter tests | Remove detail fetcher mocking, pass enriched properties |

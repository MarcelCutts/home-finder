# Floorplan Analysis Feature Design

## Overview

Add floorplan analysis to filter rental properties based on living room size. The goal is to identify 1-bedroom flats with living rooms spacious enough to accommodate a home office AND host parties (8+ people).

## Problem

- Current pipeline returns ~103 properties - too many to review manually
- User needs: 1-bed if living room is large enough for office + parties, otherwise 2-bed
- No way to filter by room size currently

## Solution

Two-phase pipeline approach:
1. **Phase 1 (existing):** Cheap filters reduce ~hundreds of properties to ~10-30 candidates
2. **Phase 2 (new):** Fetch detail pages, extract floorplan URLs, run Claude LLM analysis

## Architecture

```
Phase 1 (unchanged):
  Scrape → Criteria → Location → Dedupe → New Only → Commute
                                                         ↓
                                              properties_to_notify (~10-30)
                                                         ↓
Phase 2 (new):                                           ↓
  ┌──────────────────────────────────────────────────────┘
  ↓
  DetailFetcher (fetch property detail pages)
  ↓
  FloorplanFilter
    ├── Extract floorplan URL from detail page HTML
    ├── Filter out properties without floorplans
    └── Run LLM analysis on remaining
  ↓
  Save & Notify (with floorplan analysis results)
```

## Components

### 1. DetailFetcher

Fetches property detail pages and extracts floorplan URLs. Per-platform logic required.

| Platform | Floorplan Location | Extraction Method |
|----------|-------------------|-------------------|
| Rightmove | JSON in `<script>` tag | Parse `window.PAGE_MODEL` JSON |
| Zoopla | JSON in `__NEXT_DATA__` | Parse Next.js data |
| OpenRent | Image gallery | Look for `floorplan` class/id |
| OnTheMarket | JSON or image gallery | Similar to Rightmove |

Rate limiting: 1-2 second delay between requests, ~10-30 requests per run.

### 2. FloorplanAnalysis Model

```python
class FloorplanAnalysis(BaseModel):
    living_room_sqm: float | None = None
    can_fit_office: bool | None = None
    can_host_party_8_plus: bool | None = None
    is_spacious_enough: bool  # Key decision
    confidence: str  # "high", "medium", "low"
    reasoning: str  # Brief explanation
```

### 3. FloorplanFilter

Orchestrates detail fetching and LLM analysis.

Key behaviors:
- **2+ bedroom properties:** Auto-pass, skip LLM (saves API costs)
- **1-bedroom properties:** Require LLM approval for spacious living room
- **No floorplan:** Filtered out entirely

### 4. LLM Integration

- Model: `claude-sonnet-4-20250514`
- Input: Floorplan image URL + analysis prompt
- Output: Structured JSON matching FloorplanAnalysis schema
- Cost estimate: ~$0.02 per floorplan, ~$0.60/run for 30 properties

## Configuration

New settings (env vars with `HOME_FINDER_` prefix):

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `anthropic_api_key` | SecretStr | "" | Anthropic API key |
| `enable_floorplan_filter` | bool | True | Toggle floorplan filtering |

## Pipeline Integration

After commute filter, before save & notify:

1. Check if Anthropic API key configured
2. Run FloorplanFilter on `properties_to_notify`
3. Build lookup of analysis results
4. Filter properties list to those with floorplans + passing analysis
5. Include analysis in Telegram notifications

Graceful degradation:
- No API key → skip filter entirely
- Detail fetch fails → property filtered out
- LLM call fails → property filtered out (fail-safe)

## Testing Strategy

### Unit Tests (mocked)
- Properties without floorplans filtered out
- 2+ bed properties skip LLM, auto-pass
- 1-bed spacious passes
- 1-bed small filtered out
- Invalid LLM JSON handled gracefully

### Integration Tests (real HTML fixtures, mocked HTTP)
- Per-platform floorplan URL extraction
- Fixtures for with/without floorplan cases

### E2E Tests (manual, real APIs)
- Real property URLs (update as listings expire)
- Verify no crashes, valid response structure

## File Changes

### New Files
- `src/home_finder/filters/floorplan.py` - DetailFetcher, FloorplanFilter, FloorplanAnalysis
- `tests/test_floorplan_filter.py` - Unit tests
- `tests/test_detail_fetcher.py` - Integration tests
- `tests/fixtures/*_detail_*.html` - HTML fixtures (8 files)

### Modified Files
- `src/home_finder/filters/__init__.py` - Export FloorplanFilter
- `src/home_finder/config.py` - Add anthropic_api_key, enable_floorplan_filter
- `src/home_finder/main.py` - Integrate floorplan filter step
- `src/home_finder/notifiers/telegram.py` - Add floorplan analysis to messages
- `pyproject.toml` - Add anthropic dependency

## Dependencies

```toml
anthropic = ">=0.40.0"
```

## Estimated Costs

| Volume | Claude Sonnet Cost |
|--------|-------------------|
| 30 properties/run | ~$0.60 |
| 3 runs/day | ~$1.80/day |
| Monthly | ~$54/month |

Note: 2+ bed properties skip LLM, actual cost likely lower.

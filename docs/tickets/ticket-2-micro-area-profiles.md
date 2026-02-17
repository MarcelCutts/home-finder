# Ticket 2: Micro-Area Neighbourhood Intelligence

**Depends on:** None (do this first — it restructures `area_context.json`)
**Blocks:** Tickets 1, 4, 5, 6 (they add keys to the same JSON file after this restructure)

## Goal

Replace single-paragraph area descriptions with richer, micro-area-aware context. The current `area_context` data is a flat string per outcode (e.g., `"E5": "Clapton is 15-20% cheaper than..."`). This ticket restructures it to support sub-area profiles while maintaining backward compatibility with all existing code.

---

## 2A. Restructure area context data

### File: `src/home_finder/data/area_context.json`

**Current format** of the `area_context` key:
```json
{
  "area_context": {
    "E5": "Clapton is 15-20% cheaper than neighbouring Hackney Downs...",
    "E8": "Hackney's core rental market...",
    "E9": "Hackney Wick and Homerton...",
    ...
  }
}
```

**New format** — keep the existing text as `overview`, add structured `micro_areas`:
```json
{
  "area_context": {
    "E5": {
      "overview": "Clapton is 15-20% cheaper than neighbouring Hackney Downs...(existing text, possibly updated with research findings)...",
      "micro_areas": {
        "Upper Clapton": {
          "character": "Quieter, more residential. Near Stamford Hill community. Period conversions and ex-council estates. Less nightlife, more families.",
          "transport": "Clapton Overground (8 min walk). Bus routes 253, 254 to Hackney/Finsbury Park.",
          "creative_scene": "Limited — mostly residential. 15 min cycle to Dalston creative hub.",
          "broadband": "Openreach FTTP available on most streets. Community Fibre partial coverage.",
          "hosting_tolerance": "moderate",
          "wfh_suitability": "good",
          "value": "Best value in E5 — lower footfall means less demand pressure."
        },
        "Chatsworth Road": {
          "character": "Trendy high street with independent shops, cafes, Sunday market. Victorian terraces.",
          "transport": "Clapton Overground (5 min walk). Homerton Overground (10 min walk).",
          "creative_scene": "Growing — several artist studios nearby, the Clapton Hart, brewery tap rooms.",
          "broadband": "Openreach FTTP widely available. Hyperoptic in some new builds.",
          "hosting_tolerance": "moderate",
          "wfh_suitability": "good",
          "value": "Premium within E5 — Chatsworth Road proximity adds 5-10% to rents."
        }
      }
    },
    "E8": {
      "overview": "Hackney's core rental market...(existing text)...",
      "micro_areas": {
        "Dalston": { ... },
        "London Fields": { ... },
        "Hackney Downs": { ... },
        "Hackney Central": { ... }
      }
    }
  }
}
```

**Populate micro-areas for all current outcodes:** E3, E5, E8, E9, E10, E15, E17, N15, N16, N17. Use Q2 research output to fill in character, transport, creative_scene, broadband, hosting_tolerance, wfh_suitability, and value for 2-4 micro-areas per outcode.

### File: `src/home_finder/data/area_context.py`

The current type annotation is:
```python
AREA_CONTEXT: Final[dict[str, str]] = _DATA["area_context"]
```

This needs to handle both old (string) and new (dict) formats. Update:

```python
class MicroArea(TypedDict, total=False):
    """Sub-area profile within an outcode."""
    character: str
    transport: str
    creative_scene: str
    broadband: str
    hosting_tolerance: str  # "high" | "moderate" | "low"
    wfh_suitability: str  # "good" | "moderate" | "poor"
    value: str

class AreaContext(TypedDict, total=False):
    """Structured area context for an outcode."""
    overview: str
    micro_areas: dict[str, MicroArea]

# Supports both old format (str) and new format (AreaContext dict)
AREA_CONTEXT: Final[dict[str, str | AreaContext]] = _DATA.get("area_context", {})


def get_area_overview(outcode: str) -> str | None:
    """Get area overview text, handling both old string and new dict formats.

    This is the backward-compatible accessor used by quality prompts and anywhere
    that previously read AREA_CONTEXT[outcode] as a string.
    """
    ctx = AREA_CONTEXT.get(outcode)
    if ctx is None:
        return None
    if isinstance(ctx, str):
        return ctx
    return ctx.get("overview", "")


def get_micro_areas(outcode: str) -> dict[str, MicroArea] | None:
    """Get micro-area profiles for an outcode, or None if not available."""
    ctx = AREA_CONTEXT.get(outcode)
    if isinstance(ctx, dict):
        micro = ctx.get("micro_areas")
        return micro if micro else None
    return None
```

---

## 2B. Update all consumers of `AREA_CONTEXT` to use `get_area_overview()`

There are exactly two consumers of `AREA_CONTEXT` that read area text:

### 1. `src/home_finder/filters/quality.py` (line 616)

**Current:**
```python
area_context = AREA_CONTEXT.get(outcode) if outcode else None
```

**Change to:**
```python
from home_finder.data.area_context import get_area_overview
area_context = get_area_overview(outcode) if outcode else None
```

This is inside `analyze_single_merged()` at line 616. The variable `area_context` is passed as a string to `build_user_prompt()` and `build_evaluation_prompt()`, which both pass it to `_format_property_context()`. All of these expect `str | None` — unchanged.

### 2. `src/home_finder/web/routes.py` (line 688)

**Current:**
```python
area_context["description"] = AREA_CONTEXT.get(outcode)
```

**Change to:**
```python
from home_finder.data.area_context import get_area_overview, get_micro_areas
area_context["description"] = get_area_overview(outcode)
area_context["micro_areas"] = get_micro_areas(outcode)
```

---

## 2C. Update prompt injection (minimal change)

### File: `src/home_finder/filters/quality_prompts.py`

No change needed to `_format_property_context()`, `build_user_prompt()`, or `build_evaluation_prompt()`. They already receive the overview string (via `get_area_overview()`), which is the same content as before — just retrieved differently.

The micro-area data is too verbose for prompt injection (~200 token per-property budget). It's display-only on the web dashboard.

**Optional enhancement:** If the overview text is updated with richer findings from Q2 research, the prompts automatically benefit since they receive `get_area_overview()` output.

---

## 2D. Update detail page template

### File: `src/home_finder/web/templates/detail.html`

The current Area section (lines 247-312) displays `area_context.description` as a paragraph. After this paragraph and before the benchmarks, add micro-area cards.

After the `<p class="area-description">` line (line 250), add:

```html
{% if area_context.micro_areas %}
<h4 class="section-subhead">Neighbourhood Profiles</h4>
<div class="micro-area-grid">
    {% for name, area in area_context.micro_areas.items() %}
    <div class="micro-area-card">
        <h5>{{ name }}
            {% if area.hosting_tolerance %}
            <span class="badge
                {% if area.hosting_tolerance == 'high' %}badge-green
                {% elif area.hosting_tolerance == 'moderate' %}badge-amber
                {% else %}badge-red{% endif %}"
                title="Hosting tolerance">{{ area.hosting_tolerance }}</span>
            {% endif %}
            {% if area.wfh_suitability %}
            <span class="badge
                {% if area.wfh_suitability == 'good' %}badge-green
                {% elif area.wfh_suitability == 'moderate' %}badge-amber
                {% else %}badge-red{% endif %}"
                title="WFH suitability">WFH: {{ area.wfh_suitability }}</span>
            {% endif %}
        </h5>
        <p>{{ area.character }}</p>
        {% if area.transport %}<p class="detail-meta"><strong>Transport:</strong> {{ area.transport }}</p>{% endif %}
        {% if area.broadband %}<p class="detail-meta"><strong>Broadband:</strong> {{ area.broadband }}</p>{% endif %}
        {% if area.creative_scene %}<p class="detail-meta"><strong>Creative:</strong> {{ area.creative_scene }}</p>{% endif %}
        {% if area.value %}<p class="detail-meta"><strong>Value:</strong> {{ area.value }}</p>{% endif %}
    </div>
    {% endfor %}
</div>
{% endif %}
```

Add minimal CSS for the grid layout (either inline in the template's `<style>` block or in the existing stylesheet):

```css
.micro-area-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
    gap: 1rem;
    margin: 1rem 0;
}
.micro-area-card {
    border: 1px solid var(--border, #e2e8f0);
    border-radius: 8px;
    padding: 1rem;
}
.micro-area-card h5 {
    margin: 0 0 0.5rem;
    display: flex;
    align-items: center;
    gap: 0.5rem;
    flex-wrap: wrap;
}
```

---

## 2E. Tests

### Data loading / backward compatibility — `tests/test_data/test_area_context.py` (create if needed)

```python
from home_finder.data.area_context import (
    AREA_CONTEXT,
    get_area_overview,
    get_micro_areas,
)


def test_area_context_loads():
    """Area context loads from JSON."""
    assert isinstance(AREA_CONTEXT, dict)
    assert len(AREA_CONTEXT) > 0


def test_get_area_overview_new_format():
    """get_area_overview returns overview string from new dict format."""
    # E8 should be in new format after this ticket
    overview = get_area_overview("E8")
    assert overview is not None
    assert isinstance(overview, str)
    assert len(overview) > 20


def test_get_area_overview_missing_outcode():
    """get_area_overview returns None for unknown outcode."""
    assert get_area_overview("ZZ99") is None


def test_get_micro_areas_returns_dict():
    """get_micro_areas returns micro-area dict for outcodes with data."""
    micros = get_micro_areas("E8")
    if micros is not None:
        assert isinstance(micros, dict)
        for name, area in micros.items():
            assert isinstance(name, str)
            assert "character" in area


def test_get_micro_areas_missing_outcode():
    """get_micro_areas returns None for unknown outcode."""
    assert get_micro_areas("ZZ99") is None
```

### Prompt tests — `tests/test_filters/test_quality_prompts.py`

The existing `test_prompt_with_all_context` passes `area_context="Trendy East London"` as a string. This still works — `build_user_prompt()` receives a string from `get_area_overview()`. No changes needed to existing prompt tests.

Add a test verifying the function handles the new format:

```python
def test_format_context_with_string_area(self) -> None:
    """_format_property_context works with plain string area context (old format)."""
    from home_finder.filters.quality_prompts import _format_property_context
    result = _format_property_context(
        price_pcm=1800, bedrooms=2, area_average=1900,
        area_context="Trendy East London", outcode="E8",
    )
    assert '<area_context outcode="E8">' in result
    assert "Trendy East London" in result
```

### Web route tests — `tests/test_web/test_routes.py`

```python
@pytest.mark.asyncio
async def test_micro_areas_rendered_when_present(
    self, client: TestClient, storage: PropertyStorage, merged_a: MergedProperty
) -> None:
    """Detail page renders micro-area cards when data is available."""
    await storage.save_merged_property(merged_a)
    resp = client.get(f"/property/{merged_a.unique_id}")
    assert resp.status_code == 200
    # E8 should have micro-areas after this ticket
    # If micro_areas data exists for E8, check for "Neighbourhood Profiles"
    # Otherwise just check no crash

@pytest.mark.asyncio
async def test_area_section_graceful_without_micro_areas(
    self, client: TestClient, storage: PropertyStorage, prop_a: Property
) -> None:
    """Area section renders fine without micro-area data (backward compat)."""
    # Create property with outcode that might not have micro-areas
    merged = MergedProperty(
        canonical=prop_a.model_copy(update={"postcode": "SW1 1AA"}),
        sources=(PropertySource.OPENRENT,),
        source_urls={PropertySource.OPENRENT: prop_a.url},
        min_price=1900,
        max_price=1900,
    )
    await storage.save_merged_property(merged)
    resp = client.get(f"/property/{merged.unique_id}")
    # Should not crash — area section just won't show micro-areas
    assert resp.status_code in (200, 404)  # 404 if no images
```

### Quality filter tests — `tests/test_filters/test_quality.py`

Verify that `analyze_single_merged()` still works after switching to `get_area_overview()`:

```python
async def test_area_context_passed_as_string(self, quality_filter, sample_merged_property):
    """Area context is passed as overview string to prompts, not dict."""
    # ... mock both phases
    # ... verify Phase 1 prompt contains area context text, not a dict
    calls = quality_filter._client.messages.create.call_args_list
    phase1_content = calls[0].kwargs["messages"][0]["content"]
    assert "<area_context" in phase1_content
    # Should be a string, not {"overview": ..., "micro_areas": ...}
    assert "micro_areas" not in phase1_content
```

---

## Verification

1. `uv run pytest` — all existing + new tests pass
2. `uv run ruff check src tests` — no lint issues
3. `uv run mypy src` — type checking passes (new type annotation for `AREA_CONTEXT`, new functions)
4. `uv run home-finder --dry-run --max-per-scraper 2` — pipeline completes (prompts still work with overview strings)
5. `uv run home-finder --serve` — detail page shows micro-area cards for outcodes with data

---

## Files Changed Summary

| File | Change |
|------|--------|
| `src/home_finder/data/area_context.json` | Restructure `area_context` values from string to `{overview, micro_areas}` dict |
| `src/home_finder/data/area_context.py` | Add `MicroArea`, `AreaContext` TypedDicts; add `get_area_overview()`, `get_micro_areas()` functions; update `AREA_CONTEXT` type |
| `src/home_finder/filters/quality.py` | Change `AREA_CONTEXT.get(outcode)` → `get_area_overview(outcode)` (line 616) |
| `src/home_finder/web/routes.py` | Change `AREA_CONTEXT.get(outcode)` → `get_area_overview(outcode)`; add `get_micro_areas(outcode)` to context (line 688) |
| `src/home_finder/web/templates/detail.html` | Add micro-area cards grid in Area section |
| `tests/test_data/test_area_context.py` | New — data loading + backward compat tests |
| `tests/test_filters/test_quality_prompts.py` | Add string area context compatibility test |
| `tests/test_filters/test_quality.py` | Add test verifying overview string (not dict) reaches prompts |
| `tests/test_web/test_routes.py` | Add micro-area render tests + graceful degradation test |

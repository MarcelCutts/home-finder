# Ticket 4: Creative Scene & Hosting Tolerance Data

**Depends on:** Ticket 2 (micro-area profiles restructure `area_context.json` first)
**Blocks:** None

## Goal

Add per-outcode hosting tolerance ratings and creative scene data to area context. Improve the quality analysis's handling of hosting-related signals by giving Phase 2 area-level hosting tolerance context. Display creative scene info on the detail page. Optionally enhance fit score's hosting dimension with area tolerance data.

---

## 4A. Add hosting tolerance and creative scene data

### File: `src/home_finder/data/area_context.json`

Add two new top-level keys alongside existing ones (`rental_benchmarks`, `area_context`, `acoustic_profiles`, etc.):

```json
{
  "hosting_tolerance": {
    "E3": {
      "rating": "high",
      "notes": "Bow and Fish Island — creative/industrial zone with high tolerance for noise. Many live/work units. Canal-side warehouse conversions attract artists and musicians.",
      "known_friendly_areas": ["Fish Island", "Vittoria Wharf"],
      "known_sensitive_areas": ["Tredegar Square residential area"]
    },
    "E5": {
      "rating": "moderate",
      "notes": "Clapton — residential area, moderate tolerance. Chatsworth Road area more relaxed. Upper Clapton near Stamford Hill community is more sensitive.",
      "known_friendly_areas": ["Chatsworth Road area", "Near Lea Bridge Road"],
      "known_sensitive_areas": ["Upper Clapton near Stamford Hill", "Springfield Park residential"]
    },
    "E8": {
      "rating": "high",
      "notes": "Dalston and Hackney Central — established nightlife and creative culture. High tolerance near Kingsland Road/Dalston Lane. London Fields area more mixed.",
      "known_friendly_areas": ["Dalston", "Hackney Central", "Mare Street"],
      "known_sensitive_areas": ["London Fields residential streets", "De Beauvoir edges"]
    },
    "E9": {
      "rating": "high",
      "notes": "Hackney Wick — the most artist-friendly area in the search. Industrial heritage, active creative community. Very high tolerance in the Wick itself. Homerton more residential.",
      "known_friendly_areas": ["Hackney Wick", "Fish Island", "White Post Lane"],
      "known_sensitive_areas": ["Homerton High Street residential", "Victoria Park borders"]
    },
    "E10": {
      "rating": "moderate",
      "notes": "Leyton — quieter suburban feel. Some creative spaces emerging near Leyton Mills. Generally residential with moderate tolerance.",
      "known_friendly_areas": ["Near Leyton Mills retail park area", "Church Road"],
      "known_sensitive_areas": ["Residential streets off High Road Leyton"]
    },
    "E17": {
      "rating": "moderate",
      "notes": "Walthamstow — growing creative scene around the Village and God's Own Junkyard area. William Morris heritage. Generally moderate, higher near the Village.",
      "known_friendly_areas": ["Walthamstow Village", "Hoe Street creative end", "Blackhorse Road area"],
      "known_sensitive_areas": ["Residential areas off Forest Road"]
    },
    "N15": {
      "rating": "moderate",
      "notes": "Seven Sisters — diverse, busy high street. Moderate tolerance. Less creative infrastructure than Hackney areas.",
      "known_friendly_areas": ["Near Seven Sisters station area"],
      "known_sensitive_areas": ["Residential streets towards Stamford Hill"]
    },
    "N16": {
      "rating": "moderate",
      "notes": "Stoke Newington — Marcel's current base. Established but increasingly family-oriented. Church Street more relaxed, residential streets less tolerant.",
      "known_friendly_areas": ["Church Street area", "Near Newington Green"],
      "known_sensitive_areas": ["Quiet residential streets off Stoke Newington High Street"]
    },
    "N17": {
      "rating": "low",
      "notes": "Tottenham — primarily residential with less creative infrastructure. Emerging around Tottenham Hale but still early. Lower tolerance overall.",
      "known_friendly_areas": ["Near Tottenham Hale redevelopment area"],
      "known_sensitive_areas": ["Most residential areas"]
    }
  },
  "creative_scene": {
    "E3": {
      "rehearsal_spaces": ["Bow Arts (various studios)", "Poplar Union (community arts)"],
      "venues": ["Colour Factory (Hackney Wick, 10 min cycle)", "Grow Hackney (5 min walk)"],
      "creative_hubs": ["Stour Space", "Vittoria Wharf", "Here East"],
      "summary": "Bow/Fish Island is one of London's densest creative zones. Warehouses, studios, galleries cluster around the canal. Active music scene in Hackney Wick."
    },
    "E5": {
      "rehearsal_spaces": ["Dalston Rehearsal Studio (15 min cycle)", "Premises Studios (Hackney, 20 min cycle)"],
      "venues": ["EartH Hackney (Dalston, 15 min cycle)", "Hackney Empire (20 min bus)"],
      "creative_hubs": ["Hackney Downs Studios (10 min walk)", "Chatsworth Road independent shops"],
      "summary": "Close to Dalston's creative core but not in it. Clapton has a growing independent scene on Chatsworth Road but limited music infrastructure."
    },
    "E8": {
      "rehearsal_spaces": ["Premises Studios (Hackney Road)", "Dalston Rehearsal Studio", "Strong Rooms Studios"],
      "venues": ["EartH Hackney", "Dalston Superstore", "Cafe OTO", "The Shacklewell Arms", "Paper Dress Vintage"],
      "creative_hubs": ["Bootstrap Charity (Dalston)", "Hackney Downs Studios", "Netil House (London Fields)"],
      "summary": "The heart of East London's creative scene. Dalston has the highest concentration of music venues and creative spaces in the search area. Established infrastructure for music events."
    },
    "E9": {
      "rehearsal_spaces": ["Hackney Wick studios (multiple)", "Manor Garden Centre creative spaces"],
      "venues": ["Colour Factory", "Grow Hackney", "Crate Brewery (events)"],
      "creative_hubs": ["Stour Space", "Hackney Wick Creative Quarter", "Here East"],
      "summary": "Hackney Wick is London's largest creative quarter by studio density. Warehouse culture. Strong DIY/underground music scene. Homerton is more residential with less creative infrastructure."
    },
    "E10": {
      "rehearsal_spaces": ["Limited — nearest are in E8/E9 (20 min cycle)"],
      "venues": ["Leyton Technical (occasional events)", "Olympic Park venues nearby"],
      "creative_hubs": ["Leyton Create (emerging)", "Near ACME Studios"],
      "summary": "Emerging creative scene, much less established than E8/E9. Benefits from proximity to Hackney Wick and Olympic Park. Best for those wanting affordable space near creative areas."
    },
    "E17": {
      "rehearsal_spaces": ["The Walthamstow Music Hub", "Mill-E5 Studios (20 min)"],
      "venues": ["The Ye Olde Rose & Crown (live music)", "Walthamstow Assembly Hall"],
      "creative_hubs": ["God's Own Junkyard (Blackhorse Lane)", "Gnome House Studios", "Blackhorse Workshop"],
      "summary": "Walthamstow has a distinctive creative identity — William Morris heritage, God's Own Junkyard, maker spaces on Blackhorse Lane. Growing but different character to Hackney's scene."
    },
    "N16": {
      "rehearsal_spaces": ["Premises Studios (15 min cycle)", "Dalston studios (10 min cycle)"],
      "venues": ["The Waiting Room (Stoke Newington)", "Ryan's Bar (live music)"],
      "creative_hubs": ["Church Street independents", "Near Dalston creative infrastructure"],
      "summary": "Stoke Newington is more literary/food-focused than music-focused. Benefits from proximity to Dalston (10 min cycle) for music infrastructure. Marcel's current base — familiar creative landscape."
    }
  }
}
```

Populate with Q4 research output. The above is a realistic template — update/refine with actual research findings.

### File: `src/home_finder/data/area_context.py`

Add TypedDicts and exports after existing definitions:

```python
class HostingTolerance(TypedDict, total=False):
    """Hosting tolerance data for an outcode."""
    rating: str  # "high" | "moderate" | "low"
    notes: str
    known_friendly_areas: list[str]
    known_sensitive_areas: list[str]

class CreativeScene(TypedDict, total=False):
    """Creative scene data for an outcode."""
    rehearsal_spaces: list[str]
    venues: list[str]
    creative_hubs: list[str]
    summary: str

HOSTING_TOLERANCE: Final[dict[str, HostingTolerance]] = _DATA.get("hosting_tolerance", {})
CREATIVE_SCENE: Final[dict[str, CreativeScene]] = _DATA.get("creative_scene", {})
```

---

## 4B. Inject hosting context into Phase 2 evaluation

### File: `src/home_finder/filters/quality_prompts.py`

Add `hosting_tolerance: str | None = None` parameter to `_format_property_context()`:

**Current signature** (line 213):
```python
def _format_property_context(
    *,
    price_pcm: int,
    bedrooms: int,
    area_average: int,
    description: str | None = None,
    area_context: str | None = None,
    outcode: str | None = None,
    council_tax_band_c: int | None = None,
    crime_summary: str | None = None,
    rent_trend: str | None = None,
) -> str:
```

Add `hosting_tolerance: str | None = None` as the last parameter.

In the body, inside the `if area_context and outcode:` block (line 242-248), after the rent_trend line, add:

```python
if hosting_tolerance:
    parts.append(f"\nHosting tolerance: {hosting_tolerance}")
```

This goes before the `parts.append("\n</area_context>")` line (currently line 248).

**Also add the parameter to `build_user_prompt()` and `build_evaluation_prompt()`** — both delegate to `_format_property_context()`. Add `hosting_tolerance: str | None = None` to their signatures and pass it through.

**Token budget impact:** ~5 tokens per property. Well within the ~300 token budget.

### File: `src/home_finder/filters/quality.py`

In `analyze_single_merged()` (line 589), after the existing area context lookups (lines 616-628), add:

```python
from home_finder.data.area_context import HOSTING_TOLERANCE

# Look up hosting tolerance for outcode
hosting_tolerance_data = HOSTING_TOLERANCE.get(outcode) if outcode else None
hosting_tolerance_str: str | None = None
if hosting_tolerance_data:
    rating = hosting_tolerance_data.get("rating", "unknown")
    notes = hosting_tolerance_data.get("notes", "")
    hosting_tolerance_str = f"{rating} — {notes}" if notes else rating
```

Then pass `hosting_tolerance=hosting_tolerance_str` to the call(s) that invoke `build_user_prompt()` and `build_evaluation_prompt()`. These calls happen inside `_analyze_property()` — trace the parameter through.

**Current flow:**
1. `analyze_single_merged()` collects context data (lines 610-628)
2. Passes to `_analyze_property()` (around line 660-680)
3. `_analyze_property()` calls `build_user_prompt()` (line ~878) and `build_evaluation_prompt()` (line ~1024)

Add `hosting_tolerance` to the kwargs passed at each step. Check the exact parameter passing in `_analyze_property()` — it may bundle area context params into a dict or pass them individually.

---

## 4C. Enhance fit score with area hosting tolerance

### File: `src/home_finder/filters/fit_score.py`

The hosting dimension (`_score_hosting()`, line 116) currently scores purely on visual/structural signals. Add area hosting tolerance as a bonus/penalty:

**At the end of `_score_hosting()`, before the confidence calculation (line 173):**

```python
# Area hosting tolerance bonus (injected at query time)
area_tolerance = analysis.get("_area_hosting_tolerance")
if area_tolerance == "high":
    score += 10
    signals += 1
elif area_tolerance == "low":
    score -= 10
    signals += 1
elif area_tolerance == "moderate":
    signals += 1  # Count as signal but neutral score
```

### File: `src/home_finder/db/storage.py`

In `get_properties_paginated()` (the method that returns property dicts for dashboard/detail pages), inject `_area_hosting_tolerance` into the analysis dict before fit score computation:

```python
from home_finder.data.area_context import HOSTING_TOLERANCE
from home_finder.utils.address import extract_outcode

# In the property result processing loop:
outcode = extract_outcode(prop_dict.get("postcode"))
if outcode and prop_dict.get("quality_analysis"):
    ht = HOSTING_TOLERANCE.get(outcode)
    if ht:
        # Inject into analysis dict for fit score computation
        analysis = prop_dict["quality_analysis"]
        if isinstance(analysis, dict):
            analysis["_area_hosting_tolerance"] = ht.get("rating")
```

**Alternative (cleaner):** Instead of modifying storage, inject in `routes.py` where fit score is computed (line 726-729):

```python
# In property_detail(), before compute_fit_score:
if qa is not None:
    analysis_dict = qa.model_dump()
    # Inject area hosting tolerance for fit score
    if outcode:
        ht = HOSTING_TOLERANCE.get(outcode)
        if ht:
            analysis_dict["_area_hosting_tolerance"] = ht.get("rating")
    bedrooms = prop.get("bedrooms", 0) or 0
    fit_score = compute_fit_score(analysis_dict, bedrooms)
    fit_breakdown = compute_fit_breakdown(analysis_dict, bedrooms)
```

Use the routes.py approach — it keeps the injection at the presentation layer, consistent with how fit score is already computed at query time.

**Also do the same in the dashboard listing view** — wherever fit score is computed for the property cards. Check `routes.py` for the dashboard route and apply the same pattern.

---

## 4D. Display on detail page

### File: `src/home_finder/web/routes.py`

In `property_detail()`, after the existing `area_context` assembly (line 695), add:

```python
from home_finder.data.area_context import HOSTING_TOLERANCE, CREATIVE_SCENE

if outcode:
    area_context["hosting_tolerance"] = HOSTING_TOLERANCE.get(outcode)
    area_context["creative_scene"] = CREATIVE_SCENE.get(outcode)
```

### File: `src/home_finder/web/templates/detail.html`

In the Area section (inside the `<div class="area-details">` block, after the rent trend card and before the acoustic cards from Ticket 1), add:

```html
{% if area_context.hosting_tolerance %}
<div>
    <h4>Hosting &amp; Music
        <span class="badge
            {% if area_context.hosting_tolerance.rating == 'high' %}badge-green
            {% elif area_context.hosting_tolerance.rating == 'moderate' %}badge-amber
            {% else %}badge-red{% endif %}">{{ area_context.hosting_tolerance.rating }}</span>
    </h4>
    <p>{{ area_context.hosting_tolerance.notes }}</p>
    {% if area_context.hosting_tolerance.known_friendly_areas %}
    <p class="detail-meta"><strong>Friendly areas:</strong>
        {{ area_context.hosting_tolerance.known_friendly_areas | join(", ") }}
    </p>
    {% endif %}
    {% if area_context.hosting_tolerance.known_sensitive_areas %}
    <p class="detail-meta"><strong>Sensitive areas:</strong>
        {{ area_context.hosting_tolerance.known_sensitive_areas | join(", ") }}
    </p>
    {% endif %}
</div>
{% endif %}

{% if area_context.creative_scene %}
<div>
    <h4>Creative Scene</h4>
    <p>{{ area_context.creative_scene.summary }}</p>
    {% if area_context.creative_scene.venues %}
    <p class="detail-meta"><strong>Venues:</strong>
        {{ area_context.creative_scene.venues | join(" · ") }}
    </p>
    {% endif %}
    {% if area_context.creative_scene.rehearsal_spaces %}
    <p class="detail-meta"><strong>Rehearsal:</strong>
        {{ area_context.creative_scene.rehearsal_spaces | join(" · ") }}
    </p>
    {% endif %}
    {% if area_context.creative_scene.creative_hubs %}
    <p class="detail-meta"><strong>Creative hubs:</strong>
        {{ area_context.creative_scene.creative_hubs | join(" · ") }}
    </p>
    {% endif %}
</div>
{% endif %}
```

---

## 4E. Tests

### Data loading tests — `tests/test_data/test_area_context.py`

```python
from home_finder.data.area_context import HOSTING_TOLERANCE, CREATIVE_SCENE


def test_hosting_tolerance_loaded():
    """Hosting tolerance data loads for expected outcodes."""
    assert isinstance(HOSTING_TOLERANCE, dict)
    for key in ("E8", "E9", "N16"):
        assert key in HOSTING_TOLERANCE
        ht = HOSTING_TOLERANCE[key]
        assert "rating" in ht
        assert ht["rating"] in ("high", "moderate", "low")


def test_creative_scene_loaded():
    """Creative scene data loads with expected structure."""
    assert isinstance(CREATIVE_SCENE, dict)
    for key in ("E8", "E9"):
        assert key in CREATIVE_SCENE
        cs = CREATIVE_SCENE[key]
        assert "summary" in cs
        assert isinstance(cs.get("venues", []), list)
```

### Prompt tests — `tests/test_filters/test_quality_prompts.py`

```python
def test_prompt_with_hosting_tolerance(self) -> None:
    """Prompt includes hosting tolerance in area context when provided."""
    prompt = build_user_prompt(
        price_pcm=1800,
        bedrooms=2,
        area_average=1900,
        area_context="Trendy East London",
        outcode="E8",
        hosting_tolerance="high — Dalston and Hackney Central have established nightlife",
    )
    assert "Hosting tolerance: high" in prompt

def test_prompt_without_hosting_tolerance(self) -> None:
    """Prompt omits hosting tolerance when not provided."""
    prompt = build_user_prompt(
        price_pcm=1800,
        bedrooms=2,
        area_average=1900,
        area_context="Trendy East London",
        outcode="E8",
    )
    assert "Hosting tolerance" not in prompt
```

Update existing inline-snapshot tests that will change due to the new parameter (even though it defaults to `None`, verify snapshots still match).

### Fit score tests — `tests/test_filters/test_fit_score.py`

```python
def test_hosting_score_bonus_from_high_area_tolerance():
    """Hosting dimension gets bonus from high area tolerance."""
    base = _full_analysis()
    base["_area_hosting_tolerance"] = "high"
    score_with = compute_fit_score(base, 2)

    base_without = _full_analysis()
    score_without = compute_fit_score(base_without, 2)

    assert score_with > score_without


def test_hosting_score_penalty_from_low_area_tolerance():
    """Hosting dimension gets penalty from low area tolerance."""
    base = _full_analysis()
    base["_area_hosting_tolerance"] = "low"
    score_with = compute_fit_score(base, 2)

    base_without = _full_analysis()
    score_without = compute_fit_score(base_without, 2)

    assert score_with < score_without


def test_hosting_score_unchanged_without_area_tolerance():
    """Hosting dimension unchanged when _area_hosting_tolerance not present."""
    analysis = _full_analysis()
    assert "_area_hosting_tolerance" not in analysis
    score = compute_fit_score(analysis, 2)
    assert score is not None
```

### Web route tests — `tests/test_web/test_routes.py`

```python
@pytest.mark.asyncio
async def test_hosting_tolerance_card_renders(
    self, client: TestClient, storage: PropertyStorage, merged_a: MergedProperty
) -> None:
    """Hosting tolerance card renders for outcode with data."""
    await storage.save_merged_property(merged_a)
    resp = client.get(f"/property/{merged_a.unique_id}")
    assert resp.status_code == 200
    # E8 should have hosting tolerance data
    # Check for hosting section presence

@pytest.mark.asyncio
async def test_creative_scene_card_renders(
    self, client: TestClient, storage: PropertyStorage, merged_a: MergedProperty
) -> None:
    """Creative scene card renders for outcode with data."""
    await storage.save_merged_property(merged_a)
    resp = client.get(f"/property/{merged_a.unique_id}")
    assert resp.status_code == 200
```

---

## Verification

1. `uv run pytest` — all existing + new tests pass
2. `uv run ruff check src tests` — no lint issues
3. `uv run mypy src` — type checking passes
4. `uv run home-finder --dry-run --max-per-scraper 2` — pipeline completes (hosting tolerance in prompts)
5. `uv run home-finder --serve` — detail page shows hosting + creative scene cards for E8 properties
6. Verify fit score changes: compare a property in E8 (high tolerance) vs N17 (low tolerance) — hosting dimension should differ

---

## Files Changed Summary

| File | Change |
|------|--------|
| `src/home_finder/data/area_context.json` | Add `hosting_tolerance`, `creative_scene` top-level keys |
| `src/home_finder/data/area_context.py` | Add `HostingTolerance`, `CreativeScene` TypedDicts + exports |
| `src/home_finder/filters/quality_prompts.py` | Add `hosting_tolerance` param to `_format_property_context()`, `build_user_prompt()`, `build_evaluation_prompt()` |
| `src/home_finder/filters/quality.py` | Look up hosting tolerance, pass to prompt builders |
| `src/home_finder/filters/fit_score.py` | Add area tolerance bonus/penalty in `_score_hosting()` |
| `src/home_finder/web/routes.py` | Add hosting/creative to area_context dict; inject `_area_hosting_tolerance` for fit score |
| `src/home_finder/web/templates/detail.html` | Add "Hosting & Music" and "Creative Scene" cards |
| `tests/test_data/test_area_context.py` | Data loading tests for new TypedDicts |
| `tests/test_filters/test_quality_prompts.py` | Hosting tolerance prompt tests |
| `tests/test_filters/test_fit_score.py` | Hosting dimension bonus/penalty tests |
| `tests/test_web/test_routes.py` | Card render tests |

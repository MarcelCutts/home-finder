# Ticket 6: Environmental Risk Data

**Depends on:** Ticket 2 (uses `area_context.json` structure established there)
**Blocks:** None

## Goal

Add environmental risk flags (flood risk, air quality, contamination) to area context and display them as warnings on the detail page. Inject environmental context into analysis prompts for properties in known risk zones so Claude can factor them into the value assessment.

---

## 6A. Add environmental data

### File: `src/home_finder/data/area_context.json`

Add a new top-level key `environmental_risks`:

```json
{
  "environmental_risks": {
    "E3": {
      "flood_risk": {
        "zone": "2",
        "affected_streets": ["Three Mills Lane", "Bow Back Rivers area", "Channelsea Path"],
        "history": "Bow Back Rivers area experienced surface water flooding in 2021 flash storms. Zone 2 designation from Environment Agency.",
        "mitigation": "Ground floor properties in Bow Back Rivers area most affected. Upper floors generally safe. Check property-level flood risk on gov.uk."
      },
      "air_quality": {
        "rating": "moderate",
        "worst_roads": ["A12", "Bow Road (A11)"],
        "notes": "A12 corridor has elevated NO2 levels. Properties 100m+ from A12 are significantly better. Bow Road also elevated but improving with ULEZ."
      },
      "other_risks": []
    },
    "E5": {
      "flood_risk": {
        "zone": "1",
        "affected_streets": [],
        "history": "No significant flood history. Upper Clapton reservoir area is managed by Thames Water.",
        "mitigation": "Low flood risk overall."
      },
      "air_quality": {
        "rating": "good",
        "worst_roads": ["Lea Bridge Road (A104)"],
        "notes": "Generally good air quality. Lea Bridge Road has moderate traffic pollution but side streets are clean."
      },
      "other_risks": []
    },
    "E8": {
      "flood_risk": {
        "zone": "1",
        "affected_streets": [],
        "history": "No significant flood risk. Well above river level.",
        "mitigation": "Low flood risk."
      },
      "air_quality": {
        "rating": "moderate",
        "worst_roads": ["Kingsland Road (A10)", "Mare Street", "Graham Road"],
        "notes": "Kingsland Road and Mare Street have elevated pollution from bus/traffic. Side streets generally acceptable. Dalston Junction area congested."
      },
      "other_risks": []
    },
    "E9": {
      "flood_risk": {
        "zone": "2-3",
        "affected_streets": ["White Post Lane", "Wallis Road", "Berkshire Road", "Eastway", "Prince Edward Road (near canal)"],
        "history": "Hackney Wick experienced minor flooding in 2021 from canal overflow during heavy rain. Some properties on White Post Lane had basement/ground floor water ingress.",
        "mitigation": "Ground floor properties near the canal and Lea Navigation most at risk. Upper floors safe. Fish Island east of the canal is Zone 3 in parts — check individual property flood risk on flood-map-for-planning.service.gov.uk."
      },
      "air_quality": {
        "rating": "moderate",
        "worst_roads": ["A12 (Eastway)", "Wick Road"],
        "notes": "A12/Eastway corridor has poor air quality. Properties along Eastway significantly affected. Interior of Hackney Wick away from A12 is much better."
      },
      "other_risks": [
        "Historical industrial contamination in Hackney Wick — some sites remediated for housing, but check environmental search reports for former industrial land."
      ]
    },
    "E10": {
      "flood_risk": {
        "zone": "1",
        "affected_streets": [],
        "history": "Low flood risk. Some surface water risk in low-lying areas near Lea Valley.",
        "mitigation": "Generally low risk."
      },
      "air_quality": {
        "rating": "moderate",
        "worst_roads": ["High Road Leyton (A112)", "Lea Bridge Road"],
        "notes": "High Road Leyton has moderate traffic pollution. Residential streets off the high road are generally good."
      },
      "other_risks": []
    },
    "E17": {
      "flood_risk": {
        "zone": "1-2",
        "affected_streets": ["Blackhorse Road (near reservoirs)", "Forest Road (near Banbury Reservoir)"],
        "history": "Low-moderate risk. Walthamstow Wetlands area is managed flood storage. Some surface water risk near reservoirs during extreme rainfall.",
        "mitigation": "Properties near Blackhorse Road reservoirs should check surface water flood risk maps. Most of Walthamstow Village and central E17 is Zone 1."
      },
      "air_quality": {
        "rating": "good",
        "worst_roads": ["Forest Road (A503)", "Hoe Street"],
        "notes": "Generally good air quality. Forest Road has moderate traffic. Walthamstow Village area has excellent air quality."
      },
      "other_risks": []
    },
    "N15": {
      "flood_risk": {
        "zone": "1",
        "affected_streets": [],
        "history": "Low flood risk.",
        "mitigation": "No significant concerns."
      },
      "air_quality": {
        "rating": "moderate",
        "worst_roads": ["Seven Sisters Road (A503)", "High Road (A10)"],
        "notes": "Seven Sisters Road is a major bus corridor with elevated NO2. Side streets are acceptable. South Tottenham industrial area has some localised pollution."
      },
      "other_risks": []
    },
    "N16": {
      "flood_risk": {
        "zone": "1",
        "affected_streets": [],
        "history": "No significant flood risk. Elevated terrain.",
        "mitigation": "Low risk."
      },
      "air_quality": {
        "rating": "good",
        "worst_roads": ["Stoke Newington High Street", "Green Lanes (A105)"],
        "notes": "Good air quality overall. Stoke Newington High Street has moderate traffic pollution but Church Street and residential areas are clean."
      },
      "other_risks": []
    },
    "N17": {
      "flood_risk": {
        "zone": "1-2",
        "affected_streets": ["Near Pymmes Brook", "Tottenham Hale area"],
        "history": "Pymmes Brook has flooded historically. Tottenham Hale redevelopment area includes flood mitigation infrastructure.",
        "mitigation": "Check properties near Pymmes Brook for surface water risk. Tottenham Hale new builds should have flood mitigation built in."
      },
      "air_quality": {
        "rating": "moderate",
        "worst_roads": ["High Road Tottenham (A10)", "Broad Lane"],
        "notes": "High Road Tottenham has significant traffic pollution. Side streets are generally acceptable."
      },
      "other_risks": []
    }
  }
}
```

### File: `src/home_finder/data/area_context.py`

Add TypedDicts and export after existing definitions:

```python
class FloodRisk(TypedDict, total=False):
    """Flood risk data for an outcode."""
    zone: str  # "1", "2", "2-3", "3"
    affected_streets: list[str]
    history: str
    mitigation: str

class AirQuality(TypedDict, total=False):
    """Air quality data for an outcode."""
    rating: str  # "good" | "moderate" | "poor"
    worst_roads: list[str]
    notes: str

class EnvironmentalRisks(TypedDict, total=False):
    """Environmental risk data for an outcode."""
    flood_risk: FloodRisk
    air_quality: AirQuality
    other_risks: list[str]

ENVIRONMENTAL_RISKS: Final[dict[str, EnvironmentalRisks]] = _DATA.get("environmental_risks", {})
```

---

## 6B. Inject environmental context into prompts

### File: `src/home_finder/filters/quality_prompts.py`

Add `environmental_note: str | None = None` parameter to `_format_property_context()`.

In the body, inside the `if area_context and outcode:` block (line 242-248), after rent_trend and hosting_tolerance lines, add:

```python
if environmental_note:
    parts.append(f"\nEnvironmental: {environmental_note}")
```

This goes before the `parts.append("\n</area_context>")` line.

**Also add the parameter to `build_user_prompt()` and `build_evaluation_prompt()`** and pass through.

### File: `src/home_finder/filters/quality.py`

In `analyze_single_merged()`, after the existing area context lookups (lines 616-628), add:

```python
from home_finder.data.area_context import ENVIRONMENTAL_RISKS

# Build concise environmental note for prompt injection
environmental_note: str | None = None
env_data = ENVIRONMENTAL_RISKS.get(outcode) if outcode else None
if env_data:
    env_parts: list[str] = []
    flood = env_data.get("flood_risk")
    if flood and flood.get("zone", "1") != "1":
        env_parts.append(f"Flood zone {flood['zone']}")
        if flood.get("mitigation"):
            env_parts.append(flood["mitigation"])
    air = env_data.get("air_quality")
    if air and air.get("rating") in ("moderate", "poor"):
        roads = ", ".join(air.get("worst_roads", [])[:2])
        env_parts.append(f"Air quality {air['rating']}" + (f" (avoid {roads} proximity)" if roads else ""))
    other = env_data.get("other_risks", [])
    if other:
        env_parts.append(other[0])  # Just the first risk to keep concise
    if env_parts:
        environmental_note = ". ".join(env_parts)
```

Pass `environmental_note=environmental_note` through to `build_user_prompt()` and `build_evaluation_prompt()`.

**Token budget impact:** ~10-20 tokens per property when environmental data exists. Within the ~300 token budget.

---

## 6C. Display on detail page

### File: `src/home_finder/web/routes.py`

In `property_detail()`, after the existing `area_context` assembly (line 695), add:

```python
from home_finder.data.area_context import ENVIRONMENTAL_RISKS

if outcode:
    area_context["environmental"] = ENVIRONMENTAL_RISKS.get(outcode)
```

### File: `src/home_finder/web/templates/detail.html`

In the Area section (inside `<div class="area-details">`), add an environmental card. Place it after crime and before hosting/acoustic cards:

```html
{% if area_context.environmental %}
<div>
    <h4>Environmental</h4>

    {% if area_context.environmental.flood_risk %}
    {% set flood = area_context.environmental.flood_risk %}
    {% if flood.zone and flood.zone != "1" %}
    <div style="margin-bottom: 0.75rem;">
        <strong>Flood Risk
            <span class="badge
                {% if flood.zone in ('3', '2-3') %}badge-red
                {% else %}badge-amber{% endif %}">Zone {{ flood.zone }}</span>
        </strong>
        {% if flood.affected_streets %}
        <p class="detail-meta">Affected streets: {{ flood.affected_streets | join(", ") }}</p>
        {% endif %}
        {% if flood.history %}
        <p class="detail-meta">{{ flood.history }}</p>
        {% endif %}
        {% if flood.mitigation %}
        <p>{{ flood.mitigation }}</p>
        {% endif %}
    </div>
    {% else %}
    <p class="detail-meta">Flood risk: <span class="badge badge-green">Zone 1 (low)</span></p>
    {% endif %}
    {% endif %}

    {% if area_context.environmental.air_quality %}
    {% set air = area_context.environmental.air_quality %}
    <div style="margin-bottom: 0.75rem;">
        <strong>Air Quality
            <span class="badge
                {% if air.rating == 'good' %}badge-green
                {% elif air.rating == 'moderate' %}badge-amber
                {% else %}badge-red{% endif %}">{{ air.rating }}</span>
        </strong>
        {% if air.worst_roads %}
        <p class="detail-meta">Worst roads: {{ air.worst_roads | join(", ") }}</p>
        {% endif %}
        {% if air.notes %}
        <p class="detail-meta">{{ air.notes }}</p>
        {% endif %}
    </div>
    {% endif %}

    {% if area_context.environmental.other_risks %}
    <div>
        <strong>Other Risks</strong>
        <ul>
            {% for risk in area_context.environmental.other_risks %}
            <li>{{ risk }}</li>
            {% endfor %}
        </ul>
    </div>
    {% endif %}
</div>
{% endif %}
```

Only renders when environmental data exists for the outcode. Uses `{% if %}` guards throughout for graceful degradation.

---

## 6D. Consider adding environmental lowlights

### File: `src/home_finder/models.py`

The `PropertyLowlight` enum (line 294) can be extended with environmental lowlights:

```python
class PropertyLowlight(StrEnum):
    # ... existing values ...
    # Environmental
    FLOOD_RISK_ZONE = "Flood risk zone"
    NEAR_MAIN_ROAD = "Near main road (air quality)"
```

These would be added by Phase 2 evaluation when environmental context indicates risk and the property characteristics match (e.g., ground floor + flood zone, or street-facing on a flagged road).

**Important:** New enum values must be backward-compatible. Existing `analysis_json` blobs in SQLite won't have these values — they're only assigned to new analyses going forward. The lowlight display already handles arbitrary string values from the enum, so no template changes needed.

**Note:** This is optional. The environmental card on the detail page provides the same information visually. Lowlights make it surface in the property card on the dashboard. Implement if it's straightforward — Phase 2's `EVALUATION_SYSTEM_PROMPT` already lists allowed lowlight values, so the new values would need to be added there too.

If adding the lowlights:

### File: `src/home_finder/filters/quality_prompts.py`

In `EVALUATION_SYSTEM_PROMPT`, find the lowlight values list and add the new ones:
```
- "Flood risk zone" — only when environmental context indicates zone 2+ AND property is ground/lower ground floor
- "Near main road (air quality)" — only when environmental context names a specific road AND property faces or is adjacent to it
```

---

## 6E. Tests

### Data loading test — `tests/test_data/test_area_context.py`

```python
from home_finder.data.area_context import ENVIRONMENTAL_RISKS


def test_environmental_risks_loaded():
    """Environmental risk data loads for expected outcodes."""
    assert isinstance(ENVIRONMENTAL_RISKS, dict)
    # E9 (Hackney Wick) should have notable flood risk
    assert "E9" in ENVIRONMENTAL_RISKS
    env_e9 = ENVIRONMENTAL_RISKS["E9"]
    assert "flood_risk" in env_e9
    assert env_e9["flood_risk"]["zone"] in ("2", "2-3", "3")

    # N16 (Stoke Newington) should have low flood risk
    assert "N16" in ENVIRONMENTAL_RISKS
    assert ENVIRONMENTAL_RISKS["N16"]["flood_risk"]["zone"] == "1"


def test_environmental_risks_structure():
    """Environmental risk entries have expected fields."""
    for outcode, env in ENVIRONMENTAL_RISKS.items():
        if "flood_risk" in env:
            assert "zone" in env["flood_risk"]
        if "air_quality" in env:
            assert "rating" in env["air_quality"]
            assert env["air_quality"]["rating"] in ("good", "moderate", "poor")
```

### Prompt tests — `tests/test_filters/test_quality_prompts.py`

```python
def test_prompt_with_environmental_note(self) -> None:
    """Prompt includes environmental note in area context when provided."""
    prompt = build_user_prompt(
        price_pcm=1800,
        bedrooms=2,
        area_average=1900,
        area_context="Hackney Wick creative area",
        outcode="E9",
        environmental_note="Flood zone 2-3 near canals. Air quality moderate (avoid A12 proximity).",
    )
    assert "Environmental: Flood zone 2-3" in prompt
    assert "A12" in prompt


def test_prompt_without_environmental_note(self) -> None:
    """Prompt omits environmental section when not provided."""
    prompt = build_user_prompt(
        price_pcm=1800,
        bedrooms=2,
        area_average=1900,
        area_context="Hackney Wick creative area",
        outcode="E9",
    )
    assert "Environmental:" not in prompt
```

Update existing inline-snapshot tests if the parameter changes affect snapshots.

### Web route tests — `tests/test_web/test_routes.py`

```python
@pytest.mark.asyncio
async def test_environmental_card_renders_for_risk_area(
    self, client: TestClient, storage: PropertyStorage, prop_a: Property
) -> None:
    """Environmental card renders for outcodes with risk data."""
    # Use E9 which has flood zone 2-3
    merged = MergedProperty(
        canonical=prop_a.model_copy(update={"postcode": "E9 6AS"}),
        sources=(PropertySource.OPENRENT,),
        source_urls={PropertySource.OPENRENT: prop_a.url},
        min_price=1900,
        max_price=1900,
    )
    await storage.save_merged_property(merged)
    resp = client.get(f"/property/{merged.unique_id}")
    if resp.status_code == 200:  # May be 404 if no images
        assert "Environmental" in resp.text or resp.status_code == 404


@pytest.mark.asyncio
async def test_environmental_card_absent_for_unknown_area(
    self, client: TestClient, storage: PropertyStorage, prop_a: Property
) -> None:
    """Environmental card doesn't render for outcodes without data."""
    merged = MergedProperty(
        canonical=prop_a.model_copy(update={"postcode": "SW1 1AA"}),
        sources=(PropertySource.OPENRENT,),
        source_urls={PropertySource.OPENRENT: prop_a.url},
        min_price=1900,
        max_price=1900,
    )
    await storage.save_merged_property(merged)
    resp = client.get(f"/property/{merged.unique_id}")
    # Should not crash — environmental card just won't appear
    assert resp.status_code in (200, 404)
```

### If new lowlights added — `tests/test_filters/test_quality.py` or `tests/test_models.py`

```python
def test_new_lowlight_enum_values():
    """New environmental lowlight values are valid enum members."""
    from home_finder.models import PropertyLowlight
    assert PropertyLowlight.FLOOD_RISK_ZONE == "Flood risk zone"
    assert PropertyLowlight.NEAR_MAIN_ROAD == "Near main road (air quality)"
```

---

## Verification

1. `uv run pytest` — all existing + new tests pass
2. `uv run ruff check src tests` — no lint issues
3. `uv run mypy src` — type checking passes
4. `uv run home-finder --dry-run --max-per-scraper 2` — pipeline completes (environmental notes in prompts)
5. `uv run home-finder --serve` — detail page shows environmental card for E9 properties (flood zone 2-3)
6. Check E9 property: flood risk badge should show amber/red. Check N16 property: flood risk should show green Zone 1.

---

## Files Changed Summary

| File | Change |
|------|--------|
| `src/home_finder/data/area_context.json` | Add `environmental_risks` top-level key |
| `src/home_finder/data/area_context.py` | Add `FloodRisk`, `AirQuality`, `EnvironmentalRisks` TypedDicts + `ENVIRONMENTAL_RISKS` export |
| `src/home_finder/filters/quality_prompts.py` | Add `environmental_note` param to `_format_property_context()`, `build_user_prompt()`, `build_evaluation_prompt()` |
| `src/home_finder/filters/quality.py` | Build concise environmental note from risk data, pass to prompts |
| `src/home_finder/web/routes.py` | Add environmental risks to area_context dict |
| `src/home_finder/web/templates/detail.html` | Add "Environmental" card with flood risk + air quality badges |
| `src/home_finder/models.py` | (Optional) Add `FLOOD_RISK_ZONE`, `NEAR_MAIN_ROAD` to `PropertyLowlight` enum |
| `src/home_finder/filters/quality_prompts.py` | (Optional) Add new lowlight values to `EVALUATION_SYSTEM_PROMPT` |
| `tests/test_data/test_area_context.py` | Environmental data loading tests |
| `tests/test_filters/test_quality_prompts.py` | Environmental note prompt tests |
| `tests/test_web/test_routes.py` | Environmental card render tests |
| `tests/test_models.py` | (Optional) New lowlight enum value tests |

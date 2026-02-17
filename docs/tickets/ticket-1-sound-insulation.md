# Ticket 1: Sound Insulation Reference Data & Prompt Enhancement

**Depends on:** Ticket 2 (micro-area profiles restructure `area_context.json` first)
**Blocks:** None (Tickets 4 and 6 can reference this pattern but aren't blocked)

## Goal

Make the quality analysis's `hosting_noise_risk` assessment evidence-based rather than purely visual, and give Marcel actionable acoustic intelligence on the detail page.

Currently the Phase 1 visual analysis guesses at sound insulation from photos alone (`flooring_noise.hosting_noise_risk`). This ticket adds real-world acoustic data by building type so Phase 2 can cross-reference visual observations with known performance characteristics.

---

## 1A. Add acoustic profiles to reference data

### Files to modify

**`src/home_finder/data/area_context.json`** — add two new top-level keys: `acoustic_profiles` and `noise_enforcement`.

```json
{
  "acoustic_profiles": {
    "victorian": {
      "label": "Victorian/Edwardian conversion",
      "airborne_insulation_db": "35-40",
      "hosting_safety": "poor",
      "summary": "Pine boards on timber joists, typically no insulation between floors. ~35-40 dB airborne (well below Part E 45 dB standard). High noise transmission to neighbours unless heavily modified with acoustic underlay or floating floors.",
      "viewing_checks": ["Check party wall thickness by knocking", "Ask about sound testing certificate", "Look for acoustic underlay under flooring", "Check if conversion has Building Regs Part E compliance"]
    },
    "purpose_built_pre2003": {
      "label": "Purpose-built (pre-2003)",
      "airborne_insulation_db": "40-45",
      "hosting_safety": "moderate",
      "summary": "Concrete floors with some mass, but pre-Part E standards. Typically 40-45 dB airborne. Adequate for normal living, moderate risk for music/hosting events.",
      "viewing_checks": ["Check floor construction type (concrete vs timber)", "Listen for neighbour noise during viewing"]
    },
    "purpose_built_post2003": {
      "label": "Purpose-built (post-2003)",
      "airborne_insulation_db": "45+",
      "hosting_safety": "good",
      "summary": "Built to Part E standards (45+ dB airborne, 62+ dB impact). Generally good sound isolation between units. Modern acoustic design with resilient bars, floating floors.",
      "viewing_checks": ["Ask for Part E test certificate", "Check for acoustic seals on front door"]
    },
    "warehouse": {
      "label": "Warehouse/industrial conversion",
      "airborne_insulation_db": "variable (30-50)",
      "hosting_safety": "moderate",
      "summary": "Highly variable. Thick masonry walls excellent for airborne sound, but open-plan layouts and metal structures can create resonance. Converted spaces may lack proper acoustic treatment between units.",
      "viewing_checks": ["Check if conversion is residential or live/work", "Ask about neighbours and noise complaints history", "Look for exposed steel (conducts vibration)"]
    },
    "ex_council": {
      "label": "Ex-council / social housing",
      "airborne_insulation_db": "42-48",
      "hosting_safety": "moderate",
      "summary": "Concrete construction provides decent mass-based insulation (42-48 dB). Built robustly but without acoustic design intent. Walls between units are typically 200mm+ concrete — good for airborne sound.",
      "viewing_checks": ["Check wall construction between units", "Note floor type (most have concrete floors)"]
    },
    "new_build": {
      "label": "New build (2015+)",
      "airborne_insulation_db": "45-55",
      "hosting_safety": "good",
      "summary": "Built to current Part E standards with modern acoustic treatments. Typically 45-55 dB airborne. Lightweight steel-frame builds (common in East London developments) can underperform despite compliance — drywall partitions transmit more than concrete.",
      "viewing_checks": ["Ask if steel-frame or concrete-frame construction", "Check for acoustic seals around doors", "Note if drywall or blockwork party walls"]
    },
    "georgian": {
      "label": "Georgian townhouse/conversion",
      "airborne_insulation_db": "30-38",
      "hosting_safety": "poor",
      "summary": "Similar to Victorian but often with thinner lath-and-plaster walls. ~30-38 dB airborne. Original features (sash windows, high ceilings) are acoustically poor. Ground-floor or whole-house tenancies have less neighbour risk.",
      "viewing_checks": ["Check party wall type (lath-and-plaster vs brick)", "Check window glazing (single sash = very poor)", "Ask about neighbour proximity"]
    }
  },
  "noise_enforcement": {
    "Hackney": {
      "process": "Noise complaints via Hackney Council online form or 020 8356 4455. Out-of-hours noise team operates Fri-Sun evenings.",
      "threshold_info": "No fixed dB threshold — assessed as 'statutory nuisance' (subjective). Persistent complaints lead to Noise Abatement Notice.",
      "response_time": "Out-of-hours team aims to visit within 1 hour on active nights. Daytime complaints may take days."
    },
    "Haringey": {
      "process": "Report via Haringey noise app or call 020 8489 1000. Out-of-hours service available weekends.",
      "threshold_info": "Statutory nuisance standard. Music audible in neighbouring property at night (11pm-7am) likely to trigger action.",
      "response_time": "Weekend evening response typically 1-2 hours. Weekday response may be next-day investigation."
    },
    "Tower Hamlets": {
      "process": "Noise complaints via Tower Hamlets online portal. 24-hour noise team on weekends.",
      "threshold_info": "Statutory nuisance standard. Proactive enforcement in high-density areas.",
      "response_time": "24-hour weekend team responsive within 1-2 hours. High volume of complaints in borough."
    },
    "Waltham Forest": {
      "process": "Report via Waltham Forest Council website or 020 8496 3000.",
      "threshold_info": "Standard statutory nuisance framework. Less dense than Hackney — fewer complaints overall.",
      "response_time": "Out-of-hours service limited. Most complaints handled next working day."
    }
  }
}
```

**Important:** These keys are added alongside existing top-level keys (`rental_benchmarks`, `area_context`, etc.) — do NOT nest them inside any existing key. Populate with research data when available — the above is a realistic template to be refined with actual Q1 research output.

### `src/home_finder/data/area_context.py`

Add TypedDicts and exports after the existing `RentTrend` class and exports:

```python
class AcousticProfile(TypedDict, total=False):
    label: str
    airborne_insulation_db: str
    hosting_safety: str  # "good" | "moderate" | "poor"
    summary: str
    viewing_checks: list[str]

class NoiseEnforcement(TypedDict, total=False):
    process: str
    threshold_info: str
    response_time: str

ACOUSTIC_PROFILES: Final[dict[str, AcousticProfile]] = _DATA.get("acoustic_profiles", {})
NOISE_ENFORCEMENT: Final[dict[str, NoiseEnforcement]] = _DATA.get("noise_enforcement", {})
```

Note the use of `_DATA.get("acoustic_profiles", {})` — this ensures backward compatibility if the JSON hasn't been updated yet.

---

## 1B. Enhance Phase 1 visual analysis system prompt

### File: `src/home_finder/filters/quality_prompts.py`

The `VISUAL_ANALYSIS_SYSTEM_PROMPT` contains a `<stock_types>` section that describes building types for visual identification. Add acoustic performance data to each entry. This is in the cached system prompt (prompt caching via `cache_control: {"type": "ephemeral"}`), so the token cost is amortized across all properties.

**How to find the section:** Search for `<stock_types>` in the prompt string. Each building type has a bullet or paragraph. Append 1-2 lines about acoustic characteristics to each.

**Example additions:**

For the Victorian entry:
```
Acoustic: ~35-40 dB airborne (below Part E 45 dB). Pine-on-timber with no insulation. High hosting noise risk unless modified.
```

For the new-build entry:
```
Acoustic: 45-55 dB (Part E compliant). Lightweight steel-frame can underperform — check if drywall or blockwork party walls.
```

**Token budget:** This adds ~100 tokens to the system prompt. Acceptable since it's cached.

---

## 1C. Inject acoustic profile into Phase 2 per-property context

### File: `src/home_finder/filters/quality_prompts.py`

**Current `build_evaluation_prompt()` signature** (line 293):
```python
def build_evaluation_prompt(
    *,
    visual_data: dict[str, Any],
    description: str | None = None,
    price_pcm: int,
    bedrooms: int,
    area_average: int,
    area_context: str | None = None,
    outcode: str | None = None,
    council_tax_band_c: int | None = None,
    crime_summary: str | None = None,
    rent_trend: str | None = None,
) -> str:
```

Add a new parameter: `acoustic_context: str | None = None`.

After the `_format_property_context()` call (line 311), append acoustic context if provided:

```python
prompt += _format_property_context(...)

if acoustic_context:
    prompt += f"\n\n<acoustic_context>\n{acoustic_context}\n</acoustic_context>"

prompt += "\n\nBased on the visual analysis observations above..."
```

**Do NOT add this to `build_user_prompt()`** — Phase 1 is visual-only and shouldn't see building-type reference data (it would bias visual observations). The acoustic info enhances Phase 2's evaluation of hosting noise risk using the property type that Phase 1 already identified.

### File: `src/home_finder/filters/quality.py`

In `_analyze_property()`, after Phase 1 returns `visual_data` (around line 1000-1020), extract the property type and look up the acoustic profile:

```python
# After Phase 1 succeeds and visual_data is available:
acoustic_context: str | None = None
if visual_data:
    listing_ext = visual_data.get("listing_extraction") or {}
    prop_type = listing_ext.get("property_type")
    if prop_type:
        from home_finder.data.area_context import ACOUSTIC_PROFILES
        profile = ACOUSTIC_PROFILES.get(prop_type)
        if profile:
            acoustic_context = (
                f"Building type: {profile.get('label', prop_type)}\n"
                f"Sound insulation: {profile.get('airborne_insulation_db', 'unknown')} dB airborne\n"
                f"Hosting safety: {profile.get('hosting_safety', 'unknown')}\n"
                f"{profile.get('summary', '')}"
            )
```

Pass `acoustic_context=acoustic_context` to `build_evaluation_prompt()`.

**Important context for finding the right location in `quality.py`:**
- `analyze_single_merged()` is at line 589. It calls `_analyze_property()`.
- The Phase 2 call to `build_evaluation_prompt()` is around line 1024-1035.
- `visual_data` is available after Phase 1 parsing, before the Phase 2 API call.
- The `build_evaluation_prompt()` call is inside `_analyze_property()`.

---

## 1D. Display acoustic info on detail page

### File: `src/home_finder/web/routes.py`

In `property_detail()` (line 650), after assembling the `area_context` dict (lines 686-695), add acoustic profile lookup:

```python
# After existing area_context assembly (line 695)
qa = prop.get("quality_analysis")
if qa:
    # Extract property type from quality analysis for acoustic lookup
    listing_ext = qa.listing_extraction if hasattr(qa, 'listing_extraction') else None
    if listing_ext:
        prop_type = listing_ext.property_type if hasattr(listing_ext, 'property_type') else None
        if prop_type:
            from home_finder.data.area_context import ACOUSTIC_PROFILES
            area_context["acoustic_profile"] = ACOUSTIC_PROFILES.get(prop_type)

# Also add noise enforcement for the borough
if area_context.get("borough"):
    from home_finder.data.area_context import NOISE_ENFORCEMENT
    area_context["noise_enforcement"] = NOISE_ENFORCEMENT.get(area_context["borough"])
```

**Note:** `qa` is a `PropertyQualityAnalysis` Pydantic model (loaded from `analysis_json` blob). `listing_extraction` is a nested model with `property_type` field. Check access patterns — may need `qa.model_dump()` or direct attribute access depending on how it's loaded. Look at how `fit_score` accesses it (line 724-729) for the pattern.

### File: `src/home_finder/web/templates/detail.html`

In the Area section (after line 308, before the closing `</div>` of `area-details`), add:

```html
{% if area_context.acoustic_profile %}
<div>
    <h4>Sound &amp; Hosting
        <span class="badge
            {% if area_context.acoustic_profile.hosting_safety == 'good' %}badge-green
            {% elif area_context.acoustic_profile.hosting_safety == 'moderate' %}badge-amber
            {% else %}badge-red{% endif %}">{{ area_context.acoustic_profile.hosting_safety }}</span>
    </h4>
    <p><strong>{{ area_context.acoustic_profile.label }}</strong></p>
    <p>{{ area_context.acoustic_profile.summary }}</p>
    <p class="detail-meta">Airborne insulation: {{ area_context.acoustic_profile.airborne_insulation_db }} dB
        {% if area_context.acoustic_profile.airborne_insulation_db and 'below' not in area_context.acoustic_profile.summary %}
        (Part E standard: 45 dB)
        {% endif %}
    </p>
    {% if area_context.acoustic_profile.viewing_checks %}
    <h5>Viewing Checks</h5>
    <ul>
        {% for check in area_context.acoustic_profile.viewing_checks %}
        <li>{{ check }}</li>
        {% endfor %}
    </ul>
    {% endif %}
</div>
{% endif %}

{% if area_context.noise_enforcement %}
<div>
    <h4>Noise Enforcement ({{ area_context.borough }})</h4>
    <p>{{ area_context.noise_enforcement.process }}</p>
    {% if area_context.noise_enforcement.threshold_info %}
    <p class="detail-meta">{{ area_context.noise_enforcement.threshold_info }}</p>
    {% endif %}
    {% if area_context.noise_enforcement.response_time %}
    <p class="detail-meta">Response: {{ area_context.noise_enforcement.response_time }}</p>
    {% endif %}
</div>
{% endif %}
```

Use `{% if %}` guards throughout — graceful degradation when data is missing.

---

## 1E. Tests

### Data loading test

Add to an appropriate test file (e.g., `tests/test_data/test_area_context.py` — create if needed):

```python
from home_finder.data.area_context import ACOUSTIC_PROFILES, NOISE_ENFORCEMENT

def test_acoustic_profiles_loaded():
    """Acoustic profiles load from area_context.json."""
    assert isinstance(ACOUSTIC_PROFILES, dict)
    # Should have at least the core building types
    for key in ("victorian", "new_build", "warehouse", "ex_council"):
        assert key in ACOUSTIC_PROFILES
        profile = ACOUSTIC_PROFILES[key]
        assert "label" in profile
        assert "hosting_safety" in profile
        assert profile["hosting_safety"] in ("good", "moderate", "poor")

def test_noise_enforcement_loaded():
    """Noise enforcement data loads for relevant boroughs."""
    assert isinstance(NOISE_ENFORCEMENT, dict)
    assert "Hackney" in NOISE_ENFORCEMENT
    assert "process" in NOISE_ENFORCEMENT["Hackney"]
```

### Prompt tests — `tests/test_filters/test_quality_prompts.py`

1. **Update inline-snapshot for system prompt** — The `VISUAL_ANALYSIS_SYSTEM_PROMPT` will change (acoustic lines added to `<stock_types>`). Run `uv run pytest --inline-snapshot=update` to regenerate.

2. **Add test for acoustic context in evaluation prompt:**

```python
def test_evaluation_prompt_with_acoustic_context(self) -> None:
    """Evaluation prompt includes acoustic context when provided."""
    prompt = build_evaluation_prompt(
        visual_data={"listing_extraction": {"property_type": "victorian"}},
        price_pcm=1800,
        bedrooms=2,
        area_average=1900,
        acoustic_context="Building type: Victorian\nSound insulation: 35-40 dB\nHosting safety: poor",
    )
    assert "<acoustic_context>" in prompt
    assert "35-40 dB" in prompt
    assert "Hosting safety: poor" in prompt

def test_evaluation_prompt_without_acoustic_context(self) -> None:
    """Evaluation prompt omits acoustic section when not provided."""
    prompt = build_evaluation_prompt(
        visual_data={"kitchen": {"overall_quality": "modern"}},
        price_pcm=1800,
        bedrooms=1,
        area_average=1800,
    )
    assert "<acoustic_context>" not in prompt
```

### Quality filter tests — `tests/test_filters/test_quality.py`

Add a test verifying acoustic profile lookup in the analysis flow:

```python
async def test_acoustic_profile_passed_to_phase2(self, quality_filter, sample_merged_property):
    """Phase 2 receives acoustic context based on Phase 1 property type."""
    # Phase 1 response includes property_type
    visual_response = {
        # ... (use sample_visual_response pattern from existing tests)
        "listing_extraction": {"property_type": "victorian"},
    }
    # ... mock both phases, verify Phase 2 prompt contains <acoustic_context>
    calls = quality_filter._client.messages.create.call_args_list
    phase2_content = calls[1].kwargs["messages"][0]["content"]
    assert "<acoustic_context>" in phase2_content
    assert "Victorian" in phase2_content
```

Follow the existing mock pattern using `_make_two_phase_mock()` (line 676).

### Web route tests — `tests/test_web/test_routes.py`

```python
@pytest.mark.asyncio
async def test_acoustic_card_renders_with_quality_analysis(
    self, client: TestClient, storage: PropertyStorage, merged_a: MergedProperty
) -> None:
    """Acoustic card renders when property has quality analysis with property type."""
    # Save property with quality analysis that includes property_type
    # ... (follow existing test patterns)
    resp = client.get(f"/property/{merged_a.unique_id}")
    # If property has quality analysis with property_type matching acoustic data:
    # assert "Sound &amp; Hosting" in resp.text or check status only
    assert resp.status_code == 200

@pytest.mark.asyncio
async def test_acoustic_card_absent_without_quality(
    self, client: TestClient, storage: PropertyStorage, merged_a: MergedProperty
) -> None:
    """Acoustic card doesn't render when property has no quality analysis."""
    await storage.save_merged_property(merged_a)
    resp = client.get(f"/property/{merged_a.unique_id}")
    assert resp.status_code == 200
    # Should not crash, card should not appear
```

---

## Verification

1. `uv run pytest` — all existing + new tests pass
2. `uv run ruff check src tests` — no lint issues
3. `uv run mypy src` — type checking passes (new TypedDicts, new `acoustic_context` param)
4. `uv run home-finder --dry-run --max-per-scraper 2` — pipeline completes
5. `uv run home-finder --serve` — detail page shows acoustic card for analyzed properties

---

## Files Changed Summary

| File | Change |
|------|--------|
| `src/home_finder/data/area_context.json` | Add `acoustic_profiles`, `noise_enforcement` top-level keys |
| `src/home_finder/data/area_context.py` | Add `AcousticProfile`, `NoiseEnforcement` TypedDicts + exports |
| `src/home_finder/filters/quality_prompts.py` | Add acoustic lines to `<stock_types>` in system prompt; add `acoustic_context` param to `build_evaluation_prompt()` |
| `src/home_finder/filters/quality.py` | Look up acoustic profile from Phase 1 `property_type`, pass to Phase 2 |
| `src/home_finder/web/routes.py` | Look up acoustic profile + noise enforcement, add to template context |
| `src/home_finder/web/templates/detail.html` | Add "Sound & Hosting" and "Noise Enforcement" cards in Area section |
| `tests/test_data/test_area_context.py` | New — data loading tests |
| `tests/test_filters/test_quality_prompts.py` | Update system prompt snapshot; add acoustic context tests |
| `tests/test_filters/test_quality.py` | Add acoustic profile → Phase 2 integration test |
| `tests/test_web/test_routes.py` | Add acoustic card render tests |

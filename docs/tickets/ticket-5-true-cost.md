# Ticket 5: True Cost Calculator

**Depends on:** Ticket 2 (uses `area_context.json` structure)
**Blocks:** None

## Goal

Add a "true monthly cost" calculator to the detail page that goes beyond rent + council tax to include energy, water, broadband, and service charge estimates. The current detail page shows council tax by band but doesn't help Marcel understand what he'll *actually* pay per month. This ticket makes that concrete.

---

## 5A. Add cost reference data

### File: `src/home_finder/data/area_context.json`

Add new top-level keys:

```json
{
  "energy_costs_monthly": {
    "A": {"1_bed": 55, "2_bed": 70},
    "B": {"1_bed": 70, "2_bed": 90},
    "C": {"1_bed": 85, "2_bed": 110},
    "D": {"1_bed": 105, "2_bed": 135},
    "E": {"1_bed": 130, "2_bed": 165},
    "F": {"1_bed": 155, "2_bed": 195},
    "G": {"1_bed": 180, "2_bed": 230}
  },
  "water_costs_monthly": {
    "1_bed": 35,
    "2_bed": 45
  },
  "broadband_costs_monthly": {
    "fttp": 30,
    "fttc": 28,
    "cable": 32,
    "standard": 25
  },
  "service_charge_ranges": {
    "new_build": {"typical_low": 150, "typical_high": 350, "note": "Modern developments often include concierge, gym, communal gardens"},
    "purpose_built_pre2003": {"typical_low": 80, "typical_high": 200, "note": "Older purpose-built blocks — building insurance, communal maintenance"},
    "purpose_built_post2003": {"typical_low": 100, "typical_high": 250, "note": "Newer blocks with more amenities"},
    "warehouse": {"typical_low": 100, "typical_high": 250, "note": "Variable — depends on conversion quality and communal facilities"},
    "ex_council": {"typical_low": 40, "typical_high": 120, "note": "Usually lower — basic maintenance and insurance only"},
    "victorian": {"typical_low": 0, "typical_high": 50, "note": "Most Victorian conversions have no formal service charge"},
    "georgian": {"typical_low": 0, "typical_high": 50, "note": "Similar to Victorian — usually just ground rent if leasehold"},
    "edwardian": {"typical_low": 0, "typical_high": 50, "note": "Similar to Victorian conversions"}
  }
}
```

### File: `src/home_finder/data/area_context.py`

Add typed exports:

```python
class EnergyCost(TypedDict):
    """Monthly energy cost estimate by bedroom count."""
    # Keys are "1_bed", "2_bed" etc.

class ServiceChargeRange(TypedDict, total=False):
    """Service charge range for a property type."""
    typical_low: int
    typical_high: int
    note: str

ENERGY_COSTS_MONTHLY: Final[dict[str, dict[str, int]]] = _DATA.get("energy_costs_monthly", {})
WATER_COSTS_MONTHLY: Final[dict[str, int]] = _DATA.get("water_costs_monthly", {})
BROADBAND_COSTS_MONTHLY: Final[dict[str, int]] = _DATA.get("broadband_costs_monthly", {})
SERVICE_CHARGE_RANGES: Final[dict[str, ServiceChargeRange]] = _DATA.get("service_charge_ranges", {})
```

---

## 5B. Add true cost calculation utility

### New file: `src/home_finder/utils/cost_calculator.py`

```python
"""True monthly cost calculator.

Estimates total monthly living cost from rent, council tax, energy, water,
broadband, and service charge. Uses reference data from area_context.json.
"""

from __future__ import annotations

from typing import Any

from home_finder.data.area_context import (
    BROADBAND_COSTS_MONTHLY,
    ENERGY_COSTS_MONTHLY,
    SERVICE_CHARGE_RANGES,
    WATER_COSTS_MONTHLY,
)


def estimate_true_monthly_cost(
    rent_pcm: int,
    bedrooms: int,
    *,
    epc_rating: str | None = None,
    council_tax_band_c: int | None = None,
    service_charge_pcm: int | None = None,
    property_type: str | None = None,
    bills_included: bool = False,
) -> dict[str, Any]:
    """Estimate true monthly cost from available data.

    Returns a breakdown dict with:
    - rent: int
    - council_tax: int | None (estimated Band C)
    - energy: int | None (estimated from EPC rating)
    - water: int | None
    - broadband: int | None
    - service_charge: int | None (stated or estimated range)
    - service_charge_range: dict | None (if not stated, typical range for property type)
    - total_known: int (sum of known/estimated components)
    - total_with_estimates: int (sum including all estimates)
    - bills_included: bool
    - notes: list[str] (explanations of estimates)
    """
    bed_key = f"{min(bedrooms, 2)}_bed" if bedrooms >= 1 else "1_bed"
    notes: list[str] = []

    # Rent
    total_known = rent_pcm
    total_estimated = rent_pcm

    # Council tax
    council_tax = council_tax_band_c
    if council_tax:
        total_known += council_tax
        total_estimated += council_tax
    else:
        notes.append("Council tax not estimated (borough unknown)")

    # Energy
    energy: int | None = None
    if not bills_included:
        if epc_rating and epc_rating.upper() in ENERGY_COSTS_MONTHLY:
            epc_data = ENERGY_COSTS_MONTHLY[epc_rating.upper()]
            energy = epc_data.get(bed_key)
            if energy:
                total_estimated += energy
        else:
            # Default to EPC D (most common in London)
            default_energy = ENERGY_COSTS_MONTHLY.get("D", {}).get(bed_key)
            if default_energy:
                energy = default_energy
                total_estimated += default_energy
                notes.append(f"Energy estimated at EPC D (most common) — £{default_energy}/mo")
    else:
        notes.append("Bills included in rent")

    # Water
    water: int | None = None
    if not bills_included:
        water = WATER_COSTS_MONTHLY.get(bed_key)
        if water:
            total_estimated += water

    # Broadband
    broadband = BROADBAND_COSTS_MONTHLY.get("fttp")  # Assume FTTP as default
    if broadband and not bills_included:
        total_estimated += broadband

    # Service charge
    sc_range: dict[str, Any] | None = None
    if service_charge_pcm is not None:
        total_known += service_charge_pcm
        total_estimated += service_charge_pcm
    elif property_type:
        sc_range = SERVICE_CHARGE_RANGES.get(property_type)
        if sc_range:
            notes.append(
                f"Service charge unstated — typical for {property_type}: "
                f"£{sc_range.get('typical_low', '?')}-£{sc_range.get('typical_high', '?')}/mo"
            )

    return {
        "rent": rent_pcm,
        "council_tax": council_tax,
        "energy": energy,
        "water": water,
        "broadband": broadband,
        "service_charge": service_charge_pcm,
        "service_charge_range": sc_range,
        "total_known": total_known,
        "total_with_estimates": total_estimated,
        "bills_included": bills_included,
        "notes": notes,
    }
```

---

## 5C. Enhance Phase 2 value assessment with energy cost context

### File: `src/home_finder/filters/quality_prompts.py`

Currently `_format_property_context()` shows (line 237-239):
```python
if council_tax_band_c:
    true_cost = price_pcm + council_tax_band_c
    parts.append(f"\nCouncil tax (Band C est.): £{council_tax_band_c}/month")
    parts.append(f" → True monthly cost: ~£{true_cost:,}")
```

Add an `energy_estimate: int | None = None` parameter to `_format_property_context()`. Extend the true monthly cost line:

```python
if council_tax_band_c:
    true_cost = price_pcm + council_tax_band_c
    parts.append(f"\nCouncil tax (Band C est.): £{council_tax_band_c}/month")
    if energy_estimate:
        true_cost += energy_estimate
        parts.append(f" + energy ~£{energy_estimate}/mo")
    parts.append(f" → True monthly cost: ~£{true_cost:,}")
```

**Also add the parameter to `build_user_prompt()` and `build_evaluation_prompt()`** and pass through.

### File: `src/home_finder/filters/quality.py`

In `analyze_single_merged()`, after looking up council tax (line 620), estimate energy cost:

```python
from home_finder.data.area_context import ENERGY_COSTS_MONTHLY

# Estimate energy cost for prompt context
energy_estimate: int | None = None
bed_key = f"{min(prop.bedrooms, 2)}_bed" if prop.bedrooms >= 1 else "1_bed"
# Default to EPC D (we don't know EPC at this stage — Phase 1 hasn't run yet)
energy_estimate = ENERGY_COSTS_MONTHLY.get("D", {}).get(bed_key)
```

Pass `energy_estimate=energy_estimate` through to the prompt builders.

**Note:** At Phase 1 time, we don't know the EPC rating yet (Phase 1 extracts it from photos). For the prompt, using the EPC D default is acceptable — it gives Claude a ballpark. The detail page will use the actual EPC if available from Phase 1 output.

---

## 5D. Display cost breakdown on detail page

### File: `src/home_finder/web/routes.py`

In `property_detail()`, after computing fit score (line 729), compute the cost breakdown:

```python
from home_finder.utils.cost_calculator import estimate_true_monthly_cost
from home_finder.data.area_context import COUNCIL_TAX_MONTHLY

# Compute true cost breakdown
cost_breakdown = None
if prop.get("price_pcm"):
    epc_rating = None
    property_type = None
    service_charge = None
    bills_included = False

    # Extract data from quality analysis if available
    if qa:
        listing_ext = qa.listing_extraction
        if listing_ext:
            epc_rating = getattr(listing_ext, 'epc_rating', None)
            property_type = getattr(listing_ext, 'property_type', None)
            # Check for service charge in listing extraction
            service_charge_str = getattr(listing_ext, 'service_charge_pcm', None)
            if service_charge_str and isinstance(service_charge_str, (int, float)):
                service_charge = int(service_charge_str)
            bills_included = getattr(listing_ext, 'bills_included', False) or False

    # Get council tax Band C for the borough
    borough = area_context.get("borough")
    council_tax_c = COUNCIL_TAX_MONTHLY.get(borough, {}).get("C") if borough else None

    cost_breakdown = estimate_true_monthly_cost(
        rent_pcm=prop["price_pcm"],
        bedrooms=prop.get("bedrooms", 1) or 1,
        epc_rating=epc_rating,
        council_tax_band_c=council_tax_c,
        service_charge_pcm=service_charge,
        property_type=property_type,
        bills_included=bills_included,
    )
```

Add `"cost_breakdown": cost_breakdown` to the template context dict (line 731-745).

### File: `src/home_finder/web/templates/detail.html`

Add a "True Monthly Cost" card in the Overview section (near the top, after price info). Find where the price/bedrooms are displayed and add below:

```html
{% if cost_breakdown %}
<div class="cost-breakdown">
    <h4>True Monthly Cost</h4>
    <table class="cost-table">
        <tbody>
            <tr>
                <td>Rent</td>
                <td class="cost-value">&pound;{{ "{:,}".format(cost_breakdown.rent) }}</td>
            </tr>
            {% if cost_breakdown.council_tax %}
            <tr>
                <td>Council tax <small>(Band C est.)</small></td>
                <td class="cost-value">&pound;{{ cost_breakdown.council_tax }}</td>
            </tr>
            {% endif %}
            {% if cost_breakdown.energy and not cost_breakdown.bills_included %}
            <tr>
                <td>Energy <small>(est.)</small></td>
                <td class="cost-value">&pound;{{ cost_breakdown.energy }}</td>
            </tr>
            {% endif %}
            {% if cost_breakdown.water and not cost_breakdown.bills_included %}
            <tr>
                <td>Water <small>(est.)</small></td>
                <td class="cost-value">&pound;{{ cost_breakdown.water }}</td>
            </tr>
            {% endif %}
            {% if cost_breakdown.broadband and not cost_breakdown.bills_included %}
            <tr>
                <td>Broadband <small>(est. FTTP)</small></td>
                <td class="cost-value">&pound;{{ cost_breakdown.broadband }}</td>
            </tr>
            {% endif %}
            {% if cost_breakdown.service_charge %}
            <tr>
                <td>Service charge</td>
                <td class="cost-value">&pound;{{ cost_breakdown.service_charge }}</td>
            </tr>
            {% elif cost_breakdown.service_charge_range %}
            <tr>
                <td>Service charge <small>(est. range)</small></td>
                <td class="cost-value">&pound;{{ cost_breakdown.service_charge_range.typical_low }}-{{ cost_breakdown.service_charge_range.typical_high }}</td>
            </tr>
            {% endif %}
        </tbody>
        <tfoot>
            <tr class="cost-total">
                <td><strong>Total (estimated)</strong></td>
                <td class="cost-value"><strong>&pound;{{ "{:,}".format(cost_breakdown.total_with_estimates) }}</strong></td>
            </tr>
        </tfoot>
    </table>
    {% if cost_breakdown.bills_included %}
    <p class="detail-meta">Bills reported as included in rent.</p>
    {% endif %}
    {% for note in cost_breakdown.notes %}
    <p class="detail-meta">{{ note }}</p>
    {% endfor %}
</div>
{% endif %}
```

Add minimal CSS:

```css
.cost-table { width: 100%; border-collapse: collapse; margin: 0.5rem 0; }
.cost-table td { padding: 0.4rem 0; }
.cost-value { text-align: right; font-variant-numeric: tabular-nums; }
.cost-total { border-top: 2px solid var(--text, #1a1a1a); }
.cost-total td { padding-top: 0.6rem; }
```

---

## 5E. Tests

### New file: `tests/test_utils/test_cost_calculator.py`

```python
"""Tests for true monthly cost calculator."""

from home_finder.utils.cost_calculator import estimate_true_monthly_cost


class TestEstimateTrueMonthlyCost:
    """Tests for estimate_true_monthly_cost."""

    def test_basic_rent_only(self) -> None:
        """Minimal case — just rent."""
        result = estimate_true_monthly_cost(rent_pcm=1800, bedrooms=2)
        assert result["rent"] == 1800
        assert result["total_known"] == 1800
        assert result["total_with_estimates"] > 1800  # Should include defaults

    def test_with_council_tax(self) -> None:
        """Rent + council tax."""
        result = estimate_true_monthly_cost(
            rent_pcm=1800, bedrooms=2, council_tax_band_c=150
        )
        assert result["council_tax"] == 150
        assert result["total_known"] == 1950

    def test_with_known_epc(self) -> None:
        """Energy estimated from known EPC rating."""
        result = estimate_true_monthly_cost(
            rent_pcm=1800, bedrooms=1, epc_rating="C"
        )
        assert result["energy"] is not None
        assert result["energy"] > 0

    def test_with_unknown_epc_defaults_to_d(self) -> None:
        """Energy defaults to EPC D estimate when rating unknown."""
        result = estimate_true_monthly_cost(rent_pcm=1800, bedrooms=1)
        assert result["energy"] is not None
        assert any("EPC D" in note for note in result["notes"])

    def test_bills_included_skips_utilities(self) -> None:
        """Bills included — no separate energy/water/broadband."""
        result = estimate_true_monthly_cost(
            rent_pcm=1800, bedrooms=1, bills_included=True
        )
        assert any("Bills included" in note for note in result["notes"])

    def test_with_service_charge(self) -> None:
        """Known service charge added to totals."""
        result = estimate_true_monthly_cost(
            rent_pcm=1800, bedrooms=2, service_charge_pcm=200
        )
        assert result["service_charge"] == 200
        assert result["total_known"] >= 2000

    def test_without_service_charge_shows_range(self) -> None:
        """Unknown service charge shows typical range for property type."""
        result = estimate_true_monthly_cost(
            rent_pcm=1800, bedrooms=2, property_type="new_build"
        )
        assert result["service_charge"] is None
        assert result["service_charge_range"] is not None
        assert result["service_charge_range"]["typical_low"] > 0
        assert any("unstated" in note.lower() for note in result["notes"])

    def test_victorian_low_service_charge(self) -> None:
        """Victorian properties have low/no service charge range."""
        result = estimate_true_monthly_cost(
            rent_pcm=1800, bedrooms=2, property_type="victorian"
        )
        sc_range = result["service_charge_range"]
        if sc_range:
            assert sc_range["typical_low"] <= 50

    def test_full_breakdown(self) -> None:
        """Full breakdown with all data available."""
        result = estimate_true_monthly_cost(
            rent_pcm=2000,
            bedrooms=2,
            epc_rating="B",
            council_tax_band_c=150,
            service_charge_pcm=180,
            property_type="new_build",
        )
        assert result["rent"] == 2000
        assert result["council_tax"] == 150
        assert result["energy"] is not None
        assert result["water"] is not None
        assert result["broadband"] is not None
        assert result["service_charge"] == 180
        assert result["total_with_estimates"] > result["total_known"]
```

### Prompt tests — `tests/test_filters/test_quality_prompts.py`

```python
def test_true_cost_includes_energy_estimate(self) -> None:
    """True monthly cost line includes energy when provided."""
    prompt = build_user_prompt(
        price_pcm=1800,
        bedrooms=2,
        area_average=1900,
        council_tax_band_c=150,
        energy_estimate=110,
    )
    assert "energy ~£110" in prompt
    assert "True monthly cost: ~£2,060" in prompt  # 1800 + 150 + 110

def test_true_cost_without_energy(self) -> None:
    """True monthly cost line works without energy estimate."""
    prompt = build_user_prompt(
        price_pcm=1800,
        bedrooms=2,
        area_average=1900,
        council_tax_band_c=150,
    )
    assert "energy" not in prompt.lower().split("area_context")[0]  # Not in property section
    assert "True monthly cost: ~£1,950" in prompt  # 1800 + 150
```

Update existing inline-snapshot tests as needed.

### Web route tests — `tests/test_web/test_routes.py`

```python
@pytest.mark.asyncio
async def test_cost_breakdown_renders(
    self, client: TestClient, storage: PropertyStorage, merged_a: MergedProperty
) -> None:
    """Cost breakdown card renders on detail page."""
    await storage.save_merged_property(merged_a)
    resp = client.get(f"/property/{merged_a.unique_id}")
    assert resp.status_code == 200
    assert "True Monthly Cost" in resp.text

@pytest.mark.asyncio
async def test_cost_breakdown_shows_rent(
    self, client: TestClient, storage: PropertyStorage, merged_a: MergedProperty
) -> None:
    """Cost breakdown includes rent amount."""
    await storage.save_merged_property(merged_a)
    resp = client.get(f"/property/{merged_a.unique_id}")
    # merged_a has price_pcm=1900
    assert "1,900" in resp.text
```

### Data loading test

```python
from home_finder.data.area_context import (
    ENERGY_COSTS_MONTHLY,
    WATER_COSTS_MONTHLY,
    BROADBAND_COSTS_MONTHLY,
    SERVICE_CHARGE_RANGES,
)


def test_energy_costs_loaded():
    """Energy costs load with EPC ratings A-G."""
    assert isinstance(ENERGY_COSTS_MONTHLY, dict)
    for rating in ("A", "B", "C", "D", "E", "F", "G"):
        assert rating in ENERGY_COSTS_MONTHLY
        assert "1_bed" in ENERGY_COSTS_MONTHLY[rating]
        assert "2_bed" in ENERGY_COSTS_MONTHLY[rating]


def test_water_costs_loaded():
    """Water costs load for bedroom counts."""
    assert "1_bed" in WATER_COSTS_MONTHLY
    assert "2_bed" in WATER_COSTS_MONTHLY


def test_service_charge_ranges_loaded():
    """Service charge ranges load for property types."""
    assert "new_build" in SERVICE_CHARGE_RANGES
    assert "typical_low" in SERVICE_CHARGE_RANGES["new_build"]
    assert SERVICE_CHARGE_RANGES["new_build"]["typical_low"] < SERVICE_CHARGE_RANGES["new_build"]["typical_high"]
```

---

## Verification

1. `uv run pytest` — all existing + new tests pass
2. `uv run ruff check src tests` — no lint issues
3. `uv run mypy src` — type checking passes
4. `uv run home-finder --dry-run --max-per-scraper 2` — pipeline completes
5. `uv run home-finder --serve` — detail page shows cost breakdown card

---

## Files Changed Summary

| File | Change |
|------|--------|
| `src/home_finder/data/area_context.json` | Add `energy_costs_monthly`, `water_costs_monthly`, `broadband_costs_monthly`, `service_charge_ranges` |
| `src/home_finder/data/area_context.py` | Add `ServiceChargeRange` TypedDict + exports for all cost data |
| `src/home_finder/utils/cost_calculator.py` | **New** — `estimate_true_monthly_cost()` function |
| `src/home_finder/filters/quality_prompts.py` | Add `energy_estimate` param; extend true monthly cost line |
| `src/home_finder/filters/quality.py` | Look up default energy estimate, pass to prompts |
| `src/home_finder/web/routes.py` | Compute cost breakdown, pass to template |
| `src/home_finder/web/templates/detail.html` | Add "True Monthly Cost" card |
| `tests/test_utils/test_cost_calculator.py` | **New** — calculator tests |
| `tests/test_filters/test_quality_prompts.py` | Energy estimate prompt tests |
| `tests/test_web/test_routes.py` | Cost breakdown render tests |
| `tests/test_data/test_area_context.py` | Cost data loading tests |

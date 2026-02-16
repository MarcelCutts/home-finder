"""True monthly cost calculator for rental properties."""

from __future__ import annotations

from typing import Any

from home_finder.data.area_context import (
    BROADBAND_COSTS_MONTHLY,
    COUNCIL_TAX_MONTHLY,
    ENERGY_COSTS_MONTHLY,
    SERVICE_CHARGE_RANGES,
    WATER_COSTS_MONTHLY,
)


def estimate_true_monthly_cost(
    *,
    rent_pcm: int,
    borough: str | None = None,
    council_tax_band: str | None = None,
    epc_rating: str | None = None,
    bedrooms: int = 1,
    broadband_type: str | None = None,
    property_type: str | None = None,
    service_charge_pcm: int | None = None,
    bills_included: bool = False,
) -> dict[str, Any]:
    """Estimate the true monthly cost of renting a property.

    Returns a breakdown dict with individual line items and a total.
    All costs in GBP/month.

    Args:
        rent_pcm: Monthly rent.
        borough: Council borough name for tax lookup.
        council_tax_band: Council tax band (A-H), or None/unknown.
        epc_rating: EPC energy rating (A-G), or None/unknown.
        bedrooms: Number of bedrooms (1 or 2 for cost lookup).
        broadband_type: Broadband type (fttp/fttc/cable/standard).
        property_type: Property type for service charge estimate.
        service_charge_pcm: Known service charge (overrides estimate).
        bills_included: Whether bills are included in rent.
    """
    bed_key = f"{min(max(bedrooms, 1), 2)}_bed"
    items: list[dict[str, Any]] = []
    total = rent_pcm

    items.append({"label": "Rent", "amount": rent_pcm, "note": None})

    # Council tax
    council_tax: int | None = None
    if borough and council_tax_band and council_tax_band.upper() not in ("", "UNKNOWN"):
        band = council_tax_band.upper()
        borough_rates = COUNCIL_TAX_MONTHLY.get(borough)
        if borough_rates:
            council_tax = borough_rates.get(band)
    if council_tax is not None and council_tax_band:
        items.append(
            {
                "label": "Council tax",
                "amount": council_tax,
                "note": f"Band {council_tax_band.upper()}, {borough}",
            }
        )
        total += council_tax

    # Energy
    energy: int | None = None
    if not bills_included:
        has_known_epc = bool(epc_rating and epc_rating.upper() not in ("", "UNKNOWN"))
        rating = epc_rating.upper() if epc_rating and has_known_epc else "D"
        energy_band = ENERGY_COSTS_MONTHLY.get(rating)
        if energy_band:
            energy = energy_band.get(bed_key)
        if energy is not None:
            note = f"EPC {rating} est." if has_known_epc else "EPC D default"
            items.append({"label": "Energy", "amount": energy, "note": note})
            total += energy

    # Water
    water: int | None = None
    if not bills_included:
        water = WATER_COSTS_MONTHLY.get(bed_key)
        if water is not None:
            items.append({"label": "Water", "amount": water, "note": None})
            total += water

    # Broadband
    broadband: int | None = None
    if not bills_included:
        bb_type = broadband_type if broadband_type and broadband_type != "unknown" else "fttp"
        broadband = BROADBAND_COSTS_MONTHLY.get(bb_type)
        if broadband is not None:
            items.append({"label": "Broadband", "amount": broadband, "note": None})
            total += broadband

    # Service charge
    if service_charge_pcm is not None:
        items.append(
            {
                "label": "Service charge",
                "amount": service_charge_pcm,
                "note": "from listing",
            }
        )
        total += service_charge_pcm
    elif property_type and property_type != "unknown":
        sc_range = SERVICE_CHARGE_RANGES.get(property_type)
        if sc_range:
            items.append(
                {
                    "label": "Service charge",
                    "amount": None,
                    "note": (
                        f"~\u00a3{sc_range['typical_low']}-{sc_range['typical_high']}"
                        f"/mo ({property_type.replace('_', ' ')})"
                    ),
                    "range_low": sc_range["typical_low"],
                    "range_high": sc_range["typical_high"],
                }
            )

    if bills_included:
        items.append(
            {
                "label": "Bills",
                "amount": 0,
                "note": "included in rent",
            }
        )

    # Compute total range when service charge is an estimate range
    total_high: int | None = None
    sc_item = next(
        (i for i in items if i["label"] == "Service charge" and i.get("range_low")),
        None,
    )
    if sc_item:
        total_high = total + sc_item["range_high"]
        # Use the low end as a conservative total
        total += sc_item["range_low"]

    # Compute per-item percentage of total (for proportional bar display)
    for item in items:
        amt = item["amount"] or 0
        item["pct"] = round(amt / total * 100) if total > 0 else 0

    # Extras beyond rent (the "hidden cost" delta)
    extras = total - rent_pcm
    extras_high = (total_high - rent_pcm) if total_high else None

    return {
        "line_items": items,
        "total": total,
        "total_high": total_high,
        "extras": extras,
        "extras_high": extras_high,
        "is_estimate": True,
        "bills_included": bills_included,
    }

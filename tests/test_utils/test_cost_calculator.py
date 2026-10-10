"""Advert evidence cannot silently become a complete household budget."""

from decimal import Decimal

import pytest

from home_finder.models import ListingTextEvidence, PropertySource
from home_finder.utils.cost_calculator import build_monthly_budget
from home_finder.utils.listing_facts import listing_band


def evidence(description: str, other: str | None = None) -> dict:
    result = {PropertySource.RIGHTMOVE: ListingTextEvidence(description=description)}
    if other is not None:
        result[PropertySource.ZOOPLA] = ListingTextEvidence(features=(other,))
    return result


def test_unknown_costs_are_unpriced_not_zero_or_epc_d() -> None:
    budget = build_monthly_budget(rent_pcm=1800, evidence={})
    assert budget["subtotal_low"] == budget["subtotal_high"] == 1800
    assert budget["complete"] is False
    assert budget["epc_rating"] == budget["council_tax_band"] == "unknown"
    assert all(item["state"] == "unknown" and item["low"] is None for item in budget["line_items"])
    assert "deposits" in budget["assumptions"].lower()


def test_inclusive_bills_wording_is_retained_without_guessing_named_coverage() -> None:
    budget = build_monthly_budget(rent_pcm=1800, evidence=evidence("Inclusive of all bills."))
    assert budget["general_bills_claim"] is True
    assert all(item["state"] == "unknown" for item in budget["line_items"])


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "Gas and electricity included in rent. Water excluded.",
            ["unknown", "included", "included", "excluded", "unknown"],
        ),
        (
            "Rent includes water and broadband. Council tax is not included.",
            ["excluded", "unknown", "unknown", "included", "included"],
        ),
        ("All bills included.", ["unknown"] * 5),
        ("Bills not included.", ["unknown"] * 5),
        ("Water included? Ask the agent.", ["unknown"] * 5),
        ("If water included, the rent may change.", ["unknown"] * 5),
        ("Estimated water included.", ["unknown"] * 5),
        ("Not water included.", ["unknown"] * 5),
        ("Water included.", ["unknown"] * 5),
        ("Water included in the optional bills package.", ["unknown"] * 5),
        ("Water included in service charge, payable separately.", ["unknown"] * 5),
        ("Water included for an extra £50 per month.", ["unknown"] * 5),
        ("Rent includes water for an extra £50 per month.", ["unknown"] * 5),
    ],
)
def test_named_bill_coverage_and_ambiguous_wording(text: str, expected: list[str]) -> None:
    budget = build_monthly_budget(rent_pcm=1800, evidence=evidence(text))
    assert [item["state"] for item in budget["line_items"]] == expected
    assert budget["subtotal_low"] == 1800
    assert all(item["low"] is None for item in budget["line_items"])


def test_explicit_monthly_range_is_separate_and_price_refresh_is_deterministic() -> None:
    source_text = evidence(
        "Water: £30.50\u2013£40.50 per month payable separately. "
        "Broadband: £25 pcm in addition to rent."
    )
    budget = build_monthly_budget(rent_pcm=1800, evidence=source_text)
    assert budget["subtotal_low"] == Decimal("1855.50")
    assert budget["subtotal_high"] == Decimal("1865.50")
    refreshed = build_monthly_budget(rent_pcm=1700, evidence=source_text)
    assert refreshed["subtotal_low"] == Decimal("1755.50")
    assert refreshed["line_items"] == budget["line_items"]
    assert "Rightmove:" in budget["line_items"][3]["evidence"][0]


@pytest.mark.parametrize(
    "text",
    [
        "Water: £30 per week payable separately.",
        "Water: £30 per month included.",
        "Water: £50-£30 pcm payable separately.",
        "Water: £30 pcm payable separately?",
        "If water: £30 pcm payable separately, ask for a discount.",
        "Not water: £30 pcm payable separately.",
        "Water: £30 pcm payable separately unless already paid in rent.",
    ],
)
def test_uncertain_nonmonthly_or_invalid_amount_not_added(text: str) -> None:
    budget = build_monthly_budget(rent_pcm=1800, evidence=evidence(text))
    assert budget["subtotal_low"] == 1800


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("Water included in rent", "Water excluded"),
        ("Water included in rent", "Water: £30 pcm payable separately"),
        ("Water: £30 pcm payable separately", "Water: £40 pcm payable separately"),
    ],
)
def test_conflicting_sources_remain_unpriced(first: str, second: str) -> None:
    budget = build_monthly_budget(rent_pcm=1800, evidence=evidence(first, second))
    water = budget["line_items"][3]
    assert water["state"] == "conflicting"
    assert water["low"] is None
    assert len(water["evidence"]) == 2
    assert budget["subtotal_low"] == 1800


def test_no_service_charge_or_undated_reference_numbers_enter_budget(monkeypatch) -> None:
    from home_finder.data import area_context

    monkeypatch.setitem(area_context.COUNCIL_TAX_MONTHLY, "Hackney", {"C": 9999})
    monkeypatch.setitem(area_context.ENERGY_COSTS_MONTHLY, "D", {"1_bed": 9999})
    budget = build_monthly_budget(
        rent_pcm=1800,
        evidence=evidence(
            "New build, Hackney. EPC D. Council tax band C. Service charge £450 pcm."
        ),
        earlier_service_charge=450,
    )
    assert budget["subtotal_low"] == budget["subtotal_high"] == 1800
    assert budget["service_charge_review"] is True
    assert budget["epc_rating"] == "D"
    assert budget["council_tax_band"] == "C"


@pytest.mark.parametrize(
    ("text", "epc", "tax"),
    [
        ("EPC B. Council tax band C.", "B", "C"),
        ("<p>EPC rating: F</p>", "F", "unknown"),
        ("Energy Rating: C", "C", "unknown"),
        ("EPC Rating: D (potential B)", "D", "unknown"),
        ("EPC rated C", "C", "unknown"),
        ("EPC rating is C", "C", "unknown"),
        ("EPC rating: C. Budget friendly flat.", "C", "unknown"),
        ("EPC rating C. Target move in date 1 June.", "C", "unknown"),
        ("Council tax C", "unknown", "C"),
        ("Council Tax: Band C", "unknown", "C"),
        ("Council tax band: D. Budget-conscious tenants welcome.", "unknown", "D"),
        ("EPC B\nEPC D", "unknown", "unknown"),
        ("EPC B/C", "unknown", "unknown"),
        ("Council tax band C or D", "unknown", "unknown"),
        ("Council tax band C or possibly D", "unknown", "unknown"),
        ("Council tax band C subject to confirmation", "unknown", "unknown"),
        ("Council tax band C?", "unknown", "unknown"),
        ("EPC - a copy is available on request", "unknown", "unknown"),
        ("Council tax - a tenant responsibility", "unknown", "unknown"),
        ("Potential EPC B", "unknown", "unknown"),
        ("EPC D estimated", "unknown", "unknown"),
        ("Not EPC B", "unknown", "unknown"),
        ("Council tax Band C budget assumption", "unknown", "unknown"),
        ("No listing text", "unknown", "unknown"),
    ],
)
def test_source_bands_require_explicit_unambiguous_support(text: str, epc: str, tax: str) -> None:
    assert listing_band("epc", text, None) == epc
    assert listing_band("council_tax", text, None) == tax

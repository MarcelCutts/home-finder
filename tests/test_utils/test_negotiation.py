"""Observation age and unsupported research cannot justify an offer level."""

import pytest

from home_finder.data.area_context import build_property_context, format_rent_trend
from home_finder.data.rental_guidance import GUIDANCE
from home_finder.utils.negotiation import generate_negotiation_brief


@pytest.mark.parametrize("days", [0, 3, 14, 28, 200])
def test_collector_age_never_claims_time_on_market_or_a_discount(days: int) -> None:
    brief = generate_negotiation_brief(days_observed=days, price_history=[])
    text = " ".join(signal.text for signal in brief["signals"]) + brief["suggested_approach"]
    assert "First observed by Home Finder" in text
    assert "original listing date" in text
    assert "%" not in text
    assert "stale" not in text
    assert "strength" not in brief
    assert "No rent changes recorded" in text


def test_price_history_reports_changes_without_inferring_negotiability() -> None:
    brief = generate_negotiation_brief(
        days_observed=90,
        price_history=[{"change_amount": -100}, {"change_amount": 200}, {"change_amount": -50}],
    )
    assert "2 rent decrease(s) and 1 increase(s)" in brief["signals"][1].text
    assert "do not establish negotiating room" in brief["signals"][1].text


@pytest.mark.parametrize(
    ("number", "label", "expected"),
    [
        (-3.0, "rising strongly", "-3% YoY (falling)"),
        (5.0, "falling", "+5% YoY (rising)"),
        (0.0, "rising", "+0% YoY (unchanged)"),
    ],
)
def test_numeric_trend_sign_controls_wording(number: float, label: str, expected: str) -> None:
    text = format_rent_trend({"yoy_pct": number, "direction": label})
    assert text.startswith(expected)
    assert "unverified" in text
    assert "+-" not in text


def test_invalid_and_undated_references_do_not_become_current_bills() -> None:
    assert "Unknown" in format_rent_trend({"yoy_pct": float("nan"), "direction": "rising"})
    context = build_property_context("E8 2LX", 1)
    assert context.council_tax_band_c is None
    assert context.energy_estimate is None
    assert "Undated" in context.area_overview
    assert "unverified" in context.rent_trend


def test_rental_guidance_has_date_scope_and_primary_sources() -> None:
    assert GUIDANCE["effective_from"] == "1 May 2026"
    assert GUIDANCE["checked_on"] == "10 October 2026"
    assert "England" in GUIDANCE["scope"]
    assert "assured periodic" in GUIDANCE["scope"]
    assert "must not invite or accept rent above" in GUIDANCE["text"]
    assert GUIDANCE["bidding_url"].startswith("https://www.gov.uk/")

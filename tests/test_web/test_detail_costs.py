"""The detail page's costs section ("What it costs"), rebuilt from current text and rent."""

from datetime import UTC, datetime, timedelta

import pytest
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient

from home_finder.db.storage import PropertyStorage
from home_finder.models import (
    ListingExtraction,
    ListingTextEvidence,
    MergedProperty,
    Property,
    PropertyQualityAnalysis,
    PropertyType,
)


def _budget(client: TestClient, unique_id: str) -> str:
    """The cost card's text with whitespace collapsed."""
    soup = BeautifulSoup(client.get(f"/property/{unique_id}").text, "html.parser")
    return " ".join(soup.select_one(".cost-breakdown-card").get_text(" ").split())


async def test_earlier_bills_claim_remains_visible_as_unverified(
    client, storage, merged_a, base_analysis
) -> None:
    await storage.save_merged_property(merged_a)
    await storage.save_quality_analysis(
        merged_a.unique_id,
        base_analysis.model_copy(
            update={"listing_extraction": ListingExtraction(bills_included="yes")}
        ),
    )
    detail = BeautifulSoup(client.get(f"/property/{merged_a.unique_id}").text, "html.parser")
    card = detail.select_one(".cost-breakdown-card").get_text(" ", strip=True)
    assert "Earlier analysis records bills as included (unverified)" in card
    assert "In rent" not in card


@pytest.mark.parametrize(
    "description",
    [
        "Water included in the optional bills package.",
        "Water included in service charge, payable separately.",
        "Water included for an extra £50 per month.",
    ],
)
async def test_paid_package_is_not_shown_as_included_in_rent(
    client, storage, merged_a, description
) -> None:
    await storage.save_merged_property(merged_a)
    await storage.update_source_text(
        merged_a.unique_id,
        ListingTextEvidence.observed(observed_at=datetime.now(UTC), description=description),
    )
    detail = BeautifulSoup(client.get(f"/property/{merged_a.unique_id}").text, "html.parser")
    card = detail.select_one(".cost-breakdown-card")
    assert "In rent" not in card.get_text()
    assert "Included in rent" not in card.get_text()
    assert "water" in card.select_one(".money-total-note").get_text().split("Not priced:")[1]
    water = next(row for row in card.select(".cost-bills tr") if "Water" in row.get_text())
    assert "Coverage and amount unknown" in water.get_text()


async def test_current_text_and_rent_rebuild_budget_without_reanalysis(
    client: TestClient,
    storage: PropertyStorage,
    merged_a: MergedProperty,
    base_analysis: PropertyQualityAnalysis,
) -> None:
    await storage.save_merged_property(merged_a)
    legacy = base_analysis.model_copy(
        update={
            "listing_extraction": ListingExtraction(
                epc_rating="D",
                council_tax_band="C",
                service_charge_pcm=450,
            )
        }
    )
    await storage.save_quality_analysis(merged_a.unique_id, legacy)
    before = await storage.get_quality_analysis(merged_a.unique_id)
    stamp = datetime.now(UTC)
    await storage.update_source_text(
        merged_a.unique_id,
        ListingTextEvidence.observed(
            observed_at=stamp,
            description="EPC B. Council tax band D. Water: £30 pcm payable separately.",
            features=["Gas and electricity included in rent"],
        ),
    )
    budget = _budget(client, merged_a.unique_id)
    assert "Each month £1,930 Rent, water £30 as advertised." in budget
    assert "Included in rent: gas, electricity. Not priced: council tax, broadband." in budget
    assert "Listing EPC: B (an earlier AI reading said D)" in budget
    assert "Council-tax band: D (an earlier AI reading said C)" in budget
    assert "earlier analysis: £450/mo, unverified" in budget
    assert "From 1 May 2026" in budget
    assert "private assured periodic tenancies" in budget
    assert "England fee guidance" in budget
    assert await storage.get_quality_analysis(merged_a.unique_id) == before

    await storage.detect_and_record_price_change(merged_a.unique_id, 1800)
    assert "Each month £1,830" in _budget(client, merged_a.unique_id)
    await storage.update_source_text(
        merged_a.unique_id,
        ListingTextEvidence.observed(
            observed_at=stamp + timedelta(seconds=1),
            description="",
            features=[],
            origin="correction",
        ),
    )
    cleared = _budget(client, merged_a.unique_id)
    assert "Each month £1,800" in cleared
    assert (
        "Listing EPC: not in the saved advert (an earlier AI reading said D, unconfirmed)"
        in cleared
    )
    assert "Water: £30" not in cleared
    after = await storage.get_quality_analysis(merged_a.unique_id)
    # Existing price updates recompute ValueAnalysis deterministically; the
    # saved extraction/visual observations and retained legacy claims survive.
    assert after.value != before.value
    assert after.model_dump(exclude={"value"}) == before.model_dump(exclude={"value"})


async def test_unanalysed_property_still_has_budget(
    client: TestClient,
    storage: PropertyStorage,
    merged_a: MergedProperty,
) -> None:
    await storage.save_merged_property(merged_a)
    response = client.get(f"/property/{merged_a.unique_id}")
    assert "What it costs" in response.text
    assert "Each month" in response.text
    assert "Listing EPC: not stated. Council-tax band: not stated." in response.text


async def test_earlier_reading_is_the_only_claim_without_saved_advert_text(
    client: TestClient,
    storage: PropertyStorage,
    merged_a: MergedProperty,
    prop_a: Property,
    base_analysis: PropertyQualityAnalysis,
) -> None:
    analysis = base_analysis.model_copy(
        update={
            "listing_extraction": ListingExtraction(
                property_type=PropertyType.VICTORIAN,
                epc_rating="D",
                council_tax_band="C",
            ),
        }
    )
    await storage.save_merged_property(merged_a)
    await storage.save_quality_analysis(prop_a.unique_id, analysis)

    resp = client.get(f"/property/{merged_a.unique_id}")
    assert resp.status_code == 200
    assert "What it costs" in resp.text
    assert "Each month" in resp.text
    assert (
        "Council-tax band: not in the saved advert (an earlier AI reading said C, unconfirmed)"
        in resp.text
    )
    assert "Earlier analysis claims" not in resp.text
    assert "True Monthly Cost" not in resp.text


async def test_month_total_is_the_rent_when_nothing_else_is_priced(
    client: TestClient,
    storage: PropertyStorage,
    merged_a: MergedProperty,
    prop_a: Property,
    base_analysis: PropertyQualityAnalysis,
) -> None:
    analysis = base_analysis.model_copy(update={"listing_extraction": ListingExtraction()})
    await storage.save_merged_property(merged_a)
    await storage.save_quality_analysis(prop_a.unique_id, analysis)

    assert "Each month £1,900 Rent as advertised." in _budget(client, merged_a.unique_id)

"""Dashboard page (GET /): the property grid, its cards, the map data and the page shell."""

import json
import re

import pytest
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient

from home_finder.db.storage import PropertyStorage
from home_finder.models import (
    FlooringNoiseAnalysis,
    MergedProperty,
    Property,
    PropertyQualityAnalysis,
    SpaceAnalysis,
    ValueAnalysis,
)


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def _map_markers(html: str) -> list[dict]:
    return json.loads(_soup(html).select_one("#properties-map-data").string)


class TestDashboardPage:
    def test_empty_dashboard(self, client: TestClient) -> None:
        resp = client.get("/")
        assert resp.status_code == 200
        assert "No properties found" in resp.text

    async def test_saved_property_is_listed(
        self, client: TestClient, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)
        resp = client.get("/")
        assert resp.status_code == 200
        assert "1 bed in E8" in resp.text

    def test_area_options_come_from_the_configured_search_areas(self, client: TestClient) -> None:
        options = _soup(client.get("/").text).select('select[name="area"] option')
        assert [option["value"] for option in options] == ["", "E8", "E3", "N16"]

    def test_htmx_request_returns_partial(self, client: TestClient) -> None:
        resp = client.get("/", headers={"HX-Request": "true"})
        assert resp.status_code == 200
        assert "<!DOCTYPE html>" not in resp.text

    def test_non_htmx_returns_full_page(self, client: TestClient) -> None:
        resp = client.get("/")
        assert resp.status_code == 200
        assert "<!DOCTYPE html>" in resp.text

    async def test_map_data_has_a_marker_for_each_located_property(
        self, client: TestClient, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)
        markers = _map_markers(client.get("/").text)
        assert [(m["id"], m["lat"], m["lon"]) for m in markers] == [
            (merged_a.unique_id, 51.5465, -0.0553)
        ]


class TestPropertyCards:
    """Cards in the dashboard grid."""

    async def test_commute_pill_rendered(
        self,
        client: TestClient,
        storage: PropertyStorage,
        merged_a: MergedProperty,
    ) -> None:
        await storage.save_merged_property(merged_a, commute_minutes=25)
        resp = client.get("/")
        assert resp.status_code == 200
        pill = _soup(resp.text).select_one(".commute-pill")
        assert pill.get_text() == "25 min · estimate"
        assert pill.select_one(".figure").get_text() == "25 min"
        assert "journey date/time and location precision are unverified" in pill["title"]

    async def test_quick_actions_are_named_for_assistive_tech(
        self, client: TestClient, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)
        soup = _soup(client.get("/").text)
        names = [btn["aria-label"] for btn in soup.select(".card-quick-actions button")]
        assert names == ["Mark interested", "Archive"]

    async def test_traffic_noise_lowlight_reads_as_road_or_rail_noise(
        self,
        client: TestClient,
        storage: PropertyStorage,
        prop_a: Property,
        merged_a: MergedProperty,
        base_analysis: PropertyQualityAnalysis,
    ) -> None:
        """The stored tag also covers railways; only its displayed wording changes."""
        await storage.save_merged_property(merged_a)
        await storage.save_quality_analysis(
            prop_a.unique_id,
            base_analysis.model_copy(update={"lowlights": ["Potential traffic noise"]}),
        )
        dashboard = _soup(client.get("/").text)
        card_tags = [chip.get_text() for chip in dashboard.select(".highlight-negative")]
        assert card_tags == ["Potential road or rail noise"]
        tag_input = dashboard.select_one('input[name="tag"][value="Potential traffic noise"]')
        assert tag_input.parent.get_text(strip=True) == "Potential road or rail noise"

        filtered = _soup(client.get("/", params={"tag": "Potential traffic noise"}).text)
        assert filtered.select(".property-card")
        chip = filtered.select_one(".filter-chip")
        assert chip.get_text(" ", strip=True).startswith("Potential road or rail noise")

        detail = client.get(f"/property/{merged_a.unique_id}").text
        assert "Potential road or rail noise" in detail
        assert "Potential traffic noise" not in detail

    @pytest.mark.parametrize("minutes", [None, 0])
    async def test_unknown_commute_is_not_zero_minute_success(
        self, client: TestClient, storage: PropertyStorage, merged_a: MergedProperty, minutes
    ) -> None:
        await storage.save_merged_property(merged_a, commute_minutes=minutes)
        resp = client.get("/")
        assert "Commute unknown" in resp.text
        assert ">0 min" not in resp.text

    async def test_fit_tier_pill_rendered(
        self,
        client: TestClient,
        storage: PropertyStorage,
        prop_a: Property,
        merged_a: MergedProperty,
        base_analysis: PropertyQualityAnalysis,
    ) -> None:
        await storage.save_merged_property(merged_a)
        await storage.save_quality_analysis(prop_a.unique_id, base_analysis)
        resp = client.get("/")
        assert resp.status_code == 200
        assert "fit-tier-pill" in resp.text

    async def test_value_badge_rendered(
        self,
        client: TestClient,
        storage: PropertyStorage,
        prop_a: Property,
        merged_a: MergedProperty,
        base_analysis: PropertyQualityAnalysis,
    ) -> None:
        analysis = base_analysis.model_copy(
            update={
                "value": ValueAnalysis(
                    area_average=2200,
                    difference=-300,
                    rating="excellent",
                    note="Below avg",
                ),
            }
        )
        await storage.save_merged_property(merged_a)
        await storage.save_quality_analysis(prop_a.unique_id, analysis)
        resp = client.get("/")
        assert resp.status_code == 200
        assert "Value: excellent (unverified)" in resp.text

    async def test_studio_card_is_labelled_studio(
        self,
        client: TestClient,
        storage: PropertyStorage,
        merged_studio: MergedProperty,
    ) -> None:
        await storage.save_merged_property(merged_studio)
        card = _soup(client.get("/").text).select_one(".property-card")
        assert [badge.get_text(strip=True) for badge in card.select(".badge-neutral")] == ["Studio"]

    async def test_price_drop_badge_on_card(
        self, client: TestClient, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)
        await storage.detect_and_record_price_change(merged_a.unique_id, 1750)
        badge = _soup(client.get("/").text).select_one(".price-change-badge")
        assert badge.get_text(" ", strip=True) == "Dropped £150"

    async def test_off_market_card_is_marked_when_shown(
        self, client: TestClient, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)
        await storage.mark_off_market(merged_a.unique_id)

        resp = client.get("/?off_market=show")
        assert resp.status_code == 200
        assert "OFF MARKET" in resp.text
        assert "property-card-off-market" in resp.text
        assert [marker["is_off_market"] for marker in _map_markers(resp.text)] == [True]


class TestResultCount:
    """The result count is announced to assistive technology."""

    async def test_count_announced_with_results(
        self,
        client: TestClient,
        storage: PropertyStorage,
        merged_a: MergedProperty,
    ) -> None:
        await storage.save_merged_property(merged_a)
        resp = client.get("/")
        assert resp.status_code == 200
        assert 'aria-live="polite"' in resp.text
        assert "1 property found" in resp.text

    def test_zero_results_announced(self, client: TestClient) -> None:
        resp = client.get("/")
        assert resp.status_code == 200
        assert 'aria-live="polite"' in resp.text
        assert "0 properties found" in resp.text


class TestPageShell:
    """HTMX error targets and live regions from base.html and the filter form."""

    def test_filter_form_has_response_targets(self, client: TestClient) -> None:
        resp = client.get("/")
        assert resp.status_code == 200
        assert 'hx-target-4*="#error-toast"' in resp.text

    def test_body_has_hx_ext(self, client: TestClient) -> None:
        resp = client.get("/")
        assert resp.status_code == 200
        assert 'hx-ext="response-targets,morph"' in resp.text

    def test_error_toast_container_exists(self, client: TestClient) -> None:
        resp = client.get("/")
        assert resp.status_code == 200
        assert 'id="error-toast"' in resp.text
        assert 'role="alert"' in resp.text

    def test_sr_announcer_exists(self, client: TestClient) -> None:
        announcer = _soup(client.get("/").text).select_one("#sr-announcer")
        assert announcer["aria-live"] == "polite"


async def test_figures_are_marked_and_source_badges_carry_no_inline_colour(
    client, storage, prop_a, merged_a, base_analysis
) -> None:
    await storage.save_merged_property(merged_a, commute_minutes=22)
    await storage.save_quality_analysis(prop_a.unique_id, base_analysis)

    dashboard = _soup(client.get("/").text)
    assert dashboard.select_one("#nav-count .figure").get_text() == "1"
    assert re.fullmatch(r"\d/\d", dashboard.select_one(".card-must .figure").get_text())
    badges = dashboard.select(".source-badge")
    assert badges and all(not badge.has_attr("style") for badge in badges)


async def test_card_claims_no_more_than_the_brief(
    client, storage, prop_a, merged_a, base_analysis
) -> None:
    """A spacious, double-glazed home whose living room was never measured.

    The scorer's hosting icon would be good and the analysis tagged the living room
    spacious; the brief says room to host needs checking, so the card and the listing
    page say no more than that.
    """
    analysis = base_analysis.model_copy(
        update={
            "space": SpaceAnalysis(is_spacious_enough=True),
            "flooring_noise": FlooringNoiseAnalysis(has_double_glazing="yes"),
            "highlights": ["Spacious living room", "Gas hob"],
        }
    )
    await storage.save_merged_property(merged_a, commute_minutes=22)
    await storage.save_quality_analysis(prop_a.unique_id, analysis)

    card = _soup(client.get("/").text).select_one("article.property-card")
    assert "room to host also needs checking" in card.select_one(".card-lead").get_text()
    icons = {icon["title"]: icon["data-state"] for icon in card.select(".lifestyle-icon")}
    assert icons["Room to host: living-room size not shown"] == "neutral"
    assert icons["A kitchen that works: Gas hob"] == "good"
    assert [chip.get_text() for chip in card.select(".highlight-positive")] == ["Gas hob"]
    # The card fetched for a map marker is held to the brief the same way.
    marker_card = _soup(client.get(f"/property/{merged_a.unique_id}/card").text)
    assert [chip.get_text() for chip in marker_card.select(".highlight-positive")] == ["Gas hob"]

    listing = _soup(client.get(f"/property/{merged_a.unique_id}").text)
    assert [li.get_text() for li in listing.select(".verdict-fits li")] == ["Gas hob"]


@pytest.mark.parametrize(("max_price", "named"), [(2500, True), (1800, False)])
async def test_card_names_the_fit_band_only_inside_the_hard_limits(
    client, storage, prop_a, merged_a, base_analysis, max_price: int, named: bool
) -> None:
    """The score ignores rent, bedrooms and commute, so over a limit it stays a bare number."""
    await storage.save_merged_property(merged_a, commute_minutes=22)
    await storage.save_quality_analysis(prop_a.unique_id, base_analysis)
    client.app.state.settings = client.app.state.settings.model_copy(
        update={"max_price": max_price}
    )
    card = _soup(client.get("/").text).select_one("article.property-card")

    assert ("card-lead-broken" in card.select_one(".card-lead")["class"]) is not named
    assert card.select_one(".fit-badge").get_text(strip=True).isdigit()
    assert (card.select_one(".fit-tier-pill") is not None) is named
    assert (card.select_one(".fit-popover-tier") is not None) is named


@pytest.mark.parametrize(
    ("max_price", "assessed", "flagged"),
    [
        (1800, True, True),
        (2500, True, False),
        # No score, so the popup has no band to leave out.
        (1800, False, False),
    ],
)
async def test_map_marker_says_when_its_popup_must_not_name_a_fit_band(
    client, storage, prop_a, merged_a, base_analysis, max_price: int, assessed: bool, flagged: bool
) -> None:
    """The popup builds its band from the score; the marker tells it about a broken limit."""
    await storage.save_merged_property(merged_a, commute_minutes=22)
    if assessed:
        await storage.save_quality_analysis(prop_a.unique_id, base_analysis)
    client.app.state.settings = client.app.state.settings.model_copy(
        update={"max_price": max_price}
    )
    (marker,) = _map_markers(client.get("/").text)
    assert (marker["fit_score"] is not None) is assessed
    assert marker["breaks_limit"] is flagged

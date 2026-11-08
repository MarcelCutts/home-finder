"""Dashboard filters and sorting: query parameters, chips, the Filters dialog and /count."""

from datetime import UTC, datetime, timedelta

import pytest
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient

from home_finder.db.storage import PropertyStorage
from home_finder.models import (
    BedroomAnalysis,
    FlooringNoiseAnalysis,
    LightSpaceAnalysis,
    ListingExtraction,
    MergedProperty,
    OutdoorSpaceAnalysis,
    Property,
    PropertyQualityAnalysis,
    PropertyType,
    SpaceAnalysis,
    UserStatus,
)
from home_finder.web.routes import TAG_CATEGORIES


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def _card_ids(html: str) -> list[str]:
    return [card["data-property-id"] for card in _soup(html).select("article.property-card")]


def _chips(html: str) -> list[tuple[str, str]]:
    """(filter key, label) for each chip, in the order shown."""
    return [
        (chip.button["data-filter-key"], chip.find(string=True).strip())
        for chip in _soup(html).select(".filter-chip")
    ]


def _first_seen(prop: Property, age: timedelta) -> MergedProperty:
    seen = prop.model_copy(update={"first_seen": datetime.now(UTC) - age})
    return MergedProperty(
        canonical=seen,
        sources=(seen.source,),
        source_urls={seen.source: seen.url},
        min_price=seen.price_pcm,
        max_price=seen.price_pcm,
    )


class TestQueryParameters:
    """Out-of-range, empty or unknown values are clamped or ignored, never rejected."""

    @pytest.mark.parametrize("query", ["page=-5", "page=9999", "min_fit_score=150", "bedrooms=999"])
    def test_out_of_range_values_are_clamped(self, client: TestClient, query: str) -> None:
        assert client.get(f"/?{query}").status_code == 200

    @pytest.mark.parametrize(
        "query",
        [
            "bedrooms=",
            "min_price=",
            "max_price=",
            "min_fit_score=",
            "page=",
            "area=",
            "bedrooms=&min_price=&max_price=&min_fit_score=&area=&page=",
            "bedrooms=abc",
        ],
    )
    def test_empty_or_non_numeric_values_do_not_fail_validation(
        self, client: TestClient, query: str
    ) -> None:
        """Regression: an empty form field used to fail FastAPI validation with a 422."""
        assert client.get(f"/?{query}").status_code == 200

    @pytest.mark.parametrize("query", ["", "?sort=evil_injection"])
    def test_best_fit_is_the_default_sort(self, client: TestClient, query: str) -> None:
        soup = _soup(client.get(f"/{query}").text)
        assert soup.select_one('select[name="sort"] option[selected]')["value"] == "fit_desc"

    @pytest.mark.parametrize(
        ("sort", "expected"),
        [
            ("price_asc", ["studio", "a", "b"]),
            ("price_desc", ["b", "a", "studio"]),
            ("newest", ["a", "studio", "b"]),
            ("longest_listed", ["b", "studio", "a"]),
        ],
    )
    async def test_sort_orders_the_grid(
        self,
        client: TestClient,
        storage: PropertyStorage,
        prop_a: Property,
        prop_b: Property,
        studio_prop: Property,
        sort: str,
        expected: list[str],
    ) -> None:
        homes = {
            "a": _first_seen(prop_a, timedelta(days=1)),  # £1,900
            "b": _first_seen(prop_b, timedelta(days=10)),  # £2,500
            "studio": _first_seen(studio_prop, timedelta(days=5)),  # £1,500
        }
        for merged in homes.values():
            await storage.save_merged_property(merged)
        assert _card_ids(client.get(f"/?sort={sort}").text) == [
            homes[name].unique_id for name in expected
        ]


class TestResultFilters:
    async def test_filter_by_bedrooms(
        self, client: TestClient, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)
        resp = client.get("/?bedrooms=1")
        assert resp.status_code == 200
        assert "1 bed in E8" in resp.text

        resp = client.get("/?bedrooms=3")
        assert resp.status_code == 200
        assert "No properties found" in resp.text

    async def test_filter_by_area(
        self, client: TestClient, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)
        resp = client.get("/?area=E8")
        assert resp.status_code == 200
        assert "1 bed in E8" in resp.text

        resp = client.get("/?area=N16")
        assert resp.status_code == 200
        assert "No properties found" in resp.text

    async def test_studio_filter(
        self,
        client: TestClient,
        storage: PropertyStorage,
        merged_a: MergedProperty,
        merged_studio: MergedProperty,
    ) -> None:
        await storage.save_merged_property(merged_a)
        await storage.save_merged_property(merged_studio)
        assert _card_ids(client.get("/?bedrooms=0").text) == [merged_studio.unique_id]

    @pytest.mark.parametrize(
        ("query", "expected"),
        [
            ("min_price=2000", ["b"]),
            ("max_price=2000", ["a"]),
            ("min_price=1800&max_price=2000", ["a"]),
            ("min_price=3000&max_price=4000", []),
        ],
    )
    async def test_price_filters(
        self,
        client: TestClient,
        storage: PropertyStorage,
        merged_a: MergedProperty,
        merged_b: MergedProperty,
        query: str,
        expected: list[str],
    ) -> None:
        await storage.save_merged_property(merged_a)  # £1,900
        await storage.save_merged_property(merged_b)  # £2,500
        homes = {"a": merged_a, "b": merged_b}
        assert _card_ids(client.get(f"/?{query}").text) == [
            homes[name].unique_id for name in expected
        ]

    @pytest.mark.parametrize(
        ("query", "expected"),
        [
            ("bedrooms=1&area=E8", ["a"]),
            # One bedroom matches the first home and the price the second, so neither does.
            ("bedrooms=1&min_price=2000", []),
            ("bedrooms=1&min_price=1500&max_price=2000&area=E8", ["a"]),
        ],
    )
    async def test_combined_filters_must_all_match(
        self,
        client: TestClient,
        storage: PropertyStorage,
        merged_a: MergedProperty,
        merged_b: MergedProperty,
        query: str,
        expected: list[str],
    ) -> None:
        await storage.save_merged_property(merged_a)  # 1 bed, £1,900, E8
        await storage.save_merged_property(merged_b)  # 2 bed, £2,500, N16
        homes = {"a": merged_a, "b": merged_b}
        assert _card_ids(client.get(f"/?{query}").text) == [
            homes[name].unique_id for name in expected
        ]

    async def test_htmx_partial_with_filters(
        self,
        client: TestClient,
        storage: PropertyStorage,
        merged_a: MergedProperty,
    ) -> None:
        await storage.save_merged_property(merged_a)
        resp = client.get("/?bedrooms=1&area=E8", headers={"HX-Request": "true"})
        assert resp.status_code == 200
        assert "<!DOCTYPE html>" not in resp.text
        assert "1 bed in E8" in resp.text

    async def test_status_filter(
        self,
        client: TestClient,
        storage: PropertyStorage,
        merged_a: MergedProperty,
        merged_b: MergedProperty,
    ) -> None:
        await storage.save_merged_property(merged_a)
        await storage.save_merged_property(merged_b)
        await storage.update_user_status(merged_a.unique_id, UserStatus.INTERESTED)
        resp = client.get("/?status=interested")
        assert resp.status_code == 200
        assert "1 bed in E8" in resp.text
        assert "2 bed in N16" not in resp.text

    @pytest.mark.parametrize(("query", "listed"), [("", False), ("?off_market=only", True)])
    async def test_off_market_homes_are_hidden_unless_asked_for(
        self,
        client: TestClient,
        storage: PropertyStorage,
        merged_a: MergedProperty,
        query: str,
        listed: bool,
    ) -> None:
        await storage.save_merged_property(merged_a)
        await storage.mark_off_market(merged_a.unique_id)
        resp = client.get(f"/{query}")
        assert resp.status_code == 200
        assert ("OFF MARKET" in resp.text) is listed


class TestQualityFilters:
    """Filters on the saved assessment; unknown values are ignored rather than rejected."""

    @pytest.mark.parametrize(
        ("update", "param", "match", "miss"),
        [
            (
                {"listing_extraction": ListingExtraction(property_type=PropertyType.WAREHOUSE)},
                "property_type",
                "warehouse",
                "victorian",
            ),
            ({}, "hob_type", "gas", "induction"),
            (
                {"bedroom": BedroomAnalysis(office_separation="dedicated_room")},
                "office_separation",
                "dedicated_room",
                "none",
            ),
            (
                {"space": SpaceAnalysis(hosting_layout="excellent")},
                "hosting_layout",
                "excellent",
                "poor",
            ),
            (
                {"flooring_noise": FlooringNoiseAnalysis(hosting_noise_risk="low")},
                "hosting_noise_risk",
                "low",
                "high",
            ),
            (
                {"listing_extraction": ListingExtraction(broadband_type="fttp")},
                "broadband_type",
                "fttp",
                "standard",
            ),
        ],
        ids=[
            "property_type",
            "hob_type",
            "office_separation",
            "hosting_layout",
            "hosting_noise_risk",
            "broadband_type",
        ],
    )
    async def test_filter_matches_only_the_assessed_value(
        self,
        client: TestClient,
        storage: PropertyStorage,
        prop_a: Property,
        merged_a: MergedProperty,
        base_analysis: PropertyQualityAnalysis,
        update: dict,
        param: str,
        match: str,
        miss: str,
    ) -> None:
        await storage.save_merged_property(merged_a)
        await storage.save_quality_analysis(
            prop_a.unique_id, base_analysis.model_copy(update=update)
        )

        resp = client.get(f"/?{param}={match}")
        assert resp.status_code == 200
        assert "1 bed in E8" in resp.text

        resp = client.get(f"/?{param}={miss}")
        assert resp.status_code == 200
        assert "No properties found" in resp.text

    async def test_combined_quality_filters(
        self,
        client: TestClient,
        storage: PropertyStorage,
        prop_a: Property,
        merged_a: MergedProperty,
        base_analysis: PropertyQualityAnalysis,
    ) -> None:
        analysis = base_analysis.model_copy(
            update={
                "listing_extraction": ListingExtraction(property_type=PropertyType.WAREHOUSE),
                "light_space": LightSpaceAnalysis(natural_light="excellent"),
                "outdoor_space": OutdoorSpaceAnalysis(has_balcony=True),
            }
        )
        await storage.save_merged_property(merged_a)
        await storage.save_quality_analysis(prop_a.unique_id, analysis)

        resp = client.get(
            "/?property_type=warehouse&hob_type=gas&outdoor_space=yes&natural_light=excellent"
        )
        assert resp.status_code == 200
        assert "1 bed in E8" in resp.text

        resp = client.get("/?property_type=warehouse&hob_type=induction")
        assert resp.status_code == 200
        assert "No properties found" in resp.text

    @pytest.mark.parametrize(
        "query",
        [
            "property_type=evil_injection",
            "hob_type=nuclear",
            "natural_light=blazing",
            "office_separation=evil_injection",
            "hosting_layout=evil",
            "property_type=&outdoor_space=&hob_type=",
        ],
    )
    async def test_unknown_or_empty_values_are_ignored(
        self, client: TestClient, storage: PropertyStorage, merged_a: MergedProperty, query: str
    ) -> None:
        await storage.save_merged_property(merged_a)
        resp = client.get(f"/?{query}")
        assert resp.status_code == 200
        assert "1 bed in E8" in resp.text
        assert _chips(resp.text) == []


class TestAddedFilter:
    """The ?added= filter on when Home Finder first saw a listing."""

    async def test_added_1d_excludes_old_properties(
        self,
        client: TestClient,
        storage: PropertyStorage,
        prop_a: Property,
        prop_b: Property,
    ) -> None:
        """?added=1d should exclude properties first seen 5 days ago."""
        await storage.save_merged_property(_first_seen(prop_a, timedelta(days=5)))
        await storage.save_merged_property(_first_seen(prop_b, timedelta(hours=6)))

        resp = client.get("/?added=1d")
        assert resp.status_code == 200
        assert "2 bed in N16" in resp.text
        assert "1 bed in E8" not in resp.text

    async def test_added_30d_includes_recent(
        self,
        client: TestClient,
        storage: PropertyStorage,
        prop_a: Property,
    ) -> None:
        """?added=30d should include properties from 10 days ago."""
        await storage.save_merged_property(_first_seen(prop_a, timedelta(days=10)))
        resp = client.get("/?added=30d")
        assert resp.status_code == 200
        assert "1 bed in E8" in resp.text

    @pytest.mark.parametrize("value", ["", "evil"])
    async def test_empty_or_unknown_added_shows_all(
        self,
        client: TestClient,
        storage: PropertyStorage,
        prop_a: Property,
        value: str,
    ) -> None:
        await storage.save_merged_property(_first_seen(prop_a, timedelta(days=90)))
        resp = client.get(f"/?added={value}")
        assert resp.status_code == 200
        assert "1 bed in E8" in resp.text

    async def test_added_composes_with_sort(
        self,
        client: TestClient,
        storage: PropertyStorage,
        prop_a: Property,
    ) -> None:
        """?added=7d&sort=fit_desc should work together."""
        await storage.save_merged_property(_first_seen(prop_a, timedelta(days=2)))
        resp = client.get("/?added=7d&sort=fit_desc")
        assert resp.status_code == 200
        assert "1 bed in E8" in resp.text


class TestFilterChips:
    @pytest.mark.parametrize(
        ("query", "expected"),
        [
            ("bedrooms=1", [("bedrooms", "1 bed")]),
            ("bedrooms=0", [("bedrooms", "Studio")]),
            ("area=E8", [("area", "E8")]),
            (
                "bedrooms=1&min_price=1500&area=E8",
                [("bedrooms", "1 bed"), ("min_price", "Min £1,500"), ("area", "E8")],
            ),
            ("property_type=warehouse", [("property_type", "Warehouse")]),
            ("office_separation=dedicated_room", [("office_separation", "Dedicated Room")]),
            ("added=3d", [("added", "Last 3 days")]),
            ("status=new", [("status", "New")]),
        ],
    )
    def test_each_active_filter_has_a_chip(
        self, client: TestClient, query: str, expected: list[tuple[str, str]]
    ) -> None:
        assert _chips(client.get(f"/?{query}").text) == expected

    def test_chips_in_htmx_partial(self, client: TestClient) -> None:
        resp = client.get("/?area=E8", headers={"HX-Request": "true"})
        assert "<!DOCTYPE html>" not in resp.text
        assert _chips(resp.text) == [("area", "E8")]

    def test_no_chips_without_filters(self, client: TestClient) -> None:
        resp = client.get("/")
        assert resp.status_code == 200
        assert "filter-chip" not in resp.text


class TestFiltersDialog:
    """The Filters dialog, the beds toggle and the count of active dialog filters."""

    def test_dialog_present_in_page(self, client: TestClient) -> None:
        resp = client.get("/")
        assert resp.status_code == 200
        assert 'id="filter-modal"' in resp.text
        assert "<dialog" in resp.text

    def test_tag_checkboxes_rendered(self, client: TestClient) -> None:
        soup = _soup(client.get("/").text)
        values = {box["value"] for box in soup.select('input[type="checkbox"][name="tag"]')}
        assert values == {tag for tags in TAG_CATEGORIES.values() for tag in tags}

    def test_tag_categories_present(self, client: TestClient) -> None:
        soup = _soup(client.get("/").text)
        labels = [label.get_text() for label in soup.select(".tag-category-label")]
        assert labels == list(TAG_CATEGORIES)

    def test_filter_groups_present(self, client: TestClient) -> None:
        resp = client.get("/")
        assert resp.status_code == 200
        assert 'name="office_separation"' in resp.text
        assert 'name="broadband_type"' in resp.text
        assert 'name="hosting_layout"' in resp.text
        assert 'name="hosting_noise_risk"' in resp.text

    @pytest.mark.parametrize(
        ("query", "checked"),
        [("", "beds-any"), ("?bedrooms=0", "beds-0"), ("?bedrooms=1", "beds-1")],
    )
    def test_beds_toggle_checks_the_requested_bedrooms(
        self, client: TestClient, query: str, checked: str
    ) -> None:
        radios = _soup(client.get(f"/{query}").text).select('input[type="radio"][name="bedrooms"]')
        assert [radio["id"] for radio in radios] == ["beds-any", "beds-0", "beds-1", "beds-2"]
        assert [radio["id"] for radio in radios if radio.has_attr("checked")] == [checked]

    def test_no_badge_without_secondary_filters(self, client: TestClient) -> None:
        resp = client.get("/")
        assert resp.status_code == 200
        assert 'class="filter-badge" hidden' in resp.text

    @pytest.mark.parametrize(
        ("query", "count"),
        [
            ("hob_type=gas", "1"),
            ("hob_type=gas&property_type=warehouse", "2"),
            # Bedrooms, price and area are primary filters, outside the dialog.
            ("hob_type=gas&bedrooms=1&min_price=1500&area=E8", "1"),
        ],
    )
    def test_badge_counts_active_secondary_filters(
        self, client: TestClient, query: str, count: str
    ) -> None:
        badge = _soup(client.get(f"/?{query}").text).select_one(".filter-badge")
        assert not badge.has_attr("hidden")
        assert badge.get_text() == count

    def test_added_select_in_dashboard(self, client: TestClient) -> None:
        options = _soup(client.get("/").text).select('select[name="added"] option')
        assert options[0].get_text() == "All time"
        assert "Last 3 days" in [option.get_text() for option in options]


class TestFilterCount:
    """GET /count, the live result count in the Filters dialog."""

    def test_count_empty_db(self, client: TestClient) -> None:
        resp = client.get("/count")
        assert resp.status_code == 200
        assert resp.text == "0"
        assert resp.headers["content-type"] == "text/plain; charset=utf-8"

    async def test_count_with_data(
        self, client: TestClient, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)
        resp = client.get("/count")
        assert resp.status_code == 200
        assert resp.text == "1"

    @pytest.mark.parametrize("query", ["bedrooms=1", "max_price=2000"])
    async def test_count_applies_filters(
        self,
        client: TestClient,
        storage: PropertyStorage,
        merged_a: MergedProperty,
        merged_b: MergedProperty,
        query: str,
    ) -> None:
        await storage.save_merged_property(merged_a)  # 1 bed, £1,900
        await storage.save_merged_property(merged_b)  # 2 bed, £2,500
        resp = client.get(f"/count?{query}")
        assert resp.status_code == 200
        assert resp.text == "1"

    async def test_count_endpoint_with_added(
        self,
        client: TestClient,
        storage: PropertyStorage,
        prop_a: Property,
        prop_b: Property,
    ) -> None:
        await storage.save_merged_property(_first_seen(prop_a, timedelta(days=5)))
        await storage.save_merged_property(_first_seen(prop_b, timedelta(hours=6)))

        resp = client.get("/count?added=1d")
        assert resp.status_code == 200
        assert resp.text == "1"

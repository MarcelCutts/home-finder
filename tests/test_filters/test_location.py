"""Tests for LocationFilter."""

import pytest
from pydantic import HttpUrl

from home_finder.filters.location import BOROUGH_OUTCODES, LocationFilter
from home_finder.models import Property, PropertySource


@pytest.fixture
def hackney_property() -> Property:
    """Property in Hackney (E8)."""
    return Property(
        source=PropertySource.RIGHTMOVE,
        source_id="123",
        url=HttpUrl("https://example.com/123"),
        title="Flat in Hackney",
        price_pcm=2000,
        bedrooms=1,
        address="123 Mare Street, Hackney",
        postcode="E8 3RH",
    )


@pytest.fixture
def westminster_property() -> Property:
    """Property in Westminster (SW1V) - should be filtered out for East London search."""
    return Property(
        source=PropertySource.RIGHTMOVE,
        source_id="456",
        url=HttpUrl("https://example.com/456"),
        title="Flat in Pimlico",
        price_pcm=2000,
        bedrooms=1,
        address="123 Claverton Street",
        postcode="SW1V 2SA",
    )


@pytest.fixture
def no_postcode_property() -> Property:
    """Property without a postcode."""
    return Property(
        source=PropertySource.RIGHTMOVE,
        source_id="789",
        url=HttpUrl("https://example.com/789"),
        title="Flat somewhere",
        price_pcm=2000,
        bedrooms=1,
        address="123 Some Street, London",
        postcode=None,
    )


class TestLocationFilter:
    """Tests for LocationFilter class."""

    def test_missing_postcode_is_rejected_by_default(self, no_postcode_property: Property) -> None:
        assert LocationFilter(["hackney"]).is_valid_location(no_postcode_property) is False

    def test_non_strict_mode_allows_missing_postcode(self, no_postcode_property: Property) -> None:
        location_filter = LocationFilter(["hackney"], strict=False)
        assert location_filter.is_valid_location(no_postcode_property) is True

    def test_borough_search_accepts_its_outcodes_and_rejects_others(
        self, hackney_property: Property, westminster_property: Property
    ) -> None:
        location_filter = LocationFilter(["hackney"])
        assert location_filter.is_valid_location(hackney_property) is True
        assert location_filter.is_valid_location(westminster_property) is False

    def test_outcode_search_accepts_only_that_outcode(self, hackney_property: Property) -> None:
        location_filter = LocationFilter(["e8"])
        assert location_filter.is_valid_location(hackney_property) is True

        # E9 is in Hackney too, but an E8 search does not expand to the borough.
        e9_property = Property(
            source=PropertySource.RIGHTMOVE,
            source_id="999",
            url=HttpUrl("https://example.com/999"),
            title="Flat in Hackney Wick",
            price_pcm=2000,
            bedrooms=1,
            address="123 Fish Island",
            postcode="E9 5AA",
        )
        assert location_filter.is_valid_location(e9_property) is False

    def test_multiple_search_areas_accept_any_of_their_outcodes(
        self, hackney_property: Property, westminster_property: Property
    ) -> None:
        location_filter = LocationFilter(["hackney", "islington", "tower-hamlets"])
        assert location_filter.is_valid_location(hackney_property) is True
        assert location_filter.is_valid_location(westminster_property) is False

        with_westminster = LocationFilter(["hackney", "westminster"])
        assert with_westminster.is_valid_location(hackney_property) is True
        assert with_westminster.is_valid_location(westminster_property) is True

    @pytest.mark.parametrize(("strict", "expected_ids"), [(False, ["123", "789"]), (True, ["123"])])
    def test_filter_properties_keeps_accepted_properties_in_order(
        self,
        hackney_property: Property,
        westminster_property: Property,
        no_postcode_property: Property,
        strict: bool,
        expected_ids: list[str],
    ) -> None:
        """Westminster (456) is always rejected; the postcode-less 789 only when strict."""
        properties = [hackney_property, westminster_property, no_postcode_property]

        valid = LocationFilter(["hackney"], strict=strict).filter_properties(properties)

        assert [p.source_id for p in valid] == expected_ids

    def test_valid_outcodes_combine_borough_and_explicit_outcodes(self) -> None:
        location_filter = LocationFilter(["hackney", "e3", "n15"])

        assert location_filter.valid_outcodes == BOROUGH_OUTCODES["hackney"] | {"E3", "N15"}

    def test_reported_outcode_only_leakage_is_rejected_even_when_not_strict(self) -> None:
        """Outcode-only postcodes are located, not missing, so non-strict mode still checks them."""
        # These are the exact properties the user reported
        leaky_properties = [
            Property(
                source=PropertySource.RIGHTMOVE,
                source_id="171605924",
                url=HttpUrl("https://www.rightmove.co.uk/properties/171605924"),
                title="Flat, Claverton Street, SW1V",
                price_pcm=2167,
                bedrooms=1,
                address="Claverton Street, SW1V",
                postcode="SW1V",
            ),
            Property(
                source=PropertySource.RIGHTMOVE,
                source_id="171587792",
                url=HttpUrl("https://www.rightmove.co.uk/properties/171587792"),
                title="Apartment, Abercorn Place, London, NW8",
                price_pcm=2150,
                bedrooms=1,
                address="Abercorn Place, London, NW8",
                postcode="NW8",
            ),
        ]

        # Filter for East/North London areas (user's actual search)
        search_areas = [
            "hackney",
            "islington",
            "haringey",
            "tower-hamlets",
            "e3",
            "e5",
            "e9",
            "e10",
            "n15",
        ]
        location_filter = LocationFilter(search_areas, strict=False)

        assert location_filter.filter_properties(leaky_properties) == []

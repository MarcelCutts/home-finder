"""Tests for property criteria filtering."""

import pytest
from pydantic import HttpUrl

from home_finder.filters.criteria import CriteriaFilter
from home_finder.models import Property, PropertySource, SearchCriteria


@pytest.fixture
def sample_properties() -> list[Property]:
    """Create sample properties for testing."""
    return [
        Property(
            source=PropertySource.OPENRENT,
            source_id="1",
            url=HttpUrl("https://example.com/1"),
            title="1 bed flat in budget",
            price_pcm=1900,
            bedrooms=1,
            address="Address 1",
            postcode="E8 3RH",
        ),
        Property(
            source=PropertySource.RIGHTMOVE,
            source_id="2",
            url=HttpUrl("https://example.com/2"),
            title="2 bed flat at max",
            price_pcm=2200,
            bedrooms=2,
            address="Address 2",
            postcode="E8 1HN",
        ),
        Property(
            source=PropertySource.ZOOPLA,
            source_id="3",
            url=HttpUrl("https://example.com/3"),
            title="Studio too cheap",
            price_pcm=1500,
            bedrooms=0,
            address="Address 3",
            postcode="E8 2PB",
        ),
        Property(
            source=PropertySource.ONTHEMARKET,
            source_id="4",
            url=HttpUrl("https://example.com/4"),
            title="3 bed too many beds",
            price_pcm=2000,
            bedrooms=3,
            address="Address 4",
            postcode="N1 2AA",
        ),
        Property(
            source=PropertySource.OPENRENT,
            source_id="5",
            url=HttpUrl("https://example.com/5"),
            title="1 bed too expensive",
            price_pcm=2500,
            bedrooms=1,
            address="Address 5",
            postcode="N1 5AA",
        ),
    ]


@pytest.fixture
def default_criteria() -> SearchCriteria:
    """Create default search criteria."""
    return SearchCriteria(
        min_price=1800,
        max_price=2200,
        min_bedrooms=1,
        max_bedrooms=2,
        destination_postcode="N1 5AA",
        max_commute_minutes=30,
    )


class TestCriteriaFilter:
    """Tests for CriteriaFilter."""

    def test_keeps_only_properties_within_price_and_bedroom_ranges(
        self, sample_properties: list[Property], default_criteria: SearchCriteria
    ) -> None:
        criteria_filter = CriteriaFilter(default_criteria)
        filtered = criteria_filter.filter_properties(sample_properties)

        # Excluded: 3 is a £1500 studio, 4 has three beds, 5 costs £2500.
        assert [p.source_id for p in filtered] == ["1", "2"]

    def test_price_and_bedroom_limits_are_inclusive(self, default_criteria: SearchCriteria) -> None:
        criteria_filter = CriteriaFilter(default_criteria)

        # At min price, min bedrooms - should match
        prop_min = Property(
            source=PropertySource.OPENRENT,
            source_id="min",
            url=HttpUrl("https://example.com/min"),
            title="At minimum",
            price_pcm=1800,
            bedrooms=1,
            address="Min Address",
        )

        # At max price, max bedrooms - should match
        prop_max = Property(
            source=PropertySource.OPENRENT,
            source_id="max",
            url=HttpUrl("https://example.com/max"),
            title="At maximum",
            price_pcm=2200,
            bedrooms=2,
            address="Max Address",
        )

        # Just below min price - should not match
        prop_below_min = Property(
            source=PropertySource.OPENRENT,
            source_id="below",
            url=HttpUrl("https://example.com/below"),
            title="Below minimum",
            price_pcm=1799,
            bedrooms=1,
            address="Below Address",
        )

        # Just above max price - should not match
        prop_above_max = Property(
            source=PropertySource.OPENRENT,
            source_id="above",
            url=HttpUrl("https://example.com/above"),
            title="Above maximum",
            price_pcm=2201,
            bedrooms=1,
            address="Above Address",
        )

        filtered = criteria_filter.filter_properties(
            [prop_min, prop_max, prop_below_min, prop_above_max]
        )

        assert [p.source_id for p in filtered] == ["min", "max"]

    def test_empty_input_gives_empty_output(self, default_criteria: SearchCriteria) -> None:
        assert CriteriaFilter(default_criteria).filter_properties([]) == []

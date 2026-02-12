"""Tests for property criteria filtering."""

import pytest
from pydantic import HttpUrl

from home_finder.filters.criteria import CriteriaFilter
from home_finder.models import Property, PropertySource, SearchCriteria, TransportMode


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
        transport_modes=(TransportMode.CYCLING, TransportMode.PUBLIC_TRANSPORT),
    )


class TestCriteriaFilter:
    """Tests for CriteriaFilter."""

    def test_filter_by_criteria(
        self, sample_properties: list[Property], default_criteria: SearchCriteria
    ) -> None:
        """Test filtering properties by criteria."""
        filter = CriteriaFilter(default_criteria)
        filtered = filter.filter_properties(sample_properties)

        # Should only include properties that match price and bedroom criteria
        assert len(filtered) == 2

        # Check correct properties are included
        source_ids = {p.source_id for p in filtered}
        assert "1" in source_ids  # 1 bed, £1900 - matches
        assert "2" in source_ids  # 2 bed, £2200 - matches

        # Check excluded properties
        assert "3" not in source_ids  # Studio (0 beds), £1500 - too cheap, too few beds
        assert "4" not in source_ids  # 3 beds - too many beds
        assert "5" not in source_ids  # £2500 - too expensive

    def test_filter_at_boundaries(self, default_criteria: SearchCriteria) -> None:
        """Test filtering at exact boundary values."""
        filter = CriteriaFilter(default_criteria)

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

        filtered = filter.filter_properties([prop_min, prop_max, prop_below_min, prop_above_max])

        assert len(filtered) == 2
        source_ids = {p.source_id for p in filtered}
        assert "min" in source_ids
        assert "max" in source_ids
        assert "below" not in source_ids
        assert "above" not in source_ids

    def test_filter_empty_list(self, default_criteria: SearchCriteria) -> None:
        """Test filtering an empty list."""
        filter = CriteriaFilter(default_criteria)
        filtered = filter.filter_properties([])
        assert len(filtered) == 0

    def test_filter_all_match(self, default_criteria: SearchCriteria) -> None:
        """Test when all properties match."""
        filter = CriteriaFilter(default_criteria)

        matching_props = [
            Property(
                source=PropertySource.OPENRENT,
                source_id=f"match-{i}",
                url=HttpUrl(f"https://example.com/{i}"),
                title=f"Matching property {i}",
                price_pcm=2000,
                bedrooms=1,
                address=f"Address {i}",
            )
            for i in range(5)
        ]

        filtered = filter.filter_properties(matching_props)
        assert len(filtered) == 5

    def test_filter_none_match(self, default_criteria: SearchCriteria) -> None:
        """Test when no properties match."""
        filter = CriteriaFilter(default_criteria)

        non_matching_props = [
            Property(
                source=PropertySource.OPENRENT,
                source_id="too-expensive",
                url=HttpUrl("https://example.com/1"),
                title="Too expensive",
                price_pcm=5000,
                bedrooms=1,
                address="Address",
            ),
            Property(
                source=PropertySource.OPENRENT,
                source_id="too-many-beds",
                url=HttpUrl("https://example.com/2"),
                title="Too many beds",
                price_pcm=2000,
                bedrooms=5,
                address="Address",
            ),
        ]

        filtered = filter.filter_properties(non_matching_props)
        assert len(filtered) == 0

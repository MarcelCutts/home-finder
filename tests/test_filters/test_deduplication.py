"""Tests for property deduplication."""

from datetime import datetime

import pytest
from pydantic import HttpUrl

from home_finder.filters.deduplication import Deduplicator
from home_finder.models import Property, PropertySource


@pytest.fixture
def sample_properties() -> list[Property]:
    """Create sample properties for testing."""
    return [
        Property(
            source=PropertySource.OPENRENT,
            source_id="123",
            url=HttpUrl("https://openrent.com/123"),
            title="Nice flat in Hackney",
            price_pcm=2000,
            bedrooms=1,
            address="123 Mare Street, E8 3RH",
            postcode="E8 3RH",
        ),
        Property(
            source=PropertySource.RIGHTMOVE,
            source_id="456",
            url=HttpUrl("https://rightmove.co.uk/456"),
            title="1 bed flat Mare Street",
            price_pcm=2000,
            bedrooms=1,
            address="123 Mare Street, Hackney E8 3RH",
            postcode="E8 3RH",
        ),
        Property(
            source=PropertySource.ZOOPLA,
            source_id="789",
            url=HttpUrl("https://zoopla.co.uk/789"),
            title="Different property",
            price_pcm=1900,
            bedrooms=2,
            address="45 Dalston Lane, E8 2PB",
            postcode="E8 2PB",
        ),
    ]


class TestDeduplicator:
    """Tests for Deduplicator."""

    def test_dedupe_by_unique_id(self) -> None:
        """Test that same property ID from same source is deduplicated."""
        deduplicator = Deduplicator()

        # Same property ID from same source
        props = [
            Property(
                source=PropertySource.OPENRENT,
                source_id="123",
                url=HttpUrl("https://openrent.com/123"),
                title="First version",
                price_pcm=2000,
                bedrooms=1,
                address="Address",
            ),
            Property(
                source=PropertySource.OPENRENT,
                source_id="123",
                url=HttpUrl("https://openrent.com/123"),
                title="Second version",
                price_pcm=2000,
                bedrooms=1,
                address="Address",
            ),
        ]

        deduped = deduplicator.deduplicate(props)
        assert len(deduped) == 1
        assert deduped[0].unique_id == "openrent:123"

    def test_dedupe_same_id_different_sources(self) -> None:
        """Test that same ID from different sources are not deduplicated."""
        deduplicator = Deduplicator()

        props = [
            Property(
                source=PropertySource.OPENRENT,
                source_id="123",
                url=HttpUrl("https://openrent.com/123"),
                title="OpenRent listing",
                price_pcm=2000,
                bedrooms=1,
                address="Address",
            ),
            Property(
                source=PropertySource.RIGHTMOVE,
                source_id="123",
                url=HttpUrl("https://rightmove.co.uk/123"),
                title="Rightmove listing",
                price_pcm=2000,
                bedrooms=1,
                address="Address",
            ),
        ]

        deduped = deduplicator.deduplicate(props)
        # Different sources = different properties
        assert len(deduped) == 2

    def test_dedupe_cross_platform_similar(self, sample_properties: list[Property]) -> None:
        """Test cross-platform deduplication of similar listings."""
        # Enable cross-platform deduplication
        deduplicator = Deduplicator(enable_cross_platform=True)

        deduped = deduplicator.deduplicate(sample_properties)

        # First two properties are likely the same listing on different platforms
        # (same postcode, price, bedrooms)
        # Third is different (different postcode, price, bedrooms)
        # After dedup, should have 2 unique properties
        assert len(deduped) == 2

    def test_dedupe_keeps_first_seen(self) -> None:
        """Test that earlier seen property is kept during dedup."""
        deduplicator = Deduplicator()

        earlier = datetime(2025, 1, 15, 10, 0)
        later = datetime(2025, 1, 16, 10, 0)

        props = [
            Property(
                source=PropertySource.OPENRENT,
                source_id="123",
                url=HttpUrl("https://openrent.com/123"),
                title="Second seen",
                price_pcm=2000,
                bedrooms=1,
                address="Address",
                first_seen=later,
            ),
            Property(
                source=PropertySource.OPENRENT,
                source_id="123",
                url=HttpUrl("https://openrent.com/123"),
                title="First seen",
                price_pcm=2000,
                bedrooms=1,
                address="Address",
                first_seen=earlier,
            ),
        ]

        deduped = deduplicator.deduplicate(props)
        assert len(deduped) == 1
        assert deduped[0].first_seen == earlier

    def test_dedupe_empty_list(self) -> None:
        """Test deduplicating an empty list."""
        deduplicator = Deduplicator()
        deduped = deduplicator.deduplicate([])
        assert len(deduped) == 0

    def test_dedupe_all_unique(self) -> None:
        """Test deduplicating when all properties are unique."""
        deduplicator = Deduplicator()

        props = [
            Property(
                source=PropertySource.OPENRENT,
                source_id=f"unique-{i}",
                url=HttpUrl(f"https://openrent.com/{i}"),
                title=f"Unique property {i}",
                price_pcm=2000 + i * 100,
                bedrooms=1,
                address=f"Address {i}",
                postcode=f"E{i} 1AA",
            )
            for i in range(5)
        ]

        deduped = deduplicator.deduplicate(props)
        assert len(deduped) == 5


class TestCrossPlatformDedup:
    """Tests for cross-platform deduplication logic."""

    def test_same_postcode_price_beds_are_duplicates(self) -> None:
        """Test that same postcode + price + beds = likely duplicate."""
        deduplicator = Deduplicator(enable_cross_platform=True)

        props = [
            Property(
                source=PropertySource.OPENRENT,
                source_id="111",
                url=HttpUrl("https://openrent.com/111"),
                title="OpenRent listing",
                price_pcm=2000,
                bedrooms=1,
                address="123 Street",
                postcode="E8 3RH",
            ),
            Property(
                source=PropertySource.RIGHTMOVE,
                source_id="222",
                url=HttpUrl("https://rightmove.co.uk/222"),
                title="Rightmove listing",
                price_pcm=2000,
                bedrooms=1,
                address="123 Street, Hackney",
                postcode="E8 3RH",
            ),
        ]

        deduped = deduplicator.deduplicate(props)
        assert len(deduped) == 1

    def test_different_postcode_not_duplicates(self) -> None:
        """Test that different postcodes are not duplicates."""
        deduplicator = Deduplicator(enable_cross_platform=True)

        props = [
            Property(
                source=PropertySource.OPENRENT,
                source_id="111",
                url=HttpUrl("https://openrent.com/111"),
                title="Property 1",
                price_pcm=2000,
                bedrooms=1,
                address="Address 1",
                postcode="E8 3RH",
            ),
            Property(
                source=PropertySource.RIGHTMOVE,
                source_id="222",
                url=HttpUrl("https://rightmove.co.uk/222"),
                title="Property 2",
                price_pcm=2000,
                bedrooms=1,
                address="Address 2",
                postcode="E8 2PB",  # Different postcode
            ),
        ]

        deduped = deduplicator.deduplicate(props)
        assert len(deduped) == 2

    def test_different_price_not_duplicates(self) -> None:
        """Test that different prices are not duplicates."""
        deduplicator = Deduplicator(enable_cross_platform=True)

        props = [
            Property(
                source=PropertySource.OPENRENT,
                source_id="111",
                url=HttpUrl("https://openrent.com/111"),
                title="Property 1",
                price_pcm=2000,
                bedrooms=1,
                address="Address",
                postcode="E8 3RH",
            ),
            Property(
                source=PropertySource.RIGHTMOVE,
                source_id="222",
                url=HttpUrl("https://rightmove.co.uk/222"),
                title="Property 2",
                price_pcm=2100,  # Different price
                bedrooms=1,
                address="Address",
                postcode="E8 3RH",
            ),
        ]

        deduped = deduplicator.deduplicate(props)
        assert len(deduped) == 2

    def test_missing_postcode_not_cross_deduped(self) -> None:
        """Test that properties without postcodes aren't cross-platform deduped."""
        deduplicator = Deduplicator(enable_cross_platform=True)

        props = [
            Property(
                source=PropertySource.OPENRENT,
                source_id="111",
                url=HttpUrl("https://openrent.com/111"),
                title="Property 1",
                price_pcm=2000,
                bedrooms=1,
                address="Address 1",
                postcode=None,
            ),
            Property(
                source=PropertySource.RIGHTMOVE,
                source_id="222",
                url=HttpUrl("https://rightmove.co.uk/222"),
                title="Property 2",
                price_pcm=2000,
                bedrooms=1,
                address="Address 2",
                postcode=None,
            ),
        ]

        deduped = deduplicator.deduplicate(props)
        # Without postcodes, can't confidently dedupe cross-platform
        assert len(deduped) == 2

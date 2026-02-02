"""Tests for property deduplication."""

from datetime import datetime

import pytest
from pydantic import HttpUrl

from home_finder.filters.deduplication import (
    Deduplicator,
    coordinates_match,
    is_full_postcode,
    prices_match,
)
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


class TestFuzzyPriceMatching:
    """Tests for fuzzy price matching."""

    def test_prices_match_exact(self) -> None:
        """Test that exact prices match."""
        assert prices_match(2000, 2000) is True

    def test_prices_match_within_tolerance(self) -> None:
        """Test that prices within 3% tolerance match."""
        # 1.5% difference
        assert prices_match(2000, 2030) is True
        # 3% difference (at boundary)
        assert prices_match(2000, 2060) is True
        # Reverse direction
        assert prices_match(2030, 2000) is True

    def test_prices_dont_match_outside_tolerance(self) -> None:
        """Test that prices outside 3% tolerance don't match."""
        # ~5% difference (was matching before, now too far)
        assert prices_match(2000, 2100) is False
        # ~10% difference
        assert prices_match(2000, 2200) is False

    def test_prices_match_zero_handling(self) -> None:
        """Test that zero prices are handled correctly."""
        assert prices_match(0, 0) is True
        assert prices_match(0, 2000) is False
        assert prices_match(2000, 0) is False


class TestFullPostcodeDetection:
    """Tests for full postcode validation."""

    def test_full_postcode_detected(self) -> None:
        """Test that full postcodes are correctly identified."""
        assert is_full_postcode("E8 3RH") is True
        assert is_full_postcode("SW1A 1AA") is True
        assert is_full_postcode("EC1A 1BB") is True
        assert is_full_postcode("W1A 0AX") is True

    def test_partial_postcode_rejected(self) -> None:
        """Test that partial postcodes (outcode only) are rejected."""
        assert is_full_postcode("E8") is False
        assert is_full_postcode("SW1A") is False
        assert is_full_postcode("EC1") is False

    def test_none_and_empty_rejected(self) -> None:
        """Test that None and empty strings are rejected."""
        assert is_full_postcode(None) is False
        assert is_full_postcode("") is False


class TestCoordinateMatching:
    """Tests for coordinate-based matching."""

    def test_coordinates_match_same_location(self) -> None:
        """Test that identical coordinates match."""
        prop1 = Property(
            source=PropertySource.OPENRENT,
            source_id="1",
            url=HttpUrl("https://example.com/1"),
            title="Prop 1",
            price_pcm=2000,
            bedrooms=1,
            address="Address",
            latitude=51.5074,
            longitude=-0.1278,
        )
        prop2 = Property(
            source=PropertySource.RIGHTMOVE,
            source_id="2",
            url=HttpUrl("https://example.com/2"),
            title="Prop 2",
            price_pcm=2000,
            bedrooms=1,
            address="Address",
            latitude=51.5074,
            longitude=-0.1278,
        )
        assert coordinates_match(prop1, prop2) is True

    def test_coordinates_match_within_50m(self) -> None:
        """Test that coordinates within 50m match."""
        prop1 = Property(
            source=PropertySource.OPENRENT,
            source_id="1",
            url=HttpUrl("https://example.com/1"),
            title="Prop 1",
            price_pcm=2000,
            bedrooms=1,
            address="Address",
            latitude=51.5074,
            longitude=-0.1278,
        )
        # ~30m away
        prop2 = Property(
            source=PropertySource.RIGHTMOVE,
            source_id="2",
            url=HttpUrl("https://example.com/2"),
            title="Prop 2",
            price_pcm=2000,
            bedrooms=1,
            address="Address",
            latitude=51.5077,
            longitude=-0.1278,
        )
        assert coordinates_match(prop1, prop2) is True

    def test_coordinates_dont_match_far_apart(self) -> None:
        """Test that coordinates >50m apart don't match."""
        prop1 = Property(
            source=PropertySource.OPENRENT,
            source_id="1",
            url=HttpUrl("https://example.com/1"),
            title="Prop 1",
            price_pcm=2000,
            bedrooms=1,
            address="Address",
            latitude=51.5074,
            longitude=-0.1278,
        )
        # ~500m away
        prop2 = Property(
            source=PropertySource.RIGHTMOVE,
            source_id="2",
            url=HttpUrl("https://example.com/2"),
            title="Prop 2",
            price_pcm=2000,
            bedrooms=1,
            address="Address",
            latitude=51.5120,
            longitude=-0.1278,
        )
        assert coordinates_match(prop1, prop2) is False

    def test_coordinates_match_requires_both_have_coords(self) -> None:
        """Test that matching fails if either property lacks coordinates."""
        prop1 = Property(
            source=PropertySource.OPENRENT,
            source_id="1",
            url=HttpUrl("https://example.com/1"),
            title="Prop 1",
            price_pcm=2000,
            bedrooms=1,
            address="Address",
            latitude=51.5074,
            longitude=-0.1278,
        )
        prop2 = Property(
            source=PropertySource.RIGHTMOVE,
            source_id="2",
            url=HttpUrl("https://example.com/2"),
            title="Prop 2",
            price_pcm=2000,
            bedrooms=1,
            address="Address",
        )
        assert coordinates_match(prop1, prop2) is False


class TestDeduplicatorMerge:
    """Tests for deduplicate_and_merge functionality."""

    def test_merge_same_property_different_sources_with_full_postcode(self) -> None:
        """Test that same property on different sources is merged when full postcode matches."""
        deduplicator = Deduplicator(enable_cross_platform=True)

        earlier = datetime(2025, 1, 15, 10, 0)
        later = datetime(2025, 1, 16, 10, 0)

        props = [
            Property(
                source=PropertySource.OPENRENT,
                source_id="111",
                url=HttpUrl("https://openrent.com/111"),
                title="OpenRent listing",
                price_pcm=2000,
                bedrooms=1,
                address="123 Street",
                postcode="E8 3RH",  # Full postcode
                description="OpenRent description",
                first_seen=earlier,
            ),
            Property(
                source=PropertySource.RIGHTMOVE,
                source_id="222",
                url=HttpUrl("https://rightmove.co.uk/222"),
                title="Rightmove listing",
                price_pcm=2000,
                bedrooms=1,
                address="123 Street, Hackney",
                postcode="E8 3RH",  # Same full postcode
                description="Rightmove description",
                first_seen=later,
            ),
        ]

        merged = deduplicator.deduplicate_and_merge(props)

        assert len(merged) == 1
        assert len(merged[0].sources) == 2
        assert PropertySource.OPENRENT in merged[0].sources
        assert PropertySource.RIGHTMOVE in merged[0].sources
        assert merged[0].canonical.source == PropertySource.OPENRENT  # First seen
        assert PropertySource.OPENRENT in merged[0].source_urls
        assert PropertySource.RIGHTMOVE in merged[0].source_urls
        assert len(merged[0].descriptions) == 2

    def test_partial_postcode_not_merged(self) -> None:
        """Test that properties with only outcode (not full postcode) are NOT merged."""
        deduplicator = Deduplicator(enable_cross_platform=True)

        props = [
            Property(
                source=PropertySource.OPENRENT,
                source_id="111",
                url=HttpUrl("https://openrent.com/111"),
                title="Property 1",
                price_pcm=2000,
                bedrooms=1,
                address="123 Street",
                postcode="E8",  # Partial postcode only
            ),
            Property(
                source=PropertySource.RIGHTMOVE,
                source_id="222",
                url=HttpUrl("https://rightmove.co.uk/222"),
                title="Property 2",
                price_pcm=2000,
                bedrooms=1,
                address="456 Street",
                postcode="E8",  # Same partial postcode
            ),
        ]

        merged = deduplicator.deduplicate_and_merge(props)

        # Should NOT be merged - partial postcodes are too ambiguous
        assert len(merged) == 2
        assert all(len(m.sources) == 1 for m in merged)

    def test_fuzzy_price_matching_merges_within_tolerance(self) -> None:
        """Test that properties with prices within 3% tolerance are merged."""
        deduplicator = Deduplicator(enable_cross_platform=True)

        props = [
            Property(
                source=PropertySource.OPENRENT,
                source_id="111",
                url=HttpUrl("https://openrent.com/111"),
                title="Property 1",
                price_pcm=2000,
                bedrooms=1,
                address="123 Street",
                postcode="E8 3RH",  # Full postcode
            ),
            Property(
                source=PropertySource.RIGHTMOVE,
                source_id="222",
                url=HttpUrl("https://rightmove.co.uk/222"),
                title="Property 2",
                price_pcm=2030,  # 1.5% higher (within 3%)
                bedrooms=1,
                address="123 Street",
                postcode="E8 3RH",
            ),
        ]

        merged = deduplicator.deduplicate_and_merge(props)

        assert len(merged) == 1
        assert len(merged[0].sources) == 2

    def test_fuzzy_price_not_merged_outside_tolerance(self) -> None:
        """Test that properties with prices outside 3% tolerance are not merged."""
        deduplicator = Deduplicator(enable_cross_platform=True)

        props = [
            Property(
                source=PropertySource.OPENRENT,
                source_id="111",
                url=HttpUrl("https://openrent.com/111"),
                title="Property 1",
                price_pcm=2000,
                bedrooms=1,
                address="123 Street",
                postcode="E8 3RH",
            ),
            Property(
                source=PropertySource.RIGHTMOVE,
                source_id="222",
                url=HttpUrl("https://rightmove.co.uk/222"),
                title="Property 2",
                price_pcm=2100,  # 5% higher (outside 3% tolerance)
                bedrooms=1,
                address="123 Street",
                postcode="E8 3RH",
            ),
        ]

        merged = deduplicator.deduplicate_and_merge(props)

        # Should NOT be merged - price difference too large
        assert len(merged) == 2
        assert all(len(m.sources) == 1 for m in merged)

    def test_coordinates_prevent_false_merge(self) -> None:
        """Test that properties with different coordinates are NOT merged even with same postcode."""
        deduplicator = Deduplicator(enable_cross_platform=True)

        props = [
            Property(
                source=PropertySource.OPENRENT,
                source_id="111",
                url=HttpUrl("https://openrent.com/111"),
                title="Property 1",
                price_pcm=2000,
                bedrooms=1,
                address="123 Street",
                postcode="E8 3RH",
                latitude=51.5400,
                longitude=-0.0550,
            ),
            Property(
                source=PropertySource.ZOOPLA,
                source_id="222",
                url=HttpUrl("https://zoopla.co.uk/222"),
                title="Property 2",
                price_pcm=2000,
                bedrooms=1,
                address="456 Street",  # Different address
                postcode="E8 3RH",  # Same postcode
                latitude=51.5450,  # ~500m away
                longitude=-0.0550,
            ),
        ]

        merged = deduplicator.deduplicate_and_merge(props)

        # Should NOT be merged - coordinates are too far apart
        assert len(merged) == 2
        assert all(len(m.sources) == 1 for m in merged)

    def test_coordinates_confirm_merge(self) -> None:
        """Test that properties with close coordinates ARE merged."""
        deduplicator = Deduplicator(enable_cross_platform=True)

        props = [
            Property(
                source=PropertySource.OPENRENT,
                source_id="111",
                url=HttpUrl("https://openrent.com/111"),
                title="Property 1",
                price_pcm=2000,
                bedrooms=1,
                address="123 Street",
                postcode="E8 3RH",
                latitude=51.5400,
                longitude=-0.0550,
            ),
            Property(
                source=PropertySource.ZOOPLA,
                source_id="222",
                url=HttpUrl("https://zoopla.co.uk/222"),
                title="Property 2",
                price_pcm=2000,
                bedrooms=1,
                address="123 Street",
                postcode="E8 3RH",
                latitude=51.5401,  # ~10m away
                longitude=-0.0550,
            ),
        ]

        merged = deduplicator.deduplicate_and_merge(props)

        # Should be merged - coordinates confirm same location
        assert len(merged) == 1
        assert len(merged[0].sources) == 2

    def test_price_range_tracked_in_merged_property(self) -> None:
        """Test that merged property tracks min/max prices."""
        deduplicator = Deduplicator(enable_cross_platform=True)

        props = [
            Property(
                source=PropertySource.OPENRENT,
                source_id="111",
                url=HttpUrl("https://openrent.com/111"),
                title="Property 1",
                price_pcm=2000,
                bedrooms=1,
                address="123 Street",
                postcode="E8 3RH",
            ),
            Property(
                source=PropertySource.RIGHTMOVE,
                source_id="222",
                url=HttpUrl("https://rightmove.co.uk/222"),
                title="Property 2",
                price_pcm=2030,  # Within 3% tolerance
                bedrooms=1,
                address="123 Street",
                postcode="E8 3RH",
            ),
        ]

        merged = deduplicator.deduplicate_and_merge(props)

        assert len(merged) == 1
        assert merged[0].min_price == 2000
        assert merged[0].max_price == 2030
        assert merged[0].price_varies is True

    def test_all_source_urls_preserved(self) -> None:
        """Test that all source URLs are preserved in merged property."""
        deduplicator = Deduplicator(enable_cross_platform=True)

        props = [
            Property(
                source=PropertySource.OPENRENT,
                source_id="111",
                url=HttpUrl("https://openrent.com/111"),
                title="Property 1",
                price_pcm=2000,
                bedrooms=1,
                address="123 Street",
                postcode="E8 3RH",
            ),
            Property(
                source=PropertySource.RIGHTMOVE,
                source_id="222",
                url=HttpUrl("https://rightmove.co.uk/222"),
                title="Property 2",
                price_pcm=2000,
                bedrooms=1,
                address="123 Street",
                postcode="E8 3RH",
            ),
            Property(
                source=PropertySource.ZOOPLA,
                source_id="333",
                url=HttpUrl("https://zoopla.co.uk/333"),
                title="Property 3",
                price_pcm=2000,
                bedrooms=1,
                address="123 Street",
                postcode="E8 3RH",
            ),
        ]

        merged = deduplicator.deduplicate_and_merge(props)

        assert len(merged) == 1
        assert len(merged[0].source_urls) == 3
        assert str(merged[0].source_urls[PropertySource.OPENRENT]) == "https://openrent.com/111"
        assert str(merged[0].source_urls[PropertySource.RIGHTMOVE]) == "https://rightmove.co.uk/222"
        assert str(merged[0].source_urls[PropertySource.ZOOPLA]) == "https://zoopla.co.uk/333"

    def test_single_property_wrapped_as_merged(self) -> None:
        """Test that single properties are wrapped as MergedProperty."""
        deduplicator = Deduplicator(enable_cross_platform=True)

        props = [
            Property(
                source=PropertySource.OPENRENT,
                source_id="111",
                url=HttpUrl("https://openrent.com/111"),
                title="Property 1",
                price_pcm=2000,
                bedrooms=1,
                address="123 Street",
                postcode="E8 3RH",
            ),
        ]

        merged = deduplicator.deduplicate_and_merge(props)

        assert len(merged) == 1
        assert len(merged[0].sources) == 1
        assert merged[0].canonical == props[0]
        assert merged[0].min_price == merged[0].max_price == 2000
        assert merged[0].price_varies is False

    def test_merge_empty_list(self) -> None:
        """Test merging an empty list."""
        deduplicator = Deduplicator(enable_cross_platform=True)
        merged = deduplicator.deduplicate_and_merge([])
        assert len(merged) == 0

    def test_merge_without_cross_platform(self) -> None:
        """Test merge without cross-platform enabled wraps each property."""
        deduplicator = Deduplicator(enable_cross_platform=False)

        props = [
            Property(
                source=PropertySource.OPENRENT,
                source_id="111",
                url=HttpUrl("https://openrent.com/111"),
                title="Property 1",
                price_pcm=2000,
                bedrooms=1,
                address="123 Street",
                postcode="E8 3RH",
            ),
            Property(
                source=PropertySource.RIGHTMOVE,
                source_id="222",
                url=HttpUrl("https://rightmove.co.uk/222"),
                title="Property 2",
                price_pcm=2000,
                bedrooms=1,
                address="123 Street",
                postcode="E8 3RH",  # Same details
            ),
        ]

        merged = deduplicator.deduplicate_and_merge(props)

        # Without cross-platform, each property is wrapped separately
        assert len(merged) == 2
        assert all(len(m.sources) == 1 for m in merged)

    def test_merge_keeps_earliest_first_seen_as_canonical(self) -> None:
        """Test that earliest first_seen property becomes canonical."""
        deduplicator = Deduplicator(enable_cross_platform=True)

        earlier = datetime(2025, 1, 15, 10, 0)
        later = datetime(2025, 1, 16, 10, 0)

        props = [
            Property(
                source=PropertySource.RIGHTMOVE,
                source_id="222",
                url=HttpUrl("https://rightmove.co.uk/222"),
                title="Rightmove listing",
                price_pcm=2000,
                bedrooms=1,
                address="123 Street",
                postcode="E8 3RH",
                first_seen=later,
            ),
            Property(
                source=PropertySource.OPENRENT,
                source_id="111",
                url=HttpUrl("https://openrent.com/111"),
                title="OpenRent listing",
                price_pcm=2000,
                bedrooms=1,
                address="123 Street",
                postcode="E8 3RH",
                first_seen=earlier,
            ),
        ]

        merged = deduplicator.deduplicate_and_merge(props)

        assert len(merged) == 1
        assert merged[0].canonical.source == PropertySource.OPENRENT
        assert merged[0].canonical.first_seen == earlier

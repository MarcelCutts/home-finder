"""Integration tests for the full pipeline (excluding notifications)."""

from datetime import datetime

import pytest
from pydantic import HttpUrl

from home_finder.db import PropertyStorage
from home_finder.filters import CriteriaFilter, Deduplicator
from home_finder.models import Property, PropertySource, SearchCriteria, TransportMode


@pytest.fixture
async def storage():
    """Create an in-memory storage instance."""
    storage = PropertyStorage(":memory:")
    await storage.initialize()
    yield storage
    await storage.close()


@pytest.fixture
def mixed_properties():
    """Properties from multiple sources, some matching criteria, some not."""
    return [
        # Matches criteria (OpenRent)
        Property(
            source=PropertySource.OPENRENT,
            source_id="1001",
            url=HttpUrl("https://openrent.com/1001"),
            title="Nice 1-bed in Hackney",
            price_pcm=1900,
            bedrooms=1,
            address="123 Mare Street, Hackney",
            postcode="E8 3RH",
            latitude=51.5465,
            longitude=-0.0553,
            first_seen=datetime(2025, 1, 15, 10, 0),
        ),
        # Same property on Rightmove (should be deduped)
        Property(
            source=PropertySource.RIGHTMOVE,
            source_id="RM2001",
            url=HttpUrl("https://rightmove.co.uk/RM2001"),
            title="Lovely 1 bedroom flat",
            price_pcm=1900,
            bedrooms=1,
            address="123 Mare Street",
            postcode="E8 3RH",
            latitude=51.5465,
            longitude=-0.0553,
            first_seen=datetime(2025, 1, 15, 11, 0),
        ),
        # Too expensive
        Property(
            source=PropertySource.ZOOPLA,
            source_id="Z3001",
            url=HttpUrl("https://zoopla.co.uk/Z3001"),
            title="Luxury 2-bed",
            price_pcm=2500,
            bedrooms=2,
            address="456 Upper Street",
            postcode="N1 0NY",
            first_seen=datetime(2025, 1, 15, 12, 0),
        ),
        # Matches criteria (different property)
        Property(
            source=PropertySource.ONTHEMARKET,
            source_id="OTM4001",
            url=HttpUrl("https://onthemarket.com/OTM4001"),
            title="Spacious 2-bed",
            price_pcm=2100,
            bedrooms=2,
            address="789 Kingsland Road",
            postcode="E8 4AA",
            latitude=51.5482,
            longitude=-0.0761,
            first_seen=datetime(2025, 1, 15, 13, 0),
        ),
    ]


@pytest.fixture
def criteria():
    """Standard search criteria."""
    return SearchCriteria(
        min_price=1800,
        max_price=2200,
        min_bedrooms=1,
        max_bedrooms=2,
        destination_postcode="N1 5AA",
        max_commute_minutes=30,
    )


@pytest.mark.integration
class TestFullPipeline:
    """Test the full scrape -> filter -> store pipeline."""

    @pytest.mark.asyncio
    async def test_pipeline_filters_and_dedupes(self, storage, mixed_properties, criteria):
        """Properties go through criteria filter, deduplication, then storage."""
        # Step 1: Apply criteria filter
        criteria_filter = CriteriaFilter(criteria)
        filtered = criteria_filter.filter_properties(mixed_properties)

        # Should exclude the expensive one
        assert len(filtered) == 3
        assert all(p.price_pcm <= 2200 for p in filtered)

        # Step 2: Deduplicate
        deduplicator = Deduplicator(enable_cross_platform=True)
        unique = deduplicator.deduplicate(filtered)

        # OpenRent and Rightmove listings are same property
        assert len(unique) == 2

        # Step 3: Filter to new only
        new = await storage.filter_new(unique)
        assert len(new) == 2

        # Step 4: Save to storage
        for prop in new:
            await storage.save_property(prop)

        # Verify saved
        count = await storage.get_property_count()
        assert count == 2

    @pytest.mark.asyncio
    async def test_pipeline_rerun_finds_no_new(self, storage, mixed_properties, criteria):
        """Running pipeline twice should find no new properties second time."""
        criteria_filter = CriteriaFilter(criteria)
        deduplicator = Deduplicator(enable_cross_platform=True)

        # First run
        filtered = criteria_filter.filter_properties(mixed_properties)
        unique = deduplicator.deduplicate(filtered)
        new = await storage.filter_new(unique)
        for prop in new:
            await storage.save_property(prop)

        # Second run
        filtered2 = criteria_filter.filter_properties(mixed_properties)
        unique2 = deduplicator.deduplicate(filtered2)
        new2 = await storage.filter_new(unique2)

        # No new properties
        assert len(new2) == 0

    @pytest.mark.asyncio
    async def test_pipeline_with_price_updates(self, storage, criteria):
        """Properties with updated prices should be handled correctly."""
        prop_v1 = Property(
            source=PropertySource.OPENRENT,
            source_id="price-test",
            url=HttpUrl("https://openrent.com/price-test"),
            title="Test Property",
            price_pcm=2000,
            bedrooms=1,
            address="Test Address",
            postcode="E8 1AA",
        )

        prop_v2 = Property(
            source=PropertySource.OPENRENT,
            source_id="price-test",
            url=HttpUrl("https://openrent.com/price-test"),
            title="Test Property",
            price_pcm=1950,  # Price reduced
            bedrooms=1,
            address="Test Address",
            postcode="E8 1AA",
        )

        # Save first version
        await storage.save_property(prop_v1)

        # Save second version (should update)
        await storage.save_property(prop_v2)

        # Should still only have one property
        count = await storage.get_property_count()
        assert count == 1

        # Should have updated price
        tracked = await storage.get_property(prop_v1.unique_id)
        assert tracked is not None
        assert tracked.property.price_pcm == 1950


@pytest.mark.integration
class TestScraperToFilter:
    """Test scrapers output flows correctly into filters."""

    def test_scraper_output_compatible_with_filter(self, criteria):
        """Scraper output should work directly with CriteriaFilter."""
        # Simulate scraper output (list of Property)
        scraped = [
            Property(
                source=PropertySource.OPENRENT,
                source_id="test1",
                url=HttpUrl("https://openrent.com/test1"),
                title="Test Property",
                price_pcm=1950,
                bedrooms=1,
                address="Test Address",
                first_seen=datetime.now(),
            )
        ]

        filter_ = CriteriaFilter(criteria)
        result = filter_.filter_properties(scraped)

        assert len(result) == 1
        assert result[0].unique_id == "openrent:test1"

    def test_filter_chain_preserves_property_data(self, criteria):
        """Filters should preserve all property data through the chain."""
        original = Property(
            source=PropertySource.RIGHTMOVE,
            source_id="preserve-test",
            url=HttpUrl("https://rightmove.co.uk/preserve-test"),
            title="Preserve Test Property",
            price_pcm=2000,
            bedrooms=2,
            address="123 Test Street",
            postcode="E8 3RH",
            latitude=51.5465,
            longitude=-0.0553,
            description="A test description",
            image_url=HttpUrl("https://example.com/image.jpg"),
            first_seen=datetime(2025, 1, 20, 10, 30),
        )

        # Through criteria filter
        filtered = CriteriaFilter(criteria).filter_properties([original])
        assert len(filtered) == 1

        # Through deduplicator
        deduped = Deduplicator(enable_cross_platform=True).deduplicate(filtered)
        assert len(deduped) == 1

        # All data preserved
        result = deduped[0]
        assert result.source == original.source
        assert result.source_id == original.source_id
        assert result.url == original.url
        assert result.title == original.title
        assert result.price_pcm == original.price_pcm
        assert result.bedrooms == original.bedrooms
        assert result.address == original.address
        assert result.postcode == original.postcode
        assert result.latitude == original.latitude
        assert result.longitude == original.longitude
        assert result.description == original.description
        assert result.image_url == original.image_url
        assert result.first_seen == original.first_seen


@pytest.mark.integration
class TestFilterToStorage:
    """Test filtered properties store correctly."""

    @pytest.mark.asyncio
    async def test_deduped_properties_store_correctly(self, storage):
        """After deduplication, properties store and retrieve correctly."""
        props = [
            Property(
                source=PropertySource.RIGHTMOVE,
                source_id="RM123",
                url=HttpUrl("https://rightmove.co.uk/RM123"),
                title="Test Flat",
                price_pcm=2000,
                bedrooms=2,
                address="Test Address",
                postcode="E8 1AB",
                first_seen=datetime(2025, 1, 20, 10, 0),
            )
        ]

        deduplicator = Deduplicator(enable_cross_platform=True)
        unique = deduplicator.deduplicate(props)

        for prop in unique:
            await storage.save_property(prop, commute_minutes=25)

        # Retrieve and verify
        stored = await storage.get_property("rightmove:RM123")
        assert stored is not None
        assert stored.property.title == "Test Flat"
        assert stored.commute_minutes == 25

    @pytest.mark.asyncio
    async def test_storage_handles_all_sources(self, storage):
        """Storage should handle properties from all sources."""
        sources = [
            (PropertySource.OPENRENT, "openrent"),
            (PropertySource.RIGHTMOVE, "rightmove"),
            (PropertySource.ZOOPLA, "zoopla"),
            (PropertySource.ONTHEMARKET, "onthemarket"),
        ]

        for source, prefix in sources:
            prop = Property(
                source=source,
                source_id=f"{prefix}-storage-test",
                url=HttpUrl(f"https://example.com/{prefix}"),
                title=f"{prefix.title()} Property",
                price_pcm=2000,
                bedrooms=1,
                address="Test Address",
            )
            await storage.save_property(prop)

        count = await storage.get_property_count()
        assert count == 4

        # Verify each can be retrieved
        for source, prefix in sources:
            unique_id = f"{source.value}:{prefix}-storage-test"
            tracked = await storage.get_property(unique_id)
            assert tracked is not None
            assert tracked.property.source == source


@pytest.mark.integration
class TestEdgeCases:
    """Test edge cases in the pipeline."""

    @pytest.mark.asyncio
    async def test_empty_scrape_results(self, storage, criteria):
        """Pipeline handles empty scrape results gracefully."""
        criteria_filter = CriteriaFilter(criteria)
        deduplicator = Deduplicator(enable_cross_platform=True)

        filtered = criteria_filter.filter_properties([])
        assert len(filtered) == 0

        unique = deduplicator.deduplicate(filtered)
        assert len(unique) == 0

        new = await storage.filter_new(unique)
        assert len(new) == 0

    def test_all_properties_filtered_out(self, criteria):
        """Pipeline handles when all properties are filtered out."""
        # All properties outside criteria
        expensive_properties = [
            Property(
                source=PropertySource.OPENRENT,
                source_id=f"expensive-{i}",
                url=HttpUrl(f"https://example.com/{i}"),
                title=f"Expensive Property {i}",
                price_pcm=5000,  # Way above budget
                bedrooms=1,
                address=f"Address {i}",
            )
            for i in range(5)
        ]

        filtered = CriteriaFilter(criteria).filter_properties(expensive_properties)
        assert len(filtered) == 0

    def test_properties_without_postcode_not_cross_deduped(self):
        """Properties without postcodes are kept even if otherwise similar."""
        props = [
            Property(
                source=PropertySource.OPENRENT,
                source_id="no-postcode-1",
                url=HttpUrl("https://openrent.com/1"),
                title="Property 1",
                price_pcm=2000,
                bedrooms=1,
                address="Address 1",
                postcode=None,
            ),
            Property(
                source=PropertySource.RIGHTMOVE,
                source_id="no-postcode-2",
                url=HttpUrl("https://rightmove.co.uk/2"),
                title="Property 2",
                price_pcm=2000,
                bedrooms=1,
                address="Address 2",
                postcode=None,
            ),
        ]

        deduped = Deduplicator(enable_cross_platform=True).deduplicate(props)
        # Without postcodes, can't confidently dedupe
        assert len(deduped) == 2

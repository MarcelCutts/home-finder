"""Selection, matching, and persistence contracts without the run coordinator."""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from pydantic import HttpUrl

from home_finder.db import PropertyStorage
from home_finder.filters.criteria import CriteriaFilter
from home_finder.matching.deduplication import Deduplicator
from home_finder.models import Property, PropertySource, SearchCriteria
from home_finder.pipeline.matching import consolidate_candidates


@pytest_asyncio.fixture
async def storage() -> AsyncGenerator[PropertyStorage, None]:
    """Create an in-memory storage instance."""
    storage = PropertyStorage(":memory:")
    await storage.initialize()
    yield storage
    await storage.close()


@pytest.fixture
def mixed_properties() -> list[Property]:
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
def criteria() -> SearchCriteria:
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
class TestSelectionAndPersistence:
    """Selected listings become distinct stored homes through filtering and matching."""

    @pytest.mark.asyncio
    async def test_pipeline_filters_and_dedupes(
        self, storage: PropertyStorage, mixed_properties: list[Property], criteria: SearchCriteria
    ) -> None:
        """Properties go through criteria filter, deduplication, then storage."""
        criteria_filter = CriteriaFilter(criteria)
        filtered = criteria_filter.filter_properties(mixed_properties)

        # Should exclude the expensive one
        assert len(filtered) == 3
        assert all(p.price_pcm <= 2200 for p in filtered)

        deduplicator = Deduplicator(enable_cross_platform=True)
        merged = await deduplicator.deduplicate_and_merge_async(filtered)

        # OpenRent and Rightmove listings are same property → merged
        assert len(merged) == 2

        new = await storage.filter_new_merged(merged)
        assert len(new) == 2

        for m in new:
            await storage.save_merged_property(m)

        # Verify saved
        count = await storage.web.get_property_count()
        assert count == 2

    @pytest.mark.asyncio
    async def test_pipeline_rerun_finds_no_new(
        self, storage: PropertyStorage, mixed_properties: list[Property], criteria: SearchCriteria
    ) -> None:
        """Running pipeline twice should find no new properties second time."""
        criteria_filter = CriteriaFilter(criteria)
        deduplicator = Deduplicator(enable_cross_platform=True)

        # First run
        filtered = criteria_filter.filter_properties(mixed_properties)
        merged = await deduplicator.deduplicate_and_merge_async(filtered)
        new = await storage.filter_new_merged(merged)
        for m in new:
            await storage.save_merged_property(m)

        # Second run
        filtered2 = criteria_filter.filter_properties(mixed_properties)
        merged2 = await deduplicator.deduplicate_and_merge_async(filtered2)
        new2 = await storage.filter_new_merged(merged2)

        # No new properties
        assert len(new2) == 0

    @pytest.mark.asyncio
    async def test_pipeline_with_price_updates(
        self, storage: PropertyStorage, criteria: SearchCriteria
    ) -> None:
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
        count = await storage.web.get_property_count()
        assert count == 1

        # Should have updated price
        tracked = await storage.get_property(prop_v1.unique_id)
        assert tracked is not None
        assert tracked.property.price_pcm == 1950


@pytest.mark.integration
class TestScraperToFilter:
    """Test scrapers output flows correctly into filters."""

    def test_scraper_output_compatible_with_filter(self, criteria: SearchCriteria) -> None:
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

    def test_filter_chain_preserves_property_data(self, criteria: SearchCriteria) -> None:
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

        # Through deduplicator (properties_to_merged wraps as single-source)
        wrapped = Deduplicator(enable_cross_platform=True).properties_to_merged(filtered)
        assert len(wrapped) == 1

        # All data preserved on canonical
        result = wrapped[0].canonical
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
    async def test_deduped_properties_store_correctly(self, storage: PropertyStorage) -> None:
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
        wrapped = deduplicator.properties_to_merged(props)

        for m in wrapped:
            await storage.save_property(m.canonical, commute_minutes=25)

        # Retrieve and verify
        stored = await storage.get_property("rightmove:RM123")
        assert stored is not None
        assert stored.property.title == "Test Flat"
        assert stored.commute_minutes == 25

    @pytest.mark.asyncio
    async def test_storage_handles_all_sources(self, storage: PropertyStorage) -> None:
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

        count = await storage.web.get_property_count()
        assert count == 4

        # Verify each can be retrieved
        for source, prefix in sources:
            unique_id = f"{source.value}:{prefix}-storage-test"
            tracked = await storage.get_property(unique_id)
            assert tracked is not None
            assert tracked.property.source == source


@pytest.mark.integration
class TestCrossPlatformConsolidation:
    """Test cross-platform dedup across pipeline re-runs."""

    @pytest.mark.asyncio
    async def test_cross_platform_consolidation_preserves_record_and_sent_status(
        self, storage: PropertyStorage, criteria: SearchCriteria
    ) -> None:
        """Run 1: OpenRent property saved. Run 2: same flat on Zoopla → no new record."""
        deduplicator = Deduplicator(enable_cross_platform=True)

        # Run 1: OpenRent property scraped, filtered, stored
        openrent_prop = Property(
            source=PropertySource.OPENRENT,
            source_id="OR-cross-1",
            url=HttpUrl("https://openrent.com/OR-cross-1"),
            title="2-bed in Hackney",
            price_pcm=2000,
            bedrooms=2,
            address="Flat 4, 123 Mare Street, Hackney",
            postcode="E8 3RH",
            latitude=51.5465,
            longitude=-0.0553,
            first_seen=datetime.now(UTC) - timedelta(days=14),
        )
        wrapped_run1 = deduplicator.properties_to_merged([openrent_prop])
        new_run1 = await storage.filter_new_merged(wrapped_run1)
        assert len(new_run1) == 1

        for m in new_run1:
            await storage.save_merged_property(m)
        await storage.mark_notified(openrent_prop.unique_id)

        count_after_run1 = await storage.web.get_property_count()
        assert count_after_run1 == 1

        # Run 2: Same flat appears on Zoopla (different unique_id)
        zoopla_prop = Property(
            source=PropertySource.ZOOPLA,
            source_id="ZP-cross-1",
            url=HttpUrl("https://zoopla.co.uk/ZP-cross-1"),
            title="2 bed flat in Mare Street",
            price_pcm=1950,
            bedrooms=2,
            address="123 Mare Street, Hackney E8 3RH",
            postcode="E8 3RH",
            latitude=51.54652,
            longitude=-0.05528,
            first_seen=datetime.now(UTC) - timedelta(days=7),
        )
        wrapped_run2 = deduplicator.properties_to_merged([zoopla_prop])
        new_run2 = await storage.filter_new_merged(wrapped_run2)
        # Zoopla listing passes filter_new_merged (different unique_id)
        assert len(new_run2) == 1

        # Simulate scrape-time write of Zoopla source_listing
        await storage.upsert_source_listings([zoopla_prop])

        result = await consolidate_candidates(deduplicator, new_run2, storage, set())

        # No genuinely new properties — Zoopla was absorbed into OpenRent anchor
        assert result.unmatched_candidates == []

        # DB still has only 1 record
        count_after_run2 = await storage.web.get_property_count()
        assert count_after_run2 == 1

        # But the record now has 2 sources
        import json

        conn = await storage._get_connection()
        cursor = await conn.execute(
            "SELECT sources, notification_status FROM properties WHERE unique_id = ?",
            (openrent_prop.unique_id,),
        )
        row = await cursor.fetchone()
        assert row is not None
        sources = json.loads(row["sources"])
        assert "openrent" in sources
        assert "zoopla" in sources
        # Notification status unchanged
        assert row["notification_status"] == "sent"


@pytest.mark.integration
class TestEdgeCases:
    """Test edge cases in the pipeline."""

    @pytest.mark.asyncio
    async def test_empty_scrape_results(
        self, storage: PropertyStorage, criteria: SearchCriteria
    ) -> None:
        """Pipeline handles empty scrape results gracefully."""
        criteria_filter = CriteriaFilter(criteria)
        deduplicator = Deduplicator(enable_cross_platform=True)

        filtered = criteria_filter.filter_properties([])
        assert len(filtered) == 0

        merged = await deduplicator.deduplicate_and_merge_async(filtered)
        assert len(merged) == 0

        new = await storage.filter_new_merged(merged)
        assert len(new) == 0

    def test_all_properties_filtered_out(self, criteria: SearchCriteria) -> None:
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

    @pytest.mark.asyncio
    async def test_properties_without_postcode_not_cross_deduped(self) -> None:
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

        deduped = await Deduplicator(enable_cross_platform=True).deduplicate_and_merge_async(props)
        # Without postcodes, can't confidently dedupe
        assert len(deduped) == 2

"""Tests for property storage with SQLite."""

import json
from collections.abc import AsyncGenerator
from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from pydantic import HttpUrl

from home_finder.db.storage import PropertyStorage
from home_finder.models import (
    MergedProperty,
    NotificationStatus,
    Property,
    PropertyImage,
    PropertySource,
    TransportMode,
)


@pytest.fixture
def storage_sample_property() -> Property:
    """Create a sample property for storage tests."""
    return Property(
        source=PropertySource.OPENRENT,
        source_id="12345",
        url=HttpUrl("https://openrent.com/12345"),
        title="Nice 1 bed flat",
        price_pcm=1900,
        bedrooms=1,
        address="123 Mare Street, E8 3RH",
        postcode="E8 3RH",
        latitude=51.5465,
        longitude=-0.0553,
    )


@pytest.fixture
def sample_property_2() -> Property:
    """Create another sample property."""
    return Property(
        source=PropertySource.RIGHTMOVE,
        source_id="67890",
        url=HttpUrl("https://rightmove.co.uk/67890"),
        title="Lovely 2 bed apartment",
        price_pcm=2100,
        bedrooms=2,
        address="45 Dalston Lane, E8 2PB",
        postcode="E8 2PB",
    )


@pytest_asyncio.fixture
async def storage() -> AsyncGenerator[PropertyStorage, None]:
    """Create an in-memory storage instance."""
    storage = PropertyStorage(":memory:")
    await storage.initialize()
    yield storage


class TestPropertyStorage:
    """Tests for PropertyStorage."""

    @pytest.mark.asyncio
    async def test_initialize_creates_tables(self) -> None:
        """Test that initialize creates the required tables."""
        storage = PropertyStorage(":memory:")
        await storage.initialize()

        # Should be able to insert without errors
        prop = Property(
            source=PropertySource.OPENRENT,
            source_id="1",
            url=HttpUrl("https://example.com/1"),
            title="Test",
            price_pcm=2000,
            bedrooms=1,
            address="Test Address",
        )
        await storage.save_property(prop)

    @pytest.mark.asyncio
    async def test_save_property(
        self, storage: PropertyStorage, storage_sample_property: Property
    ) -> None:
        """Test saving a property."""
        await storage.save_property(storage_sample_property)

        # Should be able to retrieve it
        tracked = await storage.get_property(storage_sample_property.unique_id)
        assert tracked is not None
        assert tracked.property.source_id == "12345"
        assert tracked.property.price_pcm == 1900
        assert tracked.notification_status == NotificationStatus.PENDING

    @pytest.mark.asyncio
    async def test_save_property_with_commute(
        self, storage: PropertyStorage, storage_sample_property: Property
    ) -> None:
        """Test saving a property with commute info."""
        await storage.save_property(
            storage_sample_property,
            commute_minutes=20,
            transport_mode=TransportMode.CYCLING,
        )

        tracked = await storage.get_property(storage_sample_property.unique_id)
        assert tracked is not None
        assert tracked.commute_minutes == 20
        assert tracked.transport_mode == TransportMode.CYCLING

    @pytest.mark.asyncio
    async def test_is_seen_false_for_new_property(
        self, storage: PropertyStorage, storage_sample_property: Property
    ) -> None:
        """Test that new properties are not seen."""
        is_seen = await storage.is_seen(storage_sample_property.unique_id)
        assert is_seen is False

    @pytest.mark.asyncio
    async def test_is_seen_true_after_save(
        self, storage: PropertyStorage, storage_sample_property: Property
    ) -> None:
        """Test that saved properties are marked as seen."""
        await storage.save_property(storage_sample_property)
        is_seen = await storage.is_seen(storage_sample_property.unique_id)
        assert is_seen is True

    @pytest.mark.asyncio
    async def test_mark_notified(
        self, storage: PropertyStorage, storage_sample_property: Property
    ) -> None:
        """Test marking a property as notified."""
        await storage.save_property(storage_sample_property)
        await storage.mark_notified(storage_sample_property.unique_id)

        tracked = await storage.get_property(storage_sample_property.unique_id)
        assert tracked is not None
        assert tracked.notification_status == NotificationStatus.SENT
        assert tracked.notified_at is not None

    @pytest.mark.asyncio
    async def test_mark_notification_failed(
        self, storage: PropertyStorage, storage_sample_property: Property
    ) -> None:
        """Test marking notification as failed."""
        await storage.save_property(storage_sample_property)
        await storage.mark_notification_failed(storage_sample_property.unique_id)

        tracked = await storage.get_property(storage_sample_property.unique_id)
        assert tracked is not None
        assert tracked.notification_status == NotificationStatus.FAILED

    @pytest.mark.asyncio
    async def test_get_pending_notifications(
        self,
        storage: PropertyStorage,
        storage_sample_property: Property,
        sample_property_2: Property,
    ) -> None:
        """Test getting properties pending notification."""
        # Save two properties
        await storage.save_property(storage_sample_property)
        await storage.save_property(sample_property_2)

        # Both should be pending
        pending = await storage.get_pending_notifications()
        assert len(pending) == 2

        # Mark one as notified
        await storage.mark_notified(storage_sample_property.unique_id)

        # Only one should be pending now
        pending = await storage.get_pending_notifications()
        assert len(pending) == 1
        assert pending[0].property.source_id == "67890"

    @pytest.mark.asyncio
    async def test_get_property_not_found(self, storage: PropertyStorage) -> None:
        """Test getting a property that doesn't exist."""
        tracked = await storage.get_property("nonexistent:123")
        assert tracked is None

    @pytest.mark.asyncio
    async def test_get_all_properties(
        self,
        storage: PropertyStorage,
        storage_sample_property: Property,
        sample_property_2: Property,
    ) -> None:
        """Test getting all properties."""
        await storage.save_property(storage_sample_property)
        await storage.save_property(sample_property_2)

        all_props = await storage.get_all_properties()
        assert len(all_props) == 2

    @pytest.mark.asyncio
    async def test_save_property_updates_existing(
        self, storage: PropertyStorage, storage_sample_property: Property
    ) -> None:
        """Test that saving same property updates instead of duplicating."""
        await storage.save_property(storage_sample_property)
        await storage.save_property(storage_sample_property)  # Save again

        all_props = await storage.get_all_properties()
        assert len(all_props) == 1

    @pytest.mark.asyncio
    async def test_filter_new_properties(
        self,
        storage: PropertyStorage,
        storage_sample_property: Property,
        sample_property_2: Property,
    ) -> None:
        """Test filtering to only new properties."""
        # Save first property
        await storage.save_property(storage_sample_property)

        # Filter both - should only return the new one
        new_props = await storage.filter_new([storage_sample_property, sample_property_2])
        assert len(new_props) == 1
        assert new_props[0].unique_id == sample_property_2.unique_id

    @pytest.mark.asyncio
    async def test_get_property_count(
        self,
        storage: PropertyStorage,
        storage_sample_property: Property,
        sample_property_2: Property,
    ) -> None:
        """Test getting total property count."""
        assert await storage.get_property_count() == 0

        await storage.save_property(storage_sample_property)
        assert await storage.get_property_count() == 1

        await storage.save_property(sample_property_2)
        assert await storage.get_property_count() == 2

    @pytest.mark.asyncio
    async def test_get_unsent_notifications_returns_pending(
        self,
        storage: PropertyStorage,
        storage_sample_property: Property,
    ) -> None:
        """Test that get_unsent_notifications returns pending properties."""
        await storage.save_property(storage_sample_property)

        unsent = await storage.get_unsent_notifications()
        assert len(unsent) == 1
        assert unsent[0].notification_status == NotificationStatus.PENDING

    @pytest.mark.asyncio
    async def test_get_unsent_notifications_returns_failed(
        self,
        storage: PropertyStorage,
        storage_sample_property: Property,
    ) -> None:
        """Test that get_unsent_notifications returns failed properties."""
        await storage.save_property(storage_sample_property)
        await storage.mark_notification_failed(storage_sample_property.unique_id)

        unsent = await storage.get_unsent_notifications()
        assert len(unsent) == 1
        assert unsent[0].notification_status == NotificationStatus.FAILED

    @pytest.mark.asyncio
    async def test_get_unsent_notifications_excludes_sent(
        self,
        storage: PropertyStorage,
        storage_sample_property: Property,
        sample_property_2: Property,
    ) -> None:
        """Test that get_unsent_notifications excludes sent properties."""
        # Save both properties
        await storage.save_property(storage_sample_property)
        await storage.save_property(sample_property_2)

        # Mark first as sent, second as failed
        await storage.mark_notified(storage_sample_property.unique_id)
        await storage.mark_notification_failed(sample_property_2.unique_id)

        # Should only return the failed one
        unsent = await storage.get_unsent_notifications()
        assert len(unsent) == 1
        assert unsent[0].property.source_id == "67890"
        assert unsent[0].notification_status == NotificationStatus.FAILED

    @pytest.mark.asyncio
    async def test_get_unsent_notifications_returns_both_pending_and_failed(
        self,
        storage: PropertyStorage,
        storage_sample_property: Property,
        sample_property_2: Property,
    ) -> None:
        """Test that get_unsent_notifications returns both pending and failed."""
        # Save both - first stays pending, second marked as failed
        await storage.save_property(storage_sample_property)
        await storage.save_property(sample_property_2)
        await storage.mark_notification_failed(sample_property_2.unique_id)

        unsent = await storage.get_unsent_notifications()
        assert len(unsent) == 2

        statuses = {u.notification_status for u in unsent}
        assert NotificationStatus.PENDING in statuses
        assert NotificationStatus.FAILED in statuses


class TestGetMapMarkers:
    """Tests for get_map_markers."""

    @pytest.mark.asyncio
    async def test_returns_all_matching_with_coords(self, storage: PropertyStorage) -> None:
        """Should return all properties with coordinates, not paginated."""
        for i in range(5):
            prop = Property(
                source=PropertySource.OPENRENT,
                source_id=f"map-{i}",
                url=HttpUrl(f"https://openrent.com/map-{i}"),
                title=f"Flat {i}",
                price_pcm=1800 + i * 100,
                bedrooms=1,
                address=f"{i} Test Street",
                postcode="E8 3RH",
                latitude=51.5465 + i * 0.001,
                longitude=-0.0553,
                image_url=HttpUrl("https://example.com/img.jpg"),
            )
            await storage.save_property(prop)

        markers = await storage.get_map_markers()
        assert len(markers) == 5
        # Check marker structure
        m = markers[0]
        assert "id" in m
        assert "lat" in m
        assert "lon" in m
        assert "price" in m
        assert "url" in m
        assert m["url"].startswith("/property/")

    @pytest.mark.asyncio
    async def test_excludes_properties_without_coords(self, storage: PropertyStorage) -> None:
        """Properties missing lat/lon should not appear in map markers."""
        with_coords = Property(
            source=PropertySource.OPENRENT,
            source_id="with-coords",
            url=HttpUrl("https://openrent.com/with-coords"),
            title="Has coords",
            price_pcm=1900,
            bedrooms=1,
            address="10 Test Street",
            postcode="E8 3RH",
            latitude=51.5465,
            longitude=-0.0553,
            image_url=HttpUrl("https://example.com/img.jpg"),
        )
        without_coords = Property(
            source=PropertySource.RIGHTMOVE,
            source_id="no-coords",
            url=HttpUrl("https://rightmove.co.uk/no-coords"),
            title="No coords",
            price_pcm=2000,
            bedrooms=2,
            address="20 Test Street",
            postcode="E8",
        )
        await storage.save_property(with_coords)
        await storage.save_property(without_coords)

        markers = await storage.get_map_markers()
        assert len(markers) == 1
        assert markers[0]["id"] == with_coords.unique_id

    @pytest.mark.asyncio
    async def test_respects_filters(self, storage: PropertyStorage) -> None:
        """Filters (e.g. bedrooms) should apply to map markers."""
        one_bed = Property(
            source=PropertySource.OPENRENT,
            source_id="1bed",
            url=HttpUrl("https://openrent.com/1bed"),
            title="1 bed",
            price_pcm=1900,
            bedrooms=1,
            address="10 Test Street",
            postcode="E8 3RH",
            latitude=51.5465,
            longitude=-0.0553,
            image_url=HttpUrl("https://example.com/img.jpg"),
        )
        two_bed = Property(
            source=PropertySource.OPENRENT,
            source_id="2bed",
            url=HttpUrl("https://openrent.com/2bed"),
            title="2 bed",
            price_pcm=2200,
            bedrooms=2,
            address="20 Test Street",
            postcode="E8 3RH",
            latitude=51.5470,
            longitude=-0.0550,
            image_url=HttpUrl("https://example.com/img.jpg"),
        )
        await storage.save_property(one_bed)
        await storage.save_property(two_bed)

        markers = await storage.get_map_markers(bedrooms=1)
        assert len(markers) == 1
        assert markers[0]["id"] == one_bed.unique_id


class TestGetRecentPropertiesForDedup:
    """Tests for get_recent_properties_for_dedup."""

    @pytest.mark.asyncio
    async def test_returns_recent_properties_as_merged(self, storage: PropertyStorage) -> None:
        """Properties within the lookback window are returned as MergedProperty."""
        prop = Property(
            source=PropertySource.OPENRENT,
            source_id="dedup-1",
            url=HttpUrl("https://openrent.com/dedup-1"),
            title="Test flat",
            price_pcm=2000,
            bedrooms=2,
            address="123 Mare Street",
            postcode="E8 3RH",
            latitude=51.5465,
            longitude=-0.0553,
        )
        merged = MergedProperty(
            canonical=prop,
            sources=(PropertySource.OPENRENT,),
            source_urls={PropertySource.OPENRENT: prop.url},
            images=(),
            floorplan=None,
            min_price=2000,
            max_price=2000,
            descriptions={PropertySource.OPENRENT: "Nice flat"},
        )
        await storage.save_merged_property(merged)

        # Save some images
        images = [
            PropertyImage(
                url=HttpUrl("https://example.com/img1.jpg"),
                source=PropertySource.OPENRENT,
                image_type="gallery",
            ),
            PropertyImage(
                url=HttpUrl("https://example.com/floor.jpg"),
                source=PropertySource.OPENRENT,
                image_type="floorplan",
            ),
        ]
        await storage.save_property_images(prop.unique_id, images)

        results = await storage.get_recent_properties_for_dedup(days=7)
        assert len(results) == 1
        result = results[0]
        assert result.canonical.unique_id == prop.unique_id
        assert result.min_price == 2000
        assert result.max_price == 2000
        assert len(result.images) == 1  # gallery only
        assert result.floorplan is not None

    @pytest.mark.asyncio
    async def test_excludes_old_properties(self, storage: PropertyStorage) -> None:
        """Properties older than the lookback window are excluded."""
        from datetime import UTC

        old_prop = Property(
            source=PropertySource.OPENRENT,
            source_id="old-1",
            url=HttpUrl("https://openrent.com/old-1"),
            title="Old flat",
            price_pcm=1500,
            bedrooms=1,
            address="Old Address",
            first_seen=datetime.now(UTC) - timedelta(days=60),
        )
        await storage.save_property(old_prop)

        recent_prop = Property(
            source=PropertySource.ZOOPLA,
            source_id="recent-1",
            url=HttpUrl("https://zoopla.co.uk/recent-1"),
            title="Recent flat",
            price_pcm=1800,
            bedrooms=1,
            address="Recent Address",
            postcode="E8 1AA",
        )
        await storage.save_property(recent_prop)

        results = await storage.get_recent_properties_for_dedup(days=7)
        assert len(results) == 1
        assert results[0].canonical.source_id == "recent-1"

    @pytest.mark.asyncio
    async def test_reconstructs_multi_source_data(self, storage: PropertyStorage) -> None:
        """Multi-source properties from DB are correctly reconstructed."""
        prop = Property(
            source=PropertySource.OPENRENT,
            source_id="multi-1",
            url=HttpUrl("https://openrent.com/multi-1"),
            title="Multi-source flat",
            price_pcm=2000,
            bedrooms=2,
            address="123 Mare Street",
            postcode="E8 3RH",
        )
        merged = MergedProperty(
            canonical=prop,
            sources=(PropertySource.OPENRENT, PropertySource.ZOOPLA),
            source_urls={
                PropertySource.OPENRENT: prop.url,
                PropertySource.ZOOPLA: HttpUrl("https://zoopla.co.uk/z-1"),
            },
            images=(),
            floorplan=None,
            min_price=1950,
            max_price=2000,
            descriptions={
                PropertySource.OPENRENT: "OR desc",
                PropertySource.ZOOPLA: "ZP desc",
            },
        )
        await storage.save_merged_property(merged)

        results = await storage.get_recent_properties_for_dedup(days=7)
        assert len(results) == 1
        result = results[0]
        assert len(result.sources) == 2
        assert PropertySource.OPENRENT in result.sources
        assert PropertySource.ZOOPLA in result.sources
        assert result.min_price == 1950
        assert result.max_price == 2000
        assert PropertySource.OPENRENT in result.descriptions
        assert PropertySource.ZOOPLA in result.descriptions


class TestUpdateMergedSources:
    """Tests for update_merged_sources."""

    @pytest.mark.asyncio
    async def test_preserves_notification_status(self, storage: PropertyStorage) -> None:
        """notification_status remains 'sent' after source update."""
        prop = Property(
            source=PropertySource.OPENRENT,
            source_id="notify-1",
            url=HttpUrl("https://openrent.com/notify-1"),
            title="Notified flat",
            price_pcm=2000,
            bedrooms=2,
            address="123 Mare Street",
            postcode="E8 3RH",
        )
        merged = MergedProperty(
            canonical=prop,
            sources=(PropertySource.OPENRENT,),
            source_urls={PropertySource.OPENRENT: prop.url},
            images=(),
            floorplan=None,
            min_price=2000,
            max_price=2000,
            descriptions={},
        )
        await storage.save_merged_property(merged)
        await storage.mark_notified(prop.unique_id)

        # Update with new source
        updated = MergedProperty(
            canonical=prop,
            sources=(PropertySource.OPENRENT, PropertySource.ZOOPLA),
            source_urls={
                PropertySource.OPENRENT: prop.url,
                PropertySource.ZOOPLA: HttpUrl("https://zoopla.co.uk/z-1"),
            },
            images=(),
            floorplan=None,
            min_price=1950,
            max_price=2000,
            descriptions={PropertySource.ZOOPLA: "ZP desc"},
        )
        await storage.update_merged_sources(prop.unique_id, updated)

        tracked = await storage.get_property(prop.unique_id)
        assert tracked is not None
        assert tracked.notification_status == NotificationStatus.SENT

    @pytest.mark.asyncio
    async def test_expands_price_range(self, storage: PropertyStorage) -> None:
        """Price range is expanded when new source has different price."""
        prop = Property(
            source=PropertySource.OPENRENT,
            source_id="price-1",
            url=HttpUrl("https://openrent.com/price-1"),
            title="Price test flat",
            price_pcm=2000,
            bedrooms=2,
            address="123 Mare Street",
            postcode="E8 3RH",
        )
        merged = MergedProperty(
            canonical=prop,
            sources=(PropertySource.OPENRENT,),
            source_urls={PropertySource.OPENRENT: prop.url},
            images=(),
            floorplan=None,
            min_price=2000,
            max_price=2000,
            descriptions={},
        )
        await storage.save_merged_property(merged)

        updated = MergedProperty(
            canonical=prop,
            sources=(PropertySource.OPENRENT, PropertySource.ZOOPLA),
            source_urls={
                PropertySource.OPENRENT: prop.url,
                PropertySource.ZOOPLA: HttpUrl("https://zoopla.co.uk/z-1"),
            },
            images=(),
            floorplan=None,
            min_price=1950,
            max_price=2000,
            descriptions={},
        )
        await storage.update_merged_sources(prop.unique_id, updated)

        conn = await storage._get_connection()
        cursor = await conn.execute(
            "SELECT min_price, max_price FROM properties WHERE unique_id = ?",
            (prop.unique_id,),
        )
        row = await cursor.fetchone()
        assert row["min_price"] == 1950
        assert row["max_price"] == 2000

    @pytest.mark.asyncio
    async def test_adds_new_sources_and_urls(self, storage: PropertyStorage) -> None:
        """Sources and source_urls are merged correctly."""
        prop = Property(
            source=PropertySource.OPENRENT,
            source_id="src-1",
            url=HttpUrl("https://openrent.com/src-1"),
            title="Source test flat",
            price_pcm=2000,
            bedrooms=2,
            address="123 Mare Street",
            postcode="E8 3RH",
        )
        merged = MergedProperty(
            canonical=prop,
            sources=(PropertySource.OPENRENT,),
            source_urls={PropertySource.OPENRENT: prop.url},
            images=(),
            floorplan=None,
            min_price=2000,
            max_price=2000,
            descriptions={PropertySource.OPENRENT: "OR desc"},
        )
        await storage.save_merged_property(merged)

        updated = MergedProperty(
            canonical=prop,
            sources=(PropertySource.OPENRENT, PropertySource.ZOOPLA),
            source_urls={
                PropertySource.OPENRENT: prop.url,
                PropertySource.ZOOPLA: HttpUrl("https://zoopla.co.uk/z-1"),
            },
            images=(),
            floorplan=None,
            min_price=2000,
            max_price=2000,
            descriptions={
                PropertySource.OPENRENT: "OR desc",
                PropertySource.ZOOPLA: "ZP desc",
            },
        )
        await storage.update_merged_sources(prop.unique_id, updated)

        conn = await storage._get_connection()
        cursor = await conn.execute(
            "SELECT sources, source_urls, descriptions_json FROM properties WHERE unique_id = ?",
            (prop.unique_id,),
        )
        row = await cursor.fetchone()
        sources = json.loads(row["sources"])
        source_urls = json.loads(row["source_urls"])
        descriptions = json.loads(row["descriptions_json"])

        assert "openrent" in sources
        assert "zoopla" in sources
        assert "openrent" in source_urls
        assert "zoopla" in source_urls
        assert descriptions["openrent"] == "OR desc"
        assert descriptions["zoopla"] == "ZP desc"

    @pytest.mark.asyncio
    async def test_saves_new_images(self, storage: PropertyStorage) -> None:
        """Images from the new source are saved to property_images."""
        prop = Property(
            source=PropertySource.OPENRENT,
            source_id="img-1",
            url=HttpUrl("https://openrent.com/img-1"),
            title="Image test flat",
            price_pcm=2000,
            bedrooms=2,
            address="123 Mare Street",
            postcode="E8 3RH",
        )
        merged = MergedProperty(
            canonical=prop,
            sources=(PropertySource.OPENRENT,),
            source_urls={PropertySource.OPENRENT: prop.url},
            images=(),
            floorplan=None,
            min_price=2000,
            max_price=2000,
            descriptions={},
        )
        await storage.save_merged_property(merged)

        # Update with new source that has images
        new_img = PropertyImage(
            url=HttpUrl("https://zoopla.co.uk/img-new.jpg"),
            source=PropertySource.ZOOPLA,
            image_type="gallery",
        )
        updated = MergedProperty(
            canonical=prop,
            sources=(PropertySource.OPENRENT, PropertySource.ZOOPLA),
            source_urls={
                PropertySource.OPENRENT: prop.url,
                PropertySource.ZOOPLA: HttpUrl("https://zoopla.co.uk/z-1"),
            },
            images=(new_img,),
            floorplan=None,
            min_price=2000,
            max_price=2000,
            descriptions={},
        )
        await storage.update_merged_sources(prop.unique_id, updated)

        images = await storage.get_property_images(prop.unique_id)
        assert len(images) == 1
        assert str(images[0].url) == "https://zoopla.co.uk/img-new.jpg"

    @pytest.mark.asyncio
    async def test_nonexistent_property_is_noop(self, storage: PropertyStorage) -> None:
        """Updating a non-existent property does nothing (no error)."""
        prop = Property(
            source=PropertySource.OPENRENT,
            source_id="ghost-1",
            url=HttpUrl("https://openrent.com/ghost-1"),
            title="Ghost flat",
            price_pcm=2000,
            bedrooms=2,
            address="Nowhere",
        )
        merged = MergedProperty(
            canonical=prop,
            sources=(PropertySource.OPENRENT,),
            source_urls={PropertySource.OPENRENT: prop.url},
            images=(),
            floorplan=None,
            min_price=2000,
            max_price=2000,
            descriptions={},
        )
        # Should not raise
        await storage.update_merged_sources("nonexistent:999", merged)

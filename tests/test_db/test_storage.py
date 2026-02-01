"""Tests for property storage with SQLite."""

import pytest
import pytest_asyncio
from pydantic import HttpUrl

from home_finder.db.storage import PropertyStorage
from home_finder.models import (
    NotificationStatus,
    Property,
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
async def storage() -> PropertyStorage:
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

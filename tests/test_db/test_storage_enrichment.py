"""Tests for unenriched property storage and retry lifecycle."""

from collections.abc import AsyncGenerator

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


def _make_property(
    source: PropertySource = PropertySource.ZOOPLA,
    source_id: str = "z-1",
    postcode: str | None = "E8 3RH",
) -> Property:
    return Property(
        source=source,
        source_id=source_id,
        url=HttpUrl(f"https://example.com/{source.value}/{source_id}"),
        title="Test flat",
        price_pcm=2000,
        bedrooms=2,
        address="123 Test St",
        postcode=postcode,
        latitude=51.5465,
        longitude=-0.0553,
        image_url=HttpUrl("https://example.com/img.jpg"),
    )


def _make_merged(
    prop: Property | None = None,
    sources: tuple[PropertySource, ...] | None = None,
) -> MergedProperty:
    if prop is None:
        prop = _make_property()
    if sources is None:
        sources = (prop.source,)
    return MergedProperty(
        canonical=prop,
        sources=sources,
        source_urls=dict.fromkeys(sources, prop.url),
        images=(),
        floorplan=None,
        min_price=prop.price_pcm,
        max_price=prop.price_pcm,
    )


@pytest_asyncio.fixture
async def storage() -> AsyncGenerator[PropertyStorage, None]:
    s = PropertyStorage(":memory:")
    await s.initialize()
    yield s


class TestSaveUnenrichedProperty:
    """Tests for save_unenriched_property."""

    @pytest.mark.asyncio
    async def test_saves_with_pending_status(self, storage: PropertyStorage) -> None:
        """First save creates a pending row with attempts=1."""
        merged = _make_merged()
        await storage.save_unenriched_property(
            merged, commute_minutes=15, transport_mode=TransportMode.CYCLING
        )

        conn = await storage._get_connection()
        cursor = await conn.execute(
            "SELECT enrichment_status, enrichment_attempts, notification_status, commute_minutes "
            "FROM properties WHERE unique_id = ?",
            (merged.unique_id,),
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row["enrichment_status"] == "pending"
        assert row["enrichment_attempts"] == 1
        assert row["notification_status"] == NotificationStatus.PENDING_ENRICHMENT.value
        assert row["commute_minutes"] == 15

    @pytest.mark.asyncio
    async def test_increments_attempts_on_conflict(self, storage: PropertyStorage) -> None:
        """Second save for same property just increments attempts."""
        merged = _make_merged()
        await storage.save_unenriched_property(merged)
        await storage.save_unenriched_property(merged)

        conn = await storage._get_connection()
        cursor = await conn.execute(
            "SELECT enrichment_attempts FROM properties WHERE unique_id = ?",
            (merged.unique_id,),
        )
        row = await cursor.fetchone()
        assert row["enrichment_attempts"] == 2

    @pytest.mark.asyncio
    async def test_preserves_existing_fields_on_conflict(self, storage: PropertyStorage) -> None:
        """Conflict update does not overwrite commute data or other fields."""
        merged = _make_merged()
        await storage.save_unenriched_property(
            merged, commute_minutes=20, transport_mode=TransportMode.CYCLING
        )

        # Second save with different commute data should NOT overwrite
        await storage.save_unenriched_property(
            merged, commute_minutes=99, transport_mode=TransportMode.PUBLIC_TRANSPORT
        )

        conn = await storage._get_connection()
        cursor = await conn.execute(
            "SELECT commute_minutes, transport_mode FROM properties WHERE unique_id = ?",
            (merged.unique_id,),
        )
        row = await cursor.fetchone()
        # Original values preserved
        assert row["commute_minutes"] == 20
        assert row["transport_mode"] == "cycling"


class TestGetUnenrichedProperties:
    """Tests for get_unenriched_properties."""

    @pytest.mark.asyncio
    async def test_returns_pending_properties(self, storage: PropertyStorage) -> None:
        """Should return properties with enrichment_status='pending'."""
        merged = _make_merged()
        await storage.save_unenriched_property(merged)

        result = await storage.get_unenriched_properties(max_attempts=3)
        assert len(result) == 1
        assert result[0].unique_id == merged.unique_id

    @pytest.mark.asyncio
    async def test_excludes_enriched_properties(self, storage: PropertyStorage) -> None:
        """Should not return enriched properties."""
        # Save a normal enriched property
        merged = _make_merged()
        await storage.save_merged_property(merged)

        result = await storage.get_unenriched_properties(max_attempts=3)
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_excludes_over_max_attempts(self, storage: PropertyStorage) -> None:
        """Should exclude properties that hit the max attempts threshold."""
        merged = _make_merged()
        await storage.save_unenriched_property(merged)
        await storage.save_unenriched_property(merged)  # attempts=2
        await storage.save_unenriched_property(merged)  # attempts=3

        result = await storage.get_unenriched_properties(max_attempts=3)
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_reconstructs_merged_property(self, storage: PropertyStorage) -> None:
        """Should reconstruct MergedProperty with sources, descriptions, prices."""
        prop = _make_property()
        merged = MergedProperty(
            canonical=prop,
            sources=(PropertySource.ZOOPLA, PropertySource.OPENRENT),
            source_urls={
                PropertySource.ZOOPLA: prop.url,
                PropertySource.OPENRENT: HttpUrl("https://openrent.com/123"),
            },
            images=(),
            floorplan=None,
            min_price=1900,
            max_price=2100,
            descriptions={PropertySource.ZOOPLA: "Nice flat"},
        )
        await storage.save_unenriched_property(merged)

        result = await storage.get_unenriched_properties(max_attempts=3)
        assert len(result) == 1
        r = result[0]
        assert set(r.sources) == {PropertySource.ZOOPLA, PropertySource.OPENRENT}
        assert r.min_price == 1900
        assert r.max_price == 2100
        assert r.descriptions[PropertySource.ZOOPLA] == "Nice flat"
        # No images — enrichment failed
        assert len(r.images) == 0
        assert r.floorplan is None


class TestMarkEnriched:
    """Tests for mark_enriched."""

    @pytest.mark.asyncio
    async def test_transitions_to_enriched(self, storage: PropertyStorage) -> None:
        """Should set enrichment_status='enriched' and notification_status='pending'."""
        merged = _make_merged()
        await storage.save_unenriched_property(merged)

        await storage.mark_enriched(merged.unique_id)

        conn = await storage._get_connection()
        cursor = await conn.execute(
            "SELECT enrichment_status, notification_status FROM properties WHERE unique_id = ?",
            (merged.unique_id,),
        )
        row = await cursor.fetchone()
        assert row["enrichment_status"] == "enriched"
        assert row["notification_status"] == NotificationStatus.PENDING.value

    @pytest.mark.asyncio
    async def test_noop_for_already_pending(self, storage: PropertyStorage) -> None:
        """Should be a no-op for properties with notification_status='pending' (new properties)."""
        merged = _make_merged()
        await storage.save_merged_property(merged)  # saves with notification_status='pending'

        await storage.mark_enriched(merged.unique_id)

        tracked = await storage.get_property(merged.unique_id)
        assert tracked is not None
        assert tracked.notification_status == NotificationStatus.PENDING

    @pytest.mark.asyncio
    async def test_noop_for_sent(self, storage: PropertyStorage) -> None:
        """Should not change already-sent notifications."""
        merged = _make_merged()
        await storage.save_merged_property(merged)
        await storage.mark_notified(merged.unique_id)

        await storage.mark_enriched(merged.unique_id)

        tracked = await storage.get_property(merged.unique_id)
        assert tracked is not None
        assert tracked.notification_status == NotificationStatus.SENT


class TestExpireUnenriched:
    """Tests for expire_unenriched."""

    @pytest.mark.asyncio
    async def test_expires_over_threshold(self, storage: PropertyStorage) -> None:
        """Should mark properties with >= max_attempts as 'failed'."""
        merged = _make_merged()
        await storage.save_unenriched_property(merged)
        await storage.save_unenriched_property(merged)  # attempts=2
        await storage.save_unenriched_property(merged)  # attempts=3

        count = await storage.expire_unenriched(max_attempts=3)
        assert count == 1

        conn = await storage._get_connection()
        cursor = await conn.execute(
            "SELECT enrichment_status FROM properties WHERE unique_id = ?",
            (merged.unique_id,),
        )
        row = await cursor.fetchone()
        assert row["enrichment_status"] == "failed"

    @pytest.mark.asyncio
    async def test_does_not_expire_below_threshold(self, storage: PropertyStorage) -> None:
        """Should not expire properties with fewer attempts."""
        merged = _make_merged()
        await storage.save_unenriched_property(merged)  # attempts=1

        count = await storage.expire_unenriched(max_attempts=3)
        assert count == 0

    @pytest.mark.asyncio
    async def test_expired_not_returned_by_get_unenriched(self, storage: PropertyStorage) -> None:
        """Expired properties should not be returned by get_unenriched_properties."""
        merged = _make_merged()
        await storage.save_unenriched_property(merged)
        await storage.save_unenriched_property(merged)
        await storage.save_unenriched_property(merged)

        await storage.expire_unenriched(max_attempts=3)

        result = await storage.get_unenriched_properties(max_attempts=10)
        assert len(result) == 0  # status is 'failed', not 'pending'


class TestDeleteProperty:
    """Tests for delete_property."""

    @pytest.mark.asyncio
    async def test_deletes_property_and_images(self, storage: PropertyStorage) -> None:
        """Should delete the property row and its images."""
        merged = _make_merged()
        await storage.save_merged_property(merged)
        images = [
            PropertyImage(
                url=HttpUrl("https://example.com/img1.jpg"),
                source=PropertySource.ZOOPLA,
                image_type="gallery",
            )
        ]
        await storage.save_property_images(merged.unique_id, images)

        await storage.delete_property(merged.unique_id)

        assert await storage.get_property(merged.unique_id) is None
        stored_images = await storage.get_property_images(merged.unique_id)
        assert len(stored_images) == 0

    @pytest.mark.asyncio
    async def test_noop_for_nonexistent(self, storage: PropertyStorage) -> None:
        """Should not raise for nonexistent property."""
        await storage.delete_property("nonexistent:999")


class TestDedupAnchorExclusion:
    """Unenriched properties should be excluded from dedup anchors."""

    @pytest.mark.asyncio
    async def test_excludes_pending_from_anchors(self, storage: PropertyStorage) -> None:
        """Pending enrichment properties should not appear as dedup anchors."""
        # Save an unenriched property
        unenriched = _make_merged(_make_property(source_id="unenriched-1"))
        await storage.save_unenriched_property(unenriched)

        # Save a normal enriched property
        enriched_prop = _make_property(source=PropertySource.OPENRENT, source_id="enriched-1")
        enriched = _make_merged(enriched_prop, sources=(PropertySource.OPENRENT,))
        await storage.save_merged_property(enriched)

        anchors = await storage.get_recent_properties_for_dedup(days=7)
        anchor_ids = {a.unique_id for a in anchors}

        assert enriched.unique_id in anchor_ids
        assert unenriched.unique_id not in anchor_ids


class TestDashboardExclusion:
    """Unenriched properties should be excluded from the dashboard."""

    @pytest.mark.asyncio
    async def test_excludes_pending_from_paginated(self, storage: PropertyStorage) -> None:
        """Pending enrichment properties should not appear on the dashboard."""
        # Save unenriched
        unenriched = _make_merged(_make_property(source_id="unenriched-1"))
        await storage.save_unenriched_property(unenriched)

        # Save enriched
        enriched_prop = _make_property(source=PropertySource.OPENRENT, source_id="enriched-1")
        enriched = _make_merged(enriched_prop, sources=(PropertySource.OPENRENT,))
        await storage.save_merged_property(enriched)

        results, total = await storage.get_properties_paginated()
        result_ids = {r["unique_id"] for r in results}

        assert total == 1
        assert enriched.unique_id in result_ids
        assert unenriched.unique_id not in result_ids

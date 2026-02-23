"""Tests for dropped property storage (floorplan gate)."""

from collections.abc import AsyncGenerator, Callable

import pytest_asyncio

from home_finder.db.storage import PropertyStorage
from home_finder.models import (
    MergedProperty,
    PropertyImage,
    PropertySource,
    TransportMode,
)
from home_finder.web.filters import PropertyFilter


@pytest_asyncio.fixture
async def storage() -> AsyncGenerator[PropertyStorage, None]:
    s = PropertyStorage(":memory:")
    await s.initialize()
    yield s
    await s.close()


class TestSaveDroppedProperties:
    async def test_persists_with_correct_status(
        self,
        storage: PropertyStorage,
        make_merged_property: Callable[..., MergedProperty],
    ) -> None:
        """Dropped properties get notification_status='dropped', enrichment_status='enriched'."""
        merged = make_merged_property(source_id="drop-1")
        await storage.pipeline.save_dropped_properties([merged], {})

        conn = await storage._get_connection()
        cursor = await conn.execute(
            "SELECT notification_status, enrichment_status FROM properties WHERE unique_id = ?",
            (merged.unique_id,),
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row["notification_status"] == "dropped"
        assert row["enrichment_status"] == "enriched"

    async def test_saves_commute_data(
        self,
        storage: PropertyStorage,
        make_merged_property: Callable[..., MergedProperty],
    ) -> None:
        """Commute lookup data is persisted alongside the dropped property."""
        merged = make_merged_property(source_id="drop-commute")
        commute_lookup = {merged.unique_id: (25, TransportMode.CYCLING)}
        await storage.pipeline.save_dropped_properties([merged], commute_lookup)

        conn = await storage._get_connection()
        cursor = await conn.execute(
            "SELECT commute_minutes, transport_mode FROM properties WHERE unique_id = ?",
            (merged.unique_id,),
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row["commute_minutes"] == 25
        assert row["transport_mode"] == "cycling"

    async def test_saves_images(
        self,
        storage: PropertyStorage,
        make_merged_property: Callable[..., MergedProperty],
    ) -> None:
        """Images from dropped properties are persisted to property_images table."""
        from pydantic import HttpUrl

        images = (
            PropertyImage(
                url=HttpUrl("https://example.com/gallery1.jpg"),
                source=PropertySource.OPENRENT,
                image_type="gallery",
            ),
        )
        merged = make_merged_property(source_id="drop-img", images=images)
        await storage.pipeline.save_dropped_properties([merged], {})

        db_images = await storage.get_property_images(merged.unique_id)
        assert len(db_images) == 1
        assert db_images[0].image_type == "gallery"

    async def test_excluded_from_filter_new_merged(
        self,
        storage: PropertyStorage,
        make_merged_property: Callable[..., MergedProperty],
    ) -> None:
        """Dropped properties are 'seen' and excluded from filter_new_merged."""
        merged = make_merged_property(source_id="drop-seen")
        await storage.pipeline.save_dropped_properties([merged], {})

        # Same property should now be filtered out as "already seen"
        result = await storage.filter_new_merged([merged])
        assert len(result) == 0

    async def test_excluded_from_dashboard(
        self,
        storage: PropertyStorage,
        make_merged_property: Callable[..., MergedProperty],
    ) -> None:
        """Dropped properties don't appear in paginated dashboard results."""
        merged = make_merged_property(source_id="drop-dash")
        await storage.pipeline.save_dropped_properties([merged], {})

        props, total = await storage.web.get_properties_paginated(PropertyFilter())
        assert total == 0
        assert len(props) == 0

    async def test_excluded_from_off_market_check(
        self,
        storage: PropertyStorage,
        make_merged_property: Callable[..., MergedProperty],
    ) -> None:
        """Dropped properties are excluded from off-market URL checks."""
        merged = make_merged_property(source_id="drop-offm")
        await storage.pipeline.save_dropped_properties([merged], {})

        results = await storage.get_properties_for_off_market_check()
        assert len(results) == 0

    async def test_on_conflict_preserves_normal_properties(
        self,
        storage: PropertyStorage,
        make_merged_property: Callable[..., MergedProperty],
    ) -> None:
        """If a non-unenriched property is already in DB, ON CONFLICT preserves existing data."""
        merged = make_merged_property(source_id="drop-conflict")

        # Save as a normal pending property first
        await storage.save_merged_property(merged)

        conn = await storage._get_connection()
        cursor = await conn.execute(
            "SELECT notification_status FROM properties WHERE unique_id = ?",
            (merged.unique_id,),
        )
        row = await cursor.fetchone()
        assert row["notification_status"] == "pending"

        # Now try to save as dropped — should be a no-op
        await storage.pipeline.save_dropped_properties([merged], {})

        cursor = await conn.execute(
            "SELECT notification_status FROM properties WHERE unique_id = ?",
            (merged.unique_id,),
        )
        row = await cursor.fetchone()
        assert row["notification_status"] == "pending"  # Unchanged

    async def test_empty_list_is_noop(
        self,
        storage: PropertyStorage,
    ) -> None:
        """Passing empty list doesn't error."""
        await storage.pipeline.save_dropped_properties([], {})

    async def test_multiple_properties(
        self,
        storage: PropertyStorage,
        make_merged_property: Callable[..., MergedProperty],
    ) -> None:
        """Multiple dropped properties are all saved correctly."""
        m1 = make_merged_property(source_id="drop-multi-1")
        m2 = make_merged_property(source_id="drop-multi-2")
        await storage.pipeline.save_dropped_properties([m1, m2], {})

        # Both should be seen
        result = await storage.filter_new_merged([m1, m2])
        assert len(result) == 0

        # Neither should appear on dashboard
        _props, total = await storage.web.get_properties_paginated(PropertyFilter())
        assert total == 0

    async def test_unenriched_retry_transitions_to_dropped(
        self,
        storage: PropertyStorage,
        make_merged_property: Callable[..., MergedProperty],
    ) -> None:
        """Unenriched retries (pending_enrichment + pending) transition to dropped on conflict."""
        merged = make_merged_property(source_id="drop-unenriched")

        # Save as unenriched (simulates enrichment failure on a previous run)
        await storage.pipeline.save_unenriched_property(merged)

        conn = await storage._get_connection()
        cursor = await conn.execute(
            "SELECT notification_status, enrichment_status FROM properties WHERE unique_id = ?",
            (merged.unique_id,),
        )
        row = await cursor.fetchone()
        assert row["notification_status"] == "pending_enrichment"
        assert row["enrichment_status"] == "pending"

        # Verify it appears in unenriched retry queue
        unenriched = await storage.pipeline.get_unenriched_properties()
        assert any(m.unique_id == merged.unique_id for m in unenriched)

        # Now save as dropped (simulates: re-enriched successfully, then dropped at floorplan gate)
        await storage.pipeline.save_dropped_properties([merged], {})

        cursor = await conn.execute(
            "SELECT notification_status, enrichment_status FROM properties WHERE unique_id = ?",
            (merged.unique_id,),
        )
        row = await cursor.fetchone()
        assert row["notification_status"] == "dropped"
        assert row["enrichment_status"] == "enriched"

        # No longer in unenriched retry queue
        unenriched = await storage.pipeline.get_unenriched_properties()
        assert not any(m.unique_id == merged.unique_id for m in unenriched)

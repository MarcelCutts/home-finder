"""Tests for quality re-analysis storage methods."""

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from pydantic import HttpUrl

from home_finder.db.storage import PropertyStorage
from home_finder.models import (
    ConditionAnalysis,
    KitchenAnalysis,
    LightSpaceAnalysis,
    MergedProperty,
    NotificationStatus,
    Property,
    PropertyImage,
    PropertyQualityAnalysis,
    PropertySource,
    SpaceAnalysis,
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
    images: tuple[PropertyImage, ...] = (),
    floorplan: PropertyImage | None = None,
) -> MergedProperty:
    if prop is None:
        prop = _make_property()
    if sources is None:
        sources = (prop.source,)
    return MergedProperty(
        canonical=prop,
        sources=sources,
        source_urls={s: prop.url for s in sources},
        images=images,
        floorplan=floorplan,
        min_price=prop.price_pcm,
        max_price=prop.price_pcm,
    )


def _make_quality_analysis(rating: int = 4) -> PropertyQualityAnalysis:
    return PropertyQualityAnalysis(
        kitchen=KitchenAnalysis(overall_quality="modern"),
        condition=ConditionAnalysis(overall_condition="good"),
        light_space=LightSpaceAnalysis(natural_light="good"),
        space=SpaceAnalysis(living_room_sqm=20.0),
        overall_rating=rating,
        summary="A nice flat.",
    )


@pytest_asyncio.fixture
async def storage() -> AsyncGenerator[PropertyStorage, None]:
    s = PropertyStorage(":memory:")
    await s.initialize()
    yield s


async def _save_analyzed_property(
    storage: PropertyStorage,
    source_id: str = "z-1",
    postcode: str = "E8 3RH",
    rating: int = 4,
    notify: bool = True,
) -> MergedProperty:
    """Helper: save a property through the full pipeline and analyze it."""
    prop = _make_property(source_id=source_id, postcode=postcode)
    merged = _make_merged(prop)
    await storage.save_pre_analysis_properties([merged], {})
    await storage.complete_analysis(merged.unique_id, _make_quality_analysis(rating))
    if notify:
        await storage.mark_notified(merged.unique_id)
    return merged


class TestRequestReanalysis:
    @pytest.mark.asyncio
    async def test_flags_by_id(self, storage: PropertyStorage) -> None:
        merged = await _save_analyzed_property(storage)

        count = await storage.request_reanalysis([merged.unique_id])
        assert count == 1

        # Verify flag is set
        conn = await storage._get_connection()
        cursor = await conn.execute(
            "SELECT reanalysis_requested_at FROM quality_analyses WHERE property_unique_id = ?",
            (merged.unique_id,),
        )
        row = await cursor.fetchone()
        assert row["reanalysis_requested_at"] is not None

    @pytest.mark.asyncio
    async def test_returns_zero_for_unknown_id(self, storage: PropertyStorage) -> None:
        count = await storage.request_reanalysis(["nonexistent-id"])
        assert count == 0

    @pytest.mark.asyncio
    async def test_idempotent_re_request(self, storage: PropertyStorage) -> None:
        merged = await _save_analyzed_property(storage)

        await storage.request_reanalysis([merged.unique_id])
        conn = await storage._get_connection()
        cursor = await conn.execute(
            "SELECT reanalysis_requested_at FROM quality_analyses WHERE property_unique_id = ?",
            (merged.unique_id,),
        )
        first_ts = (await cursor.fetchone())["reanalysis_requested_at"]

        # Re-request updates timestamp
        await storage.request_reanalysis([merged.unique_id])
        cursor = await conn.execute(
            "SELECT reanalysis_requested_at FROM quality_analyses WHERE property_unique_id = ?",
            (merged.unique_id,),
        )
        second_ts = (await cursor.fetchone())["reanalysis_requested_at"]
        assert second_ts is not None
        # Both should be set (may be same or different depending on timing)
        assert first_ts is not None

    @pytest.mark.asyncio
    async def test_flags_multiple_ids(self, storage: PropertyStorage) -> None:
        m1 = await _save_analyzed_property(storage, source_id="z-1")
        m2 = await _save_analyzed_property(storage, source_id="z-2")

        count = await storage.request_reanalysis([m1.unique_id, m2.unique_id])
        assert count == 2

    @pytest.mark.asyncio
    async def test_empty_list(self, storage: PropertyStorage) -> None:
        count = await storage.request_reanalysis([])
        assert count == 0

    @pytest.mark.asyncio
    async def test_flag_by_outcode(self, storage: PropertyStorage) -> None:
        m1 = await _save_analyzed_property(storage, source_id="z-e8", postcode="E8 3RH")
        m2 = await _save_analyzed_property(storage, source_id="z-e2", postcode="E2 7QA")
        await _save_analyzed_property(storage, source_id="z-n1", postcode="N1 5AA")

        count = await storage.request_reanalysis_by_filter(outcodes=["E8"])
        assert count == 1

        queue = await storage.get_reanalysis_queue()
        assert len(queue) == 1
        assert queue[0].unique_id == m1.unique_id

        # Flag E2 as well
        count = await storage.request_reanalysis_by_filter(outcodes=["E2"])
        assert count == 1

        queue = await storage.get_reanalysis_queue()
        assert len(queue) == 2
        queue_ids = {m.unique_id for m in queue}
        assert m1.unique_id in queue_ids
        assert m2.unique_id in queue_ids

    @pytest.mark.asyncio
    async def test_flag_all(self, storage: PropertyStorage) -> None:
        await _save_analyzed_property(storage, source_id="z-1")
        await _save_analyzed_property(storage, source_id="z-2")
        await _save_analyzed_property(storage, source_id="z-3")

        count = await storage.request_reanalysis_by_filter(all_properties=True)
        assert count == 3

    @pytest.mark.asyncio
    async def test_flag_by_filter_only_targets_analyzed(self, storage: PropertyStorage) -> None:
        """Properties without quality analysis should NOT be flagged."""
        # Save a property without analysis
        prop = _make_property(source_id="z-no-qa")
        merged = _make_merged(prop)
        await storage.save_merged_property(merged)

        # Save one with analysis
        await _save_analyzed_property(storage, source_id="z-with-qa")

        count = await storage.request_reanalysis_by_filter(all_properties=True)
        assert count == 1

    @pytest.mark.asyncio
    async def test_multiple_outcodes_single_call(self, storage: PropertyStorage) -> None:
        """request_reanalysis_by_filter with multiple outcodes matches all of them."""
        m_e8 = await _save_analyzed_property(storage, source_id="z-e8", postcode="E8 3RH")
        m_e2 = await _save_analyzed_property(storage, source_id="z-e2", postcode="E2 7QA")
        m_n1 = await _save_analyzed_property(storage, source_id="z-n1", postcode="N1 5AA")

        count = await storage.request_reanalysis_by_filter(outcodes=["E8", "E2"])
        assert count == 2

        queue = await storage.get_reanalysis_queue()
        queue_ids = {m.unique_id for m in queue}
        assert m_e8.unique_id in queue_ids
        assert m_e2.unique_id in queue_ids
        assert m_n1.unique_id not in queue_ids

    @pytest.mark.asyncio
    async def test_null_postcode_excluded_from_outcode_filter(
        self, storage: PropertyStorage
    ) -> None:
        """Property with postcode=None should not match outcode filter."""
        await _save_analyzed_property(storage, source_id="z-null", postcode=None)

        count = await storage.request_reanalysis_by_filter(outcodes=["E8"])
        assert count == 0


class TestGetReanalysisQueue:
    @pytest.mark.asyncio
    async def test_returns_flagged(self, storage: PropertyStorage) -> None:
        merged = await _save_analyzed_property(storage)
        await storage.request_reanalysis([merged.unique_id])

        queue = await storage.get_reanalysis_queue()
        assert len(queue) == 1
        assert queue[0].unique_id == merged.unique_id

    @pytest.mark.asyncio
    async def test_empty_when_no_flags(self, storage: PropertyStorage) -> None:
        await _save_analyzed_property(storage)
        queue = await storage.get_reanalysis_queue()
        assert len(queue) == 0

    @pytest.mark.asyncio
    async def test_reconstructs_merged_property(self, storage: PropertyStorage) -> None:
        prop = _make_property(source_id="z-1", postcode="E8 3RH")
        images = (
            PropertyImage(
                url=HttpUrl("https://example.com/img1.jpg"),
                source=PropertySource.ZOOPLA,
                image_type="gallery",
            ),
        )
        floorplan = PropertyImage(
            url=HttpUrl("https://example.com/fp.jpg"),
            source=PropertySource.ZOOPLA,
            image_type="floorplan",
        )
        merged = _make_merged(prop, images=images, floorplan=floorplan)
        await storage.save_pre_analysis_properties([merged], {})
        await storage.complete_analysis(merged.unique_id, _make_quality_analysis())
        await storage.mark_notified(merged.unique_id)

        await storage.request_reanalysis([merged.unique_id])
        queue = await storage.get_reanalysis_queue()

        assert len(queue) == 1
        result = queue[0]
        assert result.canonical.postcode == "E8 3RH"
        assert len(result.images) == 1
        assert result.floorplan is not None

    @pytest.mark.asyncio
    async def test_outcode_filter(self, storage: PropertyStorage) -> None:
        m_e8 = await _save_analyzed_property(storage, source_id="z-e8", postcode="E8 3RH")
        m_e2 = await _save_analyzed_property(storage, source_id="z-e2", postcode="E2 7QA")

        await storage.request_reanalysis([m_e8.unique_id, m_e2.unique_id])

        queue_e8 = await storage.get_reanalysis_queue(outcode="E8")
        assert len(queue_e8) == 1
        assert queue_e8[0].unique_id == m_e8.unique_id

        queue_all = await storage.get_reanalysis_queue()
        assert len(queue_all) == 2

    @pytest.mark.asyncio
    async def test_multi_source_reconstruction(self, storage: PropertyStorage) -> None:
        """Reconstructed MergedProperty preserves multi-source data."""
        prop = _make_property(source_id="z-multi", postcode="E8 3RH")
        merged = MergedProperty(
            canonical=prop,
            sources=(PropertySource.ZOOPLA, PropertySource.OPENRENT),
            source_urls={
                PropertySource.ZOOPLA: prop.url,
                PropertySource.OPENRENT: HttpUrl("https://openrent.com/99"),
            },
            min_price=1900,
            max_price=2100,
            descriptions={
                PropertySource.ZOOPLA: "Zoopla desc",
                PropertySource.OPENRENT: "OpenRent desc",
            },
        )
        await storage.save_pre_analysis_properties([merged], {})
        await storage.complete_analysis(merged.unique_id, _make_quality_analysis())
        await storage.mark_notified(merged.unique_id)
        await storage.request_reanalysis([merged.unique_id])

        queue = await storage.get_reanalysis_queue()
        assert len(queue) == 1
        result = queue[0]

        assert set(result.sources) == {PropertySource.ZOOPLA, PropertySource.OPENRENT}
        assert len(result.source_urls) == 2
        assert result.min_price == 1900
        assert result.max_price == 2100


class TestCompleteReanalysis:
    @pytest.mark.asyncio
    async def test_saves_new_analysis(self, storage: PropertyStorage) -> None:
        merged = await _save_analyzed_property(storage, rating=3)
        await storage.request_reanalysis([merged.unique_id])

        new_analysis = _make_quality_analysis(rating=5)
        await storage.complete_reanalysis(merged.unique_id, new_analysis)

        stored = await storage.get_quality_analysis(merged.unique_id)
        assert stored is not None
        assert stored.overall_rating == 5

    @pytest.mark.asyncio
    async def test_clears_flag(self, storage: PropertyStorage) -> None:
        merged = await _save_analyzed_property(storage)
        await storage.request_reanalysis([merged.unique_id])

        await storage.complete_reanalysis(merged.unique_id, _make_quality_analysis())

        # Flag should be cleared
        queue = await storage.get_reanalysis_queue()
        assert len(queue) == 0

        conn = await storage._get_connection()
        cursor = await conn.execute(
            "SELECT reanalysis_requested_at FROM quality_analyses WHERE property_unique_id = ?",
            (merged.unique_id,),
        )
        row = await cursor.fetchone()
        assert row["reanalysis_requested_at"] is None

    @pytest.mark.asyncio
    async def test_does_not_change_notification_status(self, storage: PropertyStorage) -> None:
        merged = await _save_analyzed_property(storage, notify=True)
        await storage.request_reanalysis([merged.unique_id])

        await storage.complete_reanalysis(merged.unique_id, _make_quality_analysis(rating=5))

        tracked = await storage.get_property(merged.unique_id)
        assert tracked is not None
        assert tracked.notification_status == NotificationStatus.SENT


class TestReanalysisIntegration:
    @pytest.mark.asyncio
    async def test_full_lifecycle(self, storage: PropertyStorage) -> None:
        """Request -> queue -> complete -> verify updated and cleared."""
        # 1. Save and analyze a property
        merged = await _save_analyzed_property(storage, rating=3)

        # Verify initial analysis
        initial = await storage.get_quality_analysis(merged.unique_id)
        assert initial is not None
        assert initial.overall_rating == 3

        # 2. Request re-analysis
        count = await storage.request_reanalysis([merged.unique_id])
        assert count == 1

        # 3. Load queue
        queue = await storage.get_reanalysis_queue()
        assert len(queue) == 1

        # 4. Re-analyze with new result
        new_analysis = _make_quality_analysis(rating=5)
        await storage.complete_reanalysis(queue[0].unique_id, new_analysis)

        # 5. Verify updated
        updated = await storage.get_quality_analysis(merged.unique_id)
        assert updated is not None
        assert updated.overall_rating == 5

        # 6. Queue should be empty
        queue = await storage.get_reanalysis_queue()
        assert len(queue) == 0

        # 7. Notification status unchanged
        tracked = await storage.get_property(merged.unique_id)
        assert tracked is not None
        assert tracked.notification_status == NotificationStatus.SENT

    @pytest.mark.asyncio
    async def test_dashboard_shows_updated_analysis(self, storage: PropertyStorage) -> None:
        """After re-analysis, dashboard should show updated rating."""
        merged = await _save_analyzed_property(storage, rating=2)

        # Re-analyze with higher rating
        await storage.request_reanalysis([merged.unique_id])
        await storage.complete_reanalysis(merged.unique_id, _make_quality_analysis(rating=5))

        results, total = await storage.get_properties_paginated(min_rating=4)
        assert total == 1
        assert results[0]["quality_rating"] == 5


class TestReanalysisFeatureInteractions:
    """Tests that reanalysis flags don't interfere with other storage features."""

    @pytest.mark.asyncio
    async def test_flagged_not_in_pending_analysis_queue(
        self, storage: PropertyStorage
    ) -> None:
        """Reanalysis-flagged properties should not appear in pending_analysis queue."""
        merged = await _save_analyzed_property(storage)
        await storage.request_reanalysis([merged.unique_id])

        # get_pending_analysis_properties queries notification_status='pending_analysis'
        # which is a different mechanism from reanalysis flags
        pending = await storage.get_pending_analysis_properties()
        assert len(pending) == 0

    @pytest.mark.asyncio
    async def test_reset_failed_does_not_clear_reanalysis_flags(
        self, storage: PropertyStorage
    ) -> None:
        """reset_failed_analyses targets NULL ratings only; real ratings are untouched."""
        # Property with real rating (not fallback)
        merged = await _save_analyzed_property(storage, rating=4)
        await storage.request_reanalysis([merged.unique_id])

        # reset_failed_analyses only targets rows with NULL overall_rating
        reset_count = await storage.reset_failed_analyses()
        assert reset_count == 0

        # Flag should still be set
        queue = await storage.get_reanalysis_queue()
        assert len(queue) == 1

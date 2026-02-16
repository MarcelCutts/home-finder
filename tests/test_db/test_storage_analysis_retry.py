"""Tests for quality analysis retry storage methods."""

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
        source_urls=dict.fromkeys(sources, prop.url),
        images=images,
        floorplan=floorplan,
        min_price=prop.price_pcm,
        max_price=prop.price_pcm,
    )


def _make_quality_analysis() -> PropertyQualityAnalysis:
    return PropertyQualityAnalysis(
        kitchen=KitchenAnalysis(overall_quality="modern"),
        condition=ConditionAnalysis(overall_condition="good"),
        light_space=LightSpaceAnalysis(natural_light="good"),
        space=SpaceAnalysis(living_room_sqm=20.0),
        overall_rating=4,
        summary="A nice flat.",
    )


def _make_fallback_analysis() -> PropertyQualityAnalysis:
    """Minimal analysis created when API fails — no overall_rating."""
    return PropertyQualityAnalysis(
        kitchen=KitchenAnalysis(notes="No images available for analysis"),
        condition=ConditionAnalysis(overall_condition="unknown", confidence="low"),
        light_space=LightSpaceAnalysis(
            natural_light="unknown",
            feels_spacious=None,
            notes="No images available for analysis",
        ),
        space=SpaceAnalysis(is_spacious_enough=None, confidence="low"),
        condition_concerns=False,
        summary="No images available for quality analysis",
    )


@pytest_asyncio.fixture
async def storage() -> AsyncGenerator[PropertyStorage, None]:
    s = PropertyStorage(":memory:")
    await s.initialize()
    yield s
    await s.close()


class TestSavePreAnalysisProperties:
    @pytest.mark.asyncio
    async def test_saves_with_pending_analysis_status(self, storage: PropertyStorage) -> None:
        merged = _make_merged()
        await storage.save_pre_analysis_properties([merged], {})

        conn = await storage._get_connection()
        cursor = await conn.execute(
            "SELECT notification_status, enrichment_status FROM properties WHERE unique_id = ?",
            (merged.unique_id,),
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row["notification_status"] == NotificationStatus.PENDING_ANALYSIS.value
        assert row["enrichment_status"] == "enriched"

    @pytest.mark.asyncio
    async def test_saves_commute_data(self, storage: PropertyStorage) -> None:
        merged = _make_merged()
        commute_lookup = {merged.unique_id: (15, TransportMode.CYCLING)}
        await storage.save_pre_analysis_properties([merged], commute_lookup)

        conn = await storage._get_connection()
        cursor = await conn.execute(
            "SELECT commute_minutes, transport_mode FROM properties WHERE unique_id = ?",
            (merged.unique_id,),
        )
        row = await cursor.fetchone()
        assert row["commute_minutes"] == 15
        assert row["transport_mode"] == "cycling"

    @pytest.mark.asyncio
    async def test_saves_images(self, storage: PropertyStorage) -> None:
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
        merged = _make_merged(images=images, floorplan=floorplan)
        await storage.save_pre_analysis_properties([merged], {})

        stored = await storage.get_property_images(merged.unique_id)
        assert len(stored) == 2

    @pytest.mark.asyncio
    async def test_batch_save_multiple(self, storage: PropertyStorage) -> None:
        props = [_make_merged(_make_property(source_id=f"z-{i}")) for i in range(3)]
        await storage.save_pre_analysis_properties(props, {})

        conn = await storage._get_connection()
        cursor = await conn.execute(
            "SELECT COUNT(*) FROM properties WHERE notification_status = ?",
            (NotificationStatus.PENDING_ANALYSIS.value,),
        )
        row = await cursor.fetchone()
        assert row[0] == 3

    @pytest.mark.asyncio
    async def test_on_conflict_updates_status(self, storage: PropertyStorage) -> None:
        """Re-saving an existing property should update to pending_analysis."""
        merged = _make_merged()
        # First save as normal property
        await storage.save_merged_property(merged)
        tracked = await storage.get_property(merged.unique_id)
        assert tracked is not None
        assert tracked.notification_status == NotificationStatus.PENDING

        # Re-save as pre-analysis
        await storage.save_pre_analysis_properties([merged], {})

        conn = await storage._get_connection()
        cursor = await conn.execute(
            "SELECT notification_status FROM properties WHERE unique_id = ?",
            (merged.unique_id,),
        )
        row = await cursor.fetchone()
        assert row["notification_status"] == NotificationStatus.PENDING_ANALYSIS.value


class TestGetPendingAnalysisProperties:
    @pytest.mark.asyncio
    async def test_returns_pending_analysis(self, storage: PropertyStorage) -> None:
        merged = _make_merged()
        await storage.save_pre_analysis_properties([merged], {})

        result = await storage.get_pending_analysis_properties()
        assert len(result) == 1
        assert result[0].unique_id == merged.unique_id

    @pytest.mark.asyncio
    async def test_excludes_normal_properties(self, storage: PropertyStorage) -> None:
        merged = _make_merged()
        await storage.save_merged_property(merged)

        result = await storage.get_pending_analysis_properties()
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_excludes_completed_analysis(self, storage: PropertyStorage) -> None:
        merged = _make_merged()
        await storage.save_pre_analysis_properties([merged], {})
        await storage.complete_analysis(merged.unique_id, _make_quality_analysis())

        result = await storage.get_pending_analysis_properties()
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_reconstructs_with_images(self, storage: PropertyStorage) -> None:
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
        merged = _make_merged(images=images, floorplan=floorplan)
        await storage.save_pre_analysis_properties([merged], {})

        result = await storage.get_pending_analysis_properties()
        assert len(result) == 1
        assert len(result[0].images) == 1
        assert result[0].floorplan is not None

    @pytest.mark.asyncio
    async def test_reconstructs_multi_source(self, storage: PropertyStorage) -> None:
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
        await storage.save_pre_analysis_properties([merged], {})

        result = await storage.get_pending_analysis_properties()
        assert len(result) == 1
        r = result[0]
        assert set(r.sources) == {PropertySource.ZOOPLA, PropertySource.OPENRENT}
        assert r.min_price == 1900
        assert r.max_price == 2100
        assert r.descriptions[PropertySource.ZOOPLA] == "Nice flat"


class TestCompleteAnalysis:
    @pytest.mark.asyncio
    async def test_transitions_to_pending(self, storage: PropertyStorage) -> None:
        merged = _make_merged()
        await storage.save_pre_analysis_properties([merged], {})

        await storage.complete_analysis(merged.unique_id, _make_quality_analysis())

        conn = await storage._get_connection()
        cursor = await conn.execute(
            "SELECT notification_status FROM properties WHERE unique_id = ?",
            (merged.unique_id,),
        )
        row = await cursor.fetchone()
        assert row["notification_status"] == NotificationStatus.PENDING.value

    @pytest.mark.asyncio
    async def test_saves_quality_analysis(self, storage: PropertyStorage) -> None:
        merged = _make_merged()
        await storage.save_pre_analysis_properties([merged], {})

        analysis = _make_quality_analysis()
        await storage.complete_analysis(merged.unique_id, analysis)

        stored = await storage.get_quality_analysis(merged.unique_id)
        assert stored is not None
        assert stored.overall_rating == 4

    @pytest.mark.asyncio
    async def test_works_without_analysis(self, storage: PropertyStorage) -> None:
        """complete_analysis with None quality_analysis just transitions status."""
        merged = _make_merged()
        await storage.save_pre_analysis_properties([merged], {})

        await storage.complete_analysis(merged.unique_id, None)

        conn = await storage._get_connection()
        cursor = await conn.execute(
            "SELECT notification_status FROM properties WHERE unique_id = ?",
            (merged.unique_id,),
        )
        row = await cursor.fetchone()
        assert row["notification_status"] == NotificationStatus.PENDING.value

    @pytest.mark.asyncio
    async def test_noop_for_already_sent(self, storage: PropertyStorage) -> None:
        """Should not change already-sent notifications."""
        merged = _make_merged()
        await storage.save_merged_property(merged)
        await storage.mark_notified(merged.unique_id)

        await storage.complete_analysis(merged.unique_id, _make_quality_analysis())

        tracked = await storage.get_property(merged.unique_id)
        assert tracked is not None
        assert tracked.notification_status == NotificationStatus.SENT


class TestDashboardExcludesPendingAnalysis:
    @pytest.mark.asyncio
    async def test_excludes_pending_analysis_from_paginated(self, storage: PropertyStorage) -> None:
        # Save pending_analysis
        pending = _make_merged(_make_property(source_id="pending-1"))
        await storage.save_pre_analysis_properties([pending], {})

        # Save normal enriched
        enriched_prop = _make_property(source=PropertySource.OPENRENT, source_id="enriched-1")
        enriched = _make_merged(enriched_prop, sources=(PropertySource.OPENRENT,))
        await storage.save_merged_property(enriched)

        results, total = await storage.get_properties_paginated()
        result_ids = {r["unique_id"] for r in results}

        assert total == 1
        assert enriched.unique_id in result_ids
        assert pending.unique_id not in result_ids


class TestNotificationRetryExcludesPendingAnalysis:
    @pytest.mark.asyncio
    async def test_get_unsent_excludes_pending_analysis(self, storage: PropertyStorage) -> None:
        """pending_analysis properties should NOT be returned by get_unsent_notifications."""
        merged = _make_merged()
        await storage.save_pre_analysis_properties([merged], {})

        unsent = await storage.get_unsent_notifications()
        unsent_ids = {t.property.unique_id for t in unsent}
        assert merged.unique_id not in unsent_ids


class TestResetFailedAnalyses:
    @pytest.mark.asyncio
    async def test_resets_fallback_analysis(self, storage: PropertyStorage) -> None:
        """Properties with fallback analysis should be reset to pending_analysis."""
        merged = _make_merged()
        await storage.save_pre_analysis_properties([merged], {})
        await storage.complete_analysis(merged.unique_id, _make_fallback_analysis())
        await storage.mark_notified(merged.unique_id)

        count = await storage.reset_failed_analyses()
        assert count == 1

        conn = await storage._get_connection()
        cursor = await conn.execute(
            "SELECT notification_status FROM properties WHERE unique_id = ?",
            (merged.unique_id,),
        )
        row = await cursor.fetchone()
        assert row["notification_status"] == NotificationStatus.PENDING_ANALYSIS.value

        # Quality analysis should be deleted
        analysis = await storage.get_quality_analysis(merged.unique_id)
        assert analysis is None

    @pytest.mark.asyncio
    async def test_does_not_reset_real_analysis(self, storage: PropertyStorage) -> None:
        """Properties with real analysis (has overall_rating) should not be reset."""
        merged = _make_merged()
        await storage.save_pre_analysis_properties([merged], {})
        await storage.complete_analysis(merged.unique_id, _make_quality_analysis())

        count = await storage.reset_failed_analyses()
        assert count == 0

        analysis = await storage.get_quality_analysis(merged.unique_id)
        assert analysis is not None
        assert analysis.overall_rating == 4

    @pytest.mark.asyncio
    async def test_does_not_reset_already_pending(self, storage: PropertyStorage) -> None:
        """Properties already pending_analysis should not be double-counted."""
        merged = _make_merged()
        await storage.save_pre_analysis_properties([merged], {})
        # Still pending_analysis, no complete_analysis called

        count = await storage.reset_failed_analyses()
        assert count == 0

    @pytest.mark.asyncio
    async def test_returns_zero_when_no_fallbacks(self, storage: PropertyStorage) -> None:
        """Should return 0 when all analyses are real."""
        merged = _make_merged()
        await storage.save_pre_analysis_properties([merged], {})
        await storage.complete_analysis(merged.unique_id, _make_quality_analysis())

        count = await storage.reset_failed_analyses()
        assert count == 0

    @pytest.mark.asyncio
    async def test_reset_properties_picked_up_by_retry(self, storage: PropertyStorage) -> None:
        """After reset, properties appear in get_pending_analysis_properties."""
        merged = _make_merged()
        await storage.save_pre_analysis_properties([merged], {})
        await storage.complete_analysis(merged.unique_id, _make_fallback_analysis())
        await storage.mark_notified(merged.unique_id)

        await storage.reset_failed_analyses()

        pending = await storage.get_pending_analysis_properties()
        assert len(pending) == 1
        assert pending[0].unique_id == merged.unique_id


class TestDashboardExcludesFallbackAnalysis:
    @pytest.mark.asyncio
    async def test_excludes_fallback_from_paginated(self, storage: PropertyStorage) -> None:
        """Properties with fallback analysis should be hidden from the dashboard."""
        # Save property with fallback analysis
        fallback = _make_merged(_make_property(source_id="fallback-1"))
        await storage.save_pre_analysis_properties([fallback], {})
        await storage.complete_analysis(fallback.unique_id, _make_fallback_analysis())

        # Save property with real analysis
        real_prop = _make_property(source=PropertySource.OPENRENT, source_id="real-1")
        real = _make_merged(real_prop, sources=(PropertySource.OPENRENT,))
        await storage.save_pre_analysis_properties([real], {})
        await storage.complete_analysis(real.unique_id, _make_quality_analysis())

        results, total = await storage.get_properties_paginated()
        result_ids = {r["unique_id"] for r in results}

        assert total == 1
        assert real.unique_id in result_ids
        assert fallback.unique_id not in result_ids

    @pytest.mark.asyncio
    async def test_excludes_fallback_from_paginated_total(self, storage: PropertyStorage) -> None:
        """Paginated total should not count fallback-analysis properties."""
        fallback = _make_merged(_make_property(source_id="fallback-1"))
        await storage.save_pre_analysis_properties([fallback], {})
        await storage.complete_analysis(fallback.unique_id, _make_fallback_analysis())

        real_prop = _make_property(source=PropertySource.OPENRENT, source_id="real-1")
        real = _make_merged(real_prop, sources=(PropertySource.OPENRENT,))
        await storage.save_pre_analysis_properties([real], {})
        await storage.complete_analysis(real.unique_id, _make_quality_analysis())

        _, total = await storage.get_properties_paginated()
        assert total == 1

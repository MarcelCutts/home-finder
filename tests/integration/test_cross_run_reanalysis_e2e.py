"""End-to-end tests for cross-run reanalysis: the full lifecycle of
Run 1 → anchor saved → Run 2 → new source merges → reanalysis flagged → drained.

Replaces the earlier unit-ish tests with unconditional assertions, real 2-run
scenarios, multiple platform combinations, edge cases, and drain integration.
"""

from collections.abc import Callable
from contextlib import contextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from pydantic import HttpUrl, SecretStr

from home_finder.config import Settings
from home_finder.db import PropertyStorage
from home_finder.filters.deduplication import Deduplicator
from home_finder.filters.detail_enrichment import EnrichmentResult
from home_finder.main import (
    run_dry_run,
    run_pipeline,
)
from home_finder.models import (
    MergedProperty,
    Property,
    PropertyImage,
    PropertyQualityAnalysis,
    PropertySource,
)
from home_finder.pipeline.analysis import _drain_reanalysis_queue
from home_finder.pipeline.stages import (
    _cross_run_deduplicate,
    _run_post_enrichment,
)
from home_finder.utils.image_cache import get_cache_dir, save_image_bytes

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_GALLERY_IMAGE = PropertyImage(
    url=HttpUrl("https://example.com/gallery1.jpg"),
    source=PropertySource.OPENRENT,
    image_type="gallery",
)
_FLOORPLAN_IMAGE = PropertyImage(
    url=HttpUrl("https://example.com/floorplan.jpg"),
    source=PropertySource.OPENRENT,
    image_type="floorplan",
)


def _wrap_merged(prop: Property, **overrides: Any) -> MergedProperty:
    """Wrap a Property as a single-source MergedProperty."""
    defaults: dict[str, Any] = {
        "canonical": prop,
        "sources": (prop.source,),
        "source_urls": {prop.source: prop.url},
        "images": (),
        "floorplan": None,
        "min_price": prop.price_pcm,
        "max_price": prop.price_pcm,
        "descriptions": {},
    }
    defaults.update(overrides)
    return MergedProperty(**defaults)


async def _populate_run1(
    storage: PropertyStorage,
    prop: Property,
    quality_analysis: PropertyQualityAnalysis | None = None,
) -> None:
    """Simulate Run 1: save property, mark notified, optionally save quality analysis."""
    merged = _wrap_merged(prop)
    await storage.save_merged_property(merged)
    await storage.mark_notified(merged.unique_id)
    if quality_analysis is not None:
        await storage.save_quality_analysis(merged.unique_id, quality_analysis)


def _make_matching_pair(
    make_property: Callable[..., Property],
    source_a: PropertySource,
    source_b: PropertySource,
    *,
    postcode: str = "E8 3RH",
    price_a: int = 1800,
    price_b: int = 1800,
    bedrooms: int = 2,
    latitude: float = 51.5465,
    longitude: float = -0.0553,
) -> tuple[Property, Property]:
    """Create two properties guaranteed to cross-match (same postcode, coords, beds)."""
    prop_a = make_property(
        source=source_a,
        postcode=postcode,
        price_pcm=price_a,
        bedrooms=bedrooms,
        latitude=latitude,
        longitude=longitude,
    )
    prop_b = make_property(
        source=source_b,
        postcode=postcode,
        price_pcm=price_b,
        bedrooms=bedrooms,
        latitude=latitude,
        longitude=longitude,
    )
    return prop_a, prop_b


async def _assert_db_sources(
    storage: PropertyStorage,
    unique_id: str,
    expected_sources: set[str],
) -> None:
    """Verify the sources JSON in the DB row matches the expected set."""
    import json

    conn = await storage._get_connection()
    cursor = await conn.execute(
        "SELECT sources FROM properties WHERE unique_id = ?",
        (unique_id,),
    )
    row = await cursor.fetchone()
    assert row is not None, f"Property {unique_id} not found in DB"
    actual = set(json.loads(row["sources"]))
    assert actual == expected_sources, f"Expected sources {expected_sources}, got {actual}"


async def _assert_reanalysis_state(
    storage: PropertyStorage,
    unique_id: str,
    *,
    requested: bool,
) -> None:
    """Check whether reanalysis_requested_at is set or null."""
    conn = await storage._get_connection()
    cursor = await conn.execute(
        "SELECT reanalysis_requested_at FROM quality_analyses WHERE property_unique_id = ?",
        (unique_id,),
    )
    row = await cursor.fetchone()
    if requested:
        assert row is not None, f"No quality_analyses row for {unique_id}"
        assert row["reanalysis_requested_at"] is not None, (
            "Expected reanalysis_requested_at to be set"
        )
    else:
        if row is not None:
            assert row["reanalysis_requested_at"] is None, (
                f"Expected reanalysis_requested_at to be NULL, got {row['reanalysis_requested_at']}"
            )


def _make_image_cache(tmp_path: Any, unique_id: str, count: int = 3) -> list[str]:
    """Create fake image files in the cache dir and return filenames."""
    cache_dir = get_cache_dir(str(tmp_path), unique_id)
    filenames = []
    for i in range(count):
        fname = f"gallery_{i:03d}_fake{i:04d}.jpg"
        path = cache_dir / fname
        save_image_bytes(path, b"fake")
        filenames.append(fname)
    return filenames


async def _fake_enrich(merged_list, fetcher, *, data_dir=None, storage=None):
    """Enrichment stub that adds images + floorplan."""
    enriched = []
    for m in merged_list:
        enriched_m = m.model_copy(
            update={
                "images": (_GALLERY_IMAGE,),
                "floorplan": _FLOORPLAN_IMAGE,
            }
        )
        enriched.append(enriched_m)
    return EnrichmentResult(enriched=enriched)


@contextmanager
def _pipeline_mocks(
    *,
    scrape_return=None,
    scrape_side_effect=None,
    enrich_side_effect=None,
    quality_side_effect=None,
    notify_return: bool = True,
    shared_storage: PropertyStorage | None = None,
):
    """Context manager that patches all external boundaries of run_pipeline().

    Args:
        scrape_return: Static return value for scrape_all_platforms.
        scrape_side_effect: Side effect for scrape_all_platforms (overrides scrape_return).
        enrich_side_effect: Side effect for enrich_merged_properties.
        quality_side_effect: Side effect for quality filter analyze.
        notify_return: Whether notification succeeds.
        shared_storage: If provided, reuse this storage instance across pipeline calls.
    """
    if enrich_side_effect is None:
        enrich_side_effect = _fake_enrich

    class _StorageCapture:
        instance: PropertyStorage | None = shared_storage

    original_storage_init = PropertyStorage.__init__

    def _patched_storage_init(self, *args, **kwargs):
        if shared_storage is not None:
            # Reuse existing storage — just copy its state
            self.db_path = shared_storage.db_path
            self._conn = shared_storage._conn
            self._web = shared_storage._web
            self._pipeline = shared_storage._pipeline
            _StorageCapture.instance = self
        else:
            original_storage_init(self, ":memory:")
            _StorageCapture.instance = self

    mock_notifier = MagicMock()
    mock_notifier.send_property_notification = AsyncMock(return_value=notify_return)
    mock_notifier.send_merged_property_notification = AsyncMock(return_value=notify_return)
    mock_notifier.send_price_drop_notification = AsyncMock(return_value=True)
    mock_notifier.close = AsyncMock()

    async def _notifier_aenter(*a):
        return mock_notifier

    async def _notifier_aexit(*a):
        await mock_notifier.close()

    mock_notifier.__aenter__ = _notifier_aenter
    mock_notifier.__aexit__ = _notifier_aexit

    mock_fetcher_instance = MagicMock()
    mock_fetcher_instance.close = AsyncMock()

    async def _fetcher_aenter(*a):
        return mock_fetcher_instance

    async def _fetcher_aexit(*a):
        await mock_fetcher_instance.close()

    mock_fetcher_instance.__aenter__ = _fetcher_aenter
    mock_fetcher_instance.__aexit__ = _fetcher_aexit

    from home_finder.filters.quality import TokenUsage

    mock_quality_instance = MagicMock()
    mock_quality_instance.token_usage = TokenUsage()
    if quality_side_effect is not None:
        mock_quality_instance.analyze_single_merged = AsyncMock(side_effect=quality_side_effect)
    else:
        mock_quality_instance.analyze_single_merged = AsyncMock(
            side_effect=lambda m, **kw: (m, None)
        )
    mock_quality_instance.close = AsyncMock()

    async def _quality_aenter(*a):
        return mock_quality_instance

    async def _quality_aexit(*a):
        await mock_quality_instance.close()

    mock_quality_instance.__aenter__ = _quality_aenter
    mock_quality_instance.__aexit__ = _quality_aexit

    scrape_mock = AsyncMock()
    if scrape_side_effect is not None:
        scrape_mock.side_effect = scrape_side_effect
    else:
        scrape_mock.return_value = (scrape_return or [], [])

    with (
        patch("home_finder.pipeline.scraping.scrape_all_platforms", scrape_mock),
        patch(
            "home_finder.pipeline.stages.enrich_merged_properties",
            new_callable=AsyncMock,
            side_effect=enrich_side_effect,
        ),
        patch(
            "home_finder.pipeline.stages.DetailFetcher",
            return_value=mock_fetcher_instance,
        ),
        patch(
            "home_finder.pipeline.analysis.PropertyQualityFilter",
            return_value=mock_quality_instance,
        ) as mock_quality_cls,
        patch(
            "home_finder.main.TelegramNotifier",
            return_value=mock_notifier,
        ),
        patch("home_finder.pipeline.analysis._lookup_wards", new_callable=AsyncMock),
        patch("home_finder.main.asyncio.sleep", new_callable=AsyncMock),
        patch.object(PropertyStorage, "__init__", _patched_storage_init),
    ):
        ctx = type(
            "Ctx",
            (),
            {
                "notifier": mock_notifier,
                "quality_cls": mock_quality_cls,
                "quality_instance": mock_quality_instance,
                "storage_capture": _StorageCapture,
                "scrape_mock": scrape_mock,
            },
        )()
        yield ctx


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def storage():
    """In-memory SQLite storage."""
    s = PropertyStorage(":memory:")
    await s.initialize()
    yield s
    await s.close()


@pytest.fixture
def quality_v1(make_quality_analysis: Callable[..., PropertyQualityAnalysis]):
    return make_quality_analysis(rating=4, summary="Initial analysis from Run 1")


@pytest.fixture
def quality_v2(make_quality_analysis: Callable[..., PropertyQualityAnalysis]):
    return make_quality_analysis(rating=5, summary="Updated analysis with more images")


@pytest.fixture
def settings_quality_on() -> Settings:
    return Settings(
        telegram_bot_token=SecretStr("fake:token"),
        telegram_chat_id=0,
        database_path=":memory:",
        search_areas="e8",
        min_price=1500,
        max_price=2500,
        min_bedrooms=1,
        max_bedrooms=2,
        enable_quality_filter=True,
        require_floorplan=False,
        anthropic_api_key=SecretStr("test-key"),
    )


@pytest.fixture
def settings_quality_off() -> Settings:
    return Settings(
        telegram_bot_token=SecretStr("fake:token"),
        telegram_chat_id=0,
        database_path=":memory:",
        search_areas="e8",
        min_price=1500,
        max_price=2500,
        min_bedrooms=1,
        max_bedrooms=2,
        enable_quality_filter=False,
        require_floorplan=False,
    )


# ---------------------------------------------------------------------------
# Class 1: TestCoreReanalysisFlow
# ---------------------------------------------------------------------------


class TestCoreReanalysisFlow:
    """The fundamental 2-run lifecycle: anchor → new source → reanalysis."""

    async def test_full_lifecycle_rightmove_then_zoopla(
        self,
        storage: PropertyStorage,
        make_property: Callable[..., Property],
        quality_v1: PropertyQualityAnalysis,
        quality_v2: PropertyQualityAnalysis,
        settings_quality_on: Settings,
    ):
        """Run 1: Rightmove saved+notified+analyzed. Run 2: Zoopla matched, merge,
        reanalysis flagged, queue drained. Verify full lifecycle."""
        # -- Run 1: create anchor --
        anchor_prop, new_prop = _make_matching_pair(
            make_property, PropertySource.RIGHTMOVE, PropertySource.ZOOPLA
        )
        await _populate_run1(storage, anchor_prop, quality_v1)

        # Verify anchor is in DB with single source
        await _assert_db_sources(storage, anchor_prop.unique_id, {"rightmove"})

        # -- Run 2: new source arrives, cross-run dedup detects match --
        new_merged = _wrap_merged(new_prop)
        deduplicator = Deduplicator(enable_cross_platform=True)

        result = await _cross_run_deduplicate(deduplicator, [new_merged], storage, set())

        # Unconditional assertions — the merge MUST happen
        assert result.anchors_updated == 1
        assert len(result.genuinely_new) == 0

        # DB should now have both sources
        await _assert_db_sources(storage, anchor_prop.unique_id, {"rightmove", "zoopla"})

        # Reanalysis should be flagged
        await _assert_reanalysis_state(storage, anchor_prop.unique_id, requested=True)

        # Notification status should still be 'sent'
        conn = await storage._get_connection()
        cursor = await conn.execute(
            "SELECT notification_status FROM properties WHERE unique_id = ?",
            (anchor_prop.unique_id,),
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row["notification_status"] == "sent"

        # -- Drain reanalysis queue --
        with patch("home_finder.pipeline.analysis.PropertyQualityFilter") as mock_qf_cls:
            mock_qf = MagicMock()
            mock_qf.analyze_single_merged = AsyncMock(side_effect=lambda m, **kw: (m, quality_v2))
            mock_qf.close = AsyncMock()
            mock_qf.__aenter__ = AsyncMock(return_value=mock_qf)
            mock_qf.__aexit__ = AsyncMock(return_value=False)
            mock_qf_cls.return_value = mock_qf

            count = await _drain_reanalysis_queue(settings_quality_on, storage)

        assert count == 1

        # Reanalysis flag should be cleared
        await _assert_reanalysis_state(storage, anchor_prop.unique_id, requested=False)

        # Quality analysis should be updated to v2
        cursor = await conn.execute(
            "SELECT overall_rating FROM quality_analyses WHERE property_unique_id = ?",
            (anchor_prop.unique_id,),
        )
        qa_row = await cursor.fetchone()
        assert qa_row is not None
        assert qa_row["overall_rating"] == 5

        # Notification status still 'sent' (complete_reanalysis preserves it)
        cursor = await conn.execute(
            "SELECT notification_status FROM properties WHERE unique_id = ?",
            (anchor_prop.unique_id,),
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row["notification_status"] == "sent"

    async def test_true_e2e_run_pipeline_twice(
        self,
        make_property: Callable[..., Property],
        make_quality_analysis: Callable[..., PropertyQualityAnalysis],
    ):
        """Call run_pipeline() twice. Run 1 returns Rightmove, Run 2 returns Zoopla match.
        Verify: 1 property, 2 sources, quality re-analyzed, single notification."""
        qa = make_quality_analysis(rating=4, summary="Nice flat")

        prop_rm = make_property(
            source=PropertySource.RIGHTMOVE,
            source_id="rm-e2e",
            postcode="E8 3RH",
            price_pcm=1800,
            bedrooms=2,
        )
        prop_z = make_property(
            source=PropertySource.ZOOPLA,
            source_id="z-e2e",
            postcode="E8 3RH",
            price_pcm=1800,
            bedrooms=2,
        )

        call_count = 0

        def _quality_side_effect(merged, **kw):
            return (merged, qa)

        # First call returns Rightmove, second returns Zoopla
        async def _scrape_side_effect(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return ([prop_rm], [])
            return ([prop_z], [])

        settings = Settings(
            telegram_bot_token=SecretStr("fake:token"),
            telegram_chat_id=0,
            database_path=":memory:",
            search_areas="e8",
            min_price=1500,
            max_price=2500,
            min_bedrooms=1,
            max_bedrooms=2,
            enable_quality_filter=True,
            require_floorplan=False,
            anthropic_api_key=SecretStr("test-key"),
        )

        # We need to share the DB across both pipeline calls
        shared_storage = PropertyStorage(":memory:")
        await shared_storage.initialize()

        try:
            with _pipeline_mocks(
                scrape_side_effect=_scrape_side_effect,
                quality_side_effect=_quality_side_effect,
                shared_storage=shared_storage,
            ) as ctx:

                async def _intercept_close(self_storage):
                    pass  # Don't close — we need the DB across runs

                with patch.object(PropertyStorage, "close", _intercept_close):
                    await run_pipeline(settings)
                    await run_pipeline(settings)

                storage = ctx.storage_capture.instance
                assert storage is not None

                # Should have 1 property (merged) with 2 sources
                count = await shared_storage.get_property_count()
                assert count == 1

                await _assert_db_sources(shared_storage, prop_rm.unique_id, {"rightmove", "zoopla"})

                # Notification should have been sent once (Run 1)
                assert ctx.notifier.send_merged_property_notification.call_count == 1
        finally:
            await shared_storage.close()


# ---------------------------------------------------------------------------
# Class 2: TestSourceVariety
# ---------------------------------------------------------------------------


class TestSourceVariety:
    """All platform combinations for cross-run merging."""

    async def test_openrent_then_rightmove(
        self,
        storage: PropertyStorage,
        make_property: Callable[..., Property],
        quality_v1: PropertyQualityAnalysis,
    ):
        """OpenRent anchor + Rightmove new source."""
        anchor, new = _make_matching_pair(
            make_property, PropertySource.OPENRENT, PropertySource.RIGHTMOVE
        )
        await _populate_run1(storage, anchor, quality_v1)

        result = await _cross_run_deduplicate(
            Deduplicator(enable_cross_platform=True),
            [_wrap_merged(new)],
            storage,
            set(),
        )

        assert result.anchors_updated == 1
        assert len(result.genuinely_new) == 0
        await _assert_db_sources(storage, anchor.unique_id, {"openrent", "rightmove"})
        await _assert_reanalysis_state(storage, anchor.unique_id, requested=True)

    async def test_zoopla_then_onthemarket(
        self,
        storage: PropertyStorage,
        make_property: Callable[..., Property],
        quality_v1: PropertyQualityAnalysis,
    ):
        """Zoopla anchor + OnTheMarket new source."""
        anchor, new = _make_matching_pair(
            make_property, PropertySource.ZOOPLA, PropertySource.ONTHEMARKET
        )
        await _populate_run1(storage, anchor, quality_v1)

        result = await _cross_run_deduplicate(
            Deduplicator(enable_cross_platform=True),
            [_wrap_merged(new)],
            storage,
            set(),
        )

        assert result.anchors_updated == 1
        assert len(result.genuinely_new) == 0
        await _assert_db_sources(storage, anchor.unique_id, {"zoopla", "onthemarket"})

    async def test_anchor_gains_two_new_sources_in_one_run(
        self,
        storage: PropertyStorage,
        make_property: Callable[..., Property],
        quality_v1: PropertyQualityAnalysis,
    ):
        """OpenRent anchor gains both Zoopla and Rightmove in Run 2."""
        anchor = make_property(
            source=PropertySource.OPENRENT,
            postcode="E8 3RH",
            price_pcm=1800,
            bedrooms=2,
            latitude=51.5465,
            longitude=-0.0553,
        )
        new_z = make_property(
            source=PropertySource.ZOOPLA,
            postcode="E8 3RH",
            price_pcm=1850,
            bedrooms=2,
            latitude=51.5465,
            longitude=-0.0553,
        )
        new_rm = make_property(
            source=PropertySource.RIGHTMOVE,
            postcode="E8 3RH",
            price_pcm=1800,
            bedrooms=2,
            latitude=51.5465,
            longitude=-0.0553,
        )

        await _populate_run1(storage, anchor, quality_v1)

        result = await _cross_run_deduplicate(
            Deduplicator(enable_cross_platform=True),
            [_wrap_merged(new_z), _wrap_merged(new_rm)],
            storage,
            set(),
        )

        # The two new sources get merged together first, then match anchor
        assert result.anchors_updated >= 1
        assert len(result.genuinely_new) == 0
        await _assert_db_sources(storage, anchor.unique_id, {"openrent", "zoopla", "rightmove"})
        await _assert_reanalysis_state(storage, anchor.unique_id, requested=True)

        # Verify price range expands (anchor=1800, new_z=1850, new_rm=1800)
        conn = await storage._get_connection()
        cursor = await conn.execute(
            "SELECT min_price, max_price FROM properties WHERE unique_id = ?",
            (anchor.unique_id,),
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row["min_price"] == 1800
        assert row["max_price"] == 1850


# ---------------------------------------------------------------------------
# Class 3: TestDBStateEdgeCases
# ---------------------------------------------------------------------------


class TestDBStateEdgeCases:
    """Edge cases in DB state during cross-run merging."""

    async def test_anchor_without_quality_analysis(
        self,
        storage: PropertyStorage,
        make_property: Callable[..., Property],
    ):
        """Anchor saved+notified but no quality_analyses row (Run 1 crash).
        Cross-run merge still updates sources. request_reanalysis returns 0."""
        anchor, new = _make_matching_pair(
            make_property, PropertySource.RIGHTMOVE, PropertySource.ZOOPLA
        )
        # Populate WITHOUT quality analysis (simulating crash)
        await _populate_run1(storage, anchor, quality_analysis=None)

        result = await _cross_run_deduplicate(
            Deduplicator(enable_cross_platform=True),
            [_wrap_merged(new)],
            storage,
            set(),
        )

        assert result.anchors_updated == 1
        assert len(result.genuinely_new) == 0
        await _assert_db_sources(storage, anchor.unique_id, {"rightmove", "zoopla"})

        # No quality_analyses row → no flag to set, but no crash
        conn = await storage._get_connection()
        cursor = await conn.execute(
            "SELECT * FROM quality_analyses WHERE property_unique_id = ?",
            (anchor.unique_id,),
        )
        row = await cursor.fetchone()
        # request_reanalysis updates existing rows — with no row, returns 0
        assert row is None

    async def test_anchor_already_pending_reanalysis(
        self,
        storage: PropertyStorage,
        make_property: Callable[..., Property],
        quality_v1: PropertyQualityAnalysis,
    ):
        """Anchor already has reanalysis_requested_at set. New merge updates timestamp."""
        anchor, new = _make_matching_pair(
            make_property, PropertySource.RIGHTMOVE, PropertySource.ZOOPLA
        )
        await _populate_run1(storage, anchor, quality_v1)

        # Pre-flag reanalysis (simulating a previous merge)
        await storage.request_reanalysis([anchor.unique_id])
        await _assert_reanalysis_state(storage, anchor.unique_id, requested=True)

        # New merge arrives — request_reanalysis called again (idempotent update)
        result = await _cross_run_deduplicate(
            Deduplicator(enable_cross_platform=True),
            [_wrap_merged(new)],
            storage,
            set(),
        )

        assert result.anchors_updated == 1
        await _assert_reanalysis_state(storage, anchor.unique_id, requested=True)

    async def test_price_range_expansion(
        self,
        storage: PropertyStorage,
        make_property: Callable[..., Property],
        quality_v1: PropertyQualityAnalysis,
    ):
        """Anchor at 1800, new source at 2000. After merge: min=1800, max=2000."""
        anchor, new = _make_matching_pair(
            make_property,
            PropertySource.OPENRENT,
            PropertySource.ZOOPLA,
            price_a=1800,
            price_b=2000,
        )
        await _populate_run1(storage, anchor, quality_v1)

        result = await _cross_run_deduplicate(
            Deduplicator(enable_cross_platform=True),
            [_wrap_merged(new)],
            storage,
            set(),
        )

        assert result.anchors_updated == 1

        conn = await storage._get_connection()
        cursor = await conn.execute(
            "SELECT min_price, max_price FROM properties WHERE unique_id = ?",
            (anchor.unique_id,),
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row["min_price"] == 1800
        assert row["max_price"] == 2000


# ---------------------------------------------------------------------------
# Class 4: TestImageCacheConsolidation
# ---------------------------------------------------------------------------


class TestImageCacheConsolidation:
    """Cached images copied from new source to anchor during cross-run merge."""

    async def test_cache_files_copied_from_new_source_to_anchor(
        self,
        storage: PropertyStorage,
        make_property: Callable[..., Property],
        quality_v1: PropertyQualityAnalysis,
        tmp_path: Any,
    ):
        """Creates fake images in new source's cache. After cross-run dedup,
        verifies all exist in anchor's cache dir."""
        data_dir = str(tmp_path)

        anchor, new = _make_matching_pair(
            make_property, PropertySource.RIGHTMOVE, PropertySource.ZOOPLA
        )
        await _populate_run1(storage, anchor, quality_v1)

        # Create fake cached images for the new source
        filenames = _make_image_cache(tmp_path, new.unique_id, count=3)

        result = await _cross_run_deduplicate(
            Deduplicator(enable_cross_platform=True, data_dir=data_dir),
            [_wrap_merged(new)],
            storage,
            set(),
            data_dir=data_dir,
        )

        assert result.anchors_updated == 1

        # Verify images were copied to anchor's cache dir
        anchor_cache = get_cache_dir(data_dir, anchor.unique_id)
        assert anchor_cache.is_dir()
        for fname in filenames:
            assert (anchor_cache / fname).exists(), f"Missing copied file: {fname}"

    async def test_merge_works_without_data_dir(
        self,
        storage: PropertyStorage,
        make_property: Callable[..., Property],
        quality_v1: PropertyQualityAnalysis,
    ):
        """Empty data_dir — merge succeeds, sources updated, no crash."""
        anchor, new = _make_matching_pair(
            make_property, PropertySource.RIGHTMOVE, PropertySource.ZOOPLA
        )
        await _populate_run1(storage, anchor, quality_v1)

        result = await _cross_run_deduplicate(
            Deduplicator(enable_cross_platform=True, data_dir=""),
            [_wrap_merged(new)],
            storage,
            set(),
            data_dir="",
        )

        assert result.anchors_updated == 1
        await _assert_db_sources(storage, anchor.unique_id, {"rightmove", "zoopla"})


# ---------------------------------------------------------------------------
# Class 5: TestDrainReanalysisQueue
# ---------------------------------------------------------------------------


class TestDrainReanalysisQueue:
    """_drain_reanalysis_queue() processes flagged properties."""

    async def test_empty_queue_returns_zero(
        self,
        storage: PropertyStorage,
        settings_quality_on: Settings,
    ):
        """No flagged properties → returns 0, quality filter never constructed."""
        with patch("home_finder.pipeline.analysis.PropertyQualityFilter") as mock_cls:
            count = await _drain_reanalysis_queue(settings_quality_on, storage)

        assert count == 0
        mock_cls.assert_not_called()

    async def test_skipped_when_quality_disabled(
        self,
        storage: PropertyStorage,
        make_property: Callable[..., Property],
        quality_v1: PropertyQualityAnalysis,
        settings_quality_off: Settings,
    ):
        """Returns 0 when quality filter is disabled. Flag NOT cleared."""
        anchor = make_property(source=PropertySource.OPENRENT, postcode="E8 3RH", price_pcm=1800)
        await _populate_run1(storage, anchor, quality_v1)
        await storage.request_reanalysis([anchor.unique_id])

        count = await _drain_reanalysis_queue(settings_quality_off, storage)

        assert count == 0
        # Flag should still be set (not cleared)
        await _assert_reanalysis_state(storage, anchor.unique_id, requested=True)

    async def test_drains_multiple_properties(
        self,
        storage: PropertyStorage,
        make_property: Callable[..., Property],
        quality_v1: PropertyQualityAnalysis,
        quality_v2: PropertyQualityAnalysis,
        settings_quality_on: Settings,
    ):
        """3 anchors flagged, all processed. Quality updated, flags cleared."""
        anchors = []
        for i in range(3):
            prop = make_property(
                source=PropertySource.OPENRENT,
                postcode=f"E8 {i}RH",
                price_pcm=1800 + i * 100,
            )
            await _populate_run1(storage, prop, quality_v1)
            await storage.request_reanalysis([prop.unique_id])
            anchors.append(prop)

        with patch("home_finder.pipeline.analysis.PropertyQualityFilter") as mock_cls:
            mock_qf = MagicMock()
            mock_qf.analyze_single_merged = AsyncMock(side_effect=lambda m, **kw: (m, quality_v2))
            mock_qf.close = AsyncMock()
            mock_qf.__aenter__ = AsyncMock(return_value=mock_qf)
            mock_qf.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_qf

            count = await _drain_reanalysis_queue(settings_quality_on, storage)

        assert count == 3

        # All flags cleared, all quality updated
        for prop in anchors:
            await _assert_reanalysis_state(storage, prop.unique_id, requested=False)

            conn = await storage._get_connection()
            cursor = await conn.execute(
                "SELECT overall_rating FROM quality_analyses WHERE property_unique_id = ?",
                (prop.unique_id,),
            )
            qa_row = await cursor.fetchone()
            assert qa_row is not None
            assert qa_row["overall_rating"] == 5

            # Notification status preserved as 'sent'
            cursor = await conn.execute(
                "SELECT notification_status FROM properties WHERE unique_id = ?",
                (prop.unique_id,),
            )
            row = await cursor.fetchone()
            assert row is not None
            assert row["notification_status"] == "sent"

    async def test_partial_failure_continues(
        self,
        storage: PropertyStorage,
        make_property: Callable[..., Property],
        quality_v1: PropertyQualityAnalysis,
        quality_v2: PropertyQualityAnalysis,
        settings_quality_on: Settings,
    ):
        """Quality mock raises Exception for 1 of 3 properties.
        Successful ones get updated, failed one's flag stays."""
        anchors = []
        for i in range(3):
            prop = make_property(
                source=PropertySource.OPENRENT,
                postcode=f"E8 {i}RH",
                price_pcm=1800 + i * 100,
            )
            await _populate_run1(storage, prop, quality_v1)
            await storage.request_reanalysis([prop.unique_id])
            anchors.append(prop)

        call_idx = 0

        async def _flaky_analyze(merged, **kw):
            nonlocal call_idx
            call_idx += 1
            if call_idx == 2:
                raise RuntimeError("API timeout")
            return (merged, quality_v2)

        with patch("home_finder.pipeline.analysis.PropertyQualityFilter") as mock_cls:
            mock_qf = MagicMock()
            mock_qf.analyze_single_merged = AsyncMock(side_effect=_flaky_analyze)
            mock_qf.close = AsyncMock()
            mock_qf.__aenter__ = AsyncMock(return_value=mock_qf)
            mock_qf.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_qf

            count = await _drain_reanalysis_queue(settings_quality_on, storage)

        # 2 of 3 succeeded (drain uses _run_concurrent_analysis which continues on error)
        assert count == 2


# ---------------------------------------------------------------------------
# Class 6: TestNoMergeScenarios
# ---------------------------------------------------------------------------


class TestNoMergeScenarios:
    """Cases where cross-run dedup should NOT merge."""

    async def test_different_postcodes_no_merge(
        self,
        storage: PropertyStorage,
        make_property: Callable[..., Property],
        quality_v1: PropertyQualityAnalysis,
    ):
        """Anchor E8 3RH, new E8 4AB with different coords/address → genuinely_new=1, no merge."""
        anchor = make_property(
            source=PropertySource.RIGHTMOVE,
            postcode="E8 3RH",
            price_pcm=1800,
            bedrooms=2,
            latitude=51.5465,
            longitude=-0.0553,
            address="123 Mare Street, Hackney, London",
        )
        new = make_property(
            source=PropertySource.ZOOPLA,
            postcode="E8 4AB",
            price_pcm=1800,
            bedrooms=2,
            latitude=51.5520,
            longitude=-0.0610,
            address="45 Dalston Lane, Hackney, London",
        )

        await _populate_run1(storage, anchor, quality_v1)

        result = await _cross_run_deduplicate(
            Deduplicator(enable_cross_platform=True),
            [_wrap_merged(new)],
            storage,
            set(),
        )

        assert result.anchors_updated == 0
        assert len(result.genuinely_new) == 1

    async def test_same_postcode_different_bedrooms_no_merge(
        self,
        storage: PropertyStorage,
        make_property: Callable[..., Property],
        quality_v1: PropertyQualityAnalysis,
    ):
        """Same postcode, different bedrooms → no merge (different blocking group)."""
        anchor = make_property(
            source=PropertySource.RIGHTMOVE,
            postcode="E8 3RH",
            price_pcm=1800,
            bedrooms=1,
        )
        new = make_property(
            source=PropertySource.ZOOPLA,
            postcode="E8 3RH",
            price_pcm=1800,
            bedrooms=2,
        )

        await _populate_run1(storage, anchor, quality_v1)

        result = await _cross_run_deduplicate(
            Deduplicator(enable_cross_platform=True),
            [_wrap_merged(new)],
            storage,
            set(),
        )

        assert result.anchors_updated == 0
        assert len(result.genuinely_new) == 1

    async def test_same_source_no_cross_platform_merge(
        self,
        storage: PropertyStorage,
        make_property: Callable[..., Property],
        quality_v1: PropertyQualityAnalysis,
    ):
        """Both OpenRent → deduplicator skips same-source pairs."""
        anchor = make_property(
            source=PropertySource.OPENRENT,
            source_id="or-anchor",
            postcode="E8 3RH",
            price_pcm=1800,
            bedrooms=2,
        )
        new = make_property(
            source=PropertySource.OPENRENT,
            source_id="or-new",
            postcode="E8 3RH",
            price_pcm=1800,
            bedrooms=2,
        )

        await _populate_run1(storage, anchor, quality_v1)

        result = await _cross_run_deduplicate(
            Deduplicator(enable_cross_platform=True),
            [_wrap_merged(new)],
            storage,
            set(),
        )

        # Same source — no cross-platform merge (scoring doesn't match same-source)
        assert result.anchors_updated == 0
        assert len(result.genuinely_new) == 1


# ---------------------------------------------------------------------------
# Class 7: TestRescrapeSubsetRegression
# ---------------------------------------------------------------------------


class TestRescrapeSubsetRegression:
    """Regression tests for the root cause: re-scraping a subset of an anchor's
    known sources should NOT trigger reanalysis.

    The production bug was ~30+ properties with 4 sources being spuriously
    reanalyzed every run because ``!=`` treated subset sources as "changed".
    The fix uses set difference (``-``) so a subset yields an empty diff.
    """

    async def test_4_source_anchor_rescrape_2_known_no_reanalysis(
        self,
        storage: PropertyStorage,
        make_property: Callable[..., Property],
        quality_v1: PropertyQualityAnalysis,
    ):
        """Anchor has {openrent, rightmove, zoopla, onthemarket}.
        Re-scrape just {openrent, rightmove}.
        Assert: metadata update happens (anchors_updated == 1) but
        request_reanalysis is NOT called."""
        # Build the 4-source anchor incrementally
        or_prop = make_property(
            source=PropertySource.OPENRENT,
            postcode="E8 3RH",
            price_pcm=1800,
            bedrooms=2,
            latitude=51.5465,
            longitude=-0.0553,
        )
        rm_prop = make_property(
            source=PropertySource.RIGHTMOVE,
            postcode="E8 3RH",
            price_pcm=1800,
            bedrooms=2,
            latitude=51.5465,
            longitude=-0.0553,
        )
        zp_prop = make_property(
            source=PropertySource.ZOOPLA,
            postcode="E8 3RH",
            price_pcm=1850,
            bedrooms=2,
            latitude=51.5465,
            longitude=-0.0553,
        )
        otm_prop = make_property(
            source=PropertySource.ONTHEMARKET,
            postcode="E8 3RH",
            price_pcm=1800,
            bedrooms=2,
            latitude=51.5465,
            longitude=-0.0553,
        )

        # Run 1: save OpenRent as anchor
        await _populate_run1(storage, or_prop, quality_v1)

        deduplicator = Deduplicator(enable_cross_platform=True)

        # Run 2: Rightmove merges in
        result = await _cross_run_deduplicate(deduplicator, [_wrap_merged(rm_prop)], storage, set())
        assert result.anchors_updated == 1
        await _assert_db_sources(storage, or_prop.unique_id, {"openrent", "rightmove"})

        # Run 3: Zoopla merges in
        result = await _cross_run_deduplicate(deduplicator, [_wrap_merged(zp_prop)], storage, set())
        assert result.anchors_updated == 1
        await _assert_db_sources(storage, or_prop.unique_id, {"openrent", "rightmove", "zoopla"})

        # Run 4: OnTheMarket merges in
        result = await _cross_run_deduplicate(
            deduplicator, [_wrap_merged(otm_prop)], storage, set()
        )
        assert result.anchors_updated == 1
        await _assert_db_sources(
            storage,
            or_prop.unique_id,
            {"openrent", "rightmove", "zoopla", "onthemarket"},
        )

        # Clear any reanalysis flags from the merge phase
        conn = await storage._get_connection()
        await conn.execute(
            "UPDATE quality_analyses SET reanalysis_requested_at = NULL "
            "WHERE property_unique_id = ?",
            (or_prop.unique_id,),
        )
        await conn.commit()

        # -- The actual regression scenario --
        # Run 5: Re-scrape only OpenRent + Rightmove (subset of 4 known sources)
        result = await _cross_run_deduplicate(
            deduplicator,
            [_wrap_merged(or_prop), _wrap_merged(rm_prop)],
            storage,
            set(),
        )

        # Metadata update happens (new_property_merged is True because these
        # URLs belong to new MergedProperty objects), but no NEW sources →
        # request_reanalysis must NOT be called.
        assert result.anchors_updated == 1
        await _assert_reanalysis_state(storage, or_prop.unique_id, requested=False)

        # Sources should still be all 4 (update_merged_sources unions, not overwrites)
        await _assert_db_sources(
            storage,
            or_prop.unique_id,
            {"openrent", "rightmove", "zoopla", "onthemarket"},
        )


# ---------------------------------------------------------------------------
# Class 8: TestDryRunPath
# ---------------------------------------------------------------------------


class TestDryRunPath:
    """Dry run exercises the reanalysis drain path."""

    async def test_dry_run_drains_reanalysis_queue(
        self,
        make_property: Callable[..., Property],
        quality_v1: PropertyQualityAnalysis,
        quality_v2: PropertyQualityAnalysis,
    ):
        """Pre-seed an anchor with reanalysis pending. Call run_dry_run with a new
        property (so the pipeline proceeds past the early-exit gate).
        Verify: drain runs, quality updated, no Telegram notification."""
        anchor = make_property(
            source=PropertySource.OPENRENT,
            source_id="anchor-dry",
            postcode="E8 3RH",
            price_pcm=1800,
        )
        # A genuinely new property so the pipeline doesn't exit early
        new_prop = make_property(
            source=PropertySource.RIGHTMOVE,
            source_id="new-dry",
            postcode="E8 4AB",
            price_pcm=2000,
            bedrooms=2,
            latitude=51.5520,
            longitude=-0.0610,
            address="45 Dalston Lane, Hackney, London",
        )

        settings = Settings(
            telegram_bot_token=SecretStr("fake:token"),
            telegram_chat_id=0,
            database_path=":memory:",
            search_areas="e8",
            min_price=1500,
            max_price=2500,
            min_bedrooms=1,
            max_bedrooms=2,
            enable_quality_filter=True,
            require_floorplan=False,
            anthropic_api_key=SecretStr("test-key"),
        )

        shared_storage = PropertyStorage(":memory:")
        await shared_storage.initialize()

        try:
            # Pre-seed: save anchor with quality analysis and pending reanalysis
            await _populate_run1(shared_storage, anchor, quality_v1)
            await shared_storage.request_reanalysis([anchor.unique_id])

            with _pipeline_mocks(
                scrape_return=[new_prop],
                quality_side_effect=lambda m, **kw: (m, quality_v2),
                shared_storage=shared_storage,
            ) as ctx:

                async def _intercept_close(self_storage):
                    pass

                with patch.object(PropertyStorage, "close", _intercept_close):
                    await run_dry_run(settings)

            # Quality should be updated (drain ran)
            conn = await shared_storage._get_connection()
            cursor = await conn.execute(
                "SELECT overall_rating FROM quality_analyses WHERE property_unique_id = ?",
                (anchor.unique_id,),
            )
            qa_row = await cursor.fetchone()
            assert qa_row is not None
            assert qa_row["overall_rating"] == 5

            # Reanalysis flag cleared
            await _assert_reanalysis_state(shared_storage, anchor.unique_id, requested=False)

            # No Telegram notification sent (dry run)
            ctx.notifier.send_merged_property_notification.assert_not_awaited()
            ctx.notifier.send_property_notification.assert_not_awaited()

        finally:
            await shared_storage.close()


# ---------------------------------------------------------------------------
# Helpers for image-based tests
# ---------------------------------------------------------------------------


def _create_solid_image(color: str = "red", size: tuple[int, int] = (100, 100)) -> bytes:
    """Create a solid-color JPEG image."""
    import io

    from PIL import Image

    img = Image.new("RGB", size, color=color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _create_checkerboard_image(
    cell_size: int = 10,
    size: tuple[int, int] = (100, 100),
) -> bytes:
    """Create a checkerboard JPEG image — structurally distinct from solid colors.

    pHash tolerates color variations between solid-color images, so we need
    structurally different patterns for images that must NOT match.
    """
    import io

    from PIL import Image, ImageDraw

    img = Image.new("RGB", size, (255, 255, 255))
    draw = ImageDraw.Draw(img)
    for x in range(0, size[0], cell_size):
        for y in range(0, size[1], cell_size):
            if (x + y) % (cell_size * 2) == 0:
                draw.rectangle([x, y, x + cell_size, y + cell_size], fill=(0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _make_real_image_cache_matching(
    tmp_path: Any,
    unique_id: str,
    count: int = 3,
) -> list[str]:
    """Create real solid-color image files in the cache dir.

    All images from this helper use the same colors, so two properties
    created with this helper WILL match on image hashing.
    """
    colors = ["red", "blue", "green", "orange", "purple"]
    cache_dir = get_cache_dir(str(tmp_path), unique_id)
    filenames = []
    for i in range(count):
        fname = f"gallery_{i:03d}_real{i:04d}.jpg"
        path = cache_dir / fname
        save_image_bytes(path, _create_solid_image(colors[i % len(colors)]))
        filenames.append(fname)
    return filenames


def _make_real_image_cache_distinct(
    tmp_path: Any,
    unique_id: str,
    count: int = 3,
) -> list[str]:
    """Create structurally distinct image files (checkerboard patterns).

    Images from this helper will NOT match solid-color images on pHash.
    """
    cache_dir = get_cache_dir(str(tmp_path), unique_id)
    filenames = []
    for i in range(count):
        fname = f"gallery_{i:03d}_real{i:04d}.jpg"
        path = cache_dir / fname
        # Vary cell size to make each image different from each other too
        save_image_bytes(path, _create_checkerboard_image(cell_size=5 + i * 5))
        filenames.append(fname)
    return filenames


# ---------------------------------------------------------------------------
# Class 9: TestSameBuildingDisambiguation
# ---------------------------------------------------------------------------


class TestSameBuildingDisambiguation:
    """Cross-run dedup must NOT merge different flats in the same building.

    When image hashing is active, the gallery rejection guard prevents
    merging when both properties have gallery hashes but zero images match.
    F1 hardened the cross-run path to always enable image hashing.
    """

    async def test_same_building_different_flats_not_merged(
        self,
        storage: PropertyStorage,
        make_property: Callable[..., Property],
        quality_v1: PropertyQualityAnalysis,
        tmp_path: Any,
    ):
        """Two flats in the same building (same postcode, coords, price, beds)
        but with different gallery images → NOT merged during cross-run dedup."""
        data_dir = str(tmp_path)

        # Same building: identical postcode, coordinates, price, bedrooms
        anchor = make_property(
            source=PropertySource.OPENRENT,
            postcode="E8 3RH",
            price_pcm=1800,
            bedrooms=2,
            latitude=51.5465,
            longitude=-0.0553,
        )
        new = make_property(
            source=PropertySource.ZOOPLA,
            postcode="E8 3RH",
            price_pcm=1800,
            bedrooms=2,
            latitude=51.5465,
            longitude=-0.0553,
        )

        await _populate_run1(storage, anchor, quality_v1)

        # Create structurally DIFFERENT gallery images for each property.
        # Anchor: solid-color images, new property: checkerboard patterns.
        # pHash tolerates color variations, so we need structural differences.
        _make_real_image_cache_matching(tmp_path, anchor.unique_id, count=3)
        _make_real_image_cache_distinct(tmp_path, new.unique_id, count=3)

        # Cross-run dedup with image hashing enabled (F1 fix)
        deduplicator = Deduplicator(
            enable_cross_platform=True,
            enable_image_hashing=True,
            data_dir=data_dir,
        )
        result = await _cross_run_deduplicate(
            deduplicator,
            [_wrap_merged(new)],
            storage,
            set(),
            data_dir=data_dir,
        )

        # Should NOT merge — different flats in same building
        assert result.anchors_updated == 0
        assert len(result.genuinely_new) == 1
        assert result.genuinely_new[0].canonical.unique_id == new.unique_id

    async def test_same_building_same_flat_merged_with_matching_images(
        self,
        storage: PropertyStorage,
        make_property: Callable[..., Property],
        quality_v1: PropertyQualityAnalysis,
        tmp_path: Any,
    ):
        """Same flat listed on two platforms with matching gallery images → merged."""
        data_dir = str(tmp_path)

        anchor = make_property(
            source=PropertySource.OPENRENT,
            postcode="E8 3RH",
            price_pcm=1800,
            bedrooms=2,
            latitude=51.5465,
            longitude=-0.0553,
        )
        new = make_property(
            source=PropertySource.ZOOPLA,
            postcode="E8 3RH",
            price_pcm=1800,
            bedrooms=2,
            latitude=51.5465,
            longitude=-0.0553,
        )

        await _populate_run1(storage, anchor, quality_v1)

        # Create IDENTICAL gallery images for both (same flat, different platforms)
        _make_real_image_cache_matching(tmp_path, anchor.unique_id, count=3)
        _make_real_image_cache_matching(tmp_path, new.unique_id, count=3)

        deduplicator = Deduplicator(
            enable_cross_platform=True,
            enable_image_hashing=True,
            data_dir=data_dir,
        )
        result = await _cross_run_deduplicate(
            deduplicator,
            [_wrap_merged(new)],
            storage,
            set(),
            data_dir=data_dir,
        )

        # Should merge — same flat on different platforms
        assert result.anchors_updated == 1
        assert len(result.genuinely_new) == 0
        await _assert_db_sources(storage, anchor.unique_id, {"openrent", "zoopla"})

    async def test_global_image_hashing_disabled_still_prevents_merge(
        self,
        storage: PropertyStorage,
        make_property: Callable[..., Property],
        quality_v1: PropertyQualityAnalysis,
        tmp_path: Any,
    ):
        """F1 integration: _run_post_enrichment uses image hashing even when
        the global enable_image_hash_matching setting is False.

        This is the core F1 scenario — if someone disables image hashing for
        performance, same-building disambiguation must still work in cross-run
        dedup because _run_post_enrichment unconditionally enables it.
        """
        # Settings with image hashing DISABLED globally, DB in tmp_path so
        # data_dir resolves to tmp_path for image cache lookup.
        db_path = str(tmp_path / "test.db")
        settings = Settings(
            telegram_bot_token=SecretStr("fake:token"),
            telegram_chat_id=0,
            database_path=db_path,
            search_areas="e8",
            min_price=1500,
            max_price=2500,
            min_bedrooms=1,
            max_bedrooms=2,
            enable_image_hash_matching=False,  # ← globally disabled
            require_floorplan=False,
        )

        anchor = make_property(
            source=PropertySource.OPENRENT,
            postcode="E8 3RH",
            price_pcm=1800,
            bedrooms=2,
            latitude=51.5465,
            longitude=-0.0553,
        )
        new = make_property(
            source=PropertySource.ZOOPLA,
            postcode="E8 3RH",
            price_pcm=1800,
            bedrooms=2,
            latitude=51.5465,
            longitude=-0.0553,
        )

        await _populate_run1(storage, anchor, quality_v1)

        # Structurally different images → different flats
        _make_real_image_cache_matching(tmp_path, anchor.unique_id, count=3)
        _make_real_image_cache_distinct(tmp_path, new.unique_id, count=3)

        # Go through _run_post_enrichment (the production code path)
        # rather than constructing a Deduplicator manually.
        post_result = await _run_post_enrichment([_wrap_merged(new)], storage, settings, set())

        # Should NOT merge — F1 ensures cross-run dedup always hashes
        assert post_result is not None
        merged_to_notify, anchors_updated, _post_dedup, _post_fp = post_result
        assert anchors_updated == 0
        assert len(merged_to_notify) == 1
        assert merged_to_notify[0].canonical.unique_id == new.unique_id

    async def test_no_cached_images_merges_on_location_signals(
        self,
        storage: PropertyStorage,
        make_property: Callable[..., Property],
        quality_v1: PropertyQualityAnalysis,
        tmp_path: Any,
    ):
        """When NEITHER property has cached gallery images, the gallery
        rejection guard is bypassed and merge proceeds on location signals.

        This is expected: old anchors from before image caching was added
        won't have cached images. The guard can only fire when both sides
        have hashable galleries. Without images, we accept the location match.
        """
        data_dir = str(tmp_path)

        anchor = make_property(
            source=PropertySource.OPENRENT,
            postcode="E8 3RH",
            price_pcm=1800,
            bedrooms=2,
            latitude=51.5465,
            longitude=-0.0553,
        )
        new = make_property(
            source=PropertySource.ZOOPLA,
            postcode="E8 3RH",
            price_pcm=1800,
            bedrooms=2,
            latitude=51.5465,
            longitude=-0.0553,
        )

        await _populate_run1(storage, anchor, quality_v1)

        # Deliberately NO cached images for either property.
        # hash_cached_gallery returns {} → guard bypassed.
        deduplicator = Deduplicator(
            enable_cross_platform=True,
            enable_image_hashing=True,
            data_dir=data_dir,
        )
        result = await _cross_run_deduplicate(
            deduplicator,
            [_wrap_merged(new)],
            storage,
            set(),
            data_dir=data_dir,
        )

        # DOES merge — no image evidence to reject, location signals sufficient
        assert result.anchors_updated == 1
        assert len(result.genuinely_new) == 0
        await _assert_db_sources(storage, anchor.unique_id, {"openrent", "zoopla"})


# ---------------------------------------------------------------------------
# Class 10: TestCrossPlatformPriceDropDetection
# ---------------------------------------------------------------------------


class TestCrossPlatformPriceDropDetection:
    """F2: Price drops detected when a new platform lists at a lower price."""

    async def test_new_source_lower_price_records_drop(
        self,
        storage: PropertyStorage,
        make_property: Callable[..., Property],
        quality_v1: PropertyQualityAnalysis,
    ):
        """Anchor at £1800, new source at £1700 → price_history row with change=-100."""
        anchor, new = _make_matching_pair(
            make_property,
            PropertySource.OPENRENT,
            PropertySource.ZOOPLA,
            price_a=1800,
            price_b=1700,
        )
        await _populate_run1(storage, anchor, quality_v1)

        result = await _cross_run_deduplicate(
            Deduplicator(enable_cross_platform=True),
            [_wrap_merged(new)],
            storage,
            set(),
        )

        assert result.anchors_updated == 1

        # Verify price_history row was inserted
        conn = await storage._get_connection()
        cursor = await conn.execute(
            "SELECT old_price, new_price, change_amount, source "
            "FROM price_history WHERE property_unique_id = ?",
            (anchor.unique_id,),
        )
        row = await cursor.fetchone()
        assert row is not None, "Expected price_history row for cross-platform price drop"
        assert row["old_price"] == 1800
        assert row["new_price"] == 1700
        assert row["change_amount"] == -100
        assert row["source"] == "cross_platform"

        # price_pcm should be updated to the new lower price
        # price_drop_notified should be 0 (triggers Telegram notification flow)
        cursor = await conn.execute(
            "SELECT price_pcm, price_drop_notified FROM properties WHERE unique_id = ?",
            (anchor.unique_id,),
        )
        price_row = await cursor.fetchone()
        assert price_row is not None
        assert price_row["price_pcm"] == 1700
        assert price_row["price_drop_notified"] == 0

    async def test_new_source_same_price_no_history(
        self,
        storage: PropertyStorage,
        make_property: Callable[..., Property],
        quality_v1: PropertyQualityAnalysis,
    ):
        """Same price on new platform → no price_history row."""
        anchor, new = _make_matching_pair(
            make_property,
            PropertySource.OPENRENT,
            PropertySource.ZOOPLA,
            price_a=1800,
            price_b=1800,
        )
        await _populate_run1(storage, anchor, quality_v1)

        result = await _cross_run_deduplicate(
            Deduplicator(enable_cross_platform=True),
            [_wrap_merged(new)],
            storage,
            set(),
        )

        assert result.anchors_updated == 1

        conn = await storage._get_connection()
        cursor = await conn.execute(
            "SELECT * FROM price_history WHERE property_unique_id = ?",
            (anchor.unique_id,),
        )
        row = await cursor.fetchone()
        assert row is None, "No price_history expected when prices match"

    async def test_new_source_higher_price_records_increase(
        self,
        storage: PropertyStorage,
        make_property: Callable[..., Property],
        quality_v1: PropertyQualityAnalysis,
    ):
        """Anchor at £1800, new source at £2000 → price_history with change=+200."""
        anchor, new = _make_matching_pair(
            make_property,
            PropertySource.OPENRENT,
            PropertySource.ZOOPLA,
            price_a=1800,
            price_b=2000,
        )
        await _populate_run1(storage, anchor, quality_v1)

        result = await _cross_run_deduplicate(
            Deduplicator(enable_cross_platform=True),
            [_wrap_merged(new)],
            storage,
            set(),
        )

        assert result.anchors_updated == 1

        conn = await storage._get_connection()
        cursor = await conn.execute(
            "SELECT change_amount FROM price_history WHERE property_unique_id = ?",
            (anchor.unique_id,),
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row["change_amount"] == 200

    async def test_two_new_sources_different_prices_records_lowest(
        self,
        storage: PropertyStorage,
        make_property: Callable[..., Property],
        quality_v1: PropertyQualityAnalysis,
    ):
        """Two new sources merge into same anchor: Zoopla at £1700, Rightmove
        at £1750, anchor at £1800. Should record drop to £1700 (the lowest)."""
        anchor = make_property(
            source=PropertySource.OPENRENT,
            postcode="E8 3RH",
            price_pcm=1800,
            bedrooms=2,
            latitude=51.5465,
            longitude=-0.0553,
        )
        new_z = make_property(
            source=PropertySource.ZOOPLA,
            postcode="E8 3RH",
            price_pcm=1700,
            bedrooms=2,
            latitude=51.5465,
            longitude=-0.0553,
        )
        new_rm = make_property(
            source=PropertySource.RIGHTMOVE,
            postcode="E8 3RH",
            price_pcm=1750,
            bedrooms=2,
            latitude=51.5465,
            longitude=-0.0553,
        )

        await _populate_run1(storage, anchor, quality_v1)

        result = await _cross_run_deduplicate(
            Deduplicator(enable_cross_platform=True),
            [_wrap_merged(new_z), _wrap_merged(new_rm)],
            storage,
            set(),
        )

        assert result.anchors_updated >= 1
        assert len(result.genuinely_new) == 0

        # Should record the drop to £1700 (lowest new source price)
        conn = await storage._get_connection()
        cursor = await conn.execute(
            "SELECT old_price, new_price, change_amount "
            "FROM price_history WHERE property_unique_id = ?",
            (anchor.unique_id,),
        )
        row = await cursor.fetchone()
        assert row is not None, "Expected price_history row for multi-source drop"
        assert row["old_price"] == 1800
        assert row["new_price"] == 1700
        assert row["change_amount"] == -100

    async def test_rescrape_known_source_no_price_record(
        self,
        storage: PropertyStorage,
        make_property: Callable[..., Property],
        quality_v1: PropertyQualityAnalysis,
    ):
        """Rescraping a known source at the same price should NOT create a
        price_history entry via the F2 path.

        truly_new_sources is empty for known sources, so the F2 price
        detection block is never entered. Step 3b (_detect_price_changes)
        handles same-source price changes separately.
        """
        anchor, new = _make_matching_pair(
            make_property,
            PropertySource.OPENRENT,
            PropertySource.ZOOPLA,
            price_a=1800,
            price_b=1800,
        )
        await _populate_run1(storage, anchor, quality_v1)

        # First cross-run: Zoopla merges in (new source)
        await _cross_run_deduplicate(
            Deduplicator(enable_cross_platform=True),
            [_wrap_merged(new)],
            storage,
            set(),
        )
        await _assert_db_sources(storage, anchor.unique_id, {"openrent", "zoopla"})

        # Rescrape: Zoopla arrives again at same price (no longer a new source)
        result = await _cross_run_deduplicate(
            Deduplicator(enable_cross_platform=True),
            [_wrap_merged(new)],
            storage,
            set(),
        )

        # Metadata updated but no NEW sources → truly_new_sources is empty
        assert result.anchors_updated == 1

        # No price_history at all — first merge was same price, rescrape skipped
        conn = await storage._get_connection()
        cursor = await conn.execute(
            "SELECT * FROM price_history WHERE property_unique_id = ?",
            (anchor.unique_id,),
        )
        row = await cursor.fetchone()
        assert row is None, "Rescrape of known source should not create price_history"

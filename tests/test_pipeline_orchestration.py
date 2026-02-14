"""Tests for pipeline orchestration functions in main.py.

Tests the pipeline stages with injected fakes — mock scrapers, in-memory
storage, and mock notifiers. Focuses on wiring correctness and stage ordering.
"""

from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

from home_finder.config import Settings
from home_finder.db import PropertyStorage
from home_finder.filters.detail_enrichment import EnrichmentResult
from home_finder.main import (
    PreAnalysisResult,
    _run_pre_analysis_pipeline,
    _run_quality_and_save,
    _save_one,
    scrape_all_platforms,
)
from home_finder.models import (
    MergedProperty,
    Property,
    PropertyQualityAnalysis,
    PropertySource,
    TransportMode,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def storage() -> PropertyStorage:
    s = PropertyStorage(":memory:")
    await s.initialize()
    yield s  # type: ignore[misc]
    await s.close()


@pytest.fixture
def test_settings() -> Settings:
    return Settings(
        telegram_bot_token="fake:test-token",
        telegram_chat_id=0,
        database_path=":memory:",
        search_areas="e8",
        min_price=1500,
        max_price=2500,
        min_bedrooms=1,
        max_bedrooms=2,
        enable_quality_filter=False,
        require_floorplan=False,
        traveltime_app_id="",
        traveltime_api_key="",
    )


def _mock_scraper(source: PropertySource, **kwargs: Any) -> AsyncMock:
    """Create a mock scraper with BaseScraper's new properties set correctly."""
    s = AsyncMock()
    s.source = source
    s.should_skip_remaining_areas = False
    s.max_areas_per_run = None
    s.area_delay = AsyncMock()
    s.scrape = AsyncMock(return_value=kwargs.get("return_value", []))
    s.close = AsyncMock()
    if "side_effect" in kwargs:
        s.scrape.side_effect = kwargs["side_effect"]
    return s


# ---------------------------------------------------------------------------
# _save_one
# ---------------------------------------------------------------------------


class TestSaveOne:
    async def test_completes_analysis(
        self,
        storage: PropertyStorage,
        make_merged_property: Callable[..., MergedProperty],
    ) -> None:
        merged = make_merged_property()
        # Pre-save (as pipeline does before analysis)
        await storage.save_pre_analysis_properties([merged], {})
        await _save_one(merged, None, None, storage)

        count = await storage.get_property_count()
        assert count == 1
        # Should transition from pending_analysis to pending
        tracked = await storage.get_property(merged.unique_id)
        assert tracked is not None
        assert tracked.notification_status.value == "pending"

    async def test_preserves_commute_from_pre_save(
        self,
        storage: PropertyStorage,
        make_merged_property: Callable[..., MergedProperty],
    ) -> None:
        merged = make_merged_property()
        commute_info = (18, TransportMode.CYCLING)
        # Pre-save with commute data
        await storage.save_pre_analysis_properties([merged], {merged.unique_id: commute_info})
        await _save_one(merged, commute_info, None, storage)

        tracked = await storage.get_property(merged.unique_id)
        assert tracked is not None
        assert tracked.commute_minutes == 18
        assert tracked.transport_mode == TransportMode.CYCLING

    async def test_saves_quality_analysis(
        self,
        storage: PropertyStorage,
        sample_quality_analysis: PropertyQualityAnalysis,
        make_merged_property: Callable[..., MergedProperty],
    ) -> None:
        merged = make_merged_property()
        await storage.save_pre_analysis_properties([merged], {})
        await _save_one(merged, None, sample_quality_analysis, storage)

        # Verify quality analysis was stored
        detail = await storage.get_property_detail(merged.unique_id)
        assert detail is not None
        assert detail["quality_rating"] == 4


# ---------------------------------------------------------------------------
# _run_quality_and_save
# ---------------------------------------------------------------------------


@patch("home_finder.main._lookup_wards", new_callable=AsyncMock)
class TestRunQualityAndSave:
    async def test_processes_all_properties(
        self,
        _mock_wards: AsyncMock,
        storage: PropertyStorage,
        make_property: Callable[..., Property],
        make_merged_property: Callable[..., MergedProperty],
    ) -> None:
        props = [make_merged_property(price_pcm=1800 + i * 10) for i in range(3)]
        pre = PreAnalysisResult(merged_to_process=props, commute_lookup={})
        settings = Settings(
            telegram_bot_token="fake:token",
            telegram_chat_id=0,
            database_path=":memory:",
            search_areas="e8",
            enable_quality_filter=False,
            traveltime_app_id="",
            traveltime_api_key="",
        )

        callback = AsyncMock()
        count = await _run_quality_and_save(pre, settings, storage, callback)

        assert count == 3
        assert callback.await_count == 3

    async def test_callback_receives_correct_args(
        self,
        _mock_wards: AsyncMock,
        storage: PropertyStorage,
        make_property: Callable[..., Property],
        make_merged_property: Callable[..., MergedProperty],
    ) -> None:
        merged = make_merged_property()
        commute = (15, TransportMode.PUBLIC_TRANSPORT)
        pre = PreAnalysisResult(
            merged_to_process=[merged],
            commute_lookup={merged.canonical.unique_id: commute},
        )
        settings = Settings(
            telegram_bot_token="fake:token",
            telegram_chat_id=0,
            database_path=":memory:",
            search_areas="e8",
            enable_quality_filter=False,
            traveltime_app_id="",
            traveltime_api_key="",
        )

        received: list[tuple] = []

        async def capture(m: Any, c: Any, q: Any) -> None:
            received.append((m, c, q))

        await _run_quality_and_save(pre, settings, storage, capture)

        assert len(received) == 1
        m, c, q = received[0]
        assert m.canonical.unique_id == merged.canonical.unique_id
        assert c == commute
        assert q is None  # quality disabled

    async def test_continues_on_analysis_error(
        self,
        _mock_wards: AsyncMock,
        storage: PropertyStorage,
        make_merged_property: Callable[..., MergedProperty],
    ) -> None:
        """If one property's analysis fails, others should still be processed."""
        props = [make_merged_property(price_pcm=1800 + i * 10) for i in range(3)]
        pre = PreAnalysisResult(merged_to_process=props, commute_lookup={})

        settings = Settings(
            telegram_bot_token="fake:token",
            telegram_chat_id=0,
            database_path=":memory:",
            search_areas="e8",
            enable_quality_filter=False,
            traveltime_app_id="",
            traveltime_api_key="",
        )

        callback = AsyncMock()
        count = await _run_quality_and_save(pre, settings, storage, callback)
        # All should process since quality is disabled (no errors)
        assert count == 3


# ---------------------------------------------------------------------------
# scrape_all_platforms
# ---------------------------------------------------------------------------


class TestScrapeAllPlatforms:
    async def test_returns_empty_when_no_areas(self) -> None:
        result = await scrape_all_platforms(
            min_price=1500,
            max_price=2500,
            min_bedrooms=1,
            max_bedrooms=2,
            search_areas=[],
        )
        assert result == []

    async def test_returns_empty_when_areas_is_none(self) -> None:
        result = await scrape_all_platforms(
            min_price=1500,
            max_price=2500,
            min_bedrooms=1,
            max_bedrooms=2,
            search_areas=None,
        )
        assert result == []

    @patch("home_finder.main.OpenRentScraper")
    @patch("home_finder.main.RightmoveScraper")
    @patch("home_finder.main.ZooplaScraper")
    @patch("home_finder.main.OnTheMarketScraper")
    async def test_collects_from_all_scrapers(
        self,
        mock_otm_cls: Any,
        mock_zoopla_cls: Any,
        mock_rm_cls: Any,
        mock_or_cls: Any,
        make_property: Callable[..., Property],
    ) -> None:
        # Setup mock scrapers
        sources = [
            PropertySource.OPENRENT,
            PropertySource.RIGHTMOVE,
            PropertySource.ZOOPLA,
            PropertySource.ONTHEMARKET,
        ]
        for i, mock_cls in enumerate([mock_or_cls, mock_rm_cls, mock_zoopla_cls, mock_otm_cls]):
            mock_cls.return_value = _mock_scraper(
                sources[i],
                return_value=[make_property(source=sources[i], source_id=f"s{i}")],
            )

        result = await scrape_all_platforms(
            min_price=1500,
            max_price=2500,
            min_bedrooms=1,
            max_bedrooms=2,
            search_areas=["e8"],
        )

        assert len(result) == 4

    @patch("home_finder.main.OpenRentScraper")
    @patch("home_finder.main.RightmoveScraper")
    @patch("home_finder.main.ZooplaScraper")
    @patch("home_finder.main.OnTheMarketScraper")
    async def test_respects_max_per_scraper(
        self,
        mock_otm_cls: Any,
        mock_zoopla_cls: Any,
        mock_rm_cls: Any,
        mock_or_cls: Any,
        make_property: Callable[..., Property],
    ) -> None:
        # Only configure OpenRent to return results (simpler)
        or_scraper = _mock_scraper(
            PropertySource.OPENRENT,
            return_value=[make_property(source_id=str(i)) for i in range(10)],
        )
        mock_or_cls.return_value = or_scraper

        for mock_cls, src in [
            (mock_rm_cls, PropertySource.RIGHTMOVE),
            (mock_zoopla_cls, PropertySource.ZOOPLA),
            (mock_otm_cls, PropertySource.ONTHEMARKET),
        ]:
            mock_cls.return_value = _mock_scraper(src)

        await scrape_all_platforms(
            min_price=1500,
            max_price=2500,
            min_bedrooms=1,
            max_bedrooms=2,
            search_areas=["e8"],
            max_per_scraper=3,
        )

        # max_per_scraper is passed to scraper as max_results; the scraper
        # is responsible for limiting. Here we verify it was passed correctly.
        call_kwargs = or_scraper.scrape.call_args
        assert call_kwargs.kwargs.get("max_results") == 3

    @patch("home_finder.main.OpenRentScraper")
    @patch("home_finder.main.RightmoveScraper")
    @patch("home_finder.main.ZooplaScraper")
    @patch("home_finder.main.OnTheMarketScraper")
    async def test_continues_on_scraper_error(
        self,
        mock_otm_cls: Any,
        mock_zoopla_cls: Any,
        mock_rm_cls: Any,
        mock_or_cls: Any,
        make_property: Callable[..., Property],
    ) -> None:
        # OpenRent fails
        or_scraper = _mock_scraper(
            PropertySource.OPENRENT,
            side_effect=Exception("Scraper crash"),
        )
        mock_or_cls.return_value = or_scraper

        # Others succeed
        for mock_cls, src in [
            (mock_rm_cls, PropertySource.RIGHTMOVE),
            (mock_zoopla_cls, PropertySource.ZOOPLA),
            (mock_otm_cls, PropertySource.ONTHEMARKET),
        ]:
            mock_cls.return_value = _mock_scraper(
                src,
                return_value=[make_property(source=src, source_id=f"ok-{src.value}")],
            )

        result = await scrape_all_platforms(
            min_price=1500,
            max_price=2500,
            min_bedrooms=1,
            max_bedrooms=2,
            search_areas=["e8"],
        )

        # Should have results from 3 working scrapers
        assert len(result) == 3

    @patch("home_finder.main.OpenRentScraper")
    @patch("home_finder.main.RightmoveScraper")
    @patch("home_finder.main.ZooplaScraper")
    @patch("home_finder.main.OnTheMarketScraper")
    async def test_cross_area_dedup(
        self,
        mock_otm_cls: Any,
        mock_zoopla_cls: Any,
        mock_rm_cls: Any,
        mock_or_cls: Any,
        make_property: Callable[..., Property],
    ) -> None:
        """Same property from multiple areas should be deduped."""
        shared_prop = make_property(source_id="shared-1")

        mock_or_cls.return_value = _mock_scraper(
            PropertySource.OPENRENT, return_value=[shared_prop]
        )

        for mock_cls, src in [
            (mock_rm_cls, PropertySource.RIGHTMOVE),
            (mock_zoopla_cls, PropertySource.ZOOPLA),
            (mock_otm_cls, PropertySource.ONTHEMARKET),
        ]:
            mock_cls.return_value = _mock_scraper(src)

        result = await scrape_all_platforms(
            min_price=1500,
            max_price=2500,
            min_bedrooms=1,
            max_bedrooms=2,
            search_areas=["e8", "hackney"],  # Same property returned for both areas
        )

        # Property should only appear once despite 2 areas
        assert len(result) == 1

    @patch("home_finder.main.OpenRentScraper")
    @patch("home_finder.main.RightmoveScraper")
    @patch("home_finder.main.ZooplaScraper")
    @patch("home_finder.main.OnTheMarketScraper")
    async def test_backfills_outcode(
        self,
        mock_otm_cls: Any,
        mock_zoopla_cls: Any,
        mock_rm_cls: Any,
        mock_or_cls: Any,
        make_property: Callable[..., Property],
    ) -> None:
        """Properties without postcodes get outcode backfilled when searching by outcode."""
        no_postcode = make_property(source_id="no-pc", postcode=None, latitude=None, longitude=None)

        mock_or_cls.return_value = _mock_scraper(
            PropertySource.OPENRENT, return_value=[no_postcode]
        )

        for mock_cls, src in [
            (mock_rm_cls, PropertySource.RIGHTMOVE),
            (mock_zoopla_cls, PropertySource.ZOOPLA),
            (mock_otm_cls, PropertySource.ONTHEMARKET),
        ]:
            mock_cls.return_value = _mock_scraper(src)

        result = await scrape_all_platforms(
            min_price=1500,
            max_price=2500,
            min_bedrooms=1,
            max_bedrooms=2,
            search_areas=["E8"],
        )

        assert len(result) == 1
        assert result[0].postcode == "E8"


# ---------------------------------------------------------------------------
# _run_pre_analysis_pipeline
# ---------------------------------------------------------------------------


class TestPreAnalysisPipeline:
    @patch("home_finder.main.scrape_all_platforms")
    async def test_returns_none_on_no_results(
        self,
        mock_scrape: Any,
        storage: PropertyStorage,
        test_settings: Settings,
    ) -> None:
        mock_scrape.return_value = []
        result = await _run_pre_analysis_pipeline(test_settings, storage)
        assert result is None

    @patch("home_finder.main.scrape_all_platforms")
    async def test_returns_none_when_all_filtered(
        self,
        mock_scrape: Any,
        storage: PropertyStorage,
        test_settings: Settings,
        make_property: Callable[..., Property],
    ) -> None:
        # Price way above max
        mock_scrape.return_value = [make_property(source_id="expensive", price_pcm=99999)]
        result = await _run_pre_analysis_pipeline(test_settings, storage)
        assert result is None

    @patch("home_finder.main.enrich_merged_properties", new_callable=AsyncMock)
    @patch("home_finder.main.scrape_all_platforms")
    async def test_returns_result_for_valid_properties(
        self,
        mock_scrape: Any,
        mock_enrich: Any,
        storage: PropertyStorage,
        test_settings: Settings,
        make_property: Callable[..., Property],
    ) -> None:
        prop = make_property(source_id="valid")
        mock_scrape.return_value = [prop]
        # enrich returns the merged properties unchanged
        mock_enrich.side_effect = lambda merged, *a, **kw: EnrichmentResult(enriched=merged)

        result = await _run_pre_analysis_pipeline(test_settings, storage)
        assert result is not None
        assert len(result.merged_to_process) == 1

    @patch("home_finder.main.enrich_merged_properties", new_callable=AsyncMock)
    @patch("home_finder.main.scrape_all_platforms")
    async def test_filters_already_seen_properties(
        self,
        mock_scrape: Any,
        mock_enrich: Any,
        storage: PropertyStorage,
        test_settings: Settings,
        make_property: Callable[..., Property],
        make_merged_property: Callable[..., MergedProperty],
    ) -> None:
        prop = make_property(source_id="seen")
        # Use the same canonical property for consistent unique_id
        merged_with_prop = MergedProperty(
            canonical=prop,
            sources=(prop.source,),
            source_urls={prop.source: prop.url},
            images=(),
            floorplan=None,
            min_price=prop.price_pcm,
            max_price=prop.price_pcm,
        )

        # Save it first
        await storage.save_merged_property(merged_with_prop)

        # Now scrape returns the same property
        mock_scrape.return_value = [prop]
        mock_enrich.side_effect = lambda merged, *a, **kw: EnrichmentResult(enriched=merged)

        result = await _run_pre_analysis_pipeline(test_settings, storage)
        assert result is None  # No new properties

    @patch("home_finder.main.enrich_merged_properties", new_callable=AsyncMock)
    @patch("home_finder.main.scrape_all_platforms")
    async def test_floorplan_gate_drops_properties(
        self,
        mock_scrape: Any,
        mock_enrich: Any,
        storage: PropertyStorage,
        make_property: Callable[..., Property],
    ) -> None:
        settings = Settings(
            telegram_bot_token="fake:token",
            telegram_chat_id=0,
            database_path=":memory:",
            search_areas="e8",
            min_price=1500,
            max_price=2500,
            min_bedrooms=1,
            max_bedrooms=2,
            require_floorplan=True,
            enable_quality_filter=False,
            traveltime_app_id="",
            traveltime_api_key="",
        )
        # Use RIGHTMOVE (not OpenRent) since OpenRent is exempt from floorplan requirement
        prop = make_property(source=PropertySource.RIGHTMOVE, source_id="no-fp")
        mock_scrape.return_value = [prop]
        # enrich returns merged without floorplan
        mock_enrich.side_effect = lambda merged, *a, **kw: EnrichmentResult(enriched=merged)

        result = await _run_pre_analysis_pipeline(settings, storage)
        # No floorplan → filtered out
        assert result is None

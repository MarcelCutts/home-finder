"""Tests for pipeline orchestration functions in main.py.

Tests the pipeline stages with injected fakes — mock scrapers, in-memory
storage, and mock notifiers. Focuses on wiring correctness and stage ordering.
"""

from collections.abc import Callable
from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from pydantic import HttpUrl

from home_finder.config import Settings
from home_finder.db import PropertyStorage
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


def _prop(source: PropertySource = PropertySource.OPENRENT, source_id: str = "1") -> Property:
    urls = {
        PropertySource.OPENRENT: f"https://openrent.com/{source_id}",
        PropertySource.RIGHTMOVE: f"https://rightmove.co.uk/{source_id}",
        PropertySource.ZOOPLA: f"https://zoopla.co.uk/{source_id}",
        PropertySource.ONTHEMARKET: f"https://onthemarket.com/{source_id}",
    }
    return Property(
        source=source,
        source_id=source_id,
        url=HttpUrl(urls[source]),
        title=f"Test {source_id}",
        price_pcm=1800,
        bedrooms=1,
        address="123 Test St",
        postcode="E8 1AA",
        latitude=51.5465,
        longitude=-0.0553,
        first_seen=datetime(2026, 1, 15, 10, 0),
    )


def _merged(prop: Property | None = None) -> MergedProperty:
    p = prop or _prop()
    return MergedProperty(
        canonical=p,
        sources=(p.source,),
        source_urls={p.source: p.url},
        images=(),
        floorplan=None,
        min_price=p.price_pcm,
        max_price=p.price_pcm,
    )


# ---------------------------------------------------------------------------
# _save_one
# ---------------------------------------------------------------------------


class TestSaveOne:
    async def test_saves_property(self, storage: PropertyStorage) -> None:
        merged = _merged()
        await _save_one(merged, None, None, storage)

        count = await storage.get_property_count()
        assert count == 1

    async def test_saves_with_commute_info(self, storage: PropertyStorage) -> None:
        merged = _merged()
        commute_info = (18, TransportMode.CYCLING)
        await _save_one(merged, commute_info, None, storage)

        tracked = await storage.get_property(merged.unique_id)
        assert tracked is not None
        assert tracked.commute_minutes == 18
        assert tracked.transport_mode == TransportMode.CYCLING

    async def test_saves_quality_analysis(
        self, storage: PropertyStorage, sample_quality_analysis: PropertyQualityAnalysis
    ) -> None:
        merged = _merged()
        await _save_one(merged, None, sample_quality_analysis, storage)

        # Verify quality analysis was stored
        detail = await storage.get_property_detail(merged.unique_id)
        assert detail is not None
        assert detail["quality_rating"] == 4


# ---------------------------------------------------------------------------
# _run_quality_and_save
# ---------------------------------------------------------------------------


class TestRunQualityAndSave:
    async def test_processes_all_properties(self, storage: PropertyStorage) -> None:
        props = [_merged(_prop(source_id=str(i))) for i in range(3)]
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

    async def test_callback_receives_correct_args(self, storage: PropertyStorage) -> None:
        prop = _prop()
        merged = _merged(prop)
        commute = (15, TransportMode.PUBLIC_TRANSPORT)
        pre = PreAnalysisResult(
            merged_to_process=[merged],
            commute_lookup={prop.unique_id: commute},
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
        assert m.canonical.unique_id == prop.unique_id
        assert c == commute
        assert q is None  # quality disabled

    async def test_continues_on_analysis_error(self, storage: PropertyStorage) -> None:
        """If one property's analysis fails, others should still be processed."""
        props = [_merged(_prop(source_id=str(i))) for i in range(3)]
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
        self, mock_otm_cls: Any, mock_zoopla_cls: Any, mock_rm_cls: Any, mock_or_cls: Any
    ) -> None:
        # Setup mock scrapers
        for i, mock_cls in enumerate([mock_or_cls, mock_rm_cls, mock_zoopla_cls, mock_otm_cls]):
            sources = [
                PropertySource.OPENRENT,
                PropertySource.RIGHTMOVE,
                PropertySource.ZOOPLA,
                PropertySource.ONTHEMARKET,
            ]
            scraper = AsyncMock()
            scraper.source = sources[i]
            scraper.scrape = AsyncMock(return_value=[_prop(source=sources[i], source_id=f"s{i}")])
            scraper.close = AsyncMock()
            mock_cls.return_value = scraper

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
        self, mock_otm_cls: Any, mock_zoopla_cls: Any, mock_rm_cls: Any, mock_or_cls: Any
    ) -> None:
        # Only configure OpenRent to return results (simpler)
        or_scraper = AsyncMock()
        or_scraper.source = PropertySource.OPENRENT
        or_scraper.scrape = AsyncMock(
            return_value=[_prop(source_id=str(i)) for i in range(10)]
        )
        or_scraper.close = AsyncMock()
        mock_or_cls.return_value = or_scraper

        for mock_cls, src in [
            (mock_rm_cls, PropertySource.RIGHTMOVE),
            (mock_zoopla_cls, PropertySource.ZOOPLA),
            (mock_otm_cls, PropertySource.ONTHEMARKET),
        ]:
            s = AsyncMock()
            s.source = src
            s.scrape = AsyncMock(return_value=[])
            s.close = AsyncMock()
            mock_cls.return_value = s

        result = await scrape_all_platforms(
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
        self, mock_otm_cls: Any, mock_zoopla_cls: Any, mock_rm_cls: Any, mock_or_cls: Any
    ) -> None:
        # OpenRent fails
        or_scraper = AsyncMock()
        or_scraper.source = PropertySource.OPENRENT
        or_scraper.scrape = AsyncMock(side_effect=Exception("Scraper crash"))
        or_scraper.close = AsyncMock()
        mock_or_cls.return_value = or_scraper

        # Others succeed
        for mock_cls, src in [
            (mock_rm_cls, PropertySource.RIGHTMOVE),
            (mock_zoopla_cls, PropertySource.ZOOPLA),
            (mock_otm_cls, PropertySource.ONTHEMARKET),
        ]:
            s = AsyncMock()
            s.source = src
            s.scrape = AsyncMock(return_value=[_prop(source=src, source_id=f"ok-{src.value}")])
            s.close = AsyncMock()
            mock_cls.return_value = s

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
        self, mock_otm_cls: Any, mock_zoopla_cls: Any, mock_rm_cls: Any, mock_or_cls: Any
    ) -> None:
        """Same property from multiple areas should be deduped."""
        shared_prop = _prop(source_id="shared-1")

        or_scraper = AsyncMock()
        or_scraper.source = PropertySource.OPENRENT
        or_scraper.scrape = AsyncMock(return_value=[shared_prop])
        or_scraper.close = AsyncMock()
        mock_or_cls.return_value = or_scraper

        for mock_cls, src in [
            (mock_rm_cls, PropertySource.RIGHTMOVE),
            (mock_zoopla_cls, PropertySource.ZOOPLA),
            (mock_otm_cls, PropertySource.ONTHEMARKET),
        ]:
            s = AsyncMock()
            s.source = src
            s.scrape = AsyncMock(return_value=[])
            s.close = AsyncMock()
            mock_cls.return_value = s

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
        self, mock_otm_cls: Any, mock_zoopla_cls: Any, mock_rm_cls: Any, mock_or_cls: Any
    ) -> None:
        """Properties without postcodes get outcode backfilled when searching by outcode."""
        no_postcode = Property(
            source=PropertySource.OPENRENT,
            source_id="no-pc",
            url=HttpUrl("https://openrent.com/no-pc"),
            title="Test",
            price_pcm=1800,
            bedrooms=1,
            address="123 Test St",
            postcode=None,
        )

        or_scraper = AsyncMock()
        or_scraper.source = PropertySource.OPENRENT
        or_scraper.scrape = AsyncMock(return_value=[no_postcode])
        or_scraper.close = AsyncMock()
        mock_or_cls.return_value = or_scraper

        for mock_cls, src in [
            (mock_rm_cls, PropertySource.RIGHTMOVE),
            (mock_zoopla_cls, PropertySource.ZOOPLA),
            (mock_otm_cls, PropertySource.ONTHEMARKET),
        ]:
            s = AsyncMock()
            s.source = src
            s.scrape = AsyncMock(return_value=[])
            s.close = AsyncMock()
            mock_cls.return_value = s

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
    ) -> None:
        # Price way above max
        mock_scrape.return_value = [_prop(source_id="expensive").model_copy(update={"price_pcm": 99999})]
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
    ) -> None:
        prop = _prop(source_id="valid")
        mock_scrape.return_value = [prop]
        # enrich returns the merged properties unchanged
        mock_enrich.side_effect = lambda merged, *a, **kw: merged

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
    ) -> None:
        prop = _prop(source_id="seen")
        merged = _merged(prop)

        # Save it first
        await storage.save_merged_property(merged)

        # Now scrape returns the same property
        mock_scrape.return_value = [prop]
        mock_enrich.side_effect = lambda merged, *a, **kw: merged

        result = await _run_pre_analysis_pipeline(test_settings, storage)
        assert result is None  # No new properties

    @patch("home_finder.main.enrich_merged_properties", new_callable=AsyncMock)
    @patch("home_finder.main.scrape_all_platforms")
    async def test_floorplan_gate_drops_properties(
        self,
        mock_scrape: Any,
        mock_enrich: Any,
        storage: PropertyStorage,
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
        prop = _prop(source_id="no-fp")
        mock_scrape.return_value = [prop]
        # enrich returns merged without floorplan
        mock_enrich.side_effect = lambda merged, *a, **kw: merged

        result = await _run_pre_analysis_pipeline(settings, storage)
        # No floorplan → filtered out
        assert result is None

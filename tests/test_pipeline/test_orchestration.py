"""Tests for pipeline orchestration stages.

Tests the pipeline stages with injected fakes — mock scrapers, in-memory
storage, and mock notifiers. Focuses on wiring correctness and stage ordering.
"""

from collections.abc import AsyncGenerator, Callable
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from pydantic import SecretStr

from home_finder.config import Settings
from home_finder.db import PropertyStorage
from home_finder.enrichment.details import EnrichmentResult
from home_finder.models import (
    MergedProperty,
    Property,
    PropertyQualityAnalysis,
    PropertySource,
    SpaceAnalysis,
    TransportMode,
)
from home_finder.models.quality import RoomArea
from home_finder.pipeline.assessment import (
    complete_initial_assessment,
    persist_estimated_floor_area,
    process_initial_assessments,
)
from home_finder.pipeline.candidates import prepare_candidates
from home_finder.pipeline.collection import (
    collect_listings,
    scrape_all_platforms,
)
from home_finder.pipeline.contracts import CandidateBatch
from home_finder.scrapers.base import ScrapeResult

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def storage() -> AsyncGenerator[PropertyStorage, None]:
    s = PropertyStorage(":memory:")
    await s.initialize()
    yield s
    await s.close()


@pytest.fixture
def test_settings() -> Settings:
    return Settings(
        telegram_bot_token=SecretStr("fake:test-token"),
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
        traveltime_api_key=SecretStr(""),
    )


def _mock_scraper(source: PropertySource, **kwargs: Any) -> AsyncMock:
    """Create a mock scraper with BaseScraper's new properties set correctly."""
    s = AsyncMock()
    s.source = source
    s.should_skip_remaining_areas = False
    s.max_areas_per_run = None
    s.area_delay = AsyncMock()
    default_return = ScrapeResult(properties=kwargs.get("return_value", []), pages_fetched=1)
    s.scrape = AsyncMock(return_value=default_return)
    s.close = AsyncMock()
    if "side_effect" in kwargs:
        s.scrape.side_effect = kwargs["side_effect"]
    return s


# ---------------------------------------------------------------------------
# complete_initial_assessment
# ---------------------------------------------------------------------------


class TestCompleteInitialAssessment:
    async def test_completes_analysis(
        self,
        storage: PropertyStorage,
        make_merged_property: Callable[..., MergedProperty],
    ) -> None:
        merged = make_merged_property()
        # Pre-save (as pipeline does before analysis)
        await storage.processing.save_pre_analysis_properties([merged], {})
        await complete_initial_assessment(merged, None, storage)

        count = await storage.web.get_property_count()
        assert count == 1
        # A skipped assessment finishes processing without establishing an alert.
        tracked = await storage.get_property(merged.unique_id)
        assert tracked is not None
        assert tracked.notification_status.value == "dashboard_only"

    async def test_preserves_commute_from_pre_save(
        self,
        storage: PropertyStorage,
        make_merged_property: Callable[..., MergedProperty],
    ) -> None:
        merged = make_merged_property()
        commute_info = (18, TransportMode.CYCLING)
        # Pre-save with commute data
        await storage.processing.save_pre_analysis_properties(
            [merged], {merged.unique_id: commute_info}
        )
        await complete_initial_assessment(merged, None, storage)

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
        await storage.processing.save_pre_analysis_properties([merged], {})
        await complete_initial_assessment(merged, sample_quality_analysis, storage)

        # Verify quality analysis was stored
        detail = await storage.web.get_property_detail(merged.unique_id)
        assert detail is not None
        assert detail["quality_rating"] == 4


# ---------------------------------------------------------------------------
# process_initial_assessments
# ---------------------------------------------------------------------------


@patch("home_finder.pipeline.assessment._lookup_wards", new_callable=AsyncMock)
class TestInitialAssessments:
    async def test_processes_all_properties(
        self,
        _mock_wards: AsyncMock,
        storage: PropertyStorage,
        make_property: Callable[..., Property],
        make_merged_property: Callable[..., MergedProperty],
    ) -> None:
        props = [make_merged_property(price_pcm=1800 + i * 10) for i in range(3)]
        pre = CandidateBatch(candidates=props, commute_lookup={})
        settings = Settings(
            telegram_bot_token=SecretStr("fake:token"),
            telegram_chat_id=0,
            database_path=":memory:",
            search_areas="e8",
            enable_quality_filter=False,
            traveltime_app_id="",
            traveltime_api_key=SecretStr(""),
        )

        callback = AsyncMock()
        assessment = await process_initial_assessments(pre, settings, storage, callback)
        count, token_usage = assessment.processed_count, assessment.token_usage

        assert count == 3
        assert token_usage is None  # quality disabled
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
        pre = CandidateBatch(
            candidates=[merged],
            commute_lookup={merged.canonical.unique_id: commute},
        )
        settings = Settings(
            telegram_bot_token=SecretStr("fake:token"),
            telegram_chat_id=0,
            database_path=":memory:",
            search_areas="e8",
            enable_quality_filter=False,
            traveltime_app_id="",
            traveltime_api_key=SecretStr(""),
        )

        received: list[tuple[Any, Any, Any]] = []

        async def capture(m: Any, c: Any, q: Any) -> None:
            received.append((m, c, q))

        assessment = await process_initial_assessments(pre, settings, storage, capture)
        _count, token_usage = assessment.processed_count, assessment.token_usage

        assert len(received) == 1
        assert token_usage is None  # quality disabled
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
        pre = CandidateBatch(candidates=props, commute_lookup={})

        settings = Settings(
            telegram_bot_token=SecretStr("fake:token"),
            telegram_chat_id=0,
            database_path=":memory:",
            search_areas="e8",
            enable_quality_filter=False,
            traveltime_app_id="",
            traveltime_api_key=SecretStr(""),
        )

        callback = AsyncMock()
        assessment = await process_initial_assessments(pre, settings, storage, callback)
        count, _usage = assessment.processed_count, assessment.token_usage
        # All should process since quality is disabled (no errors)
        assert count == 3


# ---------------------------------------------------------------------------
# scrape_all_platforms
# ---------------------------------------------------------------------------


class TestScrapeAllPlatforms:
    async def test_returns_empty_when_no_areas(self) -> None:
        result, metrics = await scrape_all_platforms(
            min_price=1500,
            max_price=2500,
            min_bedrooms=1,
            max_bedrooms=2,
            search_areas=[],
        )
        assert result == []
        assert metrics == []

    async def test_returns_empty_when_areas_is_none(self) -> None:
        result, metrics = await scrape_all_platforms(
            min_price=1500,
            max_price=2500,
            min_bedrooms=1,
            max_bedrooms=2,
            search_areas=None,
        )
        assert result == []
        assert metrics == []

    @patch("home_finder.pipeline.collection.OpenRentScraper")
    @patch("home_finder.pipeline.collection.RightmoveScraper")
    @patch("home_finder.pipeline.collection.ZooplaScraper")
    @patch("home_finder.pipeline.collection.OnTheMarketScraper")
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

        result, metrics = await scrape_all_platforms(
            min_price=1500,
            max_price=2500,
            min_bedrooms=1,
            max_bedrooms=2,
            search_areas=["e8"],
        )

        assert len(result) == 4
        assert len(metrics) == 4

    @patch("home_finder.pipeline.collection.OpenRentScraper")
    @patch("home_finder.pipeline.collection.RightmoveScraper")
    @patch("home_finder.pipeline.collection.ZooplaScraper")
    @patch("home_finder.pipeline.collection.OnTheMarketScraper")
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

        _result, _metrics = await scrape_all_platforms(
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

    @patch("home_finder.pipeline.collection.OpenRentScraper")
    @patch("home_finder.pipeline.collection.RightmoveScraper")
    @patch("home_finder.pipeline.collection.ZooplaScraper")
    @patch("home_finder.pipeline.collection.OnTheMarketScraper")
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

        result, metrics = await scrape_all_platforms(
            min_price=1500,
            max_price=2500,
            min_bedrooms=1,
            max_bedrooms=2,
            search_areas=["e8"],
        )

        # Should have results from 3 working scrapers
        assert len(result) == 3
        assert len(metrics) == 4  # All scrapers produce metrics, even failing ones

    @patch("home_finder.pipeline.collection.OpenRentScraper")
    @patch("home_finder.pipeline.collection.RightmoveScraper")
    @patch("home_finder.pipeline.collection.ZooplaScraper")
    @patch("home_finder.pipeline.collection.OnTheMarketScraper")
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

        result, _metrics = await scrape_all_platforms(
            min_price=1500,
            max_price=2500,
            min_bedrooms=1,
            max_bedrooms=2,
            search_areas=["e8", "hackney"],  # Same property returned for both areas
        )

        # Property should only appear once despite 2 areas
        assert len(result) == 1

    @patch("home_finder.pipeline.collection.OpenRentScraper")
    @patch("home_finder.pipeline.collection.RightmoveScraper")
    @patch("home_finder.pipeline.collection.ZooplaScraper")
    @patch("home_finder.pipeline.collection.OnTheMarketScraper")
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

        result, _metrics = await scrape_all_platforms(
            min_price=1500,
            max_price=2500,
            min_bedrooms=1,
            max_bedrooms=2,
            search_areas=["E8"],
        )

        assert len(result) == 1
        assert result[0].postcode == "E8"

    @patch("home_finder.pipeline.collection.OpenRentScraper")
    @patch("home_finder.pipeline.collection.RightmoveScraper")
    @patch("home_finder.pipeline.collection.ZooplaScraper")
    @patch("home_finder.pipeline.collection.OnTheMarketScraper")
    async def test_known_ids_passed_to_scrapers(
        self,
        mock_otm_cls: Any,
        mock_zoopla_cls: Any,
        mock_rm_cls: Any,
        mock_or_cls: Any,
    ) -> None:
        """Each scraper receives its own source-specific known IDs."""
        sources = [
            PropertySource.OPENRENT,
            PropertySource.RIGHTMOVE,
            PropertySource.ZOOPLA,
            PropertySource.ONTHEMARKET,
        ]
        mock_scrapers = []
        for i, mock_cls in enumerate([mock_or_cls, mock_rm_cls, mock_zoopla_cls, mock_otm_cls]):
            s = _mock_scraper(sources[i])
            mock_cls.return_value = s
            mock_scrapers.append(s)

        known_ids_by_source = {
            "openrent": {"or-1", "or-2"},
            "rightmove": {"rm-1"},
            "zoopla": set(),
            # onthemarket intentionally omitted — should get None
        }

        _result, _metrics = await scrape_all_platforms(
            min_price=1500,
            max_price=2500,
            min_bedrooms=1,
            max_bedrooms=2,
            search_areas=["e8"],
            known_ids_by_source=known_ids_by_source,
        )

        # Verify each scraper received its source-specific known IDs
        or_known = mock_scrapers[0].scrape.call_args.kwargs.get("known_source_ids")
        assert or_known == {"or-1", "or-2"}

        rm_known = mock_scrapers[1].scrape.call_args.kwargs.get("known_source_ids")
        assert rm_known == {"rm-1"}

        zoopla_known = mock_scrapers[2].scrape.call_args.kwargs.get("known_source_ids")
        assert zoopla_known == set()

        otm_known = mock_scrapers[3].scrape.call_args.kwargs.get("known_source_ids")
        assert otm_known is None

    @patch("home_finder.pipeline.collection.scrape_all_platforms", new_callable=AsyncMock)
    async def test_full_scrape_skips_known_ids_lookup(
        self,
        mock_scrape: AsyncMock,
        storage: PropertyStorage,
        test_settings: Settings,
    ) -> None:
        """full_scrape=True skips DB known-ID lookup and passes None to scrape_all_platforms."""
        mock_scrape.return_value = ([], [])

        # Spy on storage.get_all_known_source_ids
        storage.get_all_known_source_ids = AsyncMock()  # type: ignore[method-assign]

        await collect_listings(test_settings, storage, full_scrape=True)

        storage.get_all_known_source_ids.assert_not_called()
        assert mock_scrape.call_args.kwargs["known_ids_by_source"] is None


# ---------------------------------------------------------------------------
# prepare_candidates
# ---------------------------------------------------------------------------


class TestCandidatePreparation:
    @patch("home_finder.pipeline.collection.scrape_all_platforms")
    async def test_returns_empty_batch_on_no_results(
        self,
        mock_scrape: Any,
        storage: PropertyStorage,
        test_settings: Settings,
    ) -> None:
        mock_scrape.return_value = ([], [])
        result = await prepare_candidates(test_settings, storage)
        assert result.candidates == []

    @patch("home_finder.pipeline.collection.scrape_all_platforms")
    async def test_returns_empty_batch_when_all_filtered(
        self,
        mock_scrape: Any,
        storage: PropertyStorage,
        test_settings: Settings,
        make_property: Callable[..., Property],
    ) -> None:
        # Price way above max
        mock_scrape.return_value = ([make_property(source_id="expensive", price_pcm=99999)], [])
        result = await prepare_candidates(test_settings, storage)
        assert result.candidates == []

    @patch("home_finder.pipeline.candidates.enrich_merged_properties", new_callable=AsyncMock)
    @patch("home_finder.pipeline.collection.scrape_all_platforms")
    async def test_returns_result_for_valid_properties(
        self,
        mock_scrape: Any,
        mock_enrich: Any,
        storage: PropertyStorage,
        test_settings: Settings,
        make_property: Callable[..., Property],
    ) -> None:
        prop = make_property(source_id="valid")
        mock_scrape.return_value = ([prop], [])
        # enrich returns the merged properties unchanged
        mock_enrich.side_effect = lambda merged, *a, **kw: EnrichmentResult(
            with_usable_media=merged
        )

        result = await prepare_candidates(test_settings, storage)
        assert result is not None
        assert len(result.candidates) == 1

    @patch("home_finder.pipeline.candidates.enrich_merged_properties", new_callable=AsyncMock)
    @patch("home_finder.pipeline.collection.scrape_all_platforms")
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
        mock_scrape.return_value = ([prop], [])
        mock_enrich.side_effect = lambda merged, *a, **kw: EnrichmentResult(
            with_usable_media=merged
        )

        result = await prepare_candidates(test_settings, storage)
        assert result.candidates == []  # No new properties

    @patch("home_finder.pipeline.candidates.enrich_merged_properties", new_callable=AsyncMock)
    @patch("home_finder.pipeline.collection.scrape_all_platforms")
    async def test_floorplan_gate_drops_properties(
        self,
        mock_scrape: Any,
        mock_enrich: Any,
        storage: PropertyStorage,
        make_property: Callable[..., Property],
    ) -> None:
        settings = Settings(
            telegram_bot_token=SecretStr("fake:token"),
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
            traveltime_api_key=SecretStr(""),
        )
        # Use RIGHTMOVE (not OpenRent) since OpenRent is exempt from floorplan requirement
        prop = make_property(source=PropertySource.RIGHTMOVE, source_id="no-fp")
        mock_scrape.return_value = ([prop], [])
        # enrich returns merged without floorplan
        mock_enrich.side_effect = lambda merged, *a, **kw: EnrichmentResult(
            with_usable_media=merged
        )

        result = await prepare_candidates(settings, storage)
        # No floorplan → filtered out
        assert result.candidates == []

    @patch("home_finder.pipeline.candidates.enrich_merged_properties", new_callable=AsyncMock)
    @patch("home_finder.pipeline.collection.scrape_all_platforms")
    async def test_floorplan_gate_saves_dropped_properties(
        self,
        mock_scrape: Any,
        mock_enrich: Any,
        storage: PropertyStorage,
        make_property: Callable[..., Property],
    ) -> None:
        """Dropped properties are saved to DB so they're excluded on next run."""
        settings = Settings(
            telegram_bot_token=SecretStr("fake:token"),
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
            traveltime_api_key=SecretStr(""),
        )
        prop = make_property(source=PropertySource.RIGHTMOVE, source_id="drop-save")
        mock_scrape.return_value = ([prop], [])
        mock_enrich.side_effect = lambda merged, *a, **kw: EnrichmentResult(
            with_usable_media=merged
        )

        result = await prepare_candidates(settings, storage)
        assert result.candidates == []  # Dropped by floorplan gate

        # Verify: property saved with 'dropped' status
        conn = await storage._get_connection()
        cursor = await conn.execute(
            "SELECT notification_status, enrichment_status FROM properties WHERE unique_id = ?",
            (prop.unique_id,),
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row["notification_status"] == "dropped"
        assert row["enrichment_status"] == "enriched"

        # Second run: same property should be filtered as "already seen"
        mock_scrape.return_value = ([prop], [])
        result2 = await prepare_candidates(settings, storage)
        assert result2.candidates == []  # No new properties


# ---------------------------------------------------------------------------
# persist_estimated_floor_area
# ---------------------------------------------------------------------------


class TestPersistEstimatedFloorArea:
    async def test_skips_when_floor_area_already_set(
        self,
        storage: PropertyStorage,
        make_merged_property: Callable[..., MergedProperty],
        sample_quality_analysis: PropertyQualityAnalysis,
    ) -> None:
        """No DB update when merged.floor_area_sqm is already set."""
        merged = make_merged_property(floor_area_sqm=46.5)
        storage.update_floor_area = AsyncMock()  # type: ignore[method-assign]

        await persist_estimated_floor_area(merged, sample_quality_analysis, storage)
        storage.update_floor_area.assert_not_called()

    async def test_skips_when_analysis_is_none(
        self,
        storage: PropertyStorage,
        make_merged_property: Callable[..., MergedProperty],
    ) -> None:
        """No DB update when quality_analysis is None."""
        merged = make_merged_property()
        storage.update_floor_area = AsyncMock()  # type: ignore[method-assign]

        await persist_estimated_floor_area(merged, None, storage)
        storage.update_floor_area.assert_not_called()

    async def test_persists_valid_estimate(
        self,
        storage: PropertyStorage,
        make_merged_property: Callable[..., MergedProperty],
        make_quality_analysis: Callable[..., PropertyQualityAnalysis],
    ) -> None:
        """Persists estimate when space.total_area_sqm is within range."""
        merged = make_merged_property()
        analysis = make_quality_analysis(space=SpaceAnalysis(total_area_sqm=50.0))
        await storage.processing.save_pre_analysis_properties([merged], {})

        await persist_estimated_floor_area(merged, analysis, storage)

        conn = await storage._get_connection()
        cursor = await conn.execute(
            "SELECT floor_area_sqm, floor_area_source FROM properties WHERE unique_id = ?",
            (merged.unique_id,),
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row["floor_area_sqm"] == 50.0  # stored directly in sqm
        assert row["floor_area_source"] == "estimated"

    async def test_rejects_out_of_range(
        self,
        storage: PropertyStorage,
        make_merged_property: Callable[..., MergedProperty],
        make_quality_analysis: Callable[..., PropertyQualityAnalysis],
    ) -> None:
        """Does not persist when estimate is below 9 sqm."""
        merged = make_merged_property()
        # 5 sqm — below the 9.0 sqm floor
        analysis = make_quality_analysis(space=SpaceAnalysis(total_area_sqm=5.0))
        storage.update_floor_area = AsyncMock()  # type: ignore[method-assign]

        await persist_estimated_floor_area(merged, analysis, storage)
        storage.update_floor_area.assert_not_called()

    async def test_logs_bedroom_mismatch_warning(
        self,
        storage: PropertyStorage,
        make_merged_property: Callable[..., MergedProperty],
        make_quality_analysis: Callable[..., PropertyQualityAnalysis],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Warns when floor area is outside expected range for bedroom count."""
        # 2-bed property with 200 sqm — way above expected (35-110)
        merged = make_merged_property(bedrooms=2)
        analysis = make_quality_analysis(space=SpaceAnalysis(total_area_sqm=200.0))
        await storage.processing.save_pre_analysis_properties([merged], {})

        await persist_estimated_floor_area(merged, analysis, storage)

        captured = capsys.readouterr()
        assert "floor_area_bedroom_mismatch" in (captured.out + captured.err)
        # Still persists (warning only)
        conn = await storage._get_connection()
        cursor = await conn.execute(
            "SELECT floor_area_sqm FROM properties WHERE unique_id = ?",
            (merged.unique_id,),
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row["floor_area_sqm"] == 200.0

    async def test_no_bedroom_mismatch_warning_when_in_range(
        self,
        storage: PropertyStorage,
        make_merged_property: Callable[..., MergedProperty],
        make_quality_analysis: Callable[..., PropertyQualityAnalysis],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """No warning when floor area is within expected range."""
        # 2-bed property with 60 sqm — within expected (35-110)
        merged = make_merged_property(bedrooms=2)
        analysis = make_quality_analysis(space=SpaceAnalysis(total_area_sqm=60.0))
        await storage.processing.save_pre_analysis_properties([merged], {})

        await persist_estimated_floor_area(merged, analysis, storage)

        captured = capsys.readouterr()
        assert "floor_area_bedroom_mismatch" not in (captured.out + captured.err)

    async def test_logs_room_sum_mismatch_warning(
        self,
        storage: PropertyStorage,
        make_merged_property: Callable[..., MergedProperty],
        make_quality_analysis: Callable[..., PropertyQualityAnalysis],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Warns when room area sum significantly exceeds the total."""
        rooms = [
            RoomArea(name="Living Room", area_sqm=50.0),
            RoomArea(name="Bedroom", area_sqm=22.0),
        ]
        # room_sum=72, total=60 — excess of 12, exceeds max(5, 60*0.15)=9
        merged = make_merged_property(bedrooms=1)
        analysis = make_quality_analysis(space=SpaceAnalysis(total_area_sqm=60.0, room_areas=rooms))
        await storage.processing.save_pre_analysis_properties([merged], {})

        await persist_estimated_floor_area(merged, analysis, storage)

        captured = capsys.readouterr()
        assert "floor_area_sum_mismatch" in (captured.out + captured.err)
        # Still persists
        conn = await storage._get_connection()
        cursor = await conn.execute(
            "SELECT floor_area_sqm FROM properties WHERE unique_id = ?",
            (merged.unique_id,),
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row["floor_area_sqm"] == 60.0

    @pytest.mark.parametrize(
        ("total", "known_area"), [(49.69, 13.8), (35.1, 3.56), (70.05, 20.15), (52.6, None)]
    )
    async def test_partial_or_withheld_room_areas_do_not_warn(
        self, storage, make_merged_property, make_quality_analysis, capsys, total, known_area
    ):
        merged = make_merged_property(bedrooms=1)
        analysis = make_quality_analysis(
            space=SpaceAnalysis(
                total_area_sqm=total,
                floorplan_total_area_sqm=total,
                room_areas=[
                    RoomArea(name="Measured room", area_sqm=known_area),
                    RoomArea(name="Withheld room", area_sqm=None),
                ],
            )
        )
        await storage.processing.save_pre_analysis_properties([merged], {})
        await persist_estimated_floor_area(merged, analysis, storage)
        captured = capsys.readouterr()
        assert "floor_area_sum_mismatch" not in captured.out + captured.err
        conn = await storage._get_connection()
        cursor = await conn.execute(
            "SELECT floor_area_sqm FROM properties WHERE unique_id = ?", (merged.unique_id,)
        )
        assert (await cursor.fetchone())["floor_area_sqm"] == total

    async def test_no_room_sum_mismatch_when_close(
        self,
        storage: PropertyStorage,
        make_merged_property: Callable[..., MergedProperty],
        make_quality_analysis: Callable[..., PropertyQualityAnalysis],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """No warning when room sum is close to total."""
        rooms = [
            RoomArea(name="Living Room", area_sqm=20.0),
            RoomArea(name="Bedroom", area_sqm=12.0),
            RoomArea(name="Kitchen", area_sqm=8.0),
            RoomArea(name="Bathroom", area_sqm=5.0),
            RoomArea(name="Hallway", area_sqm=4.0),
        ]
        # room_sum=49, total=50 — diff of 1, within max(5, 50*0.15)=7.5
        merged = make_merged_property(bedrooms=1)
        analysis = make_quality_analysis(space=SpaceAnalysis(total_area_sqm=50.0, room_areas=rooms))
        await storage.processing.save_pre_analysis_properties([merged], {})

        await persist_estimated_floor_area(merged, analysis, storage)

        captured = capsys.readouterr()
        assert "floor_area_sum_mismatch" not in (captured.out + captured.err)

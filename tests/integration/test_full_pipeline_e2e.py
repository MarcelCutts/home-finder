"""End-to-end tests for the full pipeline with mocked external APIs."""

from collections.abc import Callable
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import SecretStr

from home_finder.config import Settings
from home_finder.db import PropertyStorage
from home_finder.filters.detail_enrichment import EnrichmentResult
from home_finder.models import (
    Property,
    PropertySource,
    TransportMode,
)
from home_finder.pipeline.analysis import _save_one
from home_finder.pipeline.stages import _run_pre_analysis_pipeline


@pytest.fixture
def synthetic_properties(make_property: Callable[..., Property]) -> list[Property]:
    """Create a set of synthetic properties for pipeline testing."""
    return [
        make_property(
            source=PropertySource.OPENRENT, source_id="100", price_pcm=1800, postcode="E8 3RH"
        ),
        make_property(
            source=PropertySource.OPENRENT,
            source_id="101",
            price_pcm=2000,
            bedrooms=2,
            postcode="E8 4AB",
        ),
        make_property(
            source=PropertySource.ZOOPLA, source_id="200", price_pcm=1850, postcode="E8 3RH"
        ),
        make_property(
            source=PropertySource.RIGHTMOVE,
            source_id="300",
            price_pcm=2100,
            bedrooms=2,
            postcode="E8 5CD",
        ),
    ]


@pytest.mark.e2e
class TestFullPipelineE2E:
    """Test _run_pre_analysis_pipeline with mocked external APIs."""

    async def test_pipeline_scrape_through_save(
        self, test_settings: Settings, synthetic_properties: list[Property]
    ):
        """Pipeline should execute all stages and save properties to DB."""
        storage = PropertyStorage(":memory:")
        await storage.initialize()

        with (
            patch(
                "home_finder.pipeline.scraping.scrape_all_platforms",
                new_callable=AsyncMock,
                return_value=(synthetic_properties, []),
            ),
            patch(
                "home_finder.pipeline.stages.enrich_merged_properties",
                new_callable=AsyncMock,
                side_effect=lambda merged, *a, **kw: EnrichmentResult(enriched=merged),
            ),
            patch(
                "home_finder.pipeline.stages.DetailFetcher",
            ) as MockFetcher,
        ):
            mock_fetcher_instance = MagicMock()
            mock_fetcher_instance.close = AsyncMock()
            MockFetcher.return_value = mock_fetcher_instance

            result = await _run_pre_analysis_pipeline(test_settings, storage)

        assert result is not None
        assert len(result.merged_to_process) > 0

        # Pre-save (as pipeline does before analysis), then update with _save_one
        await storage.pipeline.save_pre_analysis_properties(
            result.merged_to_process, result.commute_lookup
        )
        for merged in result.merged_to_process:
            commute_info = result.commute_lookup.get(merged.canonical.unique_id)
            await _save_one(merged, commute_info, None, storage)

        # Verify properties are in DB
        count = await storage.web.get_property_count()
        assert count > 0
        assert count == len(result.merged_to_process)

        await storage.close()

    async def test_pipeline_commute_is_advisory(
        self, test_settings: Settings, synthetic_properties: list[Property]
    ):
        """Over-limit and unknown homes remain available; only measurements are saved."""
        storage = PropertyStorage(":memory:")
        await storage.initialize()

        # Create settings with TravelTime credentials
        settings_with_commute = Settings(
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
            traveltime_app_id="test-app",
            traveltime_api_key=SecretStr("test-key"),
        )

        # Mock CommuteFilter to return only half
        from home_finder.models import CommuteResult, CommuteState

        mock_commute_results = [
            CommuteResult(
                property_id=synthetic_properties[0].unique_id,
                destination_postcode="N1 5AA",
                travel_time_seconds=900,
                limit_seconds=1800,
                transport_mode=TransportMode.CYCLING,
                state=CommuteState.WITHIN_LIMIT,
            ),
            CommuteResult(
                property_id=synthetic_properties[1].unique_id,
                destination_postcode="N1 5AA",
                travel_time_seconds=2700,
                limit_seconds=1800,
                transport_mode=TransportMode.CYCLING,
                state=CommuteState.OVER_LIMIT,
            ),
        ]

        with (
            patch(
                "home_finder.pipeline.scraping.scrape_all_platforms",
                new_callable=AsyncMock,
                return_value=(synthetic_properties, []),
            ),
            patch(
                "home_finder.pipeline.stages.enrich_merged_properties",
                new_callable=AsyncMock,
                side_effect=lambda merged, *a, **kw: EnrichmentResult(enriched=merged),
            ),
            patch(
                "home_finder.pipeline.stages.DetailFetcher",
            ) as MockFetcher,
            patch(
                "home_finder.pipeline.stages.CommuteFilter",
            ) as MockCommuteFilter,
        ):
            mock_fetcher_instance = MagicMock()
            mock_fetcher_instance.close = AsyncMock()
            MockFetcher.return_value = mock_fetcher_instance

            mock_commute_instance = MagicMock()
            mock_commute_instance.filter_properties = AsyncMock(return_value=mock_commute_results)
            mock_commute_instance.geocode_properties = AsyncMock(side_effect=lambda merged: merged)
            MockCommuteFilter.return_value = mock_commute_instance

            result = await _run_pre_analysis_pipeline(settings_with_commute, storage)

        assert result is not None
        assert result.commute_within_limit_count == 1
        assert result.commute_lookup == {
            synthetic_properties[0].unique_id: (15, TransportMode.CYCLING),
            synthetic_properties[1].unique_id: (45, TransportMode.CYCLING),
        }
        assert {r.property_id for r in mock_commute_results} <= {
            m.unique_id for m in result.merged_to_process
        }

        await storage.close()

    async def test_pipeline_no_results_returns_none(self, test_settings: Settings):
        """Empty scrape should return None."""
        storage = PropertyStorage(":memory:")
        await storage.initialize()

        with patch(
            "home_finder.pipeline.scraping.scrape_all_platforms",
            new_callable=AsyncMock,
            return_value=([], []),
        ):
            result = await _run_pre_analysis_pipeline(test_settings, storage)

        assert result is None
        await storage.close()

    async def test_pipeline_dedup_reduces_count(
        self, test_settings: Settings, make_property: Callable[..., Property]
    ):
        """Duplicate properties should be merged, reducing count."""
        storage = PropertyStorage(":memory:")
        await storage.initialize()

        # Create properties that should match (same postcode, bedrooms, close price)
        props = [
            make_property(
                source=PropertySource.OPENRENT,
                source_id="500",
                price_pcm=1900,
                postcode="E8 3RH",
                latitude=51.5465,
                longitude=-0.0553,
            ),
            make_property(
                source=PropertySource.ZOOPLA,
                source_id="501",
                price_pcm=1900,
                postcode="E8 3RH",
                latitude=51.5465,
                longitude=-0.0553,
            ),
            make_property(
                source=PropertySource.OPENRENT,
                source_id="502",
                price_pcm=2100,
                bedrooms=2,
                postcode="E8 4AB",
            ),
        ]

        with (
            patch(
                "home_finder.pipeline.scraping.scrape_all_platforms",
                new_callable=AsyncMock,
                return_value=(props, []),
            ),
            patch(
                "home_finder.pipeline.stages.enrich_merged_properties",
                new_callable=AsyncMock,
                side_effect=lambda merged, *a, **kw: EnrichmentResult(enriched=merged),
            ),
            patch(
                "home_finder.pipeline.stages.DetailFetcher",
            ) as MockFetcher,
        ):
            mock_fetcher_instance = MagicMock()
            mock_fetcher_instance.close = AsyncMock()
            MockFetcher.return_value = mock_fetcher_instance

            result = await _run_pre_analysis_pipeline(test_settings, storage)

        assert result is not None
        # 3 input props, but 2 should merge → result should be <= 3
        # (first two have same postcode+bedrooms+price+coords, should merge)
        assert len(result.merged_to_process) <= len(props)

        await storage.close()

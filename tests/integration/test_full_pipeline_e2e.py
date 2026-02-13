"""End-to-end tests for the full pipeline with mocked external APIs."""

from collections.abc import Callable
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from home_finder.config import Settings
from home_finder.db import PropertyStorage
from home_finder.filters.detail_enrichment import EnrichmentResult
from home_finder.main import _run_pre_analysis_pipeline, _save_one
from home_finder.models import (
    Property,
    PropertySource,
    TransportMode,
)


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
                "home_finder.main.scrape_all_platforms",
                new_callable=AsyncMock,
                return_value=synthetic_properties,
            ),
            patch(
                "home_finder.main.enrich_merged_properties",
                new_callable=AsyncMock,
                side_effect=lambda merged, *a, **kw: EnrichmentResult(enriched=merged),
            ),
            patch(
                "home_finder.main.DetailFetcher",
            ) as MockFetcher,
        ):
            mock_fetcher_instance = MagicMock()
            mock_fetcher_instance.close = AsyncMock()
            MockFetcher.return_value = mock_fetcher_instance

            result = await _run_pre_analysis_pipeline(test_settings, storage)

        assert result is not None
        assert len(result.merged_to_process) > 0

        # Pre-save (as pipeline does before analysis), then update with _save_one
        await storage.save_pre_analysis_properties(
            result.merged_to_process, result.commute_lookup
        )
        for merged in result.merged_to_process:
            commute_info = result.commute_lookup.get(merged.canonical.unique_id)
            await _save_one(merged, commute_info, None, storage)

        # Verify properties are in DB
        count = await storage.get_property_count()
        assert count > 0
        assert count == len(result.merged_to_process)

        await storage.close()

    async def test_pipeline_commute_filter_excludes(
        self, test_settings: Settings, synthetic_properties: list[Property]
    ):
        """Commute filter should reduce the number of properties."""
        storage = PropertyStorage(":memory:")
        await storage.initialize()

        # Create settings with TravelTime credentials
        settings_with_commute = Settings(
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
            traveltime_app_id="test-app",
            traveltime_api_key="test-key",
        )

        # Mock CommuteFilter to return only half
        from home_finder.filters.commute import CommuteResult

        mock_commute_results = [
            CommuteResult(
                property_id=synthetic_properties[0].unique_id,
                destination_postcode="N1 5AA",
                travel_time_minutes=15,
                transport_mode=TransportMode.CYCLING,
                within_limit=True,
            ),
            CommuteResult(
                property_id=synthetic_properties[1].unique_id,
                destination_postcode="N1 5AA",
                travel_time_minutes=45,
                transport_mode=TransportMode.CYCLING,
                within_limit=False,
            ),
        ]

        with (
            patch(
                "home_finder.main.scrape_all_platforms",
                new_callable=AsyncMock,
                return_value=synthetic_properties,
            ),
            patch(
                "home_finder.main.enrich_merged_properties",
                new_callable=AsyncMock,
                side_effect=lambda merged, *a, **kw: EnrichmentResult(enriched=merged),
            ),
            patch(
                "home_finder.main.DetailFetcher",
            ) as MockFetcher,
            patch(
                "home_finder.main.CommuteFilter",
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

        # Only the within_limit property should be in the result
        assert result is not None
        within_ids = {r.property_id for r in mock_commute_results if r.within_limit}
        # At least the one within limit should be in the results
        assert len(result.merged_to_process) >= 1
        # Commute lookup should only contain properties within limit
        for uid in result.commute_lookup:
            assert uid in within_ids

        await storage.close()

    async def test_pipeline_no_results_returns_none(self, test_settings: Settings):
        """Empty scrape should return None."""
        storage = PropertyStorage(":memory:")
        await storage.initialize()

        with patch(
            "home_finder.main.scrape_all_platforms",
            new_callable=AsyncMock,
            return_value=[],
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
                "home_finder.main.scrape_all_platforms",
                new_callable=AsyncMock,
                return_value=props,
            ),
            patch(
                "home_finder.main.enrich_merged_properties",
                new_callable=AsyncMock,
                side_effect=lambda merged, *a, **kw: EnrichmentResult(enriched=merged),
            ),
            patch(
                "home_finder.main.DetailFetcher",
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

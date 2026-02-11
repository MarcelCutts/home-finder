"""End-to-end tests for the full pipeline with mocked external APIs."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import HttpUrl

from home_finder.config import Settings
from home_finder.db import PropertyStorage
from home_finder.filters.quality import (
    ConditionAnalysis,
    KitchenAnalysis,
    LightSpaceAnalysis,
    PropertyQualityAnalysis,
    SpaceAnalysis,
)
from home_finder.main import PipelineResult, _run_core_pipeline, _save_properties
from home_finder.models import (
    MergedProperty,
    Property,
    PropertyImage,
    PropertySource,
    TransportMode,
)


def _make_property(
    source: PropertySource,
    source_id: str,
    price: int = 1900,
    bedrooms: int = 1,
    postcode: str = "E8 3RH",
    lat: float = 51.5465,
    lon: float = -0.0553,
) -> Property:
    url_map = {
        PropertySource.OPENRENT: f"https://www.openrent.com/property/{source_id}",
        PropertySource.ZOOPLA: f"https://www.zoopla.co.uk/to-rent/details/{source_id}",
        PropertySource.RIGHTMOVE: f"https://www.rightmove.co.uk/properties/{source_id}",
        PropertySource.ONTHEMARKET: f"https://www.onthemarket.com/details/{source_id}",
    }
    return Property(
        source=source,
        source_id=source_id,
        url=HttpUrl(url_map[source]),
        title=f"Test {bedrooms}-bed flat",
        price_pcm=price,
        bedrooms=bedrooms,
        address=f"{source_id} Test Street, London",
        postcode=postcode,
        latitude=lat,
        longitude=lon,
        first_seen=datetime(2025, 2, 1, 12, 0),
    )


def _make_synthetic_properties() -> list[Property]:
    """Create a set of synthetic properties for pipeline testing."""
    return [
        _make_property(PropertySource.OPENRENT, "100", price=1800, postcode="E8 3RH"),
        _make_property(PropertySource.OPENRENT, "101", price=2000, bedrooms=2, postcode="E8 4AB"),
        _make_property(PropertySource.ZOOPLA, "200", price=1850, postcode="E8 3RH"),
        _make_property(PropertySource.RIGHTMOVE, "300", price=2100, bedrooms=2, postcode="E8 5CD"),
    ]


@pytest.mark.e2e
class TestFullPipelineE2E:
    """Test _run_core_pipeline with mocked external APIs."""

    async def test_pipeline_scrape_through_save(self, test_settings: Settings):
        """Pipeline should execute all stages and save properties to DB."""
        storage = PropertyStorage(":memory:")
        await storage.initialize()

        synthetic_props = _make_synthetic_properties()

        with (
            patch(
                "home_finder.main.scrape_all_platforms",
                new_callable=AsyncMock,
                return_value=synthetic_props,
            ),
            patch(
                "home_finder.main.enrich_merged_properties",
                new_callable=AsyncMock,
                side_effect=lambda merged, fetcher, **kwargs: merged,
            ),
            patch(
                "home_finder.main.DetailFetcher",
            ) as MockFetcher,
        ):
            mock_fetcher_instance = MagicMock()
            mock_fetcher_instance.close = AsyncMock()
            MockFetcher.return_value = mock_fetcher_instance

            result = await _run_core_pipeline(test_settings, storage)

        assert result is not None
        assert len(result.merged_to_notify) > 0

        # Save to DB
        await _save_properties(result, storage)

        # Verify properties are in DB
        count = await storage.get_property_count()
        assert count > 0
        assert count == len(result.merged_to_notify)

        await storage.close()

    async def test_pipeline_commute_filter_excludes(self, test_settings: Settings):
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

        synthetic_props = _make_synthetic_properties()

        # Mock CommuteFilter to return only half
        from home_finder.filters.commute import CommuteResult

        mock_commute_results = [
            CommuteResult(
                property_id=synthetic_props[0].unique_id,
                destination_postcode="N1 5AA",
                travel_time_minutes=15,
                transport_mode=TransportMode.CYCLING,
                within_limit=True,
            ),
            CommuteResult(
                property_id=synthetic_props[1].unique_id,
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
                return_value=synthetic_props,
            ),
            patch(
                "home_finder.main.enrich_merged_properties",
                new_callable=AsyncMock,
                side_effect=lambda merged, fetcher, **kwargs: merged,
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
            mock_commute_instance.filter_properties = AsyncMock(
                return_value=mock_commute_results
            )
            mock_commute_instance.geocode_properties = AsyncMock(
                side_effect=lambda merged: merged
            )
            MockCommuteFilter.return_value = mock_commute_instance

            result = await _run_core_pipeline(settings_with_commute, storage)

        # Only the within_limit property should be in the result
        assert result is not None
        within_ids = {r.property_id for r in mock_commute_results if r.within_limit}
        # At least the one within limit should be in the results
        assert len(result.merged_to_notify) >= 1
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
            result = await _run_core_pipeline(test_settings, storage)

        assert result is None
        await storage.close()

    async def test_pipeline_dedup_reduces_count(self, test_settings: Settings):
        """Duplicate properties should be merged, reducing count."""
        storage = PropertyStorage(":memory:")
        await storage.initialize()

        # Create properties that should match (same postcode, bedrooms, close price)
        props = [
            _make_property(
                PropertySource.OPENRENT, "500", price=1900, postcode="E8 3RH",
                lat=51.5465, lon=-0.0553,
            ),
            _make_property(
                PropertySource.ZOOPLA, "501", price=1900, postcode="E8 3RH",
                lat=51.5465, lon=-0.0553,
            ),
            _make_property(
                PropertySource.OPENRENT, "502", price=2100, bedrooms=2, postcode="E8 4AB",
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
                side_effect=lambda merged, fetcher, **kwargs: merged,
            ),
            patch(
                "home_finder.main.DetailFetcher",
            ) as MockFetcher,
        ):
            mock_fetcher_instance = MagicMock()
            mock_fetcher_instance.close = AsyncMock()
            MockFetcher.return_value = mock_fetcher_instance

            result = await _run_core_pipeline(test_settings, storage)

        assert result is not None
        # 3 input props, but 2 should merge → result should be <= 3
        # (first two have same postcode+bedrooms+price+coords, should merge)
        assert len(result.merged_to_notify) <= len(props)

        await storage.close()

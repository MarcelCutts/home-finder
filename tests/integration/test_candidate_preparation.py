"""Candidate preparation and persistence with mocked acquisition boundaries.

Boundary: ``prepare_candidates`` from collection through selection, enrichment
admission, commute assessment and consolidation, with real storage. Collection,
detail acquisition and TravelTime are replaced.
"""

from collections.abc import Callable
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import SecretStr

from home_finder.config import Settings
from home_finder.db import PropertyStorage
from home_finder.enrichment.details import EnrichmentResult
from home_finder.models import (
    CommuteResult,
    CommuteState,
    Property,
    PropertySource,
    TransportMode,
)
from home_finder.pipeline.assessment import complete_initial_assessment
from home_finder.pipeline.candidates import prepare_candidates


@pytest.fixture
def preparation_settings(tmp_path: Path) -> Settings:
    """No analysis or commute provider; the image cache lives in tmp_path."""
    return Settings(
        telegram_bot_token=SecretStr("fake:test-token"),
        telegram_chat_id=0,
        database_path=str(tmp_path / "home_finder.db"),
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
class TestCandidatePreparation:
    """prepare_candidates with collection, detail acquisition and TravelTime replaced."""

    async def test_prepared_candidates_can_be_persisted_without_analysis(
        self,
        storage: PropertyStorage,
        preparation_settings: Settings,
        synthetic_properties: list[Property],
    ):
        """Prepare candidates, then persist them through the assessment-completion contract."""
        with (
            patch(
                "home_finder.pipeline.collection.scrape_all_platforms",
                new_callable=AsyncMock,
                return_value=(synthetic_properties, []),
            ),
            patch(
                "home_finder.pipeline.candidates.enrich_merged_properties",
                new_callable=AsyncMock,
                side_effect=lambda merged, *a, **kw: EnrichmentResult(with_usable_media=merged),
            ),
            patch(
                "home_finder.pipeline.candidates.DetailFetcher",
            ) as MockFetcher,
        ):
            mock_fetcher_instance = MagicMock()
            mock_fetcher_instance.close = AsyncMock()
            MockFetcher.return_value = mock_fetcher_instance

            result = await prepare_candidates(preparation_settings, storage)

        # Every observed listing is represented by exactly one candidate.
        represented_urls = [
            str(url) for candidate in result.candidates for url in candidate.source_urls.values()
        ]
        assert sorted(represented_urls) == sorted(str(prop.url) for prop in synthetic_properties)

        # Pre-save (as pipeline does before analysis), then update with complete_initial_assessment
        await storage.processing.save_pre_analysis_properties(
            result.candidates, result.commute_lookup
        )
        for merged in result.candidates:
            await complete_initial_assessment(merged, None, storage)

        for candidate in result.candidates:
            assert await storage.get_property(candidate.unique_id) is not None
        assert await storage.web.get_property_count() == len(result.candidates)

    async def test_commute_assessment_preserves_over_limit_candidates(
        self,
        storage: PropertyStorage,
        preparation_settings: Settings,
        synthetic_properties: list[Property],
    ):
        """Over-limit and unknown homes remain available; only measurements are saved."""
        settings_with_commute = preparation_settings.model_copy(
            update={
                "traveltime_app_id": "test-app",
                "traveltime_api_key": SecretStr("test-key"),
            }
        )

        # Two homes are measured (one within, one over the limit); the rest are unknown.

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
                "home_finder.pipeline.collection.scrape_all_platforms",
                new_callable=AsyncMock,
                return_value=(synthetic_properties, []),
            ),
            patch(
                "home_finder.pipeline.candidates.enrich_merged_properties",
                new_callable=AsyncMock,
                side_effect=lambda merged, *a, **kw: EnrichmentResult(with_usable_media=merged),
            ),
            patch(
                "home_finder.pipeline.candidates.DetailFetcher",
            ) as MockFetcher,
            patch(
                "home_finder.pipeline.candidates.CommuteAssessor",
            ) as MockCommuteFilter,
        ):
            mock_fetcher_instance = MagicMock()
            mock_fetcher_instance.close = AsyncMock()
            MockFetcher.return_value = mock_fetcher_instance

            mock_commute_instance = MagicMock()
            mock_commute_instance.assess_commutes = AsyncMock(return_value=mock_commute_results)
            mock_commute_instance.geocode_properties = AsyncMock(side_effect=lambda merged: merged)
            MockCommuteFilter.return_value = mock_commute_instance

            result = await prepare_candidates(settings_with_commute, storage)

        assert result.progress.commute_within_limit_count == 1
        assert result.commute_lookup == {
            synthetic_properties[0].unique_id: (15, TransportMode.CYCLING),
            synthetic_properties[1].unique_id: (45, TransportMode.CYCLING),
        }
        assert {r.property_id for r in mock_commute_results} <= {
            m.unique_id for m in result.candidates
        }

    async def test_no_observations_returns_empty_batch(
        self, storage: PropertyStorage, preparation_settings: Settings
    ):
        with patch(
            "home_finder.pipeline.collection.scrape_all_platforms",
            new_callable=AsyncMock,
            return_value=([], []),
        ):
            result = await prepare_candidates(preparation_settings, storage)

        assert result.candidates == []
        assert result.commute_lookup == {}

    async def test_matching_listings_share_a_candidate_and_keep_the_distinct_home(
        self,
        storage: PropertyStorage,
        preparation_settings: Settings,
        make_property: Callable[..., Property],
    ):
        # The first two match (same postcode, coordinates, size and price); the third differs.
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
                "home_finder.pipeline.collection.scrape_all_platforms",
                new_callable=AsyncMock,
                return_value=(props, []),
            ),
            patch(
                "home_finder.pipeline.candidates.enrich_merged_properties",
                new_callable=AsyncMock,
                side_effect=lambda merged, *a, **kw: EnrichmentResult(with_usable_media=merged),
            ),
            patch(
                "home_finder.pipeline.candidates.DetailFetcher",
            ) as MockFetcher,
        ):
            mock_fetcher_instance = MagicMock()
            mock_fetcher_instance.close = AsyncMock()
            MockFetcher.return_value = mock_fetcher_instance

            result = await prepare_candidates(preparation_settings, storage)

        assert len(result.candidates) == 2
        assert {frozenset(candidate.sources) for candidate in result.candidates} == {
            frozenset({PropertySource.OPENRENT, PropertySource.ZOOPLA}),
            frozenset({PropertySource.OPENRENT}),
        }
        represented_urls = {
            str(url) for candidate in result.candidates for url in candidate.source_urls.values()
        }
        assert represented_urls == {str(prop.url) for prop in props}

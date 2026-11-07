"""Which collected listings become candidates: criteria, seen listings and media admission.

Collection and enrichment are replaced; storage, selection and consolidation are real.
"""

from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import SecretStr

from home_finder.config import Settings
from home_finder.db import PropertyStorage
from home_finder.enrichment.details import EnrichmentResult
from home_finder.models import (
    MergedProperty,
    Property,
    PropertySource,
)
from home_finder.pipeline.candidates import prepare_candidates


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
    async def test_already_seen_listing_is_not_enriched_again(
        self,
        mock_scrape: Any,
        mock_enrich: Any,
        storage: PropertyStorage,
        test_settings: Settings,
        make_property: Callable[..., Property],
    ) -> None:
        prop = make_property(source_id="seen")
        await storage.save_merged_property(
            MergedProperty(
                canonical=prop,
                sources=(prop.source,),
                source_urls={prop.source: prop.url},
                min_price=prop.price_pcm,
                max_price=prop.price_pcm,
            )
        )
        mock_scrape.return_value = ([prop], [])
        mock_enrich.side_effect = lambda merged, *a, **kw: EnrichmentResult(
            with_usable_media=merged
        )

        result = await prepare_candidates(test_settings, storage)

        # Consolidation would also absorb the listing into its stored home, so
        # the empty batch alone does not show that the seen filter ran.
        assert result.candidates == []
        assert result.progress.new_listing_count == 0
        mock_enrich.assert_not_awaited()

    @pytest.mark.parametrize("source", [PropertySource.RIGHTMOVE, PropertySource.ZOOPLA])
    @patch("home_finder.pipeline.candidates.enrich_merged_properties", new_callable=AsyncMock)
    @patch("home_finder.pipeline.collection.scrape_all_platforms")
    async def test_media_admission_drops_and_saves_a_home_without_floorplan(
        self,
        mock_scrape: Any,
        mock_enrich: Any,
        storage: PropertyStorage,
        test_settings: Settings,
        make_property: Callable[..., Property],
        source: PropertySource,
    ) -> None:
        """The dropped home is saved so the next run skips it as already seen."""
        settings = test_settings.model_copy(update={"require_floorplan": True})
        # OpenRent-only homes are exempt from the floorplan requirement.
        prop = make_property(source=source, source_id="no-fp")
        mock_scrape.return_value = ([prop], [])
        mock_enrich.side_effect = lambda merged, *a, **kw: EnrichmentResult(
            with_usable_media=merged
        )

        result = await prepare_candidates(settings, storage)
        assert result.candidates == []
        assert result.progress.media_admitted_count == 0
        conn = await storage._get_connection()
        cursor = await conn.execute(
            "SELECT notification_status, enrichment_status FROM properties WHERE unique_id = ?",
            (prop.unique_id,),
        )
        row = await cursor.fetchone()
        assert row is not None
        assert (row["notification_status"], row["enrichment_status"]) == ("dropped", "enriched")

        second_run = await prepare_candidates(settings, storage)
        assert second_run.candidates == []
        assert second_run.progress.new_listing_count == 0
        mock_enrich.assert_awaited_once()

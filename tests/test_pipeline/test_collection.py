"""Collection across platforms: scraper coordination, area handling and known IDs."""

from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import SecretStr

from home_finder.config import Settings
from home_finder.db import PropertyStorage
from home_finder.models import (
    Property,
    PropertySource,
)
from home_finder.pipeline.collection import collect_listings, scrape_all_platforms
from home_finder.scrapers.base import ScrapeResult


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
    """A scraper double with the BaseScraper attributes collection reads."""
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


class TestScrapeAllPlatforms:
    @pytest.mark.parametrize("search_areas", [[], None])
    async def test_no_search_areas_collect_nothing(self, search_areas) -> None:
        result, metrics = await scrape_all_platforms(
            min_price=1500,
            max_price=2500,
            min_bedrooms=1,
            max_bedrooms=2,
            search_areas=search_areas,
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
        """Each scraper receives its own source-specific known IDs.

        collect_listings no longer supplies known IDs (see
        test_collection_never_stops_at_known_ids); this covers the parameter itself.
        """
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


@pytest.mark.parametrize("full_scrape", [False, True])
@patch("home_finder.pipeline.collection.scrape_all_platforms", new_callable=AsyncMock)
async def test_collection_never_stops_at_known_ids(
    mock_scrape: AsyncMock, storage: PropertyStorage, test_settings: Settings, full_scrape: bool
) -> None:
    """A known prefix does not prove earlier collection finished, so every run
    paginates normally. ``full_scrape`` is a compatibility flag with no effect."""
    mock_scrape.return_value = ([], [])
    storage.get_all_known_source_ids = AsyncMock()  # type: ignore[method-assign]

    await collect_listings(test_settings, storage, full_scrape=full_scrape)

    storage.get_all_known_source_ids.assert_not_called()
    assert mock_scrape.call_args.kwargs["known_ids_by_source"] is None

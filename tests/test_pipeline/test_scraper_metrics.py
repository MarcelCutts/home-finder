"""Tests for ScraperMetrics collection during scrape_all_platforms()."""

from dataclasses import asdict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from home_finder.models import Property, PropertySource
from home_finder.pipeline.scraping import ScraperMetrics, scrape_all_platforms
from home_finder.scrapers.base import ScrapeResult


def _make_scrape_result(
    properties: list[Property] | None = None,
    pages_fetched: int = 1,
    pages_failed: int = 0,
    parse_errors: int = 0,
) -> ScrapeResult:
    return ScrapeResult(
        properties=properties or [],
        pages_fetched=pages_fetched,
        pages_failed=pages_failed,
        parse_errors=parse_errors,
    )


class TestScraperMetrics:
    def test_defaults(self) -> None:
        m = ScraperMetrics()
        assert m.scraper_name == ""
        assert m.duration_seconds == 0.0
        assert m.areas_attempted == 0
        assert m.properties_found == 0
        assert m.is_healthy is True
        assert m.error_message is None

    def test_asdict_round_trip(self) -> None:
        m = ScraperMetrics(
            scraper_name="openrent",
            started_at="2026-02-23T10:00:00+00:00",
            areas_attempted=3,
            properties_found=10,
        )
        d = asdict(m)
        assert d["scraper_name"] == "openrent"
        assert d["areas_attempted"] == 3
        assert d["properties_found"] == 10


class TestScraperMetricsCollection:
    """Test that scrape_all_platforms() collects per-scraper metrics."""

    @pytest.fixture
    def _patch_scrapers(self):
        """Patch all scraper classes so we control scrape() return values."""
        mock_scrapers = {}
        for name in ["OpenRentScraper", "RightmoveScraper", "ZooplaScraper", "OnTheMarketScraper"]:
            mock_cls = MagicMock()
            mock_instance = MagicMock()
            mock_instance.scrape = AsyncMock(return_value=_make_scrape_result())
            mock_instance.close = AsyncMock()
            mock_instance.area_delay = AsyncMock()
            mock_instance.should_skip_remaining_areas = False
            mock_instance.max_areas_per_run = None
            mock_cls.return_value = mock_instance
            mock_scrapers[name] = (mock_cls, mock_instance)

        # Set source values
        for name, source_val in [
            ("OpenRentScraper", PropertySource.OPENRENT),
            ("RightmoveScraper", PropertySource.RIGHTMOVE),
            ("ZooplaScraper", PropertySource.ZOOPLA),
            ("OnTheMarketScraper", PropertySource.ONTHEMARKET),
        ]:
            mock_scrapers[name][1].source = source_val

        _mod = "home_finder.pipeline.scraping"
        with (
            patch(f"{_mod}.OpenRentScraper", mock_scrapers["OpenRentScraper"][0]),
            patch(f"{_mod}.RightmoveScraper", mock_scrapers["RightmoveScraper"][0]),
            patch(f"{_mod}.ZooplaScraper", mock_scrapers["ZooplaScraper"][0]),
            patch(f"{_mod}.OnTheMarketScraper", mock_scrapers["OnTheMarketScraper"][0]),
        ):
            yield mock_scrapers

    async def test_returns_metrics_per_scraper(self, _patch_scrapers) -> None:
        _props, metrics = await scrape_all_platforms(
            min_price=1500,
            max_price=2500,
            min_bedrooms=1,
            max_bedrooms=2,
            search_areas=["e8"],
        )
        assert len(metrics) == 4
        names = {m.scraper_name for m in metrics}
        assert names == {"openrent", "rightmove", "zoopla", "onthemarket"}

    async def test_metrics_have_timing(self, _patch_scrapers) -> None:
        _props, metrics = await scrape_all_platforms(
            min_price=1500,
            max_price=2500,
            min_bedrooms=1,
            max_bedrooms=2,
            search_areas=["e8"],
        )
        for m in metrics:
            assert m.started_at != ""
            assert m.completed_at is not None
            assert m.duration_seconds >= 0

    async def test_metrics_count_areas(self, _patch_scrapers) -> None:
        _props, metrics = await scrape_all_platforms(
            min_price=1500,
            max_price=2500,
            min_bedrooms=1,
            max_bedrooms=2,
            search_areas=["e8", "n1", "e5"],
        )
        for m in metrics:
            assert m.areas_attempted == 3
            assert m.areas_completed == 3

    async def test_empty_areas_returns_empty_metrics(self) -> None:
        props, metrics = await scrape_all_platforms(
            min_price=1500,
            max_price=2500,
            min_bedrooms=1,
            max_bedrooms=2,
            search_areas=[],
        )
        assert props == []
        assert metrics == []

    async def test_healthy_flag(self, _patch_scrapers) -> None:
        # Default: pages_fetched=1, parse_errors=0 → healthy
        _props, metrics = await scrape_all_platforms(
            min_price=1500,
            max_price=2500,
            min_bedrooms=1,
            max_bedrooms=2,
            search_areas=["e8"],
        )
        for m in metrics:
            assert m.is_healthy is True

    async def test_only_scrapers_filter(self, _patch_scrapers) -> None:
        _props, metrics = await scrape_all_platforms(
            min_price=1500,
            max_price=2500,
            min_bedrooms=1,
            max_bedrooms=2,
            search_areas=["e8"],
            only_scrapers={"openrent", "rightmove"},
        )
        assert len(metrics) == 2
        names = {m.scraper_name for m in metrics}
        assert names == {"openrent", "rightmove"}

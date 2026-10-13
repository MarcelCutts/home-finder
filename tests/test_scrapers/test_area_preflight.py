"""A misspelled area must fail before collection or location filtering."""

from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from home_finder.config import Settings
from home_finder.data.location_mappings import (
    AREA_MAPPINGS,
    normalize_area,
    resolve_area_outcodes,
)
from home_finder.filters.location import LocationFilter
from home_finder.pipeline.scraping import scrape_all_platforms


@pytest.mark.parametrize("areas", ["hakney", "e8,hakney", "E8 3RH", "north wherever"])
def test_settings_reject_unknown_or_non_outcode_area(areas):
    with pytest.raises(ValidationError, match="Unknown search area"):
        Settings(_env_file=None, search_areas=areas)


@pytest.mark.parametrize("strict", [True, False])
def test_filter_cannot_silently_ignore_unknown_area(strict):
    with pytest.raises(ValueError, match="hakney"):
        LocationFilter(["e8", "hakney"], strict=strict)


def test_platform_aliases_and_filter_coverage_share_preflight():
    for area in AREA_MAPPINGS:
        canonical = resolve_area_outcodes([area.name])
        assert canonical
        for alias in area.aliases:
            assert normalize_area(alias) == area.name
            assert resolve_area_outcodes([alias]) == canonical
            assert LocationFilter([alias]).valid_outcodes == canonical
    assert resolve_area_outcodes([" EC1A ", "e8"]) == {"EC1A", "E8"}
    assert Settings(_env_file=None, search_areas="").get_search_areas() == []


async def test_direct_collection_preflights_before_creating_scrapers(monkeypatch):
    constructors = []
    for name in ("OpenRentScraper", "ZooplaScraper", "RightmoveScraper", "OnTheMarketScraper"):
        constructor = Mock(side_effect=AssertionError("scraper constructed before preflight"))
        monkeypatch.setattr(f"home_finder.pipeline.scraping.{name}", constructor)
        constructors.append(constructor)
    with pytest.raises(ValueError, match="hakney"):
        await scrape_all_platforms(
            min_price=1000,
            max_price=2500,
            min_bedrooms=0,
            max_bedrooms=2,
            search_areas=["e8", "hakney"],
        )
    for constructor in constructors:
        constructor.assert_not_called()

"""Scrape-only executes collection and prints observations without persisting homes."""

from unittest.mock import AsyncMock, Mock

from home_finder.config import Settings
from home_finder.pipeline import collection


async def test_scrape_only_forwards_selection_and_reports_observations(
    tmp_path, monkeypatch, make_property
):
    database = tmp_path / "unused.db"
    settings = Settings(
        database_path=str(database),
        search_areas="e8,n16",
        min_price=1700,
        max_price=2300,
        min_bedrooms=1,
        max_bedrooms=2,
        min_bathrooms=1,
        include_let_agreed=False,
        zoopla_max_areas_per_run=1,
        proxy_url="http://fixture-proxy.example:8080",
    )
    home = make_property()
    scrape = AsyncMock(return_value=([home], []))
    logger = Mock()
    monkeypatch.setattr(collection, "scrape_all_platforms", scrape)
    monkeypatch.setattr(collection, "logger", logger)

    await collection.run_scrape_only(settings, max_per_scraper=2, only_scrapers={"openrent"})

    scrape.assert_awaited_once_with(
        min_price=1700,
        max_price=2300,
        min_bedrooms=1,
        max_bedrooms=2,
        search_areas=["e8", "n16"],
        furnish_types=settings.get_furnish_types(),
        min_bathrooms=1,
        include_let_agreed=False,
        max_per_scraper=2,
        proxy_url="http://fixture-proxy.example:8080",
        only_scrapers={"openrent"},
        zoopla_max_areas=1,
    )
    logger.info.assert_any_call("scrape_only_complete", count=1)
    logger.info.assert_any_call(
        "scraped_property",
        source=home.source.value,
        title=home.title,
        price_pcm=home.price_pcm,
        bedrooms=home.bedrooms,
        address=home.address,
        postcode=home.postcode,
        url=str(home.url),
    )
    assert not database.exists()

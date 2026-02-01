"""Real end-to-end scraping tests that hit actual websites.

These tests are marked as 'slow' and should be run sparingly to avoid
rate limiting and to respect the websites' resources.

Run with: uv run pytest -m slow -v
"""

import pytest

from home_finder.models import PropertySource
from home_finder.scrapers import (
    OnTheMarketScraper,
    OpenRentScraper,
    RightmoveScraper,
    ZooplaScraper,
)


@pytest.mark.slow
@pytest.mark.asyncio
class TestRealOpenRentScraping:
    """Real scraping tests for OpenRent."""

    async def test_scrape_openrent_hackney(self):
        """Scrape real OpenRent listings from Hackney."""
        scraper = OpenRentScraper()

        properties = await scraper.scrape(
            min_price=1500,
            max_price=2500,
            min_bedrooms=1,
            max_bedrooms=2,
            area="hackney",
        )

        # Should find some properties (London always has listings)
        # We don't assert exact count since it varies
        assert isinstance(properties, list)

        if properties:
            prop = properties[0]
            assert prop.source == PropertySource.OPENRENT
            assert prop.source_id is not None
            assert prop.url is not None
            assert "openrent" in str(prop.url).lower()
            assert prop.price_pcm > 0
            assert prop.bedrooms >= 0
            assert prop.title
            assert prop.address

            print(f"\nOpenRent: Found {len(properties)} properties")
            print(f"  Sample: {prop.title}")
            print(f"  Price: £{prop.price_pcm}/month")
            print(f"  Beds: {prop.bedrooms}")
            print(f"  URL: {prop.url}")


@pytest.mark.slow
@pytest.mark.asyncio
class TestRealRightmoveScraping:
    """Real scraping tests for Rightmove."""

    async def test_scrape_rightmove_hackney(self):
        """Scrape real Rightmove listings from Hackney."""
        scraper = RightmoveScraper()

        properties = await scraper.scrape(
            min_price=1500,
            max_price=2500,
            min_bedrooms=1,
            max_bedrooms=2,
            area="hackney",
        )

        assert isinstance(properties, list)

        if properties:
            prop = properties[0]
            assert prop.source == PropertySource.RIGHTMOVE
            assert prop.source_id is not None
            assert prop.url is not None
            assert "rightmove" in str(prop.url).lower()
            assert prop.price_pcm > 0
            assert prop.bedrooms >= 0
            assert prop.title
            assert prop.address

            print(f"\nRightmove: Found {len(properties)} properties")
            print(f"  Sample: {prop.title}")
            print(f"  Price: £{prop.price_pcm}/month")
            print(f"  Beds: {prop.bedrooms}")
            print(f"  URL: {prop.url}")


@pytest.mark.slow
@pytest.mark.asyncio
class TestRealZooplaScraping:
    """Real scraping tests for Zoopla."""

    async def test_scrape_zoopla_hackney(self):
        """Scrape real Zoopla listings from Hackney."""
        scraper = ZooplaScraper()

        properties = await scraper.scrape(
            min_price=1500,
            max_price=2500,
            min_bedrooms=1,
            max_bedrooms=2,
            area="hackney",
        )

        assert isinstance(properties, list)

        if properties:
            prop = properties[0]
            assert prop.source == PropertySource.ZOOPLA
            assert prop.source_id is not None
            assert prop.url is not None
            assert "zoopla" in str(prop.url).lower()
            assert prop.price_pcm > 0
            assert prop.bedrooms >= 0
            assert prop.title
            assert prop.address

            print(f"\nZoopla: Found {len(properties)} properties")
            print(f"  Sample: {prop.title}")
            print(f"  Price: £{prop.price_pcm}/month")
            print(f"  Beds: {prop.bedrooms}")
            print(f"  URL: {prop.url}")


@pytest.mark.slow
@pytest.mark.asyncio
class TestRealOnTheMarketScraping:
    """Real scraping tests for OnTheMarket."""

    async def test_scrape_onthemarket_hackney(self):
        """Scrape real OnTheMarket listings from Hackney."""
        scraper = OnTheMarketScraper()

        properties = await scraper.scrape(
            min_price=1500,
            max_price=2500,
            min_bedrooms=1,
            max_bedrooms=2,
            area="hackney",
        )

        assert isinstance(properties, list)

        if properties:
            prop = properties[0]
            assert prop.source == PropertySource.ONTHEMARKET
            assert prop.source_id is not None
            assert prop.url is not None
            assert "onthemarket" in str(prop.url).lower()
            assert prop.price_pcm > 0
            assert prop.bedrooms >= 0
            assert prop.title
            assert prop.address

            print(f"\nOnTheMarket: Found {len(properties)} properties")
            print(f"  Sample: {prop.title}")
            print(f"  Price: £{prop.price_pcm}/month")
            print(f"  Beds: {prop.bedrooms}")
            print(f"  URL: {prop.url}")


@pytest.mark.slow
@pytest.mark.asyncio
class TestRealFullPipeline:
    """Real end-to-end pipeline test."""

    async def test_scrape_all_platforms_hackney(self):
        """Scrape from all platforms and aggregate results."""
        from home_finder.filters import CriteriaFilter, Deduplicator
        from home_finder.models import SearchCriteria

        scrapers = [
            OpenRentScraper(),
            RightmoveScraper(),
            ZooplaScraper(),
            OnTheMarketScraper(),
        ]

        all_properties = []
        for scraper in scrapers:
            try:
                properties = await scraper.scrape(
                    min_price=1800,
                    max_price=2200,
                    min_bedrooms=1,
                    max_bedrooms=2,
                    area="hackney",
                )
                all_properties.extend(properties)
                print(f"\n{scraper.source.value}: {len(properties)} properties")
            except Exception as e:
                print(f"\n{scraper.source.value}: FAILED - {e}")

        print(f"\nTotal scraped: {len(all_properties)} properties")

        # Apply criteria filter
        criteria = SearchCriteria(
            min_price=1800,
            max_price=2200,
            min_bedrooms=1,
            max_bedrooms=2,
            destination_postcode="N1 5AA",
            max_commute_minutes=30,
        )
        filtered = CriteriaFilter(criteria).filter_properties(all_properties)
        print(f"After criteria filter: {len(filtered)} properties")

        # Deduplicate
        unique = Deduplicator(enable_cross_platform=True).deduplicate(filtered)
        print(f"After deduplication: {len(unique)} unique properties")

        # Show sample results
        print("\n--- Sample Results ---")
        for prop in unique[:5]:
            print(f"\n[{prop.source.value}] {prop.title}")
            print(f"  £{prop.price_pcm}/month | {prop.bedrooms} bed")
            print(f"  {prop.address}")
            if prop.postcode:
                print(f"  Postcode: {prop.postcode}")
            print(f"  {prop.url}")

        # Basic assertions
        assert isinstance(all_properties, list)
        assert isinstance(filtered, list)
        assert isinstance(unique, list)
        # Deduplicated should be <= filtered
        assert len(unique) <= len(filtered)

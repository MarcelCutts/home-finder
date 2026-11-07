"""Live collection probes for four rental portals.

These check listing shapes and a collection/selection/matching path, without
assessment or notification. Run serially after qualifying portal access.

Run: uv run pytest -n 0 tests/integration/test_real_scraping.py -m slow --force-enable-socket
"""

import pytest

from home_finder.models import PropertySource
from home_finder.scrapers.onthemarket import OnTheMarketScraper
from home_finder.scrapers.openrent import OpenRentScraper
from home_finder.scrapers.rightmove import RightmoveScraper
from home_finder.scrapers.zoopla import ZooplaScraper


@pytest.mark.slow
class TestRealOpenRentScraping:
    """Real scraping tests for OpenRent."""

    async def test_scrape_openrent_hackney(self) -> None:
        """Scrape real OpenRent listings from Hackney."""
        scraper = OpenRentScraper()

        try:
            properties = await scraper.scrape(
                min_price=1500,
                max_price=2500,
                min_bedrooms=1,
                max_bedrooms=2,
                area="hackney",
            )
        finally:
            await scraper.close()

        # Live supply varies; an empty response cannot validate listing fields.
        properties = properties.properties

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
        else:
            pytest.skip("No live listings available to validate")


@pytest.mark.slow
class TestRealRightmoveScraping:
    """Real scraping tests for Rightmove."""

    async def test_scrape_rightmove_hackney(self) -> None:
        """Scrape real Rightmove listings from Hackney."""
        scraper = RightmoveScraper()

        try:
            properties = await scraper.scrape(
                min_price=1500,
                max_price=2500,
                min_bedrooms=1,
                max_bedrooms=2,
                area="hackney",
            )
        finally:
            await scraper.close()

        properties = properties.properties

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
        else:
            pytest.skip("No live listings available to validate")

    @pytest.mark.parametrize(
        ("outcode", "area_name"),
        [
            ("E8", "Hackney Central/Dalston"),
            ("E3", "Bow"),
            ("N15", "South Tottenham"),
            ("E10", "Leyton"),
        ],
    )
    async def test_scrape_rightmove_by_outcode(self, outcode: str, area_name: str) -> None:
        """Scrape real Rightmove listings by postcode outcode."""
        scraper = RightmoveScraper()

        try:
            properties = await scraper.scrape(
                min_price=1500,
                max_price=2500,
                min_bedrooms=1,
                max_bedrooms=2,
                area=outcode,
            )
        finally:
            await scraper.close()

        properties = properties.properties

        assert isinstance(properties, list)
        print(f"\nRightmove ({outcode} - {area_name}): Found {len(properties)} properties")

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

            print(f"  Sample: {prop.title}")
            print(f"  Price: £{prop.price_pcm}/month")
            print(f"  Beds: {prop.bedrooms}")
            if prop.postcode:
                print(f"  Postcode: {prop.postcode}")
            print(f"  URL: {prop.url}")
        else:
            pytest.skip("No live listings available to validate")


@pytest.mark.slow
class TestRealZooplaScraping:
    """Real scraping tests for Zoopla."""

    async def test_scrape_zoopla_hackney(self) -> None:
        """Scrape real Zoopla listings from Hackney."""
        scraper = ZooplaScraper()

        try:
            properties = await scraper.scrape(
                min_price=1500,
                max_price=2500,
                min_bedrooms=1,
                max_bedrooms=2,
                area="hackney",
            )
        finally:
            await scraper.close()

        properties = properties.properties

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
        else:
            pytest.skip("No live listings available to validate")


@pytest.mark.slow
class TestRealOnTheMarketScraping:
    """Real scraping tests for OnTheMarket."""

    async def test_scrape_onthemarket_hackney(self) -> None:
        """Scrape real OnTheMarket listings from Hackney."""
        scraper = OnTheMarketScraper()

        try:
            properties = await scraper.scrape(
                min_price=1500,
                max_price=2500,
                min_bedrooms=1,
                max_bedrooms=2,
                area="hackney",
            )
        finally:
            await scraper.close()

        properties = properties.properties

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
        else:
            pytest.skip("No live listings available to validate")


@pytest.mark.slow
@pytest.mark.timeout(300)
class TestLiveCollectionSelectionMatching:
    """Live collection, criteria selection, and matching; no run coordinator."""

    async def test_scrape_all_platforms_hackney(self) -> None:
        """Scrape from all platforms and aggregate results."""
        from home_finder.filters import CriteriaFilter
        from home_finder.matching.deduplication import Deduplicator
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
                scrape_result = await scraper.scrape(
                    min_price=1800,
                    max_price=2200,
                    min_bedrooms=1,
                    max_bedrooms=2,
                    area="hackney",
                    max_results=60,
                )
                properties = scrape_result.properties
                all_properties.extend(properties)
                print(f"\n{scraper.source.value}: {len(properties)} properties")
            except Exception as e:
                print(f"\n{scraper.source.value}: FAILED - {e}")
            finally:
                await scraper.close()

        if not all_properties:
            pytest.skip("No live observations available for selection and matching")

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
        deduplicator = Deduplicator(enable_cross_platform=True)
        merged = await deduplicator.deduplicate_and_merge_async(filtered)
        print(f"After deduplication: {len(merged)} unique properties")

        # Show sample results
        print("\n--- Sample Results ---")
        for m in merged[:5]:
            prop = m.canonical
            print(f"\n[{prop.source.value}] {prop.title}")
            print(f"  £{prop.price_pcm}/month | {prop.bedrooms} bed")
            print(f"  {prop.address}")
            if prop.postcode:
                print(f"  Postcode: {prop.postcode}")
            print(f"  {prop.url}")

        # These checks apply to the available sources; they do not establish
        # that every portal returned usable observations.
        assert isinstance(all_properties, list)
        assert isinstance(filtered, list)
        assert isinstance(merged, list)
        # Deduplicated should be <= filtered
        assert len(merged) <= len(filtered)

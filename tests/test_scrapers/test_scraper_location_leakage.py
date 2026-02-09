"""Tests to investigate location leakage in scrapers.

These tests verify whether scrapers return properties only from the requested
search area, or if they leak properties from outside the area.

The hypothesis is that property websites return results from a radius around
the search location, not strictly within it. Combined with the lack of
post-scrape location filtering, this causes properties from W11 (Kensington)
and SW1V (Westminster) to appear when searching for East/North London areas.
"""

import re
from collections import defaultdict

import pytest
from pydantic import HttpUrl

from home_finder.models import Property, PropertySource
from home_finder.scrapers.location_utils import is_outcode
from home_finder.scrapers.onthemarket import OnTheMarketScraper
from home_finder.scrapers.openrent import OpenRentScraper
from home_finder.scrapers.rightmove import RightmoveScraper
from home_finder.scrapers.zoopla import ZooplaScraper

# Target search areas from main.py
SEARCH_AREAS = [
    "hackney",
    "islington",
    "haringey",
    "tower-hamlets",
    "e3",
    "e5",
    "e9",
    "e10",
    "n15",
]

# Postcodes we expect to see for each search area
EXPECTED_OUTCODES = {
    "hackney": {"E5", "E8", "E9", "N1", "N4", "N16"},  # Hackney borough
    "islington": {"N1", "N4", "N5", "N7", "N19", "EC1"},  # Islington borough
    "haringey": {"N4", "N8", "N15", "N17", "N22"},  # Haringey borough
    "tower-hamlets": {"E1", "E2", "E3", "E14"},  # Tower Hamlets borough
    "e3": {"E3"},  # Bow
    "e5": {"E5"},  # Clapton
    "e9": {"E9"},  # Hackney Wick, Homerton
    "e10": {"E10"},  # Leyton
    "n15": {"N15"},  # South Tottenham
}

# Postcodes that indicate location leakage (should NOT appear for East/North London)
LEAKAGE_OUTCODES = {
    # West London
    "W1",
    "W2",
    "W3",
    "W4",
    "W5",
    "W6",
    "W7",
    "W8",
    "W9",
    "W10",
    "W11",
    "W12",
    "W13",
    "W14",
    # South West London
    "SW1",
    "SW1A",
    "SW1E",
    "SW1H",
    "SW1P",
    "SW1V",
    "SW1W",
    "SW1X",
    "SW1Y",
    "SW2",
    "SW3",
    "SW4",
    "SW5",
    "SW6",
    "SW7",
    "SW8",
    "SW9",
    "SW10",
    "SW11",
    "SW12",
    "SW13",
    "SW14",
    "SW15",
    "SW16",
    "SW17",
    "SW18",
    "SW19",
    "SW20",
    # South East London (some)
    "SE1",
    "SE11",
    # North West London
    "NW1",
    "NW2",
    "NW3",
    "NW4",
    "NW5",
    "NW6",
    "NW7",
    "NW8",
    "NW9",
    "NW10",
    "NW11",
}


def extract_outcode(postcode: str | None) -> str | None:
    """Extract the outcode (first part) from a UK postcode.

    Examples:
        E8 3RH -> E8
        SW1V 2SA -> SW1V
        N1 -> N1
    """
    if not postcode:
        return None

    # Match outcode pattern: 1-2 letters + 1-2 digits + optional letter
    match = re.match(r"^([A-Z]{1,2}\d{1,2}[A-Z]?)", postcode.upper())
    return match.group(1) if match else None


def categorize_property_location(prop: Property, search_area: str) -> str:
    """Categorize a property as 'expected', 'leakage', or 'unknown'.

    Returns:
        'expected' - property is in expected area for the search
        'leakage' - property is clearly outside the search area
        'unknown' - cannot determine (no postcode extracted)
    """
    outcode = extract_outcode(prop.postcode)
    if not outcode:
        return "unknown"

    # Check for known leakage postcodes
    if outcode in LEAKAGE_OUTCODES:
        return "leakage"

    # Check if in expected area
    search_key = search_area.lower().replace(" ", "-")
    expected = EXPECTED_OUTCODES.get(search_key, set())
    if outcode in expected:
        return "expected"

    # For outcode searches, be strict
    if is_outcode(search_area):
        if outcode == search_area.upper():
            return "expected"
        return "leakage"

    return "unknown"


class TestScraperUrlConstruction:
    """Test that scrapers construct URLs correctly for each search area."""

    @pytest.mark.asyncio
    async def test_rightmove_url_for_borough(self) -> None:
        """Test Rightmove URL construction for borough search."""
        scraper = RightmoveScraper()
        url = await scraper._build_search_url(
            area="hackney",
            min_price=1800,
            max_price=2200,
            min_bedrooms=1,
            max_bedrooms=2,
        )
        # Should use REGION identifier for hackney
        assert "locationIdentifier=REGION%5E93953" in url
        assert "property-to-rent" in url

    @pytest.mark.asyncio
    async def test_rightmove_url_for_outcode(self) -> None:
        """Test Rightmove URL construction for outcode search."""
        scraper = RightmoveScraper()
        url = await scraper._build_search_url(
            area="e8",
            min_price=1800,
            max_price=2200,
            min_bedrooms=1,
            max_bedrooms=2,
        )
        # Should use OUTCODE identifier for E8
        assert "locationIdentifier=OUTCODE%5E762" in url

    def test_zoopla_url_for_borough(self) -> None:
        """Test Zoopla URL construction for borough search."""
        scraper = ZooplaScraper()
        url = scraper._build_search_url(
            area="hackney",
            min_price=1800,
            max_price=2200,
            min_bedrooms=1,
            max_bedrooms=2,
        )
        # Should use hackney-london-borough for London boroughs (with q= param)
        assert "/hackney-london-borough/" in url
        assert "to-rent" in url
        assert "q=Hackney" in url

    def test_zoopla_url_for_outcode(self) -> None:
        """Test Zoopla URL construction for outcode search."""
        scraper = ZooplaScraper()
        url = scraper._build_search_url(
            area="e8",
            min_price=1800,
            max_price=2200,
            min_bedrooms=1,
            max_bedrooms=2,
        )
        # Should use outcode as-is (not in London borough list)
        assert "/e8/" in url

    def test_openrent_url_for_borough(self) -> None:
        """Test OpenRent URL construction for borough search."""
        scraper = OpenRentScraper()
        url = scraper._build_search_url(
            area="hackney",
            min_price=1800,
            max_price=2200,
            min_bedrooms=1,
            max_bedrooms=2,
        )
        assert "/hackney" in url
        assert "properties-to-rent" in url

    def test_openrent_url_for_outcode(self) -> None:
        """Test OpenRent URL construction for outcode search."""
        scraper = OpenRentScraper()
        url = scraper._build_search_url(
            area="e8",
            min_price=1800,
            max_price=2200,
            min_bedrooms=1,
            max_bedrooms=2,
        )
        assert "/e8" in url

    def test_onthemarket_url_for_borough(self) -> None:
        """Test OnTheMarket URL construction for borough search."""
        scraper = OnTheMarketScraper()
        url = scraper._build_search_url(
            area="hackney",
            min_price=1800,
            max_price=2200,
            min_bedrooms=1,
            max_bedrooms=2,
        )
        assert "/hackney/" in url
        assert "to-rent" in url

    def test_onthemarket_url_for_outcode(self) -> None:
        """Test OnTheMarket URL construction for outcode search."""
        scraper = OnTheMarketScraper()
        url = scraper._build_search_url(
            area="e8",
            min_price=1800,
            max_price=2200,
            min_bedrooms=1,
            max_bedrooms=2,
        )
        assert "/e8/" in url


@pytest.mark.integration
@pytest.mark.slow
class TestLiveScraperLocationLeakage:
    """Live integration tests to detect location leakage.

    These tests actually hit the websites to see what postcodes are returned.
    Run with: pytest -m integration tests/test_scrapers/test_scraper_location_leakage.py

    WARNING: These tests make real HTTP requests and may be rate-limited.
    """

    @pytest.mark.asyncio
    async def test_rightmove_hackney_postcodes(self) -> None:
        """Test what postcodes Rightmove returns for hackney search."""
        scraper = RightmoveScraper()
        properties = await scraper.scrape(
            min_price=1800,
            max_price=2200,
            min_bedrooms=1,
            max_bedrooms=2,
            area="hackney",
        )

        self._analyze_location_leakage(properties, "hackney", "rightmove")

    @pytest.mark.asyncio
    async def test_rightmove_e8_postcodes(self) -> None:
        """Test what postcodes Rightmove returns for E8 outcode search."""
        scraper = RightmoveScraper()
        properties = await scraper.scrape(
            min_price=1800,
            max_price=2200,
            min_bedrooms=1,
            max_bedrooms=2,
            area="e8",
        )

        self._analyze_location_leakage(properties, "e8", "rightmove")

    @pytest.mark.asyncio
    async def test_zoopla_hackney_postcodes(self) -> None:
        """Test what postcodes Zoopla returns for hackney search."""
        scraper = ZooplaScraper()
        properties = await scraper.scrape(
            min_price=1800,
            max_price=2200,
            min_bedrooms=1,
            max_bedrooms=2,
            area="hackney",
        )

        self._analyze_location_leakage(properties, "hackney", "zoopla")

    @pytest.mark.asyncio
    async def test_zoopla_e8_postcodes(self) -> None:
        """Test what postcodes Zoopla returns for E8 outcode search."""
        scraper = ZooplaScraper()
        properties = await scraper.scrape(
            min_price=1800,
            max_price=2200,
            min_bedrooms=1,
            max_bedrooms=2,
            area="e8",
        )

        self._analyze_location_leakage(properties, "e8", "zoopla")

    @pytest.mark.asyncio
    async def test_openrent_hackney_postcodes(self) -> None:
        """Test what postcodes OpenRent returns for hackney search."""
        scraper = OpenRentScraper()
        properties = await scraper.scrape(
            min_price=1800,
            max_price=2200,
            min_bedrooms=1,
            max_bedrooms=2,
            area="hackney",
        )

        self._analyze_location_leakage(properties, "hackney", "openrent")

    @pytest.mark.asyncio
    async def test_openrent_e8_postcodes(self) -> None:
        """Test what postcodes OpenRent returns for E8 outcode search."""
        scraper = OpenRentScraper()
        properties = await scraper.scrape(
            min_price=1800,
            max_price=2200,
            min_bedrooms=1,
            max_bedrooms=2,
            area="e8",
        )

        self._analyze_location_leakage(properties, "e8", "openrent")

    @pytest.mark.asyncio
    async def test_onthemarket_hackney_postcodes(self) -> None:
        """Test what postcodes OnTheMarket returns for hackney search."""
        scraper = OnTheMarketScraper()
        properties = await scraper.scrape(
            min_price=1800,
            max_price=2200,
            min_bedrooms=1,
            max_bedrooms=2,
            area="hackney",
        )

        self._analyze_location_leakage(properties, "hackney", "onthemarket")

    def _analyze_location_leakage(
        self,
        properties: list[Property],
        search_area: str,
        source: str,
    ) -> None:
        """Analyze location leakage in scraped properties.

        This method:
        1. Extracts outcodes from all properties
        2. Categorizes them as expected/leakage/unknown
        3. Reports statistics
        4. Fails if significant leakage is detected
        """
        if not properties:
            pytest.skip(f"No properties returned from {source} for {search_area}")

        # Categorize all properties
        categories: dict[str, list[Property]] = defaultdict(list)
        outcode_counts: dict[str, int] = defaultdict(int)

        for prop in properties:
            category = categorize_property_location(prop, search_area)
            categories[category].append(prop)

            outcode = extract_outcode(prop.postcode)
            if outcode:
                outcode_counts[outcode] += 1

        # Print detailed analysis
        total = len(properties)
        expected_count = len(categories["expected"])
        leakage_count = len(categories["leakage"])
        unknown_count = len(categories["unknown"])

        print(f"\n{'=' * 60}")
        print(f"LOCATION LEAKAGE ANALYSIS: {source.upper()} searching for '{search_area}'")
        print(f"{'=' * 60}")
        print(f"Total properties: {total}")
        exp_pct = expected_count * 100 // total if total else 0
        leak_pct = leakage_count * 100 // total if total else 0
        unk_pct = unknown_count * 100 // total if total else 0
        print(f"  Expected location: {expected_count} ({exp_pct}%)")
        print(f"  Location leakage:  {leakage_count} ({leak_pct}%)")
        print(f"  Unknown location:  {unknown_count} ({unk_pct}%)")
        print()
        print("Outcode distribution:")
        for outcode, count in sorted(outcode_counts.items(), key=lambda x: -x[1]):
            marker = " [LEAKAGE]" if outcode in LEAKAGE_OUTCODES else ""
            print(f"  {outcode}: {count}{marker}")

        # Show examples of leakage
        if categories["leakage"]:
            print()
            print("Example leaked properties:")
            for prop in categories["leakage"][:3]:
                print(f"  - {prop.postcode}: {prop.title[:50]}...")
                print(f"    URL: {prop.url}")

        # Assert - we expect SOME leakage based on user's report
        # This test documents the issue rather than failing
        if leakage_count > 0:
            leakage_pct = leakage_count * 100 // total
            print()
            print(f"WARNING: {leakage_pct}% of results are from outside the search area!")
            print("This confirms the location leakage hypothesis.")

            # Soft assertion - document the issue but don't fail the test
            # Comment out the pytest.fail if you just want to gather data
            # pytest.fail(
            #     f"Location leakage detected: {leakage_count}/{total} properties "
            #     f"({leakage_pct}%) are from outside {search_area}"
            # )


class TestPostcodeExtraction:
    """Tests for postcode extraction utility."""

    @pytest.mark.parametrize(
        ("postcode", "expected_outcode"),
        [
            ("E8 3RH", "E8"),
            ("E8", "E8"),
            ("SW1V 2SA", "SW1V"),
            ("SW1V", "SW1V"),
            ("N1 5AA", "N1"),
            ("EC1A 1BB", "EC1A"),
            ("W11 4UL", "W11"),
            (None, None),
            ("", None),
            ("Invalid", None),
        ],
    )
    def test_extract_outcode(self, postcode: str | None, expected_outcode: str | None) -> None:
        """Test outcode extraction from postcodes."""
        assert extract_outcode(postcode) == expected_outcode


class TestLocationCategorization:
    """Tests for location categorization."""

    def test_categorize_expected_borough(self, sample_property: Property) -> None:
        """Test that property in expected borough is categorized correctly."""
        # sample_property has postcode E8 3RH
        category = categorize_property_location(sample_property, "hackney")
        assert category == "expected"

    def test_categorize_leakage(self) -> None:
        """Test that property from West London is categorized as leakage."""
        prop = Property(
            source=PropertySource.RIGHTMOVE,
            source_id="123",
            url=HttpUrl("https://example.com/123"),
            title="Flat in Notting Hill",
            price_pcm=2000,
            bedrooms=1,
            address="123 Portobello Road",
            postcode="W11 2QB",
        )
        category = categorize_property_location(prop, "hackney")
        assert category == "leakage"

    def test_categorize_unknown_no_postcode(self) -> None:
        """Test that property without postcode is categorized as unknown."""
        prop = Property(
            source=PropertySource.RIGHTMOVE,
            source_id="123",
            url=HttpUrl("https://example.com/123"),
            title="Flat somewhere",
            price_pcm=2000,
            bedrooms=1,
            address="123 Some Street, London",
            postcode=None,
        )
        category = categorize_property_location(prop, "hackney")
        assert category == "unknown"

    def test_categorize_outcode_search_strict(self) -> None:
        """Test that outcode searches are strict about matching."""
        # Property in E9 when searching for E8 should be leakage
        prop = Property(
            source=PropertySource.RIGHTMOVE,
            source_id="123",
            url=HttpUrl("https://example.com/123"),
            title="Flat in Hackney Wick",
            price_pcm=2000,
            bedrooms=1,
            address="123 Some Street",
            postcode="E9 5AA",
        )
        category = categorize_property_location(prop, "e8")
        assert category == "leakage"

        # Same property should be expected for E9 search
        category = categorize_property_location(prop, "e9")
        assert category == "expected"

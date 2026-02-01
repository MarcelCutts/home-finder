"""Rightmove property scraper."""

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from crawlee.crawlers import BeautifulSoupCrawler, BeautifulSoupCrawlingContext
from pydantic import HttpUrl

from home_finder.logging import get_logger
from home_finder.models import Property, PropertySource
from home_finder.scrapers.base import BaseScraper

logger = get_logger(__name__)


# Mapping of common area names to Rightmove location identifiers
# These are REGION% encoded identifiers that Rightmove uses
RIGHTMOVE_LOCATIONS = {
    "hackney": "REGION%5E93965",
    "islington": "REGION%5E93980",
    "haringey": "REGION%5E93963",
    "tower-hamlets": "REGION%5E94034",
    "tower hamlets": "REGION%5E94034",
}


class RightmoveScraper(BaseScraper):
    """Scraper for Rightmove.co.uk listings."""

    BASE_URL = "https://www.rightmove.co.uk"

    @property
    def source(self) -> PropertySource:
        return PropertySource.RIGHTMOVE

    async def scrape(
        self,
        *,
        min_price: int,
        max_price: int,
        min_bedrooms: int,
        max_bedrooms: int,
        area: str,
    ) -> list[Property]:
        """Scrape Rightmove for matching properties."""
        properties: list[Property] = []

        url = self._build_search_url(
            area=area,
            min_price=min_price,
            max_price=max_price,
            min_bedrooms=min_bedrooms,
            max_bedrooms=max_bedrooms,
        )

        async def handle_page(context: BeautifulSoupCrawlingContext) -> None:
            soup = context.soup
            parsed = self._parse_search_results(soup, str(context.request.url))
            properties.extend(parsed)
            logger.info(
                "scraped_rightmove_page",
                url=str(context.request.url),
                properties_found=len(parsed),
            )

        crawler = BeautifulSoupCrawler(max_requests_per_crawl=1)
        crawler.router.default_handler(handle_page)

        await crawler.run([url])

        return properties

    def _build_search_url(
        self,
        *,
        area: str,
        min_price: int,
        max_price: int,
        min_bedrooms: int,
        max_bedrooms: int,
    ) -> str:
        """Build the Rightmove search URL with filters."""
        # Normalize area name and look up location identifier
        area_key = area.lower().replace("_", "-").replace(" ", "-")
        location_id = RIGHTMOVE_LOCATIONS.get(area_key, RIGHTMOVE_LOCATIONS.get("hackney"))

        params = [
            f"locationIdentifier={location_id}",
            f"minBedrooms={min_bedrooms}",
            f"maxBedrooms={max_bedrooms}",
            f"minPrice={min_price}",
            f"maxPrice={max_price}",
            "propertyTypes=flat",  # Only flats, not houses
            "mustHave=",
            "dontShow=houseShare",  # Exclude house shares
            "furnishTypes=",
            "keywords=",
        ]
        return f"{self.BASE_URL}/property-to-rent/find.html?{'&'.join(params)}"

    def _parse_search_results(
        self, soup: BeautifulSoup, base_url: str
    ) -> list[Property]:
        """Parse property listings from search results page."""
        properties: list[Property] = []

        # Find all property cards - try new data-testid format first, then old format
        property_cards = soup.find_all(attrs={"data-testid": re.compile(r"^propertyCard-\d+$")})

        if not property_cards:
            # Fallback to old data-test format
            property_cards = soup.find_all("div", {"data-test": "propertyCard"})

        for card in property_cards:
            try:
                prop = self._parse_property_card(card)
                if prop:
                    properties.append(prop)
            except Exception as e:
                logger.warning("failed_to_parse_property_card", error=str(e))

        return properties

    def _parse_property_card(self, card: BeautifulSoup) -> Property | None:
        """Parse a single property card element."""
        # Extract property ID from card data-testid or id attribute
        card_testid = card.get("data-testid", "")
        card_id = card.get("id", "")

        property_id = None
        if card_testid and card_testid.startswith("propertyCard-"):
            # New format: data-testid="propertyCard-0" - need to get ID from link
            pass
        elif card_id:
            property_id = card_id.replace("property-", "")

        # Try to extract from link - look for any anchor with /properties/ in href
        link = card.find("a", href=re.compile(r"/properties/\d+"))
        if not link:
            # Try class-based search
            link = card.find("a", class_="propertyCard-link")
        if not link:
            # Try new structure with propertyCard in class name
            link = card.find("a", class_=re.compile(r"propertyCard"))
        if not link:
            # Just find any anchor tag
            all_links = card.find_all("a", href=True)
            for a in all_links:
                if "/properties/" in a.get("href", ""):
                    link = a
                    break

        if link:
            href = link.get("href", "")
            if not property_id:
                property_id = self._extract_property_id(href)
        else:
            href = ""

        if not property_id:
            return None

        if not href:
            return None
        full_url = urljoin(self.BASE_URL, href.split("#")[0])

        # Extract address - try new data-testid first, then old class
        address_elem = card.find(attrs={"data-testid": "property-address"})
        if address_elem:
            # New structure: get text from nested <address> tag
            inner_address = address_elem.find("address")
            if inner_address:
                address = inner_address.get_text(strip=True)
            else:
                address = address_elem.get_text(strip=True)
        else:
            # Old structure
            address_elem = card.find("address", class_="propertyCard-address")
            if not address_elem:
                address_elem = card.find("address")
            address = address_elem.get_text(strip=True) if address_elem else ""

        if not address:
            return None

        # Extract property type from new structure
        property_type = ""
        info_elem = card.find(attrs={"data-testid": "property-information"})
        if info_elem:
            type_span = info_elem.find("span", class_=re.compile(r"propertyType", re.I))
            if type_span:
                property_type = type_span.get_text(strip=True)

        # Build title from property type and address
        if property_type:
            title = f"{property_type}, {address}"
        else:
            # Extract title - try old structure
            title_elem = card.find("h2", class_="propertyCard-title")
            if not title_elem:
                title_elem = card.find("h2")
            title = title_elem.get_text(strip=True) if title_elem else address

        # Extract bedrooms from new structure first (span with bedroomsCount class)
        bedrooms = None
        if info_elem:
            bedrooms_span = info_elem.find("span", class_=re.compile(r"bedroomsCount", re.I))
            if bedrooms_span:
                bedrooms_text = bedrooms_span.get_text(strip=True)
                if bedrooms_text.isdigit():
                    bedrooms = int(bedrooms_text)

        if bedrooms is None:
            # Try extracting from title
            bedrooms = self._extract_bedrooms(title)

        if bedrooms is None:
            # Try from property details lozenge (old structure)
            details_elem = card.find(attrs={"data-testid": "property-details-lozenge"})
            if details_elem:
                details_text = details_elem.get_text(strip=True).lower()
                match = re.search(r"(\d+)\s*beds?", details_text)
                if match:
                    bedrooms = int(match.group(1))

        if bedrooms is None:
            # Try from tag list (old structure)
            tags = card.find_all("li", class_="propertyCard-tag")
            for tag in tags:
                tag_text = tag.get_text(strip=True).lower()
                match = re.match(r"(\d+)\s*beds?", tag_text)
                if match:
                    bedrooms = int(match.group(1))
                    break

        if bedrooms is None:
            return None

        # Extract price - try new data-testid first
        price_elem = card.find(attrs={"data-testid": "property-price"})
        price_text = ""
        if price_elem:
            # New structure: price is in a div with class containing "price" but not "secondary"
            price_div = price_elem.find("div", class_=re.compile(r"PropertyPrice_price__"))
            if price_div:
                price_text = price_div.get_text(strip=True)
            else:
                price_text = price_elem.get_text(strip=True)
        else:
            # Old structure
            price_elem = card.find("div", class_="propertyCard-priceValue")
            if not price_elem:
                price_elem = card.find(class_=re.compile(r"[Pp]rice"))
            price_text = price_elem.get_text(strip=True) if price_elem else ""

        price = self._extract_price(price_text)
        if price is None:
            return None

        # Extract postcode
        postcode = self._extract_postcode(address)

        # Extract image URL
        img = card.find("img")
        image_url = img.get("src") if img else None
        if image_url and not image_url.startswith("http"):
            image_url = f"https:{image_url}"

        return Property(
            source=PropertySource.RIGHTMOVE,
            source_id=property_id,
            url=HttpUrl(full_url),
            title=title,
            price_pcm=price,
            bedrooms=bedrooms,
            address=address,
            postcode=postcode,
            image_url=HttpUrl(image_url) if image_url else None,
        )

    def _extract_property_id(self, url: str) -> str | None:
        """Extract property ID from URL."""
        match = re.search(r"/properties/(\d+)", url)
        return match.group(1) if match else None

    def _extract_price(self, text: str) -> int | None:
        """Extract monthly price from text.

        Handles both pcm (per calendar month) and pw (per week) formats.
        """
        if not text:
            return None

        # Match price with optional thousand separator
        match = re.search(r"£([\d,]+)", text)
        if not match:
            return None

        price = int(match.group(1).replace(",", ""))

        # Convert weekly to monthly if needed
        if "pw" in text.lower():
            price = int(price * 52 / 12)

        return price

    def _extract_bedrooms(self, text: str) -> int | None:
        """Extract bedroom count from text."""
        if not text:
            return None

        text_lower = text.lower()

        # Handle studio
        if "studio" in text_lower:
            return 0

        # Match "1 bedroom", "2 bed", etc.
        match = re.search(r"(\d+)\s*bed(?:room)?s?", text_lower)
        return int(match.group(1)) if match else None

    def _extract_postcode(self, address: str) -> str | None:
        """Extract UK postcode from address."""
        if not address:
            return None

        # UK postcode pattern: area code + optional district + space + sector + unit
        # E.g., "E8", "E8 3RH", "N1 2AA", "SW1A 1AA"
        # Try full postcode first
        match = re.search(
            r"\b([A-Z]{1,2}\d{1,2}[A-Z]?)\s*(\d[A-Z]{2})?\b",
            address.upper(),
        )
        if match:
            outward = match.group(1)
            inward = match.group(2)
            if inward:
                return f"{outward} {inward}"
            return outward
        return None

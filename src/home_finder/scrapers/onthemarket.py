"""OnTheMarket property scraper."""

import re

from bs4 import BeautifulSoup
from crawlee.crawlers import BeautifulSoupCrawler, BeautifulSoupCrawlingContext
from pydantic import HttpUrl

from home_finder.logging import get_logger
from home_finder.models import Property, PropertySource
from home_finder.scrapers.base import BaseScraper

logger = get_logger(__name__)


class OnTheMarketScraper(BaseScraper):
    """Scraper for OnTheMarket.com listings."""

    BASE_URL = "https://www.onthemarket.com"

    @property
    def source(self) -> PropertySource:
        return PropertySource.ONTHEMARKET

    async def scrape(
        self,
        *,
        min_price: int,
        max_price: int,
        min_bedrooms: int,
        max_bedrooms: int,
        area: str,
    ) -> list[Property]:
        """Scrape OnTheMarket for matching properties."""
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
                "scraped_onthemarket_page",
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
        """Build the OnTheMarket search URL with filters."""
        # Normalize area name
        area_slug = area.lower().replace(" ", "-")

        params = [
            f"min-bedrooms={min_bedrooms}",
            f"max-bedrooms={max_bedrooms}",
            f"min-price={min_price}",
            f"max-price={max_price}",
            "property-types=flats-apartments",
            "rent-frequency=per-month",
        ]
        return f"{self.BASE_URL}/to-rent/property/{area_slug}/?{'&'.join(params)}"

    def _parse_search_results(
        self, soup: BeautifulSoup, base_url: str
    ) -> list[Property]:
        """Parse property listings from search results page."""
        properties: list[Property] = []

        # Find all property cards
        # OnTheMarket uses various card class names
        property_cards = soup.find_all("li", class_="otm-PropertyCard")

        for card in property_cards:
            try:
                prop = self._parse_property_card(card)
                if prop:
                    properties.append(prop)
            except Exception as e:
                logger.warning("failed_to_parse_onthemarket_card", error=str(e))

        return properties

    def _parse_property_card(self, card: BeautifulSoup) -> Property | None:
        """Parse a single property card element."""
        # Extract property ID from data attribute or URL
        property_id = card.get("data-property-id")

        if not property_id:
            link = card.find("a", class_="otm-PropertyCard__link")
            if link:
                href = link.get("href", "")
                property_id = self._extract_property_id(href)

        if not property_id:
            return None

        property_id = str(property_id)

        # Extract URL
        link = card.find("a", class_="otm-PropertyCard__link")
        if not link:
            link = card.find("a")
        href = link.get("href", "") if link else ""
        if not href:
            return None

        if not href.startswith("http"):
            href = f"{self.BASE_URL}{href}"

        # Extract price
        price_elem = card.find("p", class_="otm-PropertyCard__price")
        if not price_elem:
            price_elem = card.find(class_=re.compile(r"price", re.I))
        price_text = price_elem.get_text(strip=True) if price_elem else ""
        price = self._extract_price(price_text)
        if price is None:
            return None

        # Extract address
        address_elem = card.find("p", class_="otm-PropertyCard__address")
        if not address_elem:
            address_elem = card.find(class_=re.compile(r"address", re.I))
        address = address_elem.get_text(strip=True) if address_elem else ""
        if not address:
            return None

        # Extract title
        title_elem = card.find("h3", class_="otm-PropertyCard__title")
        if not title_elem:
            title_elem = card.find("h3")
        title = title_elem.get_text(strip=True) if title_elem else address

        # Extract bedrooms from title
        bedrooms = self._extract_bedrooms(title)
        if bedrooms is None:
            # Try from features list
            features = card.find_all("li")
            for feat in features:
                feat_text = feat.get_text(strip=True).lower()
                match = re.match(r"(\d+)\s*beds?", feat_text)
                if match:
                    bedrooms = int(match.group(1))
                    break

        if bedrooms is None:
            return None

        # Extract postcode
        postcode = self._extract_postcode(address)

        # Extract image URL
        img = card.find("img")
        image_url = img.get("src") if img else None
        if image_url and not image_url.startswith("http"):
            image_url = f"https:{image_url}"

        return Property(
            source=PropertySource.ONTHEMARKET,
            source_id=property_id,
            url=HttpUrl(href),
            title=title,
            price_pcm=price,
            bedrooms=bedrooms,
            address=address,
            postcode=postcode,
            image_url=HttpUrl(image_url) if image_url else None,
        )

    def _extract_property_id(self, url: str) -> str | None:
        """Extract property ID from URL."""
        # OnTheMarket URLs: /details/15234567/
        match = re.search(r"/details/(\d+)", url)
        return match.group(1) if match else None

    def _extract_price(self, text: str) -> int | None:
        """Extract monthly price from text."""
        if not text:
            return None

        # Match price
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

        # Match "1 bed", "2 bedroom", etc.
        match = re.search(r"(\d+)\s*bed(?:room)?s?", text_lower)
        return int(match.group(1)) if match else None

    def _extract_postcode(self, address: str) -> str | None:
        """Extract UK postcode from address."""
        if not address:
            return None

        # UK postcode pattern
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

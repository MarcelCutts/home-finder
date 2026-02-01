"""Zoopla property scraper using curl_cffi for TLS fingerprint impersonation."""

import json
import re

from bs4 import BeautifulSoup, Tag
from curl_cffi.requests import AsyncSession
from pydantic import HttpUrl, ValidationError

from home_finder.logging import get_logger
from home_finder.models import Property, PropertySource
from home_finder.scrapers.base import BaseScraper
from home_finder.scrapers.zoopla_models import (
    ZooplaListing,
    ZooplaListingsAdapter,
    ZooplaNextData,
)

logger = get_logger(__name__)

HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
}


class ZooplaScraper(BaseScraper):
    """Scraper for Zoopla.co.uk listings using curl_cffi."""

    BASE_URL = "https://www.zoopla.co.uk"

    @property
    def source(self) -> PropertySource:
        return PropertySource.ZOOPLA

    async def scrape(
        self,
        *,
        min_price: int,
        max_price: int,
        min_bedrooms: int,
        max_bedrooms: int,
        area: str,
    ) -> list[Property]:
        """Scrape Zoopla for matching properties."""
        url = self._build_search_url(
            area=area,
            min_price=min_price,
            max_price=max_price,
            min_bedrooms=min_bedrooms,
            max_bedrooms=max_bedrooms,
        )

        html = await self._fetch_page(url)
        if not html:
            logger.warning("zoopla_fetch_failed", url=url)
            return []

        # Try JSON extraction first (more reliable for Next.js pages)
        next_data = self._extract_next_data(html)
        if next_data:
            properties = self._parse_next_data_properties(next_data)
            if properties:
                logger.info(
                    "scraped_zoopla_page",
                    url=url,
                    properties_found=len(properties),
                    method="json",
                )
                return properties

        # Fallback to HTML parsing
        soup = BeautifulSoup(html, "html.parser")
        properties = self._parse_search_results(soup, url)
        logger.info(
            "scraped_zoopla_page",
            url=url,
            properties_found=len(properties),
            method="html",
        )
        return properties

    async def _fetch_page(self, url: str) -> str | None:
        """Fetch page using curl_cffi with Chrome impersonation."""
        try:
            async with AsyncSession() as session:
                response = await session.get(
                    url,
                    impersonate="chrome",
                    headers=HEADERS,
                    timeout=30,
                )
                if response.status_code == 200:
                    text: str = response.text
                    return text
                logger.warning(
                    "zoopla_http_error",
                    status=response.status_code,
                    url=url,
                )
                return None
        except Exception as e:
            logger.error("zoopla_fetch_exception", error=str(e), url=url)
            return None

    def _extract_next_data(self, html: str) -> ZooplaNextData | None:
        """Extract and validate listing data from Next.js page.

        Zoopla uses Next.js App Router with React Server Components,
        so data is embedded in self.__next_f.push() calls rather than
        the traditional __NEXT_DATA__ script tag.
        """
        # First try traditional __NEXT_DATA__ (older pages)
        soup = BeautifulSoup(html, "html.parser")
        script = soup.find("script", {"id": "__NEXT_DATA__"})
        if script and script.string:
            try:
                return ZooplaNextData.model_validate_json(script.string)
            except (json.JSONDecodeError, ValidationError) as e:
                logger.debug("zoopla_next_data_parse_failed", error=str(e))

        # Try RSC format - find script containing regularListingsFormatted
        for script in soup.find_all("script"):
            if script.string and "regularListingsFormatted" in script.string:
                listings = self._extract_rsc_listings(script.string)
                if listings:
                    # Wrap in the expected structure
                    return ZooplaNextData.model_validate(
                        {"props": {"pageProps": {"regularListingsFormatted": listings}}}
                    )

        return None

    def _extract_rsc_listings(self, script_content: str) -> list[ZooplaListing] | None:
        """Extract listing data from React Server Components format.

        RSC data is JSON-encoded inside self.__next_f.push() calls.
        The content uses escaped quotes that need to be unescaped.
        """
        try:
            # Find the array between markers
            start_marker = 'regularListingsFormatted\\":'
            end_marker = ',\\"extendedListingsFormatted'

            start_idx = script_content.find(start_marker)
            if start_idx == -1:
                return None

            start_idx += len(start_marker)
            end_idx = script_content.find(end_marker, start_idx)
            if end_idx == -1:
                return None

            listings_json_escaped = script_content[start_idx:end_idx]

            # Unescape the JSON - content is inside a JS string literal
            listings_json = listings_json_escaped.replace('\\"', '"').replace("\\\\", "\\")

            # Validate with Pydantic TypeAdapter
            return ZooplaListingsAdapter.validate_json(listings_json)

        except (json.JSONDecodeError, ValidationError) as e:
            logger.warning("zoopla_rsc_parse_failed", error=str(e))

        return None

    def _parse_next_data_properties(self, data: ZooplaNextData) -> list[Property]:
        """Parse properties from validated Next.js JSON data."""
        properties: list[Property] = []

        for listing in data.get_listings():
            prop = self._listing_to_property(listing)
            if prop:
                properties.append(prop)

        return properties

    def _listing_to_property(self, listing: ZooplaListing) -> Property | None:
        """Convert a validated ZooplaListing to a Property."""
        # Get detail URL
        detail_url = listing.get_detail_url()
        if not detail_url:
            return None
        if not detail_url.startswith("http"):
            detail_url = f"{self.BASE_URL}{detail_url}"

        # Get price
        price = listing.get_price_pcm()
        if price is None:
            return None

        # Get bedrooms
        bedrooms = listing.get_bedrooms()
        if bedrooms is None:
            return None

        # Get address and title
        address = listing.get_address()
        title = listing.get_title()

        # Extract postcode from address
        postcode = self._extract_postcode(address)

        # Get image URL
        image_url = listing.get_image_url()

        # Get coordinates
        latitude = listing.pos.lat if listing.pos else None
        longitude = listing.pos.lng if listing.pos else None

        return Property(
            source=PropertySource.ZOOPLA,
            source_id=str(listing.listing_id),
            url=HttpUrl(detail_url),
            title=title,
            price_pcm=price,
            bedrooms=bedrooms,
            address=address,
            postcode=postcode,
            image_url=HttpUrl(image_url) if image_url else None,
            latitude=latitude,
            longitude=longitude,
        )

    # Zoopla uses "-london" suffix for London boroughs
    LONDON_BOROUGH_SLUGS = {
        "hackney": "hackney-london",
        "islington": "islington-london",
        "haringey": "haringey-london",
        "tower-hamlets": "tower-hamlets-london",
        "camden": "camden-london",
        "westminster": "westminster-london",
        "kensington": "kensington-and-chelsea-london",
        "lambeth": "lambeth-london",
        "southwark": "southwark-london",
        "newham": "newham-london",
        "waltham-forest": "waltham-forest-london",
        "barnet": "barnet-london",
        "brent": "brent-london",
        "ealing": "ealing-london",
        "enfield": "enfield-london",
        "greenwich": "greenwich-london",
        "hammersmith": "hammersmith-and-fulham-london",
        "lewisham": "lewisham-london",
        "wandsworth": "wandsworth-london",
    }

    def _build_search_url(
        self,
        *,
        area: str,
        min_price: int,
        max_price: int,
        min_bedrooms: int,
        max_bedrooms: int,
    ) -> str:
        """Build the Zoopla search URL with filters."""
        # Normalize area name
        area_slug = area.lower().replace(" ", "-")

        # Use London borough slug if available
        area_slug = self.LONDON_BOROUGH_SLUGS.get(area_slug, area_slug)

        params = [
            f"beds_min={min_bedrooms}",
            f"beds_max={max_bedrooms}",
            f"price_min={min_price}",
            f"price_max={max_price}",
            "price_frequency=per_month",
            "property_sub_type=flats",
            "is_shared_accommodation=false",
            "is_retirement_home=false",
            "is_student_accommodation=false",
        ]
        return f"{self.BASE_URL}/to-rent/property/{area_slug}/?{'&'.join(params)}"

    def _parse_search_results(self, soup: BeautifulSoup, _base_url: str) -> list[Property]:
        """Parse property listings from search results page (HTML fallback)."""
        properties: list[Property] = []

        # Find all search result cards
        result_cards = soup.find_all("div", {"data-testid": "search-result"})

        for card in result_cards:
            try:
                prop = self._parse_property_card(card)
                if prop:
                    properties.append(prop)
            except Exception as e:
                logger.warning("failed_to_parse_zoopla_card", error=str(e))

        return properties

    def _parse_property_card(self, card: Tag) -> Property | None:
        """Parse a single property card element (HTML fallback)."""
        # Extract URL and property ID
        link = card.find("a", {"data-testid": "listing-details-link"})
        if not link:
            return None

        href = link.get("href", "")
        if not isinstance(href, str) or not href:
            return None

        property_id = self._extract_property_id(href)
        if not property_id:
            return None

        # Ensure full URL
        if not href.startswith("http"):
            href = f"{self.BASE_URL}{href}"

        # Extract price
        price_elem = card.find(attrs={"data-testid": "listing-price"})
        price_text = price_elem.get_text(strip=True) if price_elem else ""
        price = self._extract_price(price_text)
        if price is None:
            return None

        # Extract title
        title_elem = card.find("h2", {"data-testid": "listing-title"})
        title = title_elem.get_text(strip=True) if title_elem else ""
        if not title:
            title_elem = card.find("h2")
            title = title_elem.get_text(strip=True) if title_elem else ""

        # Extract bedrooms from title
        bedrooms = self._extract_bedrooms(title)
        if bedrooms is None:
            # Try from feature list
            feature_items = card.find_all("li")
            for item in feature_items:
                item_text = item.get_text(strip=True).lower()
                match = re.match(r"(\d+)\s*beds?", item_text)
                if match:
                    bedrooms = int(match.group(1))
                    break

        if bedrooms is None:
            return None

        # Extract address
        address_elem = card.find("address", {"data-testid": "listing-address"})
        if not address_elem:
            address_elem = card.find("address")
        address = address_elem.get_text(strip=True) if address_elem else ""
        if not address:
            address = title

        # Extract postcode
        postcode = self._extract_postcode(address)

        # Extract image URL
        img = card.find("img")
        image_url = img.get("src") if img else None
        if not isinstance(image_url, str):
            image_url = None
        elif not image_url.startswith("http"):
            image_url = f"https:{image_url}"

        return Property(
            source=PropertySource.ZOOPLA,
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
        # Zoopla URLs: /to-rent/details/66543210/
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

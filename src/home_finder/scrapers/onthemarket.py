"""OnTheMarket property scraper using curl_cffi for TLS fingerprint impersonation."""

import json
import re
from typing import Any

from curl_cffi.requests import AsyncSession
from pydantic import HttpUrl

from home_finder.logging import get_logger
from home_finder.models import Property, PropertySource
from home_finder.scrapers.base import BaseScraper

logger = get_logger(__name__)

HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
}


class OnTheMarketScraper(BaseScraper):
    """Scraper for OnTheMarket.com listings using curl_cffi."""

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
        url = self._build_search_url(
            area=area,
            min_price=min_price,
            max_price=max_price,
            min_bedrooms=min_bedrooms,
            max_bedrooms=max_bedrooms,
        )

        html = await self._fetch_page(url)
        if not html:
            logger.warning("onthemarket_fetch_failed", url=url)
            return []

        # Parse __NEXT_DATA__ JSON
        properties = self._parse_next_data(html)
        logger.info(
            "scraped_onthemarket_page",
            url=url,
            properties_found=len(properties),
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
                    "onthemarket_http_error",
                    status=response.status_code,
                    url=url,
                )
                return None
        except Exception as e:
            logger.error("onthemarket_fetch_exception", error=str(e), url=url)
            return None

    def _parse_next_data(self, html: str) -> list[Property]:
        """Parse properties from __NEXT_DATA__ JSON."""
        match = re.search(
            r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>',
            html,
            re.DOTALL,
        )
        if not match:
            logger.warning("onthemarket_no_next_data")
            return []

        try:
            data = json.loads(match.group(1))
            listings = (
                data.get("props", {})
                .get("initialReduxState", {})
                .get("results", {})
                .get("list", [])
            )
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning("onthemarket_parse_failed", error=str(e))
            return []

        properties: list[Property] = []
        for listing in listings:
            try:
                prop = self._listing_to_property(listing)
                if prop:
                    properties.append(prop)
            except Exception as e:
                logger.warning("onthemarket_listing_parse_failed", error=str(e))

        return properties

    def _listing_to_property(self, listing: dict[str, Any]) -> Property | None:
        """Convert a listing dict to a Property."""
        # Extract property ID
        property_id = listing.get("id")
        if not property_id:
            details_url = listing.get("details-url", "")
            match = re.search(r"/details/(\d+)", details_url)
            property_id = match.group(1) if match else None
        if not property_id:
            return None
        property_id = str(property_id)

        # Extract URL
        details_url = listing.get("details-url", "")
        if not details_url:
            return None
        if not details_url.startswith("http"):
            details_url = f"{self.BASE_URL}{details_url}"

        # Extract price
        price_text = listing.get("short-price", "")
        price = self._extract_price(price_text)
        if price is None:
            return None

        # Extract bedrooms
        bedrooms = listing.get("bedrooms")
        if bedrooms is None:
            title = listing.get("property-title", "")
            bedrooms = self._extract_bedrooms(title)
        if bedrooms is None:
            return None

        # Extract address and title
        address = listing.get("address", "")
        if not address:
            return None
        title = listing.get("property-title", address)

        # Extract postcode
        postcode = self._extract_postcode(address)

        # Extract image URL
        image_url: str | None = None
        images = listing.get("images", [])
        if images:
            image_url = images[0].get("default") or images[0].get("webp")
        if not image_url:
            cover = listing.get("cover-image", {})
            image_url = cover.get("default") or cover.get("webp")

        # Extract coordinates
        location = listing.get("location", {})
        latitude = location.get("lat")
        longitude = location.get("lon")
        if latitude is None or longitude is None:
            latitude, longitude = None, None

        return Property(
            source=PropertySource.ONTHEMARKET,
            source_id=property_id,
            url=HttpUrl(details_url),
            title=title,
            price_pcm=price,
            bedrooms=bedrooms,
            address=address,
            postcode=postcode,
            image_url=HttpUrl(image_url) if image_url else None,
            latitude=latitude,
            longitude=longitude,
        )

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

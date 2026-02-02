"""OpenRent property scraper."""

import re
from urllib.parse import urljoin

from crawlee.crawlers import BeautifulSoupCrawler, BeautifulSoupCrawlingContext
from crawlee.storage_clients import MemoryStorageClient
from pydantic import HttpUrl

from home_finder.logging import get_logger
from home_finder.models import Property, PropertySource
from home_finder.scrapers.base import BaseScraper

logger = get_logger(__name__)


class OpenRentScraper(BaseScraper):
    """Scraper for OpenRent.co.uk listings."""

    BASE_URL = "https://www.openrent.co.uk"

    @property
    def source(self) -> PropertySource:
        return PropertySource.OPENRENT

    async def scrape(
        self,
        *,
        min_price: int,
        max_price: int,
        min_bedrooms: int,
        max_bedrooms: int,
        area: str,
    ) -> list[Property]:
        """Scrape OpenRent for matching properties."""
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
                "scraped_openrent_page",
                url=str(context.request.url),
                properties_found=len(parsed),
            )

        crawler = BeautifulSoupCrawler(
            max_requests_per_crawl=1,
            storage_client=MemoryStorageClient(),
        )
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
        """Build the OpenRent search URL with filters."""
        # OpenRent URL format: /properties-to-rent/{area}?filters...
        area_slug = area.lower().replace(" ", "-")
        params = [
            f"prices_min={min_price}",
            f"prices_max={max_price}",
            f"bedrooms_min={min_bedrooms}",
            f"bedrooms_max={max_bedrooms}",
        ]
        return f"{self.BASE_URL}/properties-to-rent/{area_slug}?{'&'.join(params)}"

    def _parse_search_results(self, soup: "BeautifulSoup", base_url: str) -> list[Property]:  # type: ignore[name-defined]  # noqa: F821
        """Parse property listings from search results page.

        OpenRent embeds property data in JavaScript arrays on the page.
        We extract these arrays and correlate them with the listing links.
        """
        properties: list[Property] = []

        # Find all property links
        property_links = soup.find_all("a", href=re.compile(r"/property-to-rent/"))

        # Extract JavaScript data arrays from script tags
        js_data = self._extract_js_arrays(soup)

        # Map property IDs to their indices
        property_ids = js_data.get("PROPERTYIDS", [])
        prices = js_data.get("prices", [])
        bedrooms = js_data.get("bedrooms", [])
        latitudes = js_data.get("PROPERTYLISTLATITUDES", [])
        longitudes = js_data.get("PROPERTYLISTLONGITUDES", [])

        # Track seen IDs to avoid duplicates from multiple links to same property
        seen_ids: set[str] = set()

        for link in property_links:
            href = link.get("href", "")
            if not href:
                continue

            # Extract property ID from URL
            match = re.search(r"/(\d+)$", href)
            if not match:
                continue

            property_id = match.group(1)
            if property_id in seen_ids:
                continue
            seen_ids.add(property_id)

            # Find index in JS arrays
            try:
                idx = property_ids.index(int(property_id))
            except (ValueError, TypeError):
                # Property ID not in arrays, try to parse from HTML
                idx = -1

            # Extract data from JS arrays or parse from HTML
            price = prices[idx] if idx >= 0 and idx < len(prices) else None
            beds = bedrooms[idx] if idx >= 0 and idx < len(bedrooms) else None
            lat = latitudes[idx] if idx >= 0 and idx < len(latitudes) else None
            lon = longitudes[idx] if idx >= 0 and idx < len(longitudes) else None
            # Property model requires both or neither coordinate
            if lat is None or lon is None:
                lat, lon = None, None

            # Parse title/address from link text
            title, address, postcode = self._parse_link_text(link)

            # Try to get price from HTML if not in JS
            if price is None:
                price = self._extract_price_from_html(link)

            # Try to get bedrooms from HTML if not in JS
            if beds is None:
                beds = self._extract_bedrooms_from_html(link)

            if price is None or beds is None or not title:
                logger.debug(
                    "skipping_incomplete_property",
                    property_id=property_id,
                    has_price=price is not None,
                    has_beds=beds is not None,
                    has_title=bool(title),
                )
                continue

            full_url = urljoin(self.BASE_URL, href)

            try:
                prop = Property(
                    source=PropertySource.OPENRENT,
                    source_id=property_id,
                    url=HttpUrl(full_url),
                    title=title,
                    price_pcm=int(price),
                    bedrooms=int(beds),
                    address=address or title,
                    postcode=postcode,
                    latitude=float(lat) if lat else None,
                    longitude=float(lon) if lon else None,
                )
                properties.append(prop)
            except Exception as e:
                logger.warning(
                    "failed_to_create_property",
                    property_id=property_id,
                    error=str(e),
                )

        return properties

    def _extract_js_arrays(self, soup: "BeautifulSoup") -> dict[str, list[int | float]]:  # type: ignore[name-defined]  # noqa: F821
        """Extract JavaScript array variables from script tags."""
        data: dict[str, list[int | float]] = {}
        array_patterns = {
            "PROPERTYIDS": r"PROPERTYIDS\s*=\s*\[([\d,\s]+)\]",
            "prices": r"prices\s*=\s*\[([\d,\s]+)\]",
            "bedrooms": r"bedrooms\s*=\s*\[([\d,\s]+)\]",
            "PROPERTYLISTLATITUDES": r"PROPERTYLISTLATITUDES\s*=\s*\[([\d.,\s-]+)\]",
            "PROPERTYLISTLONGITUDES": r"PROPERTYLISTLONGITUDES\s*=\s*\[([\d.,\s-]+)\]",
        }

        for script in soup.find_all("script"):
            script_text = script.string or ""
            for name, pattern in array_patterns.items():
                if name in data:
                    continue
                match = re.search(pattern, script_text)
                if match:
                    values_str = match.group(1)
                    # Parse comma-separated values
                    values = []
                    for v in values_str.split(","):
                        v = v.strip()
                        if v:
                            try:
                                # Try int first, then float
                                if "." in v:
                                    values.append(float(v))
                                else:
                                    values.append(int(v))
                            except ValueError:
                                pass
                    data[name] = values

        return data

    def _parse_link_text(self, link) -> tuple[str, str | None, str | None]:  # type: ignore[no-untyped-def]
        """Extract title, address, and postcode from link element.

        Returns:
            Tuple of (title, address, postcode). Address and postcode may be None.
        """
        title = ""
        address = None
        postcode = None

        # First, try to find the title element by class (new OpenRent structure)
        title_elem = link.find("div", class_=re.compile(r"fw-medium.*text-primary.*fs-3"))
        if title_elem:
            title = title_elem.get_text(strip=True)
        else:
            # Also try image alt text which often has the title
            img = link.find("img", class_="propertyPic")
            if img and img.get("alt"):
                title = img.get("alt")

        # If still no title, fall back to parsing all text
        if not title:
            text_parts = [t.strip() for t in link.stripped_strings]

            for part in text_parts:
                # Skip price-like text
                if "£" in part or "per month" in part.lower() or "per week" in part.lower():
                    continue
                # Skip distance text (e.g., "0.05 km" or just "0.05" or "km")
                if re.match(r"^[\d.]+\s*(km|mi)?$", part.lower()):
                    continue
                if part.lower() in ("km", "mi"):
                    continue
                # Skip standalone feature text (beds, baths, etc) - but NOT full titles
                if re.match(r"^\d+\s+(Bed|Bath)s?$", part):
                    continue
                if part in ("Furnished", "Unfurnished", "Part-Furnished"):
                    continue
                # Skip UI text
                if part in ("View Details", "View Property"):
                    continue
                # Skip short text that's likely not a title
                if len(part) < 5:
                    continue

                # This is likely the title/address (e.g., "1 Bed Flat, Mare Street, E8 3RH")
                if not title:
                    title = part
                    break

        # Extract postcode from title
        if title:
            postcode_match = re.search(
                r"\b([A-Z]{1,2}\d{1,2}[A-Z]?\s*\d?[A-Z]{0,2})\b",
                title.upper(),
            )
            if postcode_match:
                postcode = postcode_match.group(1)
                address = title

        return title, address, postcode

    def _extract_price_from_html(self, link) -> int | None:  # type: ignore[no-untyped-def]
        """Try to extract price from HTML element text."""
        for text in link.stripped_strings:
            if "£" in text and "per month" in text.lower():
                # Extract number from "£2,300 per month"
                match = re.search(r"£([\d,]+)", text)
                if match:
                    return int(match.group(1).replace(",", ""))
        return None

    def _extract_bedrooms_from_html(self, link) -> int | None:  # type: ignore[no-untyped-def]
        """Try to extract bedroom count from HTML element text."""
        for text in link.stripped_strings:
            # Match "1 Bed" or "2 Beds"
            match = re.match(r"^(\d+)\s+Bed", text)
            if match:
                return int(match.group(1))
            # Also check title like "1 Bed Flat, ..."
            match = re.search(r"(\d+)\s+Bed\s+(?:Flat|House|Apartment|Room)", text)
            if match:
                return int(match.group(1))
        return None

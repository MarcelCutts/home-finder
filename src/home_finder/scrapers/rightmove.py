"""Rightmove property scraper."""

import asyncio
import re
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup, Tag
from crawlee.crawlers import BeautifulSoupCrawler, BeautifulSoupCrawlingContext
from crawlee.storage_clients import MemoryStorageClient
from pydantic import HttpUrl

from home_finder.data.location_mappings import RIGHTMOVE_LOCATIONS, RIGHTMOVE_OUTCODES
from home_finder.logging import get_logger
from home_finder.models import FurnishType, Property, PropertySource
from home_finder.scrapers.base import BaseScraper
from home_finder.scrapers.parsing import extract_bedrooms, extract_postcode, extract_price
from home_finder.utils.address import is_outcode

logger = get_logger(__name__)

# Cache for discovered outcode identifiers
_outcode_cache: dict[str, str] = {}


async def get_rightmove_outcode_id(outcode: str) -> str | None:
    """Look up Rightmove location identifier for an outcode via typeahead API.

    Args:
        outcode: UK postcode outcode (e.g., "E8", "N15").

    Returns:
        Rightmove location identifier (e.g., "OUTCODE^707") or None if not found.
    """
    outcode = outcode.upper()

    if outcode in _outcode_cache:
        return _outcode_cache[outcode]

    # Tokenize: split into 2-char chunks
    tokens = [outcode[i : i + 2] for i in range(0, len(outcode), 2)]
    tokenized = "/".join(tokens) + "/"

    url = f"https://www.rightmove.co.uk/typeAhead/uknostreet/{tokenized}"

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                for loc in data.get("typeAheadLocations", []):
                    name = loc.get("displayName", "").upper()
                    # Match exact outcode or outcode followed by comma/space
                    if (
                        name == outcode
                        or name.startswith(f"{outcode},")
                        or name.startswith(f"{outcode} ")
                    ):
                        identifier = loc.get("locationIdentifier")
                        if isinstance(identifier, str):
                            _outcode_cache[outcode] = identifier
                            logger.debug(
                                "rightmove_outcode_resolved",
                                outcode=outcode,
                                identifier=identifier,
                            )
                            return identifier
    except Exception as e:
        logger.warning("rightmove_outcode_lookup_error", outcode=outcode, error=str(e))

    return None


class RightmoveScraper(BaseScraper):
    """Scraper for Rightmove.co.uk listings."""

    BASE_URL = "https://www.rightmove.co.uk"

    @property
    def source(self) -> PropertySource:
        return PropertySource.RIGHTMOVE

    # Pagination constants
    RESULTS_PER_PAGE = 24
    MAX_PAGES = 20
    PAGE_DELAY_SECONDS = 2.0

    async def scrape(
        self,
        *,
        min_price: int,
        max_price: int,
        min_bedrooms: int,
        max_bedrooms: int,
        area: str,
        furnish_types: tuple[FurnishType, ...] = (),
        min_bathrooms: int = 0,
        include_let_agreed: bool = True,
        max_results: int | None = None,
        known_source_ids: set[str] | None = None,
    ) -> list[Property]:
        """Scrape Rightmove for matching properties (all pages)."""
        search_url = await self._build_search_url(
            area=area,
            min_price=min_price,
            max_price=max_price,
            min_bedrooms=min_bedrooms,
            max_bedrooms=max_bedrooms,
            furnish_types=furnish_types,
            min_bathrooms=min_bathrooms,
            include_let_agreed=include_let_agreed,
        )
        if not search_url:
            return []

        async def fetch_page(page_idx: int) -> list[Property]:
            index = page_idx * self.RESULTS_PER_PAGE
            url = f"{search_url}&index={index}" if page_idx > 0 else search_url

            page_properties: list[Property] = []

            async def handle_page(
                context: BeautifulSoupCrawlingContext,
                _props: list[Property] = page_properties,
            ) -> None:
                soup = context.soup
                parsed = self._parse_search_results(soup, str(context.request.url))
                _props.extend(parsed)

            crawler = BeautifulSoupCrawler(
                max_requests_per_crawl=1,
                storage_client=MemoryStorageClient(),
            )
            crawler.router.default_handler(handle_page)
            await crawler.run([url])

            logger.info(
                "scraped_rightmove_page",
                url=url,
                page=page_idx + 1,
                properties_found=len(page_properties),
            )
            return page_properties

        async def delay() -> None:
            await asyncio.sleep(self.PAGE_DELAY_SECONDS)

        properties = await self._paginate(
            fetch_page,
            max_pages=self.MAX_PAGES,
            known_source_ids=known_source_ids,
            max_results=max_results,
            page_delay=delay,
        )

        logger.info(
            "scraped_rightmove_complete",
            area=area,
            total_properties=len(properties),
        )

        return properties

    async def _build_search_url(
        self,
        *,
        area: str,
        min_price: int,
        max_price: int,
        min_bedrooms: int,
        max_bedrooms: int,
        furnish_types: tuple[FurnishType, ...] = (),
        min_bathrooms: int = 0,
        include_let_agreed: bool = True,
    ) -> str:
        """Build the Rightmove search URL with filters.

        Supports both borough names (e.g., "hackney") and postcodes (e.g., "E8").
        """
        area_key = area.lower().replace("_", "-").replace(" ", "-")
        area_upper = area.upper()

        # Check if it's an outcode (postcode)
        if is_outcode(area):
            # First try hardcoded mapping (most reliable)
            location_id = RIGHTMOVE_OUTCODES.get(area_upper)
            if not location_id:
                # Try API lookup for unknown outcodes
                api_id = await get_rightmove_outcode_id(area)
                if api_id:
                    # URL encoding: ^ becomes %5E
                    location_id = api_id.replace("^", "%5E")
                else:
                    logger.error("rightmove_outcode_not_found", outcode=area)
                    return ""
        else:
            # Borough lookup
            location_id = RIGHTMOVE_LOCATIONS.get(area_key)
            if not location_id:
                logger.error("rightmove_borough_not_found", area=area_key)
                return ""

        params = [
            f"locationIdentifier={location_id}",
            f"maxBedrooms={max_bedrooms}",
            f"minPrice={min_price}",
            f"maxPrice={max_price}",
            "dontShow=houseShare",
            "letType=longTerm",
            "sortType=6",
        ]

        if min_bedrooms > 0:
            params.append(f"minBedrooms={min_bedrooms}")

        if furnish_types:
            rm_values = {
                FurnishType.FURNISHED: "furnished",
                FurnishType.UNFURNISHED: "unfurnished",
                FurnishType.PART_FURNISHED: "partFurnished",
            }
            ft_str = ",".join(rm_values[ft] for ft in furnish_types if ft in rm_values)
            if ft_str:
                params.append(f"furnishTypes={ft_str}")

        if min_bathrooms > 0:
            params.append(f"minBathrooms={min_bathrooms}")

        if not include_let_agreed:
            params.append("includeLetAgreed=false")

        return f"{self.BASE_URL}/property-to-rent/find.html?{'&'.join(params)}"

    def _parse_search_results(self, soup: BeautifulSoup, base_url: str) -> list[Property]:
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

    def _parse_property_card(self, card: Tag) -> Property | None:
        """Parse a single property card element."""
        id_and_url = self._extract_id_and_url(card)
        if id_and_url is None:
            return None
        property_id, full_url = id_and_url

        address = self._extract_address(card)
        if not address:
            return None

        info_elem = card.find(attrs={"data-testid": "property-information"})
        title = self._extract_title(card, info_elem, address)

        bedrooms = self._extract_bedrooms(card, info_elem, title)
        if bedrooms is None:
            return None

        price = self._extract_price(card)
        if price is None:
            return None

        image_url = self._extract_image_url(card)

        return Property(
            source=PropertySource.RIGHTMOVE,
            source_id=property_id,
            url=HttpUrl(full_url),
            title=title,
            price_pcm=price,
            bedrooms=bedrooms,
            address=address,
            postcode=extract_postcode(address),
            image_url=HttpUrl(image_url) if image_url else None,
        )

    def _extract_id_and_url(self, card: Tag) -> tuple[str, str] | None:
        """Extract property ID and full URL from a card element."""
        card_testid = card.get("data-testid", "")
        card_id = card.get("id", "")

        property_id = None
        if isinstance(card_testid, str) and card_testid.startswith("propertyCard-"):
            # New format: data-testid="propertyCard-0" - need to get ID from link
            pass
        elif isinstance(card_id, str) and card_id:
            property_id = card_id.replace("property-", "")

        # Try to extract from link - look for any anchor with /properties/ in href
        link = card.find("a", href=re.compile(r"/properties/\d+"))
        if not link:
            link = card.find("a", class_="propertyCard-link")
        if not link:
            link = card.find("a", class_=re.compile(r"propertyCard"))
        if not link:
            all_links = card.find_all("a", href=True)
            for a in all_links:
                a_href = a.get("href", "")
                if isinstance(a_href, str) and "/properties/" in a_href:
                    link = a
                    break

        if link:
            href = link.get("href", "")
            if not isinstance(href, str):
                href = ""
            if not property_id and href:
                property_id = self._extract_property_id(href)
        else:
            href = ""

        if not property_id or not href:
            return None

        full_url = urljoin(self.BASE_URL, href.split("#")[0])
        return property_id, full_url

    def _extract_address(self, card: Tag) -> str | None:
        """Extract address text from a card element."""
        address_elem = card.find(attrs={"data-testid": "property-address"})
        if address_elem:
            inner_address = address_elem.find("address")
            if inner_address:
                address = inner_address.get_text(strip=True)
            else:
                address = address_elem.get_text(strip=True)
        else:
            address_elem = card.find("address", class_="propertyCard-address")
            if not address_elem:
                address_elem = card.find("address")
            address = address_elem.get_text(strip=True) if address_elem else ""

        return address or None

    def _extract_title(self, card: Tag, info_elem: Tag | None, address: str) -> str:
        """Extract or build title from a card element."""
        property_type = ""
        if info_elem:
            type_span = info_elem.find("span", class_=re.compile(r"propertyType", re.I))
            if type_span:
                property_type = type_span.get_text(strip=True)

        if property_type:
            return f"{property_type}, {address}"

        title_elem = card.find("h2", class_="propertyCard-title")
        if not title_elem:
            title_elem = card.find("h2")
        return title_elem.get_text(strip=True) if title_elem else address

    def _extract_bedrooms(self, card: Tag, info_elem: Tag | None, title: str) -> int | None:
        """Extract bedroom count from a card element."""
        if info_elem:
            bedrooms_span = info_elem.find("span", class_=re.compile(r"bedroomsCount", re.I))
            if bedrooms_span:
                bedrooms_text = bedrooms_span.get_text(strip=True)
                if bedrooms_text.isdigit():
                    return int(bedrooms_text)

        bedrooms = extract_bedrooms(title)
        if bedrooms is not None:
            return bedrooms

        # Try from property details lozenge (old structure)
        details_elem = card.find(attrs={"data-testid": "property-details-lozenge"})
        if details_elem:
            details_text = details_elem.get_text(strip=True).lower()
            match = re.search(r"(\d+)\s*beds?", details_text)
            if match:
                return int(match.group(1))

        # Try from tag list (old structure)
        tags = card.find_all("li", class_="propertyCard-tag")
        for tag in tags:
            tag_text = tag.get_text(strip=True).lower()
            match = re.match(r"(\d+)\s*beds?", tag_text)
            if match:
                return int(match.group(1))

        return None

    def _extract_price(self, card: Tag) -> int | None:
        """Extract monthly price from a card element."""
        price_elem = card.find(attrs={"data-testid": "property-price"})
        price_text = ""
        if price_elem:
            price_div = price_elem.find("div", class_=re.compile(r"PropertyPrice_price__"))
            if price_div:
                price_text = price_div.get_text(strip=True)
            else:
                price_text = price_elem.get_text(strip=True)
        else:
            price_elem = card.find("div", class_="propertyCard-priceValue")
            if not price_elem:
                price_elem = card.find(class_=re.compile(r"[Pp]rice"))
            price_text = price_elem.get_text(strip=True) if price_elem else ""

        return extract_price(price_text)

    def _extract_image_url(self, card: Tag) -> str | None:
        """Extract image URL from a card element, preferring lazy-loaded sources."""
        img = card.find("img")
        if not img:
            return None

        image_url: str | None = None
        for attr in ("data-src", "srcset", "src"):
            raw = img.get(attr)
            if isinstance(raw, str) and raw.strip():
                # srcset format: "url 1x, url2 2x" — take first URL
                candidate = raw.split(",")[0].strip().split(" ")[0]
                if candidate:
                    image_url = candidate
                    break

        if image_url and not image_url.startswith("http"):
            image_url = f"https:{image_url}"
        return image_url

    def _extract_property_id(self, url: str) -> str | None:
        """Extract property ID from URL."""
        match = re.search(r"/properties/(\d+)", url)
        return match.group(1) if match else None

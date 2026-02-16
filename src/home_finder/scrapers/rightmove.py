"""Rightmove property scraper."""

import asyncio
import re
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup, Tag
from crawlee.crawlers import BeautifulSoupCrawler, BeautifulSoupCrawlingContext
from crawlee.storage_clients import MemoryStorageClient
from pydantic import HttpUrl

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


# Mapping of London borough names to Rightmove REGION identifiers
# Source: https://github.com/BrandonLow96/Rightmove-scrapping
# These identifiers are used in the locationIdentifier URL parameter
RIGHTMOVE_LOCATIONS = {
    # Central London
    "city-of-london": "REGION%5E61224",
    "westminster": "REGION%5E93980",
    "camden": "REGION%5E93941",
    "islington": "REGION%5E93965",
    # East London
    "hackney": "REGION%5E93953",
    "tower-hamlets": "REGION%5E61417",
    "tower hamlets": "REGION%5E61417",
    "newham": "REGION%5E61231",
    "waltham-forest": "REGION%5E61232",
    "waltham forest": "REGION%5E61232",
    "barking-dagenham": "REGION%5E61400",
    "barking and dagenham": "REGION%5E61400",
    "havering": "REGION%5E61228",
    "redbridge": "REGION%5E61537",
    # North London
    "haringey": "REGION%5E61227",
    "enfield": "REGION%5E93950",
    "barnet": "REGION%5E93929",
    # West London
    "kensington-chelsea": "REGION%5E61229",
    "kensington and chelsea": "REGION%5E61229",
    "hammersmith-fulham": "REGION%5E61407",
    "hammersmith and fulham": "REGION%5E61407",
    "brent": "REGION%5E93935",
    "ealing": "REGION%5E93947",
    "hounslow": "REGION%5E93962",
    "hillingdon": "REGION%5E93959",
    "harrow": "REGION%5E93956",
    # South London
    "lambeth": "REGION%5E93971",
    "southwark": "REGION%5E61518",
    "lewisham": "REGION%5E61413",
    "greenwich": "REGION%5E61226",
    "bromley": "REGION%5E93938",
    "bexley": "REGION%5E93932",
    "croydon": "REGION%5E93944",
    "sutton": "REGION%5E93974",
    "merton": "REGION%5E61414",
    "wandsworth": "REGION%5E93977",
    "kingston-thames": "REGION%5E93968",
    "kingston upon thames": "REGION%5E93968",
    "richmond-thames": "REGION%5E61415",
    "richmond upon thames": "REGION%5E61415",
}

# Mapping of UK outcodes to Rightmove OUTCODE identifiers
# These are pre-discovered identifiers (the typeahead API is unreliable)
RIGHTMOVE_OUTCODES = {
    # East London
    "E1": "OUTCODE%5E743",
    "E2": "OUTCODE%5E755",
    "E3": "OUTCODE%5E756",  # Bow
    "E4": "OUTCODE%5E757",
    "E5": "OUTCODE%5E758",  # Clapton
    "E6": "OUTCODE%5E759",
    "E7": "OUTCODE%5E760",
    "E8": "OUTCODE%5E762",  # Hackney Central, Dalston
    "E9": "OUTCODE%5E763",  # Hackney Wick, Homerton
    "E10": "OUTCODE%5E745",  # Leyton
    "E11": "OUTCODE%5E746",
    "E14": "OUTCODE%5E749",
    "E15": "OUTCODE%5E750",
    "E17": "OUTCODE%5E752",
    # North London
    "N1": "OUTCODE%5E1666",
    "N4": "OUTCODE%5E1682",
    "N5": "OUTCODE%5E1683",
    "N7": "OUTCODE%5E1685",
    "N8": "OUTCODE%5E1686",
    "N15": "OUTCODE%5E1672",  # South Tottenham
    "N16": "OUTCODE%5E1673",
    "N17": "OUTCODE%5E1674",
}


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
        properties: list[Property] = []
        seen_ids: set[str] = set()

        base_url = await self._build_search_url(
            area=area,
            min_price=min_price,
            max_price=max_price,
            min_bedrooms=min_bedrooms,
            max_bedrooms=max_bedrooms,
            furnish_types=furnish_types,
            min_bathrooms=min_bathrooms,
            include_let_agreed=include_let_agreed,
        )
        if not base_url:
            return []

        for page in range(self.MAX_PAGES):
            index = page * self.RESULTS_PER_PAGE
            url = f"{base_url}&index={index}" if page > 0 else base_url

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
                page=page + 1,
                properties_found=len(page_properties),
            )

            if not page_properties:
                break

            # Early-stop: all results on this page are already in DB
            if known_source_ids is not None and all(
                p.source_id in known_source_ids for p in page_properties
            ):
                logger.info(
                    "early_stop_all_known",
                    source=self.source.value,
                    area=area,
                    page=page + 1,
                )
                break

            # Deduplicate within run (Rightmove can return overlapping results)
            new_properties = [p for p in page_properties if p.source_id not in seen_ids]
            for p in new_properties:
                seen_ids.add(p.source_id)

            if not new_properties:
                break

            properties.extend(new_properties)

            if max_results is not None and len(properties) >= max_results:
                properties = properties[:max_results]
                break

            # Be polite - delay between pages
            if page < self.MAX_PAGES - 1:
                await asyncio.sleep(self.PAGE_DELAY_SECONDS)

        logger.info(
            "scraped_rightmove_complete",
            area=area,
            total_properties=len(properties),
            pages_scraped=min(page + 1, self.MAX_PAGES),
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
        # Extract property ID from card data-testid or id attribute
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
            # Try class-based search
            link = card.find("a", class_="propertyCard-link")
        if not link:
            # Try new structure with propertyCard in class name
            link = card.find("a", class_=re.compile(r"propertyCard"))
        if not link:
            # Just find any anchor tag
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
            bedrooms = extract_bedrooms(title)

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

        price = extract_price(price_text)
        if price is None:
            return None

        # Extract postcode
        postcode = extract_postcode(address)

        # Extract image URL — prefer lazy-loaded real image over placeholder
        img = card.find("img")
        image_url: str | None = None
        if img:
            # Try data-src first (lazy-loaded), then srcset, then src
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

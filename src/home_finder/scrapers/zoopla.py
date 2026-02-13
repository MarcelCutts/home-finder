"""Zoopla property scraper using curl_cffi for TLS fingerprint impersonation."""

from __future__ import annotations

import asyncio
import json
import re
import urllib.parse
from contextlib import suppress
from typing import Any

from bs4 import BeautifulSoup, Tag
from curl_cffi.requests import AsyncSession
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, TypeAdapter, ValidationError

from home_finder.logging import get_logger
from home_finder.models import FurnishType, Property, PropertySource
from home_finder.scrapers.base import BaseScraper
from home_finder.scrapers.constants import BROWSER_HEADERS
from home_finder.scrapers.parsing import extract_bedrooms, extract_postcode, extract_price
from home_finder.utils.address import is_outcode

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Pydantic models for Zoopla JSON data structures
# ---------------------------------------------------------------------------


class ZooplaListingUris(BaseModel):
    """URIs associated with a Zoopla listing."""

    model_config = ConfigDict(extra="ignore")

    detail: str = ""


class ZooplaFeature(BaseModel):
    """A feature of a Zoopla listing (e.g., bedrooms, bathrooms)."""

    model_config = ConfigDict(extra="ignore")

    icon_id: str = Field(default="", validation_alias="iconId")
    content: int | str | None = None


class ZooplaImage(BaseModel):
    """Image data for a Zoopla listing."""

    model_config = ConfigDict(extra="ignore")

    src: str = ""


class ZooplaPosition(BaseModel):
    """Geographic position of a listing."""

    model_config = ConfigDict(extra="ignore")

    lat: float | None = None
    lng: float | None = None


class ZooplaListing(BaseModel):
    """A single property listing from Zoopla's JSON data.

    Handles both RSC format and traditional Next.js format.
    """

    model_config = ConfigDict(extra="ignore")

    # Required fields
    listing_id: int = Field(validation_alias="listingId")

    # URL fields - RSC uses listingUris, older format uses detailUrl
    listing_uris: ZooplaListingUris | None = Field(default=None, validation_alias="listingUris")
    detail_url: str | None = Field(default=None, validation_alias="detailUrl")

    # Price fields - prefer unformatted, fall back to formatted
    # Note: priceUnformatted can be float for weekly prices (e.g., 357.69 pw)
    price_unformatted: float | int | None = Field(default=None, validation_alias="priceUnformatted")
    price: str = ""

    # Features - can be list (RSC) or dict (older format)
    features: list[ZooplaFeature] | dict[str, Any] = Field(default_factory=list)

    # Property details
    title: str = ""
    address: str = ""

    # Image - can be object or None
    image: ZooplaImage | None = None

    # Position
    pos: ZooplaPosition | None = None

    def get_detail_url(self) -> str | None:
        """Get the detail URL from either format."""
        if self.listing_uris and self.listing_uris.detail:
            return self.listing_uris.detail
        return self.detail_url

    def get_price_pcm(self) -> int | None:
        """Extract monthly price from available price fields."""
        if self.price_unformatted is not None:
            # price_unformatted can be float for weekly prices
            price = int(self.price_unformatted)
            # Check if it's a weekly price (needs conversion to monthly)
            if "pw" in self.price.lower():
                price = int(price * 52 / 12)
            return price

        if not self.price:
            return None

        # Parse formatted price string like "£1,900 pcm"
        match = re.search(r"£([\d,]+)", self.price)
        if not match:
            return None

        price = int(match.group(1).replace(",", ""))

        # Convert weekly to monthly if needed
        if "pw" in self.price.lower():
            price = int(price * 52 / 12)

        return price

    def get_bedrooms(self) -> int | None:
        """Extract bedroom count from features or title."""
        # Try RSC format: features as list of {iconId, content}
        if isinstance(self.features, list):
            for feature in self.features:
                if feature.icon_id == "bed" and feature.content is not None:
                    if isinstance(feature.content, int):
                        return feature.content
                    if isinstance(feature.content, str) and feature.content.isdigit():
                        return int(feature.content)

        # Try older dict format: features.beds
        if isinstance(self.features, dict):
            beds = self.features.get("beds")
            if isinstance(beds, int):
                return beds

        # Fallback: parse from title
        return self._extract_bedrooms_from_text(self.title)

    def get_image_url(self) -> str | None:
        """Get the image URL, ensuring it has a protocol."""
        if not self.image or not self.image.src:
            return None

        url = self.image.src
        if not url.startswith("http"):
            url = f"https:{url}"
        return url

    def get_address(self) -> str:
        """Get address, falling back to title if empty."""
        return self.address or self.title

    def get_title(self) -> str:
        """Get title, falling back to address if empty."""
        return self.title or self.address

    @staticmethod
    def _extract_bedrooms_from_text(text: str) -> int | None:
        """Extract bedroom count from text like '2 bed flat'."""
        if not text:
            return None

        text_lower = text.lower()

        # Handle studio
        if "studio" in text_lower:
            return 0

        # Match "1 bed", "2 bedroom", etc.
        match = re.search(r"(\d+)\s*bed(?:room)?s?", text_lower)
        return int(match.group(1)) if match else None


# TypeAdapter for parsing lists of listings directly (for RSC format)
ZooplaListingsAdapter = TypeAdapter(list[ZooplaListing])

# Known London borough slugs and their Zoopla query values.
# Using the borough path format gives proper geographic boundaries.
# Maps: slug -> (path_segment, q_param)
BOROUGH_AREAS: dict[str, tuple[str, str]] = {
    "hackney": ("hackney-london-borough", "Hackney (London Borough), London"),
    "islington": ("islington-london-borough", "Islington (London Borough), London"),
    "tower-hamlets": ("tower-hamlets-london-borough", "Tower Hamlets (London Borough), London"),
    "camden": ("camden-london-borough", "Camden (London Borough), London"),
    "lambeth": ("lambeth-london-borough", "Lambeth (London Borough), London"),
    "southwark": ("southwark-london-borough", "Southwark (London Borough), London"),
    "haringey": ("haringey-london-borough", "Haringey (London Borough), London"),
    "lewisham": ("lewisham-london-borough", "Lewisham (London Borough), London"),
    "newham": ("newham-london-borough", "Newham (London Borough), London"),
    "waltham-forest": (
        "waltham-forest-london-borough",
        "Waltham Forest (London Borough), London",
    ),
    "greenwich": ("greenwich-london-borough", "Greenwich (London Borough), London"),
    "barnet": ("barnet-london-borough", "Barnet (London Borough), London"),
    "brent": ("brent-london-borough", "Brent (London Borough), London"),
    "ealing": ("ealing-london-borough", "Ealing (London Borough), London"),
    "enfield": ("enfield-london-borough", "Enfield (London Borough), London"),
    "westminster": (
        "city-of-westminster-london-borough",
        "City of Westminster (London Borough), London",
    ),
    "kensington": (
        "kensington-and-chelsea-london-borough",
        "Kensington and Chelsea (London Borough), London",
    ),
    "hammersmith": (
        "hammersmith-and-fulham-london-borough",
        "Hammersmith and Fulham (London Borough), London",
    ),
    "wandsworth": ("wandsworth-london-borough", "Wandsworth (London Borough), London"),
}


_SHARED_ACCOMMODATION_PATTERNS = re.compile(
    r"flat\s*share|house\s*share|room\s+(?:in|to)\s+rent|shared\s+(?:flat|house|accommodation)",
    re.IGNORECASE,
)


class ZooplaScraper(BaseScraper):
    """Scraper for Zoopla.co.uk listings using curl_cffi."""

    BASE_URL = "https://www.zoopla.co.uk"

    # Pagination constants
    MAX_PAGES = 20
    PAGE_DELAY_SECONDS = 0.5

    def __init__(self, *, proxy_url: str = "") -> None:
        self._session: AsyncSession | None = None  # type: ignore[type-arg]
        self._proxy_url = proxy_url

    async def _get_session(self) -> AsyncSession:  # type: ignore[type-arg]
        """Get or create a reusable curl_cffi session."""
        if self._session is None:
            self._session = AsyncSession()
        return self._session

    async def close(self) -> None:
        """Close the curl_cffi session."""
        if self._session is not None:
            await self._session.close()
            self._session = None

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
        furnish_types: tuple[FurnishType, ...] = (),
        min_bathrooms: int = 0,
        include_let_agreed: bool = True,
        max_results: int | None = None,
        known_source_ids: set[str] | None = None,
    ) -> list[Property]:
        """Scrape Zoopla for matching properties (all pages)."""
        all_properties: list[Property] = []
        seen_ids: set[str] = set()

        for page in range(1, self.MAX_PAGES + 1):
            url = self._build_search_url(
                area=area,
                min_price=min_price,
                max_price=max_price,
                min_bedrooms=min_bedrooms,
                max_bedrooms=max_bedrooms,
                furnish_types=furnish_types,
                min_bathrooms=min_bathrooms,
                include_let_agreed=include_let_agreed,
                page=page,
            )

            html = await self._fetch_page(url)
            if not html:
                logger.warning("zoopla_fetch_failed", url=url, page=page)
                break

            # Try RSC extraction first (primary method)
            properties = self._parse_rsc_properties(html)
            method = "rsc"

            if not properties:
                # Fallback to HTML parsing
                soup = BeautifulSoup(html, "html.parser")
                properties = self._parse_search_results(soup, url)
                method = "html"

            logger.info(
                "scraped_zoopla_page",
                url=url,
                page=page,
                properties_found=len(properties),
                method=method,
            )

            if not properties:
                break

            # Early-stop: all results on this page are already in DB
            if known_source_ids is not None and all(
                p.source_id in known_source_ids for p in properties
            ):
                logger.info(
                    "early_stop_all_known",
                    source=self.source.value,
                    area=area,
                    page=page,
                )
                break

            # Deduplicate (Zoopla can return overlapping results)
            new_properties = [p for p in properties if p.source_id not in seen_ids]
            for p in new_properties:
                seen_ids.add(p.source_id)

            if not new_properties:
                break

            all_properties.extend(new_properties)

            if max_results is not None and len(all_properties) >= max_results:
                all_properties = all_properties[:max_results]
                break

            # Be polite - delay between pages
            if page < self.MAX_PAGES:
                await asyncio.sleep(self.PAGE_DELAY_SECONDS)

        logger.info(
            "scraped_zoopla_complete",
            area=area,
            total_properties=len(all_properties),
            pages_scraped=page,
        )

        return all_properties

    async def _fetch_page(self, url: str) -> str | None:
        """Fetch page using curl_cffi with Chrome impersonation."""
        session = await self._get_session()
        kwargs: dict[str, object] = {
            "impersonate": "chrome",
            "headers": BROWSER_HEADERS,
            "timeout": 30,
        }
        if self._proxy_url:
            kwargs["proxy"] = self._proxy_url

        for attempt in range(3):
            try:
                response = await session.get(url, **kwargs)  # type: ignore[arg-type]
                if response.status_code == 200:
                    text: str = response.text
                    return text
                if response.status_code == 429:
                    delay = 2.0 * (2**attempt)
                    logger.debug(
                        "zoopla_rate_limited",
                        url=url,
                        attempt=attempt + 1,
                        delay=delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                logger.warning(
                    "zoopla_http_error",
                    status=response.status_code,
                    url=url,
                )
                return None
            except Exception as e:
                logger.error("zoopla_fetch_exception", error=str(e), url=url)
                return None

        return None

    def _extract_rsc_listings(self, html: str) -> list[ZooplaListing]:
        """Extract listing data from React Server Components payload.

        The RSC format is: self.__next_f.push([1, "id:json_content"])
        The second element is a string containing an RSC line ID followed by
        a colon and then JSON content.
        """
        pattern = r"self\.__next_f\.push\(\s*\[(.*?)\]\s*\)"
        matches = re.findall(pattern, html, re.DOTALL)
        listings: list[ZooplaListing] = []

        for match in matches:
            if "regularListingsFormatted" not in match and "listingId" not in match:
                continue

            # Parse the push call arguments as a JSON array: [1, "id:content"]
            try:
                arr = json.loads(f"[{match}]")
            except json.JSONDecodeError:
                continue

            if len(arr) < 2 or not isinstance(arr[1], str):
                continue

            payload = arr[1]

            # Split on first colon to separate RSC line ID from content
            colon_idx = payload.find(":")
            if colon_idx < 0:
                continue

            rsc_content = payload[colon_idx + 1 :]

            # The RSC content is a JSON array like ["$","$L7a",null,{props}]
            try:
                parsed = json.loads(rsc_content)
            except json.JSONDecodeError:
                continue

            found = self._extract_listings_from_parsed(parsed)
            listings.extend(found)

        # Deduplicate by listing_id
        seen: set[int] = set()
        unique: list[ZooplaListing] = []
        for listing in listings:
            if listing.listing_id not in seen:
                seen.add(listing.listing_id)
                unique.append(listing)

        return unique

    def _extract_listings_from_parsed(self, data: Any, depth: int = 0) -> list[ZooplaListing]:
        """Recursively search a parsed JSON structure for listing data."""
        if depth > 15:
            return []

        listings: list[ZooplaListing] = []

        if isinstance(data, dict):
            # Check for known container key
            if "regularListingsFormatted" in data:
                raw = data["regularListingsFormatted"]
                if isinstance(raw, list):
                    for item in raw:
                        if isinstance(item, dict) and "listingId" in item:
                            try:
                                listings.append(ZooplaListing.model_validate(item))
                            except ValidationError:
                                continue

            # Check if this dict is itself a listing
            if "listingId" in data and not listings:
                with suppress(ValidationError):
                    listings.append(ZooplaListing.model_validate(data))

            # Recurse into values
            if not listings:
                for value in data.values():
                    if isinstance(value, (dict, list)):
                        listings.extend(self._extract_listings_from_parsed(value, depth + 1))

        elif isinstance(data, list):
            for item in data:
                listings.extend(self._extract_listings_from_parsed(item, depth + 1))

        return listings

    def _parse_rsc_properties(self, html: str) -> list[Property]:
        """Parse properties from RSC payload in HTML."""
        listings = self._extract_rsc_listings(html)
        properties: list[Property] = []
        for listing in listings:
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

        # Skip shared accommodation listings
        if _SHARED_ACCOMMODATION_PATTERNS.search(title):
            return None

        # Extract postcode from address
        postcode = extract_postcode(address)

        # Get image URL
        image_url = listing.get_image_url()

        # Get coordinates (Property model requires both or neither)
        latitude = listing.pos.lat if listing.pos else None
        longitude = listing.pos.lng if listing.pos else None
        if latitude is None or longitude is None:
            latitude, longitude = None, None

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

    def _build_search_url(
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
        page: int = 1,
    ) -> str:
        """Build the Zoopla search URL with filters.

        Supports three area formats:
        - Known borough slug (e.g. "hackney") -> /to-rent/property/hackney-london-borough/
        - Outcode (e.g. "E8", "N17") -> /to-rent/property/e8/
        - Anything else -> /to-rent/property/{area}/
        """
        area_lower = area.lower().strip()

        if area_lower in BOROUGH_AREAS:
            path_seg, q_val = BOROUGH_AREAS[area_lower]
        elif is_outcode(area):
            path_seg = area_lower
            q_val = area.upper()
        else:
            path_seg = area_lower.replace(" ", "-")
            q_val = area

        params: dict[str, str] = {
            "q": q_val,
            "beds_max": str(max_bedrooms),
            "price_min": str(min_price),
            "price_max": str(max_price),
            "price_frequency": "per_month",
            "property_sub_type": "flats",
            "is_shared_accommodation": "false",
            "is_retirement_home": "false",
            "is_student_accommodation": "false",
            "results_sort": "newest_listings",
            "search_source": "to-rent",
        }

        if min_bedrooms > 0:
            params["beds_min"] = str(min_bedrooms)

        # NOTE: furnished_state, bathrooms_min, and available_only are intentionally
        # NOT passed as URL params. Zoopla's furnished_state filter excludes listings
        # that lack furnishing metadata (the majority), returning "No results" even
        # for areas with hundreds of listings. These are filtered client-side instead.

        if page > 1:
            params["pn"] = str(page)

        return f"{self.BASE_URL}/to-rent/property/{path_seg}/?{urllib.parse.urlencode(params)}"

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
        price = extract_price(price_text)
        if price is None:
            return None

        # Extract title
        title_elem = card.find("h2", {"data-testid": "listing-title"})
        title = title_elem.get_text(strip=True) if title_elem else ""
        if not title:
            title_elem = card.find("h2")
            title = title_elem.get_text(strip=True) if title_elem else ""

        # Skip shared accommodation listings
        if _SHARED_ACCOMMODATION_PATTERNS.search(title):
            return None

        # Extract bedrooms from title
        bedrooms = extract_bedrooms(title)
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
        postcode = extract_postcode(address)

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

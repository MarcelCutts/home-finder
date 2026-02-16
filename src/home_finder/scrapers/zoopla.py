"""Zoopla property scraper using curl_cffi for TLS fingerprint impersonation."""

from __future__ import annotations

import asyncio
import json
import random
import re
import urllib.parse
from contextlib import suppress
from typing import Any

from bs4 import BeautifulSoup, Tag
from curl_cffi.requests import AsyncSession
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, TypeAdapter, ValidationError

from home_finder.data.location_mappings import BOROUGH_AREAS
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
        return extract_bedrooms(self.title)

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


# TypeAdapter for parsing lists of listings directly (for RSC format)
ZooplaListingsAdapter = TypeAdapter(list[ZooplaListing])

_SHARED_ACCOMMODATION_PATTERNS = re.compile(
    r"flat\s*share|house\s*share|room\s+(?:in|to)\s+rent|shared\s+(?:flat|house|accommodation)",
    re.IGNORECASE,
)


class ZooplaScraper(BaseScraper):
    """Scraper for Zoopla.co.uk listings using curl_cffi."""

    BASE_URL = "https://www.zoopla.co.uk"

    # Pagination constants
    MAX_PAGES = 20

    # Cloudflare challenge markers in response body
    _CF_CHALLENGE_MARKERS = (
        "Just a moment",
        "Cloudflare Ray ID",
        "cf-browser-verification",
        "_cf_chl_opt",
    )

    # Browser profiles to rotate between areas (avoids single-fingerprint detection)
    _IMPERSONATE_TARGETS = ("chrome", "safari", "chrome131", "safari184")

    def __init__(self, *, proxy_url: str = "", max_areas: int | None = None) -> None:
        self._session: AsyncSession | None = None  # type: ignore[type-arg]
        self._proxy_url = proxy_url
        self._max_areas = max_areas
        self._consecutive_blocks: int = 0
        self._warmed_up: bool = False

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

    async def _reset_session(self) -> None:
        """Close and discard the current session to get a fresh TLS fingerprint."""
        if self._session is not None:
            await self._session.close()
            self._session = None
        self._warmed_up = False
        logger.info("zoopla_session_reset")

    async def _page_delay(self) -> None:
        """Random delay between page fetches (human-like pacing)."""
        await asyncio.sleep(random.uniform(1.5, 3.5))

    async def area_delay(self) -> None:
        """Adaptive delay between area searches based on consecutive blocks."""
        if self._consecutive_blocks >= 3:
            delay = random.uniform(45.0, 75.0)
        elif self._consecutive_blocks >= 1:
            delay = random.uniform(20.0, 40.0)
        else:
            delay = random.uniform(10.0, 20.0)
        logger.info(
            "zoopla_area_delay",
            delay=f"{delay:.1f}s",
            consecutive_blocks=self._consecutive_blocks,
        )
        await asyncio.sleep(delay)

    @property
    def max_areas_per_run(self) -> int | None:
        return self._max_areas

    @property
    def should_skip_remaining_areas(self) -> bool:
        return self._consecutive_blocks >= 5

    async def _warm_up(self) -> None:
        """Visit the Zoopla homepage to establish cookies before searching."""
        if self._warmed_up:
            return
        try:
            session = await self._get_session()
            target = self._pick_impersonate_target()
            await session.get(
                f"{self.BASE_URL}/",
                impersonate=target,  # type: ignore[arg-type]
                headers=BROWSER_HEADERS,
                timeout=15,
            )
            self._warmed_up = True
            delay = random.uniform(2.0, 4.0)
            logger.info("zoopla_warmup_success", delay=f"{delay:.1f}s")
            await asyncio.sleep(delay)
        except Exception as e:
            logger.warning("zoopla_warmup_failed", error=str(e))
            self._warmed_up = True  # Don't retry on failure

    def _is_cloudflare_challenge(self, response: Any) -> bool:
        """Detect Cloudflare challenge pages.

        Matches 403/503 with challenge HTML, or cf-mitigated header.
        """
        if response.headers.get("cf-mitigated") == "challenge":
            return True
        if response.status_code in (403, 503):
            text = response.text[:2000]
            return any(marker in text for marker in self._CF_CHALLENGE_MARKERS)
        return False

    def _pick_impersonate_target(self) -> str:
        """Pick a random browser profile for TLS fingerprint rotation."""
        return random.choice(self._IMPERSONATE_TARGETS)

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
        # Establish cookies on first search of the session
        await self._warm_up()

        # Pick one browser profile per area (switching mid-session is suspicious)
        impersonate_target = self._pick_impersonate_target()

        async def fetch_page(page_idx: int) -> list[Property]:
            page = page_idx + 1  # Zoopla uses 1-based pages
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

            html = await self._fetch_page(url, impersonate_target=impersonate_target)
            if not html:
                logger.warning("zoopla_fetch_failed", url=url, page=page)
                return []

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
            return properties

        all_properties = await self._paginate(
            fetch_page,
            max_pages=self.MAX_PAGES,
            known_source_ids=known_source_ids,
            max_results=max_results,
            page_delay=self._page_delay,
        )

        logger.info(
            "scraped_zoopla_complete",
            area=area,
            total_properties=len(all_properties),
        )

        return all_properties

    async def _fetch_page(self, url: str, *, impersonate_target: str = "chrome") -> str | None:
        """Fetch page using curl_cffi with TLS impersonation and Cloudflare retry."""
        session = await self._get_session()
        kwargs: dict[str, object] = {
            "impersonate": impersonate_target,
            "headers": BROWSER_HEADERS,
            "timeout": 30,
        }
        if self._proxy_url:
            kwargs["proxy"] = self._proxy_url

        for attempt in range(4):
            try:
                response = await session.get(url, **kwargs)  # type: ignore[arg-type]

                if response.status_code == 200:
                    if not self._is_cloudflare_challenge(response):
                        self._consecutive_blocks = 0
                        text: str = response.text
                        return text
                    # Soft challenge (200 with challenge HTML) — treat as challenge
                    logger.warning("zoopla_soft_challenge", url=url, attempt=attempt + 1)

                if response.status_code == 429 or self._is_cloudflare_challenge(response):
                    delay = 2.0 * (2**attempt) + random.uniform(0, 2.0)
                    logger.info(
                        "zoopla_cf_backoff",
                        url=url,
                        status=response.status_code,
                        attempt=attempt + 1,
                        delay=f"{delay:.1f}s",
                    )
                    await asyncio.sleep(delay)
                    continue

                # Hard HTTP error (404, 500, etc.) — don't retry
                logger.warning("zoopla_http_error", status=response.status_code, url=url)
                return None

            except Exception as e:
                if attempt < 3:
                    delay = 2.0 * (2**attempt) + random.uniform(0, 1.0)
                    logger.warning(
                        "zoopla_fetch_exception_retrying",
                        error=str(e),
                        attempt=attempt + 1,
                        delay=f"{delay:.1f}s",
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error("zoopla_fetch_exception", error=str(e), url=url)
                    return None

        # All retries exhausted — this area was blocked
        self._consecutive_blocks += 1
        logger.warning(
            "zoopla_fetch_exhausted",
            url=url,
            consecutive_blocks=self._consecutive_blocks,
        )

        # Reset session after 2 consecutive blocks to get a fresh TLS fingerprint
        if self._consecutive_blocks == 2:
            await self._reset_session()

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

"""OnTheMarket property scraper using curl_cffi for TLS fingerprint impersonation."""

import asyncio
import json
import random
import re
from typing import Any

from curl_cffi.requests import AsyncSession
from pydantic import HttpUrl

from home_finder.logging import get_logger
from home_finder.models import FurnishType, Property, PropertySource
from home_finder.scrapers.base import BaseScraper, ScrapeResult
from home_finder.scrapers.constants import BROWSER_HEADERS
from home_finder.scrapers.parsing import extract_bedrooms, extract_postcode, extract_price

logger = get_logger(__name__)


class OnTheMarketScraper(BaseScraper):
    """Scraper for OnTheMarket.com listings using curl_cffi."""

    BASE_URL = "https://www.onthemarket.com"

    # Pagination constants
    MAX_PAGES = 20
    PAGE_DELAY_SECONDS = 0.5

    # Retry constants
    MAX_RETRIES = 4
    INITIAL_BACKOFF_SECONDS = 2.0
    MAX_BACKOFF_SECONDS = 30.0

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
        return PropertySource.ONTHEMARKET

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
    ) -> ScrapeResult:
        """Scrape OnTheMarket for matching properties (all pages)."""
        search_url = self._build_search_url(
            area=area,
            min_price=min_price,
            max_price=max_price,
            min_bedrooms=min_bedrooms,
            max_bedrooms=max_bedrooms,
            furnish_types=furnish_types,
            min_bathrooms=min_bathrooms,
            include_let_agreed=include_let_agreed,
        )
        parse_errors = 0

        async def fetch_page(page_idx: int) -> list[Property] | None:
            nonlocal parse_errors
            page = page_idx + 1  # OnTheMarket uses 1-based pages
            url = f"{search_url}&page={page}" if page > 1 else search_url

            html = await self._fetch_page(url)
            if html is None:
                return None  # Signals fetch failure to _paginate

            # Parse __NEXT_DATA__ JSON
            properties = self._parse_next_data(html)
            if properties is None:
                parse_errors += 1
                return []
            logger.info(
                "scraped_onthemarket_page",
                url=url,
                page=page,
                properties_found=len(properties),
            )
            return properties

        async def delay() -> None:
            await asyncio.sleep(self.PAGE_DELAY_SECONDS)

        result = await self._paginate(
            fetch_page,
            max_pages=self.MAX_PAGES,
            known_source_ids=known_source_ids,
            max_results=max_results,
            page_delay=delay,
        )
        result.parse_errors = parse_errors

        logger.info(
            "scraped_onthemarket_complete",
            area=area,
            total_properties=len(result.properties),
            pages_fetched=result.pages_fetched,
            pages_failed=result.pages_failed,
        )

        return result

    async def _fetch_page(self, url: str) -> str | None:
        """Fetch page using curl_cffi with Chrome impersonation and retry on 429/5xx."""
        session = await self._get_session()
        backoff = self.INITIAL_BACKOFF_SECONDS

        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                kwargs: dict[str, object] = {
                    "impersonate": "chrome",
                    "headers": BROWSER_HEADERS,
                    "timeout": 30,
                }
                if self._proxy_url:
                    kwargs["proxy"] = self._proxy_url
                response = await session.get(url, **kwargs)  # type: ignore[arg-type]

                if response.status_code == 200:
                    text: str = response.text
                    return text

                if response.status_code == 429 or response.status_code >= 500:
                    if attempt < self.MAX_RETRIES:
                        jitter = random.uniform(0, 1.0)
                        logger.warning(
                            "onthemarket_retry",
                            url=url,
                            status=response.status_code,
                            attempt=attempt,
                            backoff=round(backoff + jitter, 1),
                        )
                        await asyncio.sleep(backoff + jitter)
                        backoff = min(backoff * 2, self.MAX_BACKOFF_SECONDS)
                        continue
                    logger.warning(
                        "onthemarket_retries_exhausted",
                        url=url,
                        status=response.status_code,
                        attempts=self.MAX_RETRIES,
                    )
                    return None

                logger.warning(
                    "onthemarket_http_error",
                    status=response.status_code,
                    url=url,
                )
                return None
            except Exception as e:
                if attempt < self.MAX_RETRIES:
                    jitter = random.uniform(0, 1.0)
                    logger.warning(
                        "onthemarket_fetch_exception_retrying",
                        error=str(e),
                        url=url,
                        attempt=attempt,
                        backoff=round(backoff + jitter, 1),
                    )
                    await asyncio.sleep(backoff + jitter)
                    backoff = min(backoff * 2, self.MAX_BACKOFF_SECONDS)
                else:
                    logger.error(
                        "onthemarket_fetch_exception", error=str(e), url=url, exc_info=True
                    )
                    return None

        return None

    def _parse_next_data(self, html: str) -> list[Property] | None:
        """Parse properties from __NEXT_DATA__ JSON.

        Returns None on parse error (distinct from empty list meaning no listings).
        """
        match = re.search(
            r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>',
            html,
            re.DOTALL,
        )
        if not match:
            logger.warning("onthemarket_no_next_data")
            return None

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
            return None

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
        price = extract_price(price_text)
        if price is None:
            return None

        # Extract bedrooms
        bedrooms = listing.get("bedrooms")
        if bedrooms is None:
            title = listing.get("property-title", "")
            bedrooms = extract_bedrooms(title)
        if bedrooms is None:
            return None

        # Extract address and title
        address = listing.get("address", "")
        if not address:
            return None
        title = listing.get("property-title", address)

        # Extract postcode
        postcode = extract_postcode(address)

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
        furnish_types: tuple[FurnishType, ...] = (),
        min_bathrooms: int = 0,
        include_let_agreed: bool = True,
    ) -> str:
        """Build the OnTheMarket search URL with filters."""
        # Normalize area name
        area_slug = area.lower().replace(" ", "-")

        params = [
            f"max-bedrooms={max_bedrooms}",
            f"min-price={min_price}",
            f"max-price={max_price}",
            "price-per=pcm",
            "shared=false",
            "let-length=long-term",
            "sort-field=update_date",
        ]

        if min_bedrooms > 0:
            params.insert(0, f"min-bedrooms={min_bedrooms}")

        if furnish_types and len(furnish_types) == 1:
            otm_values = {
                FurnishType.FURNISHED: "furnished",
                FurnishType.UNFURNISHED: "unfurnished",
                FurnishType.PART_FURNISHED: "part-furnished",
            }
            ft = furnish_types[0]
            if ft in otm_values:
                params.append(f"furnished={otm_values[ft]}")

        if include_let_agreed:
            params.append("let-agreed=true")

        # OnTheMarket has no bathroom count filter

        return f"{self.BASE_URL}/to-rent/property/{area_slug}/?{'&'.join(params)}"

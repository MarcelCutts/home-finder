"""Detail page fetcher for extracting gallery and floorplan URLs."""

import json
import re
from dataclasses import dataclass

import httpx
from curl_cffi.requests import AsyncSession

from home_finder.logging import get_logger
from home_finder.models import Property, PropertySource

logger = get_logger(__name__)


@dataclass
class DetailPageData:
    """Data extracted from a property detail page."""

    floorplan_url: str | None = None
    gallery_urls: list[str] | None = None


class DetailFetcher:
    """Fetches property detail pages and extracts floorplan/gallery URLs."""

    def __init__(self, max_gallery_images: int = 10) -> None:
        """Initialize the detail fetcher.

        Args:
            max_gallery_images: Maximum number of gallery images to extract.
        """
        self._client: httpx.AsyncClient | None = None
        self._max_gallery_images = max_gallery_images

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=30.0,
                follow_redirects=True,
                headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                },
            )
        return self._client

    async def fetch_floorplan_url(self, prop: Property) -> str | None:
        """Fetch detail page and extract floorplan URL.

        Args:
            prop: Property to fetch floorplan for.

        Returns:
            Floorplan URL or None if not found.
        """
        data = await self.fetch_detail_page(prop)
        return data.floorplan_url if data else None

    async def fetch_detail_page(self, prop: Property) -> DetailPageData | None:
        """Fetch detail page and extract floorplan and gallery URLs.

        Args:
            prop: Property to fetch details for.

        Returns:
            DetailPageData with floorplan and gallery URLs, or None on failure.
        """
        match prop.source:
            case PropertySource.RIGHTMOVE:
                return await self._fetch_rightmove(prop)
            case PropertySource.ZOOPLA:
                return await self._fetch_zoopla(prop)
            case PropertySource.OPENRENT:
                return await self._fetch_openrent(prop)
            case PropertySource.ONTHEMARKET:
                return await self._fetch_onthemarket(prop)
        return None

    async def _fetch_rightmove(self, prop: Property) -> DetailPageData | None:
        """Extract floorplan and gallery URLs from Rightmove detail page."""
        try:
            client = await self._get_client()
            response = await client.get(str(prop.url))
            response.raise_for_status()
            html = response.text

            # Find PAGE_MODEL JSON start
            start_match = re.search(r"window\.PAGE_MODEL\s*=\s*", html)
            if not start_match:
                logger.debug("no_page_model", property_id=prop.unique_id)
                return None

            # Extract JSON using brace counting (handles nested objects)
            start_idx = start_match.end()
            depth = 0
            end_idx = start_idx
            for i, char in enumerate(html[start_idx:]):
                if char == "{":
                    depth += 1
                elif char == "}":
                    depth -= 1
                    if depth == 0:
                        end_idx = start_idx + i + 1
                        break

            json_str = html[start_idx:end_idx]
            data = json.loads(json_str)
            property_data = data.get("propertyData", {})

            # Extract floorplan
            floorplan_url: str | None = None
            floorplans = property_data.get("floorplans", [])
            if floorplans and floorplans[0].get("url"):
                floorplan_url = floorplans[0]["url"]

            # Extract gallery images
            gallery_urls: list[str] = []
            images = property_data.get("images", [])
            for img in images[: self._max_gallery_images]:
                if img.get("url"):
                    gallery_urls.append(img["url"])

            return DetailPageData(
                floorplan_url=floorplan_url,
                gallery_urls=gallery_urls if gallery_urls else None,
            )

        except Exception as e:
            logger.warning(
                "rightmove_fetch_failed",
                property_id=prop.unique_id,
                error=str(e),
            )
            return None

    async def _fetch_zoopla(self, prop: Property) -> DetailPageData | None:
        """Extract floorplan and gallery URLs from Zoopla detail page.

        Uses curl_cffi with Chrome TLS fingerprint impersonation to bypass
        Zoopla's bot detection.
        """
        try:
            async with AsyncSession() as session:
                response = await session.get(
                    str(prop.url),
                    impersonate="chrome",
                    headers={
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                        "Accept-Language": "en-GB,en;q=0.9",
                        "Accept-Encoding": "gzip, deflate, br",
                    },
                    timeout=30,
                )
                if response.status_code != 200:
                    logger.warning(
                        "zoopla_http_error",
                        property_id=prop.unique_id,
                        status=response.status_code,
                    )
                    return None
                html: str = response.text

            # Find __NEXT_DATA__ JSON
            match = re.search(
                r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>',
                html,
                re.DOTALL,
            )
            if not match:
                return None

            data = json.loads(match.group(1))
            media = (
                data.get("props", {})
                .get("pageProps", {})
                .get("listing", {})
                .get("propertyMedia", [])
            )

            # Extract floorplan and gallery
            floorplan_url: str | None = None
            gallery_urls: list[str] = []

            for item in media:
                item_type = item.get("type")
                if item_type == "floorplan" and floorplan_url is None:
                    floorplan_url = item.get("original")
                elif item_type == "image" and len(gallery_urls) < self._max_gallery_images:
                    url = item.get("original")
                    if url:
                        gallery_urls.append(url)

            return DetailPageData(
                floorplan_url=floorplan_url,
                gallery_urls=gallery_urls if gallery_urls else None,
            )

        except Exception as e:
            logger.warning("zoopla_fetch_failed", property_id=prop.unique_id, error=str(e))
            return None

    async def _fetch_openrent(self, prop: Property) -> DetailPageData | None:
        """Extract floorplan and gallery URLs from OpenRent detail page."""
        try:
            client = await self._get_client()
            response = await client.get(str(prop.url))
            response.raise_for_status()
            html = response.text

            # Extract floorplan
            floorplan_url: str | None = None
            floorplan_match = re.search(
                r'<img[^>]*class="[^"]*floorplan[^"]*"[^>]*src="([^"]+)"',
                html,
                re.IGNORECASE,
            )
            if floorplan_match:
                floorplan_url = floorplan_match.group(1)

            # Extract gallery images from the lightbox gallery
            # OpenRent uses a lightbox with data-src attributes for full-size images
            gallery_urls: list[str] = []

            # Pattern 1: Look for gallery images in lightbox anchors
            gallery_matches = re.findall(
                r'<a[^>]*href="([^"]+)"[^>]*data-lightbox="gallery"',
                html,
                re.IGNORECASE,
            )
            for url in gallery_matches[: self._max_gallery_images]:
                if url and not url.endswith("-floorplan.jpg"):
                    gallery_urls.append(url)

            # Pattern 2: Fallback - look for property images
            if not gallery_urls:
                img_matches = re.findall(
                    r'<img[^>]*class="[^"]*property-image[^"]*"[^>]*src="([^"]+)"',
                    html,
                    re.IGNORECASE,
                )
                for url in img_matches[: self._max_gallery_images]:
                    if url and "floorplan" not in url.lower():
                        gallery_urls.append(url)

            return DetailPageData(
                floorplan_url=floorplan_url,
                gallery_urls=gallery_urls if gallery_urls else None,
            )

        except Exception as e:
            logger.warning("openrent_fetch_failed", property_id=prop.unique_id, error=str(e))
            return None

    async def _fetch_onthemarket(self, prop: Property) -> DetailPageData | None:
        """Extract floorplan and gallery URLs from OnTheMarket detail page."""
        try:
            client = await self._get_client()
            response = await client.get(str(prop.url))
            response.raise_for_status()
            html = response.text

            # OnTheMarket uses Next.js with Redux state in __NEXT_DATA__
            match = re.search(
                r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>',
                html,
                re.DOTALL,
            )
            if not match:
                return None

            data = json.loads(match.group(1))
            redux_state = data.get("props", {}).get("initialReduxState", {})
            property_data = redux_state.get("property", {})

            # Extract floorplan
            floorplan_url: str | None = None
            floorplans = property_data.get("floorplans", [])
            if floorplans and floorplans[0].get("original"):
                floorplan_url = floorplans[0]["original"]

            # Extract gallery images
            gallery_urls: list[str] = []
            images = property_data.get("images", [])
            for img in images[: self._max_gallery_images]:
                url = img.get("original") if isinstance(img, dict) else img
                if url:
                    gallery_urls.append(url)

            return DetailPageData(
                floorplan_url=floorplan_url,
                gallery_urls=gallery_urls if gallery_urls else None,
            )

        except Exception as e:
            logger.warning("onthemarket_fetch_failed", property_id=prop.unique_id, error=str(e))
            return None

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

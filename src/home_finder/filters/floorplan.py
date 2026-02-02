"""Floorplan analysis filter using Claude vision."""

import json
import re
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict

from home_finder.logging import get_logger
from home_finder.models import Property, PropertySource

logger = get_logger(__name__)


class FloorplanAnalysis(BaseModel):
    """Result of LLM floorplan analysis."""

    model_config = ConfigDict(frozen=True)

    living_room_sqm: float | None = None
    is_spacious_enough: bool
    confidence: Literal["high", "medium", "low"]
    reasoning: str


class DetailFetcher:
    """Fetches property detail pages and extracts floorplan URLs."""

    def __init__(self) -> None:
        """Initialize the detail fetcher."""
        self._client: httpx.AsyncClient | None = None

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
        match prop.source:
            case PropertySource.RIGHTMOVE:
                return await self._fetch_rightmove(prop)
            case PropertySource.ZOOPLA:
                return await self._fetch_zoopla(prop)
            case PropertySource.OPENRENT:
                return await self._fetch_openrent(prop)
            case PropertySource.ONTHEMARKET:
                return await self._fetch_onthemarket(prop)

    async def _fetch_rightmove(self, prop: Property) -> str | None:
        """Extract floorplan URL from Rightmove detail page."""
        try:
            client = await self._get_client()
            response = await client.get(str(prop.url))
            response.raise_for_status()
            html = response.text

            # Find PAGE_MODEL JSON in script tag
            match = re.search(r"window\.PAGE_MODEL\s*=\s*({.*?});", html, re.DOTALL)
            if not match:
                logger.debug("no_page_model", property_id=prop.unique_id)
                return None

            data = json.loads(match.group(1))
            floorplans = data.get("propertyData", {}).get("floorplans", [])

            if floorplans and floorplans[0].get("url"):
                return floorplans[0]["url"]

            return None

        except Exception as e:
            logger.warning(
                "rightmove_fetch_failed",
                property_id=prop.unique_id,
                error=str(e),
            )
            return None

    async def _fetch_zoopla(self, prop: Property) -> str | None:
        """Extract floorplan URL from Zoopla detail page."""
        try:
            client = await self._get_client()
            response = await client.get(str(prop.url))
            response.raise_for_status()
            html = response.text

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

            for item in media:
                if item.get("type") == "floorplan":
                    return item.get("original")

            return None

        except Exception as e:
            logger.warning("zoopla_fetch_failed", property_id=prop.unique_id, error=str(e))
            return None

    async def _fetch_openrent(self, prop: Property) -> str | None:
        """Extract floorplan URL from OpenRent detail page."""
        try:
            client = await self._get_client()
            response = await client.get(str(prop.url))
            response.raise_for_status()
            html = response.text

            # Look for floorplan image
            match = re.search(
                r'<img[^>]*class="[^"]*floorplan[^"]*"[^>]*src="([^"]+)"',
                html,
                re.IGNORECASE,
            )
            if match:
                return match.group(1)

            return None

        except Exception as e:
            logger.warning("openrent_fetch_failed", property_id=prop.unique_id, error=str(e))
            return None

    async def _fetch_onthemarket(self, prop: Property) -> str | None:
        """Extract floorplan URL from OnTheMarket detail page."""
        try:
            client = await self._get_client()
            response = await client.get(str(prop.url))
            response.raise_for_status()
            html = response.text

            # Find property-details JSON
            match = re.search(
                r'<script[^>]*data-testid="property-details"[^>]*>(.*?)</script>',
                html,
                re.DOTALL,
            )
            if not match:
                return None

            data = json.loads(match.group(1))
            floorplans = data.get("floorplans", [])

            if floorplans and floorplans[0].get("src"):
                return floorplans[0]["src"]

            return None

        except Exception as e:
            logger.warning("onthemarket_fetch_failed", property_id=prop.unique_id, error=str(e))
            return None

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

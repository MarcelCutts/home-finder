"""Floorplan analysis filter using Claude vision."""

import asyncio
import json
import re
from typing import Literal

import anthropic
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


FLOORPLAN_PROMPT = """Analyze this floorplan image for a rental property.

I need to determine if the living room/lounge is spacious enough to:
1. Fit a home office setup (desk, chair, monitors)
2. Host a party of 8+ people comfortably

Please analyze the floorplan and respond with ONLY a JSON object (no markdown, no explanation outside the JSON):

{
    "living_room_sqm": <estimated size in square meters, or null if cannot determine>,
    "is_spacious_enough": <true if living room can fit office AND host 8+ people, false otherwise>,
    "confidence": <"high", "medium", or "low">,
    "reasoning": <brief explanation of your assessment>
}

Generally, a living room needs to be at least 20-25 sqm to comfortably fit both uses.
If the floorplan doesn't show measurements or you cannot estimate, use your best judgment
based on the room proportions and mark confidence as "low".
"""


class FloorplanFilter:
    """Filter properties by floorplan analysis."""

    def __init__(self, api_key: str) -> None:
        """Initialize the floorplan filter.

        Args:
            api_key: Anthropic API key.
        """
        self._api_key = api_key
        self._client: anthropic.AsyncAnthropic | None = None
        self._detail_fetcher = DetailFetcher()

    def _get_client(self) -> anthropic.AsyncAnthropic:
        """Get or create the Anthropic client."""
        if self._client is None:
            self._client = anthropic.AsyncAnthropic(api_key=self._api_key)
        return self._client

    async def filter_properties(
        self, properties: list[Property]
    ) -> list[tuple[Property, FloorplanAnalysis]]:
        """Filter properties by floorplan analysis.

        Args:
            properties: Properties to analyze.

        Returns:
            List of (property, analysis) tuples for properties that pass.
        """
        results: list[tuple[Property, FloorplanAnalysis]] = []

        for prop in properties:
            # Step 1: Fetch floorplan URL
            floorplan_url = await self._detail_fetcher.fetch_floorplan_url(prop)

            if not floorplan_url:
                logger.info("no_floorplan", property_id=prop.unique_id)
                continue

            # Step 2: 2+ beds auto-pass
            if prop.bedrooms >= 2:
                analysis = FloorplanAnalysis(
                    is_spacious_enough=True,
                    confidence="high",
                    reasoning="2+ bedrooms - office can go in spare room",
                )
                results.append((prop, analysis))
                continue

            # Step 3: 1-bed needs LLM analysis
            analysis = await self._analyze_floorplan(floorplan_url, prop.unique_id)

            if analysis and analysis.is_spacious_enough:
                results.append((prop, analysis))
            else:
                reason = analysis.reasoning if analysis else "analysis failed"
                logger.info(
                    "filtered_small_living_room",
                    property_id=prop.unique_id,
                    reasoning=reason,
                )

            # Rate limit: small delay between LLM calls
            await asyncio.sleep(0.5)

        return results

    async def _analyze_floorplan(
        self, floorplan_url: str, property_id: str
    ) -> FloorplanAnalysis | None:
        """Analyze a floorplan image using Claude.

        Args:
            floorplan_url: URL of the floorplan image.
            property_id: Property ID for logging.

        Returns:
            Analysis result or None if analysis failed.
        """
        try:
            client = self._get_client()
            response = await client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=1024,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {"type": "url", "url": floorplan_url},
                            },
                            {"type": "text", "text": FLOORPLAN_PROMPT},
                        ],
                    }
                ],
            )

            # Parse response
            response_text = response.content[0].text
            return FloorplanAnalysis.model_validate_json(response_text)

        except Exception as e:
            logger.warning(
                "floorplan_analysis_failed",
                property_id=property_id,
                error=str(e),
            )
            return None

    async def close(self) -> None:
        """Close clients."""
        await self._detail_fetcher.close()

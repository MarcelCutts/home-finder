"""Floorplan analysis filter using Claude vision.

DEPRECATED: This module is superseded by quality.py which provides more
comprehensive property analysis. Kept for backwards compatibility.
"""

import asyncio
from typing import Literal

import anthropic
from anthropic.types import TextBlock
from pydantic import BaseModel, ConfigDict

from home_finder.logging import get_logger
from home_finder.models import Property
from home_finder.scrapers.detail_fetcher import DetailFetcher

logger = get_logger(__name__)


class FloorplanAnalysis(BaseModel):
    """Result of LLM floorplan analysis."""

    model_config = ConfigDict(frozen=True)

    living_room_sqm: float | None = None
    is_spacious_enough: bool
    confidence: Literal["high", "medium", "low"]
    reasoning: str


FLOORPLAN_PROMPT = """Analyze this floorplan image for a rental property.

I need to determine if the living room/lounge is spacious enough to:
1. Fit a home office setup (desk, chair, monitors)
2. Host a party of 8+ people comfortably

Please analyze the floorplan and respond with ONLY a JSON object
(no markdown, no explanation outside the JSON):

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
                auto_pass_analysis = FloorplanAnalysis(
                    is_spacious_enough=True,
                    confidence="high",
                    reasoning="2+ bedrooms - office can go in spare room",
                )
                results.append((prop, auto_pass_analysis))
                continue

            # Step 3: 1-bed needs LLM analysis
            llm_analysis = await self._analyze_floorplan(floorplan_url, prop.unique_id)

            if llm_analysis and llm_analysis.is_spacious_enough:
                results.append((prop, llm_analysis))
            else:
                reason = llm_analysis.reasoning if llm_analysis else "analysis failed"
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

            # Parse response - first content block should be TextBlock
            first_block = response.content[0]
            if not isinstance(first_block, TextBlock):
                logger.warning(
                    "unexpected_response_type",
                    property_id=property_id,
                    block_type=type(first_block).__name__,
                )
                return None
            return FloorplanAnalysis.model_validate_json(first_block.text)

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

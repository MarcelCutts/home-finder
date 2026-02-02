"""Property quality analysis filter using Claude vision."""

import asyncio
import json
import re
from typing import Any, Literal

import anthropic
from anthropic import RateLimitError
from anthropic.types import ImageBlockParam, TextBlock, TextBlockParam
from pydantic import BaseModel, ConfigDict

from home_finder.logging import get_logger
from home_finder.models import Property
from home_finder.scrapers.detail_fetcher import DetailFetcher

logger = get_logger(__name__)

# Rate limit settings for Tier 1
MAX_RETRIES = 3
INITIAL_RETRY_DELAY = 5.0  # seconds
DELAY_BETWEEN_CALLS = 1.5  # seconds (50 RPM = 1.2s minimum, add buffer)

# Average monthly rents by outcode and bedroom count (£/month)
# Based on ONS Private Rent data and Rightmove/Zoopla market research (Jan 2026)
# Format: {outcode: {bedrooms: average_rent}}
RENTAL_BENCHMARKS: dict[str, dict[int, int]] = {
    # Hackney
    "E2": {1: 1950, 2: 2400, 3: 3100},
    "E5": {1: 1800, 2: 2200, 3: 2800},
    "E8": {1: 1900, 2: 2350, 3: 3000},
    "E9": {1: 1850, 2: 2250, 3: 2900},
    "N16": {1: 1850, 2: 2300, 3: 2950},
    # Islington
    "N1": {1: 2100, 2: 2600, 3: 3400},
    "N4": {1: 1800, 2: 2200, 3: 2850},
    "N5": {1: 1900, 2: 2350, 3: 3050},
    "N7": {1: 1850, 2: 2300, 3: 2950},
    "N19": {1: 1750, 2: 2150, 3: 2800},
    "EC1": {1: 2300, 2: 2900, 3: 3700},
    # Haringey
    "N6": {1: 1700, 2: 2100, 3: 2700},
    "N8": {1: 1750, 2: 2150, 3: 2750},
    "N10": {1: 1650, 2: 2050, 3: 2650},
    "N11": {1: 1600, 2: 2000, 3: 2600},
    "N15": {1: 1650, 2: 2000, 3: 2550},
    "N17": {1: 1550, 2: 1900, 3: 2450},
    "N22": {1: 1600, 2: 1950, 3: 2500},
    # Tower Hamlets
    "E1": {1: 2050, 2: 2550, 3: 3300},
    "E3": {1: 1900, 2: 2350, 3: 3000},
    "E14": {1: 2100, 2: 2600, 3: 3350},
    # Waltham Forest
    "E10": {1: 1600, 2: 1950, 3: 2500},
    "E11": {1: 1550, 2: 1900, 3: 2450},
    "E17": {1: 1650, 2: 2000, 3: 2600},
}

# Default benchmark for unknown areas (East London average)
DEFAULT_BENCHMARK: dict[int, int] = {1: 1800, 2: 2200, 3: 2850}


class KitchenAnalysis(BaseModel):
    """Analysis of kitchen amenities."""

    model_config = ConfigDict(frozen=True)

    has_gas_hob: bool | None = None
    has_dishwasher: bool | None = None
    has_washing_machine: bool | None = None
    has_dryer: bool | None = None
    appliance_quality: Literal["high", "medium", "low"] | None = None
    notes: str = ""


class ConditionAnalysis(BaseModel):
    """Analysis of property condition."""

    model_config = ConfigDict(frozen=True)

    overall_condition: Literal["excellent", "good", "fair", "poor", "unknown"] = "unknown"
    has_visible_damp: bool = False
    has_visible_mold: bool = False
    has_worn_fixtures: bool = False
    maintenance_concerns: list[str] = []
    confidence: Literal["high", "medium", "low"] = "medium"


class LightSpaceAnalysis(BaseModel):
    """Analysis of natural light and space feel."""

    model_config = ConfigDict(frozen=True)

    natural_light: Literal["excellent", "good", "fair", "poor", "unknown"] = "unknown"
    window_sizes: Literal["large", "medium", "small"] | None = None
    feels_spacious: bool | None = None  # None = unknown
    ceiling_height: Literal["high", "standard", "low"] | None = None
    notes: str = ""


class SpaceAnalysis(BaseModel):
    """Analysis of living room space (replaces FloorplanFilter logic)."""

    model_config = ConfigDict(frozen=True)

    living_room_sqm: float | None = None
    is_spacious_enough: bool | None = None  # None = unknown
    confidence: Literal["high", "medium", "low"] = "low"


class ValueAnalysis(BaseModel):
    """Value-for-money assessment based on local benchmarks."""

    model_config = ConfigDict(frozen=True)

    area_average: int | None = None
    difference: int | None = None  # Negative = below average (good), positive = above
    rating: Literal["excellent", "good", "fair", "poor"] | None = None
    note: str = ""

    # LLM-assessed value considering quality (set by Claude)
    quality_adjusted_rating: Literal["excellent", "good", "fair", "poor"] | None = None
    quality_adjusted_note: str = ""


def assess_value(price_pcm: int, postcode: str | None, bedrooms: int) -> ValueAnalysis:
    """Assess value-for-money based on local rental benchmarks.

    Args:
        price_pcm: Monthly rent in GBP.
        postcode: Property postcode (e.g., "E8 2LX").
        bedrooms: Number of bedrooms.

    Returns:
        ValueAnalysis with comparison to local average.
    """
    if not postcode:
        return ValueAnalysis(note="No postcode - cannot assess value")

    # Extract outcode (e.g., "E8" from "E8 2LX")
    outcode = postcode.split()[0].upper() if " " in postcode else postcode.upper()

    # Get benchmark for this area
    benchmarks = RENTAL_BENCHMARKS.get(outcode, DEFAULT_BENCHMARK)

    # Cap bedrooms at 3 for benchmark lookup
    bed_key = min(bedrooms, 3) if bedrooms >= 1 else 1
    average = benchmarks.get(bed_key)

    if average is None:
        return ValueAnalysis(note=f"No benchmark for {bed_key}-bed in {outcode}")

    difference = price_pcm - average
    pct_diff = (difference / average) * 100

    # Determine rating
    if pct_diff <= -10:
        rating: Literal["excellent", "good", "fair", "poor"] = "excellent"
    elif pct_diff <= 0:
        rating = "good"
    elif pct_diff <= 10:
        rating = "fair"
    else:
        rating = "poor"

    # Generate note
    if difference < 0:
        note = f"£{abs(difference):,} below {outcode} average"
    elif difference > 0:
        note = f"£{difference:,} above {outcode} average"
    else:
        note = f"At {outcode} average"

    return ValueAnalysis(
        area_average=average,
        difference=difference,
        rating=rating,
        note=note,
    )


class PropertyQualityAnalysis(BaseModel):
    """Complete quality analysis of a property."""

    model_config = ConfigDict(frozen=True)

    kitchen: KitchenAnalysis
    condition: ConditionAnalysis
    light_space: LightSpaceAnalysis
    space: SpaceAnalysis

    # Advisory flags (no auto-filtering)
    condition_concerns: bool = False
    concern_severity: Literal["minor", "moderate", "serious"] | None = None

    # Value assessment (calculated, not from LLM)
    value: ValueAnalysis | None = None

    # For notifications
    summary: str


QUALITY_ANALYSIS_PROMPT_TEMPLATE = """Analyze these property images for a rental flat in London.

**Property Details:**
- Price: £{price_pcm}/month
- Bedrooms: {bedrooms}
- Area average for {bedrooms}-bed: £{area_average}/month ({price_comparison})

I need a comprehensive quality assessment covering:

1. **Kitchen Amenities**: Look for gas hob (vs electric), dishwasher, washing machine, dryer.
   Assess appliance quality (modern/high-end vs basic/dated).

2. **Property Condition**: Look for any signs of:
   - Damp (water stains, peeling paint near windows/ceilings)
   - Mold (dark patches, especially in corners, bathrooms)
   - Worn fixtures (dated bathroom fittings, tired carpets, scuffed walls)
   - Any maintenance concerns

3. **Natural Light & Space**: Assess:
   - Natural light levels (window sizes, brightness)
   - Does it feel spacious or cramped?
   - Ceiling heights if visible

4. **Living Room Size**: If a floorplan is included, estimate the living room size in sqm.
   The living room should ideally fit a home office AND host 8+ people (~20-25 sqm minimum).

5. **Value Assessment**: Given the price is {price_comparison} the area average, assess whether
   this is good value CONSIDERING THE QUALITY you see in the photos. A property slightly above
   average could still be excellent value if it's in great condition with modern appliances.
   A property below average might be poor value if it has damp or condition issues.

6. **Overall Summary**: Write a brief 1-2 sentence summary highlighting the key positives
   and any concerns a potential renter should know about.

Respond with ONLY a JSON object (no markdown, no explanation outside the JSON):

{{
    "kitchen": {{
        "has_gas_hob": <true/false/null if cannot determine>,
        "has_dishwasher": <true/false/null>,
        "has_washing_machine": <true/false/null>,
        "has_dryer": <true/false/null>,
        "appliance_quality": <"high"/"medium"/"low"/null>,
        "notes": "<any notable kitchen features or concerns>"
    }},
    "condition": {{
        "overall_condition": <"excellent"/"good"/"fair"/"poor">,
        "has_visible_damp": <true/false>,
        "has_visible_mold": <true/false>,
        "has_worn_fixtures": <true/false>,
        "maintenance_concerns": [<list of specific concerns if any>],
        "confidence": <"high"/"medium"/"low">
    }},
    "light_space": {{
        "natural_light": <"excellent"/"good"/"fair"/"poor">,
        "window_sizes": <"large"/"medium"/"small"/null>,
        "feels_spacious": <true/false>,
        "ceiling_height": <"high"/"standard"/"low"/null>,
        "notes": "<any notable observations>"
    }},
    "space": {{
        "living_room_sqm": <estimated size or null>,
        "is_spacious_enough": <true if can fit office AND host 8+ people>,
        "confidence": <"high"/"medium"/"low">
    }},
    "value_for_quality": {{
        "rating": <"excellent"/"good"/"fair"/"poor" considering quality vs price>,
        "reasoning": "<brief explanation of value assessment considering condition>"
    }},
    "condition_concerns": <true if any significant condition issues>,
    "concern_severity": <"minor"/"moderate"/"serious"/null>,
    "summary": "<1-2 sentence summary for the notification>"
}}
"""


def build_quality_prompt(price_pcm: int, bedrooms: int, area_average: int) -> str:
    """Build the quality analysis prompt with price context."""
    diff = price_pcm - area_average
    if diff < -50:
        price_comparison = f"£{abs(diff)} below"
    elif diff > 50:
        price_comparison = f"£{diff} above"
    else:
        price_comparison = "at"

    return QUALITY_ANALYSIS_PROMPT_TEMPLATE.format(
        price_pcm=price_pcm,
        bedrooms=bedrooms,
        area_average=area_average,
        price_comparison=price_comparison,
    )


def extract_json_from_response(text: str) -> dict[str, Any]:
    """Extract JSON from a response that may be wrapped in markdown code blocks.

    Args:
        text: Raw response text that may contain JSON wrapped in ```json...``` blocks.

    Returns:
        Parsed JSON as a dictionary.

    Raises:
        json.JSONDecodeError: If no valid JSON can be extracted.
    """
    # Try direct parse first
    text = text.strip()
    if text.startswith("{"):
        result: dict[str, Any] = json.loads(text)
        return result

    # Try to extract from markdown code block
    # Match ```json ... ``` or ``` ... ```
    code_block_pattern = r"```(?:json)?\s*\n?(.*?)\n?```"
    match = re.search(code_block_pattern, text, re.DOTALL)
    if match:
        json_text = match.group(1).strip()
        result = json.loads(json_text)
        return result

    # Last resort: find the first { and last } and try to parse
    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        json_text = text[first_brace : last_brace + 1]
        result = json.loads(json_text)
        return result

    raise json.JSONDecodeError("No JSON found in response", text, 0)


class PropertyQualityFilter:
    """Analyze property quality using Claude vision API."""

    def __init__(self, api_key: str, max_images: int = 10) -> None:
        """Initialize the quality filter.

        Args:
            api_key: Anthropic API key.
            max_images: Maximum number of gallery images to analyze.
        """
        self._api_key = api_key
        self._max_images = max_images
        self._client: anthropic.AsyncAnthropic | None = None
        self._detail_fetcher = DetailFetcher(max_gallery_images=max_images)

    def _get_client(self) -> anthropic.AsyncAnthropic:
        """Get or create the Anthropic client."""
        if self._client is None:
            self._client = anthropic.AsyncAnthropic(api_key=self._api_key)
        return self._client

    async def analyze_properties(
        self, properties: list[Property]
    ) -> list[tuple[Property, PropertyQualityAnalysis]]:
        """Analyze quality for a list of properties.

        Unlike the FloorplanFilter, this does NOT filter out properties.
        It enriches them with quality analysis for notification display.

        Args:
            properties: Properties to analyze.

        Returns:
            List of (property, analysis) tuples. Properties without images
            will have a minimal analysis with low confidence.
        """
        results: list[tuple[Property, PropertyQualityAnalysis]] = []

        for prop in properties:
            # Calculate value assessment (doesn't need images)
            value = assess_value(prop.price_pcm, prop.postcode, prop.bedrooms)

            # Fetch detail page with gallery and floorplan URLs
            detail_data = await self._detail_fetcher.fetch_detail_page(prop)

            if not detail_data or (not detail_data.gallery_urls and not detail_data.floorplan_url):
                logger.info("no_images_for_analysis", property_id=prop.unique_id)
                # Create minimal analysis for properties without images
                minimal = self._create_minimal_analysis(value=value)
                results.append((prop, minimal))
                continue

            # Analyze with Claude vision
            analysis = await self._analyze_property(
                prop.unique_id,
                gallery_urls=detail_data.gallery_urls or [],
                floorplan_url=detail_data.floorplan_url,
                bedrooms=prop.bedrooms,
                price_pcm=prop.price_pcm,
                area_average=value.area_average,
            )

            if analysis:
                # Merge value assessment with LLM quality-adjusted rating
                merged_value = ValueAnalysis(
                    area_average=value.area_average,
                    difference=value.difference,
                    rating=value.rating,
                    note=value.note,
                    quality_adjusted_rating=analysis.value.quality_adjusted_rating
                    if analysis.value
                    else None,
                    quality_adjusted_note=analysis.value.quality_adjusted_note
                    if analysis.value
                    else "",
                )
                # Add merged value assessment to the analysis
                analysis = PropertyQualityAnalysis(
                    kitchen=analysis.kitchen,
                    condition=analysis.condition,
                    light_space=analysis.light_space,
                    space=analysis.space,
                    condition_concerns=analysis.condition_concerns,
                    concern_severity=analysis.concern_severity,
                    value=merged_value,
                    summary=analysis.summary,
                )
                results.append((prop, analysis))
            else:
                # Fallback to minimal analysis on failure
                minimal = self._create_minimal_analysis(value=value)
                results.append((prop, minimal))

            # Rate limit: delay between API calls to respect Tier 1 limits (50 RPM)
            await asyncio.sleep(DELAY_BETWEEN_CALLS)

        return results

    def _create_minimal_analysis(
        self, value: ValueAnalysis | None = None
    ) -> PropertyQualityAnalysis:
        """Create a minimal analysis for properties without images."""
        return PropertyQualityAnalysis(
            kitchen=KitchenAnalysis(notes="No images available for analysis"),
            condition=ConditionAnalysis(
                overall_condition="unknown",
                confidence="low",
            ),
            light_space=LightSpaceAnalysis(
                natural_light="unknown",
                feels_spacious=None,
                notes="No images available for analysis",
            ),
            space=SpaceAnalysis(
                is_spacious_enough=None,
                confidence="low",
            ),
            condition_concerns=False,
            value=value,
            summary="No images available for quality analysis",
        )

    async def _analyze_property(
        self,
        property_id: str,
        *,
        gallery_urls: list[str],
        floorplan_url: str | None,
        bedrooms: int,
        price_pcm: int,
        area_average: int | None,
    ) -> PropertyQualityAnalysis | None:
        """Analyze a single property using Claude vision.

        Args:
            property_id: Property ID for logging.
            gallery_urls: List of gallery image URLs.
            floorplan_url: Floorplan URL if available.
            bedrooms: Number of bedrooms (for space assessment).
            price_pcm: Monthly rent price.
            area_average: Average rent for this area and bedroom count.

        Returns:
            Analysis result or None if analysis failed.
        """
        client = self._get_client()

        # Build image content blocks
        content: list[ImageBlockParam | TextBlockParam] = []

        # Add gallery images (up to max_images)
        for url in gallery_urls[: self._max_images]:
            content.append(
                ImageBlockParam(
                    type="image",
                    source={"type": "url", "url": url},
                )
            )

        # Add floorplan if available
        if floorplan_url:
            content.append(
                ImageBlockParam(
                    type="image",
                    source={"type": "url", "url": floorplan_url},
                )
            )

        # Build prompt with price context
        effective_average = area_average or DEFAULT_BENCHMARK.get(min(bedrooms, 3), 1800)
        prompt = build_quality_prompt(price_pcm, bedrooms, effective_average)
        content.append(TextBlockParam(type="text", text=prompt))

        logger.info(
            "analyzing_property",
            property_id=property_id,
            gallery_count=len(gallery_urls),
            has_floorplan=floorplan_url is not None,
        )

        # Retry loop with exponential backoff for rate limits
        last_error: Exception | None = None
        response_text: str = ""  # Track for error logging
        for attempt in range(MAX_RETRIES):
            try:
                response = await client.messages.create(
                    model="claude-sonnet-4-20250514",
                    max_tokens=2048,
                    messages=[{"role": "user", "content": content}],
                )

                # Parse response
                first_block = response.content[0]
                if not isinstance(first_block, TextBlock):
                    logger.warning(
                        "unexpected_response_type",
                        property_id=property_id,
                        block_type=type(first_block).__name__,
                    )
                    return None

                response_text = first_block.text

                # Check for empty response
                if not response_text.strip():
                    logger.warning(
                        "empty_response",
                        property_id=property_id,
                        attempt=attempt + 1,
                    )
                    last_error = ValueError("Empty response from API")
                    if attempt < MAX_RETRIES - 1:
                        delay = INITIAL_RETRY_DELAY * (2**attempt)
                        logger.info("retrying_after_empty", delay=delay)
                        await asyncio.sleep(delay)
                        continue
                    return None

                # Parse JSON response (handles markdown-wrapped JSON)
                data = extract_json_from_response(response_text)
                break  # Success, exit retry loop

            except RateLimitError as e:
                last_error = e
                if attempt < MAX_RETRIES - 1:
                    delay = INITIAL_RETRY_DELAY * (2**attempt)
                    logger.warning(
                        "rate_limited",
                        property_id=property_id,
                        attempt=attempt + 1,
                        retry_delay=delay,
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(
                        "rate_limit_exhausted",
                        property_id=property_id,
                        attempts=MAX_RETRIES,
                    )
                    return None

            except json.JSONDecodeError as e:
                logger.warning(
                    "json_parse_failed",
                    property_id=property_id,
                    error=str(e),
                    response_preview=response_text[:200] if response_text else "N/A",
                )
                return None

            except Exception as e:
                logger.warning(
                    "quality_analysis_failed",
                    property_id=property_id,
                    error=str(e),
                    error_type=type(e).__name__,
                )
                return None
        else:
            # All retries exhausted (loop completed without break)
            logger.error(
                "all_retries_failed",
                property_id=property_id,
                last_error=str(last_error),
            )
            return None

        # Extract value_for_quality from LLM response
        value_for_quality = data.pop("value_for_quality", {})
        quality_adjusted_rating = value_for_quality.get("rating")
        quality_adjusted_note = value_for_quality.get("reasoning", "")

        # Build value analysis with LLM assessment
        value = ValueAnalysis(
            quality_adjusted_rating=quality_adjusted_rating,
            quality_adjusted_note=quality_adjusted_note,
        )

        # Build the full analysis (with validation error handling)
        try:
            analysis = PropertyQualityAnalysis(
                kitchen=KitchenAnalysis(**data.get("kitchen", {})),
                condition=ConditionAnalysis(**data.get("condition", {})),
                light_space=LightSpaceAnalysis(**data.get("light_space", {})),
                space=SpaceAnalysis(**data.get("space", {})),
                condition_concerns=data.get("condition_concerns", False),
                concern_severity=data.get("concern_severity"),
                value=value,
                summary=data.get("summary", "Analysis completed"),
            )
        except Exception as e:
            logger.warning(
                "analysis_validation_failed",
                property_id=property_id,
                error=str(e),
            )
            return None

        # For 2+ bed properties, override space assessment
        # (office can go in spare room)
        if bedrooms >= 2 and not analysis.space.is_spacious_enough:
            analysis = PropertyQualityAnalysis(
                kitchen=analysis.kitchen,
                condition=analysis.condition,
                light_space=analysis.light_space,
                space=SpaceAnalysis(
                    living_room_sqm=analysis.space.living_room_sqm,
                    is_spacious_enough=True,
                    confidence="high",
                ),
                condition_concerns=analysis.condition_concerns,
                concern_severity=analysis.concern_severity,
                value=analysis.value,
                summary=analysis.summary,
            )

        return analysis

    async def close(self) -> None:
        """Close clients."""
        if self._client is not None:
            await self._client.close()
            self._client = None
        await self._detail_fetcher.close()

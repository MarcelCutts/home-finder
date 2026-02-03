"""Property quality analysis filter using Claude vision."""

import asyncio
import base64
from typing import Any, Literal, get_args

import anthropic
import httpx
from anthropic import (
    APIConnectionError,
    APIStatusError,
    BadRequestError,
    InternalServerError,
    RateLimitError,
)
from anthropic.types import (
    Base64ImageSourceParam,
    ImageBlockParam,
    TextBlockParam,
    ToolParam,
    ToolUseBlock,
    URLImageSourceParam,
)
from curl_cffi.requests import AsyncSession
from pydantic import BaseModel, ConfigDict

from home_finder.logging import get_logger
from home_finder.models import MergedProperty

logger = get_logger(__name__)

# Valid media types for Claude vision API
ImageMediaType = Literal["image/jpeg", "image/png", "image/gif", "image/webp"]
VALID_MEDIA_TYPES: tuple[str, ...] = get_args(ImageMediaType)

# Valid image extensions (for URL filtering)
VALID_IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".gif", ".webp")

# Rate limit settings for Tier 1
# SDK handles retry automatically, we just need delay between calls
DELAY_BETWEEN_CALLS = 1.5  # seconds (50 RPM = 1.2s minimum, add buffer)

# SDK retry configuration
MAX_RETRIES = 3
REQUEST_TIMEOUT = 180.0  # 3 minutes for vision requests

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
    """Analysis of kitchen amenities and condition."""

    model_config = ConfigDict(frozen=True)

    overall_quality: Literal["modern", "decent", "dated", "unknown"] = "unknown"
    hob_type: Literal["gas", "electric", "induction", "unknown"] | None = None
    has_dishwasher: bool | None = None
    has_washing_machine: bool | None = None
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


# System prompt for quality analysis - cached for cost savings
QUALITY_ANALYSIS_SYSTEM_PROMPT = """You are an expert property analyst \
specializing in London rental properties.

Your task is to analyze property images and provide a comprehensive quality \
assessment. You will be given gallery images, optionally a floorplan, and \
the listing description/features when available.

IMPORTANT: Cross-reference what you see in the images with the listing text. \
The description often mentions things like "new kitchen", "gas hob", "recently \
refurbished" that help confirm or clarify what's in the photos.

Analyze the following aspects:

1. **Kitchen Quality**: Focus on whether the kitchen looks MODERN or DATED.
   - Modern: New units, integrated appliances, good worktops, contemporary style
   - Dated: Old-fashioned units, worn surfaces, mismatched appliances
   - Note the hob type if visible/mentioned (gas, electric, induction)
   - Check listing for mentions of "new kitchen", "recently fitted", etc.

2. **Property Condition**: Look for any signs of:
   - Damp (water stains, peeling paint near windows/ceilings)
   - Mold (dark patches, especially in corners, bathrooms)
   - Worn fixtures (dated bathroom fittings, tired carpets, scuffed walls)
   - Cross-reference with listing mentions of "refurbished", "newly decorated"

3. **Natural Light & Space**: Assess:
   - Natural light levels (window sizes, brightness)
   - Does it feel spacious or cramped?
   - Ceiling heights if visible

4. **Living Room Size**: If a floorplan is included, estimate the living room \
size in sqm. The living room should ideally fit a home office AND host 8+ \
people (~20-25 sqm minimum).

5. **Value Assessment**: Consider if the property is good value given its \
condition and features mentioned in the listing.

6. **Overall Summary**: Write a brief 1-2 sentence summary highlighting the \
key positives and any concerns a potential renter should know about.

Always use the property_quality_analysis tool to return your assessment."""


# Tool schema for structured outputs - guarantees valid JSON response
QUALITY_ANALYSIS_TOOL: dict[str, Any] = {
    "name": "property_quality_analysis",
    "description": "Return comprehensive property quality analysis results",
    "input_schema": {
        "type": "object",
        "properties": {
            "kitchen": {
                "type": "object",
                "properties": {
                    "overall_quality": {
                        "type": "string",
                        "enum": ["modern", "decent", "dated", "unknown"],
                        "description": "Overall kitchen quality/age assessment",
                    },
                    "hob_type": {
                        "anyOf": [
                            {"type": "string", "enum": ["gas", "electric", "induction", "unknown"]},
                            {"type": "null"},
                        ],
                        "description": "Type of hob if visible or mentioned",
                    },
                    "has_dishwasher": {
                        "anyOf": [{"type": "boolean"}, {"type": "null"}],
                    },
                    "has_washing_machine": {
                        "anyOf": [{"type": "boolean"}, {"type": "null"}],
                    },
                    "notes": {
                        "type": "string",
                        "description": "Notable kitchen features or concerns",
                    },
                },
                "required": ["overall_quality", "notes"],
                "additionalProperties": False,
            },
            "condition": {
                "type": "object",
                "properties": {
                    "overall_condition": {
                        "type": "string",
                        "enum": ["excellent", "good", "fair", "poor", "unknown"],
                    },
                    "has_visible_damp": {"type": "boolean"},
                    "has_visible_mold": {"type": "boolean"},
                    "has_worn_fixtures": {"type": "boolean"},
                    "maintenance_concerns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of specific maintenance concerns",
                    },
                    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                },
                "required": [
                    "overall_condition",
                    "has_visible_damp",
                    "has_visible_mold",
                    "has_worn_fixtures",
                    "maintenance_concerns",
                    "confidence",
                ],
                "additionalProperties": False,
            },
            "light_space": {
                "type": "object",
                "properties": {
                    "natural_light": {
                        "type": "string",
                        "enum": ["excellent", "good", "fair", "poor", "unknown"],
                    },
                    "window_sizes": {
                        "anyOf": [
                            {"type": "string", "enum": ["large", "medium", "small"]},
                            {"type": "null"},
                        ],
                    },
                    "feels_spacious": {
                        "anyOf": [{"type": "boolean"}, {"type": "null"}],
                        "description": "Whether the property feels spacious",
                    },
                    "ceiling_height": {
                        "anyOf": [
                            {"type": "string", "enum": ["high", "standard", "low"]},
                            {"type": "null"},
                        ],
                    },
                    "notes": {"type": "string"},
                },
                "required": ["natural_light", "notes"],
                "additionalProperties": False,
            },
            "space": {
                "type": "object",
                "properties": {
                    "living_room_sqm": {
                        "anyOf": [{"type": "number"}, {"type": "null"}],
                        "description": "Estimated living room size in sqm from floorplan",
                    },
                    "is_spacious_enough": {
                        "anyOf": [{"type": "boolean"}, {"type": "null"}],
                        "description": "True if can fit office AND host 8+ people",
                    },
                    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                },
                "required": ["confidence"],
                "additionalProperties": False,
            },
            "value_for_quality": {
                "type": "object",
                "properties": {
                    "rating": {
                        "type": "string",
                        "enum": ["excellent", "good", "fair", "poor"],
                        "description": "Value rating considering quality vs price",
                    },
                    "reasoning": {
                        "type": "string",
                        "description": "Brief explanation of value assessment",
                    },
                },
                "required": ["rating", "reasoning"],
                "additionalProperties": False,
            },
            "condition_concerns": {
                "type": "boolean",
                "description": "True if any significant condition issues found",
            },
            "concern_severity": {
                "anyOf": [
                    {"type": "string", "enum": ["minor", "moderate", "serious"]},
                    {"type": "null"},
                ],
            },
            "summary": {
                "type": "string",
                "description": "1-2 sentence summary for notification",
            },
        },
        "required": [
            "kitchen",
            "condition",
            "light_space",
            "space",
            "value_for_quality",
            "condition_concerns",
            "concern_severity",
            "summary",
        ],
        "additionalProperties": False,
    },
    "strict": True,
}


def build_user_prompt(
    price_pcm: int,
    bedrooms: int,
    area_average: int,
    description: str | None = None,
    features: list[str] | None = None,
) -> str:
    """Build the user prompt with property-specific context."""
    diff = price_pcm - area_average
    if diff < -50:
        price_comparison = f"£{abs(diff)} below"
    elif diff > 50:
        price_comparison = f"£{diff} above"
    else:
        price_comparison = "at"

    prompt = f"""Analyze these property images.

**Property Details:**
- Price: £{price_pcm}/month
- Bedrooms: {bedrooms}
- Area average for {bedrooms}-bed: £{area_average}/month ({price_comparison})"""

    if features:
        prompt += "\n\n**Listed Features:**\n"
        prompt += "\n".join(f"- {f}" for f in features[:15])  # Limit to 15 features

    if description:
        # Truncate very long descriptions to save tokens
        desc = description[:1500] + "..." if len(description) > 1500 else description
        prompt += f"\n\n**Listing Description:**\n{desc}"

    prompt += "\n\nPlease provide your quality assessment using the "
    prompt += "property_quality_analysis tool."

    return prompt


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

    def _get_client(self) -> anthropic.AsyncAnthropic:
        """Get or create the Anthropic client with optimized settings."""
        if self._client is None:
            self._client = anthropic.AsyncAnthropic(
                api_key=self._api_key,
                max_retries=MAX_RETRIES,  # SDK handles retry with exponential backoff
                timeout=httpx.Timeout(REQUEST_TIMEOUT),  # 3 min for vision requests
            )
        return self._client

    @staticmethod
    def _is_valid_image_url(url: str) -> bool:
        """Check if URL points to a supported image format.

        Claude Vision API only supports jpeg, png, gif, and webp.
        PDFs and other formats will fail, so we filter them out.
        """
        # Extract path without query params
        path = url.split("?")[0].lower()
        return path.endswith(VALID_IMAGE_EXTENSIONS)

    @staticmethod
    def _needs_base64_download(url: str) -> bool:
        """Check if URL requires local download due to anti-bot protection.

        Some image CDNs (Zoopla's zoocdn.com) use TLS fingerprinting to block
        non-browser requests. When we send URL-based images to Claude's API,
        Anthropic's servers fetch them directly and get blocked with 403.

        For these sites, we need to download the images locally using curl_cffi
        (which can impersonate Chrome's TLS fingerprint) and send as base64.
        """
        # Zoopla image CDNs use anti-bot protection
        return "zoocdn.com" in url

    @staticmethod
    def _get_media_type(url: str) -> ImageMediaType:
        """Determine media type from URL extension."""
        # Try to get from URL extension
        ext = url.lower().split("?")[0].rsplit(".", 1)[-1] if "." in url else ""
        type_map: dict[str, ImageMediaType] = {
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
            "png": "image/png",
            "gif": "image/gif",
            "webp": "image/webp",
        }
        return type_map.get(ext, "image/jpeg")  # Default to JPEG

    async def _download_image_as_base64(self, url: str) -> tuple[str, ImageMediaType] | None:
        """Download image using curl_cffi and return base64 data with media type.

        Uses Chrome TLS fingerprint impersonation to bypass anti-bot protection.

        Args:
            url: Image URL to download.

        Returns:
            Tuple of (base64_data, media_type) or None if download failed.
        """
        try:
            async with AsyncSession() as session:
                response = await session.get(
                    url,
                    impersonate="chrome",
                    headers={
                        "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
                        "Accept-Language": "en-GB,en;q=0.9",
                        "Accept-Encoding": "gzip, deflate, br",
                    },
                    timeout=30,
                )
                if response.status_code != 200:
                    logger.warning(
                        "image_download_failed",
                        url=url,
                        status=response.status_code,
                    )
                    return None

                # Get content type from response or guess from URL
                content_type = response.headers.get("content-type", "")
                media_type_raw = content_type.split(";")[0] if content_type else ""

                # Validate and use the media type if valid, otherwise guess from URL
                if media_type_raw in VALID_MEDIA_TYPES:
                    media_type: ImageMediaType = media_type_raw  # type: ignore[assignment]
                else:
                    media_type = self._get_media_type(url)

                # Encode to base64
                image_data = base64.standard_b64encode(response.content).decode("utf-8")
                return image_data, media_type

        except Exception as e:
            logger.warning("image_download_error", url=url, error=str(e))
            return None

    async def _build_image_block(self, url: str) -> ImageBlockParam | None:
        """Build an image block, downloading as base64 if needed for anti-bot sites.

        Args:
            url: Image URL.

        Returns:
            ImageBlockParam or None if image couldn't be loaded.
        """
        if self._needs_base64_download(url):
            result = await self._download_image_as_base64(url)
            if result is None:
                return None
            image_data, media_type = result
            return ImageBlockParam(
                type="image",
                source=Base64ImageSourceParam(
                    type="base64",
                    media_type=media_type,
                    data=image_data,
                ),
            )
        else:
            # Use URL-based image (Anthropic fetches directly)
            return ImageBlockParam(
                type="image",
                source=URLImageSourceParam(type="url", url=url),
            )

    async def analyze_merged_properties(
        self, properties: list[MergedProperty]
    ) -> list[tuple[MergedProperty, PropertyQualityAnalysis]]:
        """Analyze quality for pre-enriched merged properties.

        Properties should already have images and floorplan populated
        by the detail enrichment step.

        Args:
            properties: Enriched merged properties to analyze.

        Returns:
            List of (merged_property, analysis) tuples.
        """
        results: list[tuple[MergedProperty, PropertyQualityAnalysis]] = []

        for merged in properties:
            prop = merged.canonical
            value = assess_value(prop.price_pcm, prop.postcode, prop.bedrooms)

            # Build URL lists from pre-enriched images
            gallery_urls = [str(img.url) for img in merged.images if img.image_type == "gallery"]
            floorplan_url = str(merged.floorplan.url) if merged.floorplan else None

            if not gallery_urls and not floorplan_url:
                logger.info(
                    "no_images_for_analysis",
                    property_id=merged.unique_id,
                    sources=[s.value for s in merged.sources],
                )
                minimal = self._create_minimal_analysis(value=value)
                results.append((merged, minimal))
                continue

            # Use best description from descriptions dict
            best_description: str | None = None
            for desc in merged.descriptions.values():
                if desc and (not best_description or len(desc) > len(best_description)):
                    best_description = desc

            analysis = await self._analyze_property(
                merged.unique_id,
                gallery_urls=gallery_urls[: self._max_images],
                floorplan_url=floorplan_url,
                bedrooms=prop.bedrooms,
                price_pcm=prop.price_pcm,
                area_average=value.area_average,
                description=best_description,
                features=None,
            )

            if analysis:
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
                results.append((merged, analysis))
            else:
                minimal = self._create_minimal_analysis(value=value)
                results.append((merged, minimal))

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
        description: str | None = None,
        features: list[str] | None = None,
    ) -> PropertyQualityAnalysis | None:
        """Analyze a single property using Claude vision with structured outputs.

        Args:
            property_id: Property ID for logging.
            gallery_urls: List of gallery image URLs.
            floorplan_url: Floorplan URL if available.
            bedrooms: Number of bedrooms (for space assessment).
            price_pcm: Monthly rent price.
            area_average: Average rent for this area and bedroom count.
            description: Listing description text for cross-reference.
            features: Listed features for cross-reference.

        Returns:
            Analysis result or None if analysis failed.
        """
        client = self._get_client()

        # Build image content blocks (images first for best vision performance)
        # For anti-bot sites (Zoopla), download images locally and send as base64
        content: list[ImageBlockParam | TextBlockParam] = []

        # Add gallery images (up to max_images)
        for url in gallery_urls[: self._max_images]:
            image_block = await self._build_image_block(url)
            if image_block:
                content.append(image_block)

        # Add floorplan if available and is a supported image format
        # (PDFs are not supported by Claude Vision API)
        if floorplan_url and self._is_valid_image_url(floorplan_url):
            floorplan_block = await self._build_image_block(floorplan_url)
            if floorplan_block:
                content.append(floorplan_block)
        elif floorplan_url:
            logger.debug("skipping_pdf_floorplan", url=floorplan_url)

        # Build user prompt with property context and listing text
        effective_average = area_average or DEFAULT_BENCHMARK.get(min(bedrooms, 3), 1800)
        user_prompt = build_user_prompt(
            price_pcm, bedrooms, effective_average, description, features
        )
        content.append(TextBlockParam(type="text", text=user_prompt))

        has_usable_floorplan = floorplan_url is not None and self._is_valid_image_url(floorplan_url)
        logger.info(
            "analyzing_property",
            property_id=property_id,
            gallery_count=len(gallery_urls),
            has_floorplan=has_usable_floorplan,
        )

        try:
            # Use structured outputs via tool_choice for guaranteed valid JSON
            # System prompt uses cache_control for 90% cost savings on input tokens
            tool: ToolParam = QUALITY_ANALYSIS_TOOL  # type: ignore[assignment]
            response = await client.messages.create(
                model="claude-sonnet-4-5-20250929",
                max_tokens=2048,
                system=[
                    {
                        "type": "text",
                        "text": QUALITY_ANALYSIS_SYSTEM_PROMPT,
                        "cache_control": {"type": "ephemeral"},  # Cache for 5 mins
                    }
                ],
                messages=[{"role": "user", "content": content}],
                tools=[tool],
                tool_choice={"type": "tool", "name": "property_quality_analysis"},
            )

            # Log cache performance for monitoring
            if hasattr(response, "usage"):
                usage = response.usage
                cache_read = getattr(usage, "cache_read_input_tokens", 0)
                cache_creation = getattr(usage, "cache_creation_input_tokens", 0)
                if cache_read or cache_creation:
                    logger.debug(
                        "prompt_cache_stats",
                        property_id=property_id,
                        cache_read_tokens=cache_read,
                        cache_creation_tokens=cache_creation,
                    )

            # Check for safety refusal (stop_reason: "refusal")
            if response.stop_reason == "end_turn":
                # Check if we got a tool use block
                tool_use_block = next(
                    (block for block in response.content if isinstance(block, ToolUseBlock)),
                    None,
                )
                if not tool_use_block:
                    logger.warning(
                        "no_tool_use_in_response",
                        property_id=property_id,
                        stop_reason=response.stop_reason,
                    )
                    return None

                data: dict[str, Any] = tool_use_block.input

            elif response.stop_reason == "tool_use":
                # Normal tool use response
                tool_use_block = next(
                    (block for block in response.content if isinstance(block, ToolUseBlock)),
                    None,
                )
                if not tool_use_block:
                    logger.warning(
                        "tool_use_stop_but_no_block",
                        property_id=property_id,
                    )
                    return None

                data = tool_use_block.input

            else:
                # Unexpected stop reason (e.g., max_tokens, refusal)
                logger.warning(
                    "unexpected_stop_reason",
                    property_id=property_id,
                    stop_reason=response.stop_reason,
                    request_id=getattr(response, "_request_id", None),
                )
                return None

        except BadRequestError as e:
            # 400 - Invalid request (bad images, invalid params)
            logger.warning(
                "bad_request_error",
                property_id=property_id,
                error=str(e),
                request_id=getattr(e, "_request_id", None),
            )
            return None

        except RateLimitError as e:
            # 429 - Rate limited (SDK already retried, this means exhausted)
            logger.error(
                "rate_limit_exhausted",
                property_id=property_id,
                error=str(e),
                request_id=getattr(e, "_request_id", None),
            )
            return None

        except InternalServerError as e:
            # 5xx - Server error (SDK already retried)
            logger.error(
                "server_error",
                property_id=property_id,
                error=str(e),
                request_id=getattr(e, "_request_id", None),
            )
            return None

        except APIConnectionError as e:
            # Network issues (SDK already retried)
            logger.error(
                "connection_error",
                property_id=property_id,
                error=str(e),
            )
            return None

        except APIStatusError as e:
            # Other API errors
            logger.warning(
                "api_status_error",
                property_id=property_id,
                status_code=e.status_code,
                error=str(e),
                request_id=getattr(e, "_request_id", None),
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

        # Extract value_for_quality from tool response
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

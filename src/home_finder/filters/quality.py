"""Property quality analysis filter using Claude vision."""

import asyncio
import base64
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, get_args

from home_finder.data.area_context import (
    AREA_CONTEXT,
    COUNCIL_TAX_MONTHLY,
    CRIME_RATES,
    DEFAULT_BENCHMARK,
    OUTCODE_BOROUGH,
    RENT_TRENDS,
    RENTAL_BENCHMARKS,
)
from home_finder.logging import get_logger
from home_finder.models import (
    ConditionAnalysis,
    KitchenAnalysis,
    LightSpaceAnalysis,
    MergedProperty,
    PropertyQualityAnalysis,
    SpaceAnalysis,
    ValueAnalysis,
)
from home_finder.utils.image_cache import is_valid_image_url, read_image_bytes

if TYPE_CHECKING:
    import anthropic
    from anthropic.types import ImageBlockParam

logger = get_logger(__name__)

# Valid media types for Claude vision API
ImageMediaType = Literal["image/jpeg", "image/png", "image/gif", "image/webp"]
VALID_MEDIA_TYPES: tuple[str, ...] = get_args(ImageMediaType)

# Rate limit settings for Tier 1
# SDK handles retry automatically, we just need delay between calls
DELAY_BETWEEN_CALLS = 1.5  # seconds (50 RPM = 1.2s minimum, add buffer)

# SDK retry configuration
MAX_RETRIES = 3
REQUEST_TIMEOUT = 180.0  # 3 minutes for vision requests


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


# System prompt for quality analysis - cached for cost savings
QUALITY_ANALYSIS_SYSTEM_PROMPT = """\
You are an expert London rental property analyst with perfect vision \
and meticulous attention to detail.

Your analysis goes directly into a Telegram notification for a renter actively \
searching. Be concise, specific, and actionable — flag genuine concerns, skip \
hedging language.

When you cannot determine something from the images, use "unknown" or null. \
Do not guess — a confident "unknown" is more useful than a wrong answer.

<task>
Analyze property images (gallery photos and optional floorplan) together with \
listing text to produce a structured quality assessment. Cross-reference what \
you see in the images with the listing description — it often mentions \
"new kitchen", "gas hob", "recently refurbished" that confirm or clarify \
what's in the photos.
</task>

<stock_types>
First, identify the property type — this fundamentally affects expected pricing \
and what condition issues to look for:
- Victorian/Edwardian conversion: Period features, high ceilings, sash windows. \
Baseline East London stock. Watch for awkward subdivisions, original single \
glazing, rising damp, uneven floors.
- Purpose-built new-build / Build-to-Rent: Clean lines, uniform finish, large \
windows. Commands 15-30% premium but check for small rooms, thin partition \
walls, developer-grade finishes that wear quickly.
- Warehouse/industrial conversion: High ceilings, exposed brick, large windows. \
Premium pricing (especially E9 canalside). Watch for draughts, echo/noise, \
damp from inadequate conversion.
- Ex-council / post-war estate: Concrete construction, uniform exteriors, \
communal corridors. Should be 20-40% below area average. Communal area \
quality signals management standards.
- Georgian terrace: Grand proportions, original features. Premium stock.
</stock_types>

<listing_signals>
Scan the description for cost and quality signals:
- EPC rating: Band D-G = £50-150/month higher energy bills
- "Service charge" amount: Add to headline rent for true monthly cost
- "Rent-free weeks" or move-in incentives: Calculate effective monthly discount
- "Selective licensing" or licence number: Compliant landlord (positive)
- "Ground rent" or leasehold terms: Check for escalation clauses
- Proximity to active construction/regeneration: Short-term noise but \
potential rent increases (relevant in E9, E15, N17)
</listing_signals>

<analysis_steps>
1. Kitchen Quality: Modern (new units, integrated appliances, good worktops) \
vs Dated (old-fashioned units, worn surfaces, mismatched appliances). Note hob \
type if visible/mentioned. Check listing for "new kitchen", "recently fitted".

2. Property Condition: Look for damp (water stains, peeling paint near \
windows/ceilings), mold (dark patches in corners/bathrooms), worn fixtures \
(dated bathroom fittings, tired carpets, scuffed walls). Check stock-type-specific \
issues. Cross-reference listing mentions of "refurbished", "newly decorated".

3. Natural Light & Space: Window sizes, brightness, spacious vs cramped feel, \
ceiling heights if visible.

4. Living Room Size: From floorplan if included, estimate sqm. Target: fits a \
home office AND hosts 8+ people (~20-25 sqm minimum).

5. Value Assessment: Consider stock type (new-build at +15-30% is expected, \
Victorian at +15% is overpriced, ex-council at average is poor value). Factor \
area context, true monthly cost (council tax, service charges, EPC costs, \
rent-free incentives), crime context, and rent trend trajectory. Your reasoning \
should focus on price-side factors — don't restate condition details.

6. Overall Summary: 1-2 sentences — property character and what it's like to \
live here. Don't restate condition concerns (they're listed separately) or \
value analysis (separate field).

7. Overall Rating: 1-5 stars for rental desirability.
</analysis_steps>

<rating_criteria>
Overall rating (1-5 stars):
  5 = Exceptional: Modern/refurbished to high standard, excellent light/space, \
no concerns, good or excellent value. Rare find.
  4 = Good: Well-maintained, comfortable, minor issues at most. Fair or better value.
  3 = Acceptable: Liveable but with notable trade-offs (dated kitchen, limited \
light, average condition). Price should reflect this.
  2 = Below average: Multiple issues (poor condition, cramped, dated throughout). \
Only worth it if significantly below market.
  1 = Avoid: Serious problems (damp/mold, very poor condition, major red flags).

Value-for-quality rating:
  excellent = Quality clearly exceeds what this price normally buys in the area.
  good = Fair deal — quality matches or slightly exceeds the price point.
  fair = Typical for the price — no standout value, no major overpay.
  poor = Overpriced relative to quality/condition. Renter is overpaying.
</rating_criteria>

<output_rules>
Each output field appears in a different section of the notification. Avoid \
restating information across fields:
- maintenance_concerns: Specific condition issues (shown in ⚠️ section)
- summary: Property character, layout, standout features (shown in blockquote)
- value_for_quality.reasoning: Price analysis — why the price is/isn't fair \
(shown in 📊 section)
If a fact belongs in one field, don't repeat it in another.
</output_rules>

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
                        "description": (
                            "Value justification: why this price is or isn't fair for "
                            "what you get. Focus on price factors: stock type "
                            "premium/discount, true monthly cost, area rent trajectory, "
                            "service charges, incentives. Reference condition only as "
                            "'condition justifies/doesn't justify price' — don't "
                            "restate specific issues."
                        ),
                    },
                },
                "required": ["rating", "reasoning"],
                "additionalProperties": False,
            },
            "overall_rating": {
                "type": "integer",
                "description": "Overall 1-5 star rating for rental desirability (1=worst, 5=best)",
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
                "description": (
                    "1-2 sentence property overview for notification. Focus on what "
                    "it's like to live here: character, standout features, layout feel. "
                    "Do NOT restate condition concerns (already listed separately) or "
                    "price/value analysis (covered in value_for_quality)."
                ),
            },
        },
        "required": [
            "kitchen",
            "condition",
            "light_space",
            "space",
            "value_for_quality",
            "overall_rating",
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
    area_context: str | None = None,
    outcode: str | None = None,
    council_tax_band_c: int | None = None,
    crime_summary: str | None = None,
    rent_trend: str | None = None,
) -> str:
    """Build the user prompt with property-specific context."""
    diff = price_pcm - area_average
    if diff < -50:
        price_comparison = f"£{abs(diff)} below"
    elif diff > 50:
        price_comparison = f"£{diff} above"
    else:
        price_comparison = "at"

    prompt = f"<property>\nPrice: £{price_pcm:,}/month | Bedrooms: {bedrooms}"
    prompt += f" | Area avg: £{area_average:,}/month ({price_comparison})"
    if council_tax_band_c:
        true_cost = price_pcm + council_tax_band_c
        prompt += f"\nCouncil tax (Band C est.): £{council_tax_band_c}/month"
        prompt += f" → True monthly cost: ~£{true_cost:,}"
    prompt += "\n</property>"

    if area_context and outcode:
        prompt += f'\n\n<area_context outcode="{outcode}">\n{area_context}'
        if crime_summary:
            prompt += f"\nCrime: {crime_summary}"
        if rent_trend:
            prompt += f"\nRent trend: {rent_trend}"
        prompt += "\n</area_context>"

    if features:
        prompt += "\n\n<listing_features>\n"
        prompt += "\n".join(f"- {f}" for f in features[:15])
        prompt += "\n</listing_features>"

    if description:
        desc = description[:1500] + "..." if len(description) > 1500 else description
        prompt += f"\n\n<listing_description>\n{desc}\n</listing_description>"

    prompt += "\n\nProvide your quality assessment using the "
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

    def _get_client(self) -> "anthropic.AsyncAnthropic":
        """Get or create the Anthropic client with optimized settings."""
        if self._client is None:
            import anthropic as _anthropic
            import httpx

            self._client = _anthropic.AsyncAnthropic(
                api_key=self._api_key,
                max_retries=MAX_RETRIES,  # SDK handles retry with exponential backoff
                timeout=httpx.Timeout(REQUEST_TIMEOUT),  # 3 min for vision requests
            )
        return self._client

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
            from curl_cffi.requests import AsyncSession

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

    async def _build_image_block(
        self, url: str, cached_path: Path | None = None
    ) -> "ImageBlockParam | None":
        """Build an image block, using disk cache or downloading as needed.

        Args:
            url: Image URL.
            cached_path: Path to cached image on disk (if available).

        Returns:
            ImageBlockParam or None if image couldn't be loaded.
        """
        from anthropic.types import (
            Base64ImageSourceParam,
            ImageBlockParam,
            URLImageSourceParam,
        )

        # Try disk cache first — avoids all HTTP requests
        if cached_path is not None:
            data = read_image_bytes(cached_path)
            if data is not None:
                media_type = self._get_media_type(url)
                image_data = base64.standard_b64encode(data).decode("utf-8")
                return ImageBlockParam(
                    type="image",
                    source=Base64ImageSourceParam(
                        type="base64",
                        media_type=media_type,
                        data=image_data,
                    ),
                )

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
        self,
        properties: list[MergedProperty],
        *,
        data_dir: str | None = None,
    ) -> list[tuple[MergedProperty, PropertyQualityAnalysis]]:
        """Analyze quality for pre-enriched merged properties.

        Properties should already have images and floorplan populated
        by the detail enrichment step.

        Args:
            properties: Enriched merged properties to analyze.
            data_dir: Data directory for image cache. When set, reads
                cached images from disk instead of downloading.

        Returns:
            List of (merged_property, analysis) tuples.
        """
        results: list[tuple[MergedProperty, PropertyQualityAnalysis]] = []

        for merged in properties:
            prop = merged.canonical
            value = assess_value(prop.price_pcm, prop.postcode, prop.bedrooms)

            # Extract outcode for area context lookup
            outcode: str | None = None
            if prop.postcode:
                outcode = (
                    prop.postcode.split()[0].upper()
                    if " " in prop.postcode
                    else prop.postcode.upper()
                )
            area_context = AREA_CONTEXT.get(outcode) if outcode else None

            # Look up new contextual data
            borough = OUTCODE_BOROUGH.get(outcode) if outcode else None
            council_tax_c = COUNCIL_TAX_MONTHLY.get(borough, {}).get("C") if borough else None
            crime = CRIME_RATES.get(outcode) if outcode else None
            crime_summary: str | None = None
            if crime:
                crime_summary = f"{crime['rate']}/1,000 ({crime['vs_london']} vs London avg)"
                if crime.get("note"):
                    crime_summary += f". {crime['note']}"
            trend = RENT_TRENDS.get(borough) if borough else None
            rent_trend = f"+{trend['yoy_pct']}% YoY ({trend['direction']})" if trend else None

            # Build URL lists from pre-enriched images
            gallery_urls = [str(img.url) for img in merged.images if img.image_type == "gallery"]
            floorplan_url = str(merged.floorplan.url) if merged.floorplan else None

            # Resolve cached image paths (if data_dir is set)
            gallery_cached: list[Path | None] = []
            floorplan_cached: Path | None = None
            if data_dir:
                from home_finder.utils.image_cache import get_cached_image_path

                for idx, g_url in enumerate(gallery_urls):
                    p = get_cached_image_path(data_dir, merged.unique_id, g_url, "gallery", idx)
                    gallery_cached.append(p if p.is_file() else None)
                if floorplan_url:
                    p = get_cached_image_path(
                        data_dir, merged.unique_id, floorplan_url, "floorplan", 0
                    )
                    floorplan_cached = p if p.is_file() else None
            else:
                gallery_cached = [None] * len(gallery_urls)

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
                area_context=area_context,
                outcode=outcode,
                council_tax_band_c=council_tax_c,
                crime_summary=crime_summary,
                rent_trend=rent_trend,
                gallery_cached_paths=gallery_cached[: self._max_images],
                floorplan_cached_path=floorplan_cached,
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
                    overall_rating=analysis.overall_rating,
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
        area_context: str | None = None,
        outcode: str | None = None,
        council_tax_band_c: int | None = None,
        crime_summary: str | None = None,
        rent_trend: str | None = None,
        gallery_cached_paths: list[Path | None] | None = None,
        floorplan_cached_path: Path | None = None,
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
            area_context: Area context string for the outcode.
            outcode: Property outcode (e.g., "E8").
            council_tax_band_c: Estimated monthly council tax (Band C).
            crime_summary: Crime rate summary string.
            rent_trend: YoY rent trend string.

        Returns:
            Analysis result or None if analysis failed.
        """
        from anthropic import (
            APIConnectionError,
            APIStatusError,
            BadRequestError,
            InternalServerError,
            RateLimitError,
        )
        from anthropic.types import (
            ImageBlockParam,
            TextBlockParam,
            ToolParam,
            ToolUseBlock,
        )

        client = self._get_client()

        # Build image content blocks (images first for best vision performance)
        # For anti-bot sites (Zoopla), download images locally and send as base64
        content: list[ImageBlockParam | TextBlockParam] = []

        # Add gallery images with labels (up to max_images)
        gallery_num = 0
        cached_paths = gallery_cached_paths or [None] * len(gallery_urls)
        for idx, url in enumerate(gallery_urls[: self._max_images]):
            cached = cached_paths[idx] if idx < len(cached_paths) else None
            image_block = await self._build_image_block(url, cached_path=cached)
            if image_block:
                gallery_num += 1
                content.append(TextBlockParam(type="text", text=f"Gallery image {gallery_num}:"))
                content.append(image_block)

        # Add floorplan with label if available and is a supported image format
        # (PDFs are not supported by Claude Vision API)
        if floorplan_url and is_valid_image_url(floorplan_url):
            floorplan_block = await self._build_image_block(
                floorplan_url, cached_path=floorplan_cached_path
            )
            if floorplan_block:
                content.append(TextBlockParam(type="text", text="Floorplan:"))
                content.append(floorplan_block)
        elif floorplan_url:
            logger.debug("skipping_pdf_floorplan", url=floorplan_url)

        # Build user prompt with property context and listing text
        effective_average = area_average or DEFAULT_BENCHMARK.get(min(bedrooms, 3), 1800)
        user_prompt = build_user_prompt(
            price_pcm,
            bedrooms,
            effective_average,
            description,
            features,
            area_context=area_context,
            outcode=outcode,
            council_tax_band_c=council_tax_band_c,
            crime_summary=crime_summary,
            rent_trend=rent_trend,
        )
        content.append(TextBlockParam(type="text", text=user_prompt))

        has_usable_floorplan = floorplan_url is not None and is_valid_image_url(floorplan_url)
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
                overall_rating=data.get("overall_rating"),
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
                overall_rating=analysis.overall_rating,
                summary=analysis.summary,
            )

        return analysis

    async def close(self) -> None:
        """Close clients."""
        if self._client is not None:
            await self._client.close()
            self._client = None

"""Property quality analysis filter using Claude vision."""

import asyncio
import base64
import json
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, Literal, TypeAlias, TypeGuard, get_args

from PIL import Image

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
    BathroomAnalysis,
    BedroomAnalysis,
    ConditionAnalysis,
    FlooringNoiseAnalysis,
    KitchenAnalysis,
    LightSpaceAnalysis,
    ListingExtraction,
    ListingRedFlags,
    MergedProperty,
    OutdoorSpaceAnalysis,
    PropertyQualityAnalysis,
    SpaceAnalysis,
    StorageAnalysis,
    ValueAnalysis,
    ViewingNotes,
)
from home_finder.utils.image_cache import is_valid_image_url, read_image_bytes

if TYPE_CHECKING:
    import anthropic
    from anthropic.types import ImageBlockParam

logger = get_logger(__name__)

# Valid media types for Claude vision API
ImageMediaType: TypeAlias = Literal["image/jpeg", "image/png", "image/gif", "image/webp"]
VALID_MEDIA_TYPES: Final[tuple[str, ...]] = get_args(ImageMediaType)


def _is_valid_media_type(value: str) -> TypeGuard[ImageMediaType]:
    """Check if a string is a valid image media type for Claude vision API."""
    return value in VALID_MEDIA_TYPES


# Rate limit settings for Tier 2 (1,000 RPM)
# SDK handles retry automatically, we just need a small delay to avoid bursts
DELAY_BETWEEN_CALLS: Final = 0.2  # seconds (1000 RPM = 0.06s minimum, add buffer)

# SDK retry configuration
MAX_RETRIES: Final = 3
REQUEST_TIMEOUT: Final = 180.0  # 3 minutes for vision requests

# Anthropic recommends ≤1568px on longest edge for optimal performance.
# Also well under the 2000px hard limit for requests with >20 images.
MAX_IMAGE_DIMENSION: Final = 1568


def _resize_image_bytes(data: bytes, max_dim: int = MAX_IMAGE_DIMENSION) -> bytes:
    """Downscale image so longest edge <= max_dim. Returns original bytes if already small."""
    try:
        img = Image.open(BytesIO(data))
        w, h = img.size
        if w <= max_dim and h <= max_dim:
            return data
        scale = max_dim / max(w, h)
        new_size = (int(w * scale), int(h * scale))
        # Preserve format before resize (resize clears it)
        fmt = img.format or "JPEG"
        img = img.resize(new_size, Image.LANCZOS)
        buf = BytesIO()
        img.save(buf, format=fmt, quality=85)
        return buf.getvalue()
    except Exception:
        # If Pillow can't parse the image, return original bytes unchanged
        return data


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


# System prompt for Phase 1: Visual analysis - cached for cost savings
VISUAL_ANALYSIS_SYSTEM_PROMPT: Final = """\
You are an expert London rental property analyst with perfect vision \
and meticulous attention to detail.

Your task is to observe and assess property quality from images and \
cross-reference with listing text. A separate evaluation step will \
handle value assessment, viewing preparation, and curation — focus \
purely on what you can see and verify.

When you cannot determine something from the images, use the appropriate \
sentinel value: "unknown" for enum/string fields, false for boolean fields, \
"none" for concern_severity when there are no concerns. \
For has_visible_damp, has_visible_mold, has_double_glazing, \
has_washing_machine, is_ensuite, primary_is_double, and \
can_fit_desk use "yes"/"no"/"unknown" — these are tri-state string fields. \
Do not guess — a confident "unknown" is more useful than a wrong answer.

<task>
Analyze property images (gallery photos and optional floorplan) together with \
listing text to produce a structured visual quality assessment. Cross-reference \
what you see in the images with the listing description — it often mentions \
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
Scan the description for cost and quality signals to cross-reference with images:
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

5. Overall Summary: 1-2 sentences — property character and what it's like to \
live here. Don't restate condition concerns (they're listed separately).

6. Overall Rating: 1-5 stars for rental desirability.

7. Bathroom: Condition, bathtub presence, shower type (overhead, separate \
cubicle, electric), ensuite. Cross-ref "wet room", "new bathroom", \
"recently refurbished bathroom" in description.

8. Bedroom: Can primary bedroom fit a double bed + wardrobe + desk? Check \
floorplan room labels and dimensions. "Double room" claims are often dubious.

9. Storage: Built-in wardrobes, hallway cupboard, airing cupboard. London \
flats are notoriously storage-poor — flag when absent.

10. Outdoor Space: Balcony, garden, terrace, shared garden from photos or \
description. Premium London feature worth noting.

11. Flooring & Noise: Floor type (hardwood, laminate, carpet), double glazing \
presence, road-facing rooms, railway/traffic proximity indicators.

12. Red Flags: Missing room photos (no bathroom/kitchen photos), too few \
photos total (<4), selective angles hiding issues, description gaps or \
concerning language.
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
</rating_criteria>

<output_rules>
Each output field appears in a different section of the notification. Avoid \
restating information across fields:
- maintenance_concerns: Specific condition issues (shown in ⚠️ section)
- summary: Property character, layout, standout features (shown in blockquote)
If a fact belongs in one field, don't repeat it in another.
</output_rules>

Always use the property_visual_analysis tool to return your assessment."""


# System prompt for Phase 2: Evaluation - cached for cost savings
EVALUATION_SYSTEM_PROMPT: Final = """\
You are an expert London rental property evaluator. You have been given \
structured visual analysis observations from a detailed property inspection. \
Your job is to evaluate, synthesize, and prepare actionable information.

When you cannot determine something from the available data, use the appropriate \
sentinel value: "unknown" for enum/string fields. \
For bills_included and pets_allowed use "yes"/"no"/"unknown" — these are \
tri-state string fields. Only extract what is explicitly stated in the listing.

<task>
Given visual analysis observations and listing text, produce:
1. Structured data extraction from the listing description
2. Value-for-quality assessment grounded in the visual observations
3. Property-specific viewing preparation notes
4. Curated highlights and lowlights from the structured observations
5. A one-line property tagline
</task>

<evaluation_steps>
1. Listing Data Extraction: Mine the description for EPC rating, service \
charge, deposit weeks, bills included, pets allowed, parking, council tax \
band, property type, furnished status. Only extract what is explicitly stated.

2. Value Assessment: Consider stock type (new-build at +15-30% is expected, \
Victorian at +15% is overpriced, ex-council at average is poor value). Factor \
area context, true monthly cost (council tax, service charges, EPC costs, \
rent-free incentives), crime context, and rent trend trajectory. Ground your \
reasoning in the visual observations — reference specific findings like \
"modern kitchen" or "dated bathroom" to justify the rating. Your reasoning \
should focus on price-side factors — don't restate condition details.

3. Viewing Notes: Generate property-specific items to check during a viewing, \
questions for the letting agent, and quick deal-breaker tests. Base these on \
the visual analysis findings — if damp was flagged as unknown, suggest \
checking for it; if maintenance concerns were noted, suggest inspecting those \
areas. Be specific, not generic.

4. Highlights: Review all visual analysis sub-models and pick 3-5 most notable \
positive features as 1-3 word tags (e.g. "Gas hob", "Balcony", "High ceilings", \
"New bathroom", "Pets allowed"). Do NOT include EPC rating here.

5. Lowlights: Review all visual analysis sub-models and pick 1-3 most notable \
concerns or gaps as 1-3 word tags (e.g. "No dishwasher", "Street noise", \
"Small bedroom").

6. One-liner: 6-12 word tagline capturing the property's character \
(e.g. "Bright Victorian flat with period features and a modern kitchen"). \
Synthesize from the visual observations.
</evaluation_steps>

<value_rating_criteria>
Value-for-quality rating:
  excellent = Quality clearly exceeds what this price normally buys in the area.
  good = Fair deal — quality matches or slightly exceeds the price point.
  fair = Typical for the price — no standout value, no major overpay.
  poor = Overpriced relative to quality/condition. Renter is overpaying.
</value_rating_criteria>

Always use the property_evaluation tool to return your assessment."""


# Phase 1 tool schema: Visual analysis (perceptual observations)
# strict: true ensures schema-compliant responses (no null for bool fields, etc.)
VISUAL_ANALYSIS_TOOL: Final[dict[str, Any]] = {
    "name": "property_visual_analysis",
    "description": "Return visual property quality analysis results from images",
    "strict": True,
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
                        "type": "string",
                        "enum": ["gas", "electric", "induction", "unknown"],
                        "description": "Type of hob if visible or mentioned",
                    },
                    "has_dishwasher": {"type": "boolean"},
                    "has_washing_machine": {
                        "type": "string",
                        "enum": ["yes", "no", "unknown"],
                    },
                    "notes": {
                        "type": "string",
                        "description": "Notable kitchen features or concerns",
                    },
                },
                "required": [
                    "overall_quality",
                    "hob_type",
                    "has_dishwasher",
                    "has_washing_machine",
                    "notes",
                ],
                "additionalProperties": False,
            },
            "condition": {
                "type": "object",
                "properties": {
                    "overall_condition": {
                        "type": "string",
                        "enum": ["excellent", "good", "fair", "poor", "unknown"],
                    },
                    "has_visible_damp": {
                        "type": "string",
                        "enum": ["yes", "no", "unknown"],
                    },
                    "has_visible_mold": {
                        "type": "string",
                        "enum": ["yes", "no", "unknown"],
                    },
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
                        "type": "string",
                        "enum": ["large", "medium", "small", "unknown"],
                    },
                    "feels_spacious": {
                        "type": "boolean",
                        "description": "Whether the property feels spacious",
                    },
                    "ceiling_height": {
                        "type": "string",
                        "enum": ["high", "standard", "low", "unknown"],
                    },
                    "notes": {"type": "string"},
                },
                "required": [
                    "natural_light",
                    "window_sizes",
                    "feels_spacious",
                    "ceiling_height",
                    "notes",
                ],
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
                        "type": "boolean",
                        "description": "True if can fit office AND host 8+ people",
                    },
                    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                },
                "required": ["living_room_sqm", "is_spacious_enough", "confidence"],
                "additionalProperties": False,
            },
            "bathroom": {
                "type": "object",
                "properties": {
                    "overall_condition": {
                        "type": "string",
                        "enum": ["modern", "decent", "dated", "unknown"],
                    },
                    "has_bathtub": {"type": "boolean"},
                    "shower_type": {
                        "type": "string",
                        "enum": [
                            "overhead",
                            "separate_cubicle",
                            "electric",
                            "none",
                            "unknown",
                        ],
                    },
                    "is_ensuite": {
                        "type": "string",
                        "enum": ["yes", "no", "unknown"],
                    },
                    "notes": {"type": "string"},
                },
                "required": [
                    "overall_condition",
                    "has_bathtub",
                    "shower_type",
                    "is_ensuite",
                    "notes",
                ],
                "additionalProperties": False,
            },
            "bedroom": {
                "type": "object",
                "properties": {
                    "primary_is_double": {
                        "type": "string",
                        "enum": ["yes", "no", "unknown"],
                    },
                    "has_built_in_wardrobe": {"type": "boolean"},
                    "can_fit_desk": {
                        "type": "string",
                        "enum": ["yes", "no", "unknown"],
                    },
                    "notes": {"type": "string"},
                },
                "required": [
                    "primary_is_double",
                    "has_built_in_wardrobe",
                    "can_fit_desk",
                    "notes",
                ],
                "additionalProperties": False,
            },
            "outdoor_space": {
                "type": "object",
                "properties": {
                    "has_balcony": {"type": "boolean"},
                    "has_garden": {"type": "boolean"},
                    "has_terrace": {"type": "boolean"},
                    "has_shared_garden": {"type": "boolean"},
                    "notes": {"type": "string"},
                },
                "required": [
                    "has_balcony",
                    "has_garden",
                    "has_terrace",
                    "has_shared_garden",
                    "notes",
                ],
                "additionalProperties": False,
            },
            "storage": {
                "type": "object",
                "properties": {
                    "has_built_in_wardrobes": {"type": "boolean"},
                    "has_hallway_cupboard": {"type": "boolean"},
                    "storage_rating": {
                        "type": "string",
                        "enum": ["good", "adequate", "poor", "unknown"],
                    },
                },
                "required": [
                    "has_built_in_wardrobes",
                    "has_hallway_cupboard",
                    "storage_rating",
                ],
                "additionalProperties": False,
            },
            "flooring_noise": {
                "type": "object",
                "properties": {
                    "primary_flooring": {
                        "type": "string",
                        "enum": [
                            "hardwood",
                            "laminate",
                            "carpet",
                            "tile",
                            "mixed",
                            "unknown",
                        ],
                    },
                    "has_double_glazing": {
                        "type": "string",
                        "enum": ["yes", "no", "unknown"],
                    },
                    "noise_indicators": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "notes": {"type": "string"},
                },
                "required": [
                    "primary_flooring",
                    "has_double_glazing",
                    "noise_indicators",
                    "notes",
                ],
                "additionalProperties": False,
            },
            "listing_red_flags": {
                "type": "object",
                "properties": {
                    "missing_room_photos": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Rooms not shown in photos (e.g. 'bathroom', 'kitchen')",
                    },
                    "too_few_photos": {"type": "boolean"},
                    "selective_angles": {"type": "boolean"},
                    "description_concerns": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "red_flag_count": {
                        "type": "integer",
                        "description": "Total number of red flags identified",
                    },
                },
                "required": [
                    "missing_room_photos",
                    "too_few_photos",
                    "selective_angles",
                    "description_concerns",
                    "red_flag_count",
                ],
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
                "type": "string",
                "enum": ["minor", "moderate", "serious", "none"],
            },
            "summary": {
                "type": "string",
                "description": (
                    "1-2 sentence property overview for notification. Focus on what "
                    "it's like to live here: character, standout features, layout feel. "
                    "Do NOT restate condition concerns (already listed separately)."
                ),
            },
        },
        "required": [
            "kitchen",
            "condition",
            "light_space",
            "space",
            "bathroom",
            "bedroom",
            "outdoor_space",
            "storage",
            "flooring_noise",
            "listing_red_flags",
            "overall_rating",
            "condition_concerns",
            "concern_severity",
            "summary",
        ],
        "additionalProperties": False,
    },
}


# Phase 2 tool schema: Evaluation (synthesis and text extraction)
EVALUATION_TOOL: Final[dict[str, Any]] = {
    "name": "property_evaluation",
    "description": "Return property evaluation based on visual analysis observations",
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "listing_extraction": {
                "type": "object",
                "properties": {
                    "epc_rating": {
                        "type": "string",
                        "enum": ["A", "B", "C", "D", "E", "F", "G", "unknown"],
                    },
                    "service_charge_pcm": {
                        "anyOf": [{"type": "integer"}, {"type": "null"}],
                    },
                    "deposit_weeks": {
                        "anyOf": [{"type": "integer"}, {"type": "null"}],
                    },
                    "bills_included": {
                        "type": "string",
                        "enum": ["yes", "no", "unknown"],
                    },
                    "pets_allowed": {
                        "type": "string",
                        "enum": ["yes", "no", "unknown"],
                    },
                    "parking": {
                        "type": "string",
                        "enum": ["dedicated", "street", "none", "unknown"],
                    },
                    "council_tax_band": {
                        "type": "string",
                        "enum": ["A", "B", "C", "D", "E", "F", "G", "H", "unknown"],
                    },
                    "property_type": {
                        "type": "string",
                        "enum": [
                            "victorian",
                            "edwardian",
                            "georgian",
                            "new_build",
                            "purpose_built",
                            "warehouse",
                            "ex_council",
                            "period_conversion",
                            "unknown",
                        ],
                    },
                    "furnished_status": {
                        "type": "string",
                        "enum": [
                            "furnished",
                            "unfurnished",
                            "part_furnished",
                            "unknown",
                        ],
                    },
                },
                "required": [
                    "epc_rating",
                    "service_charge_pcm",
                    "deposit_weeks",
                    "bills_included",
                    "pets_allowed",
                    "parking",
                    "council_tax_band",
                    "property_type",
                    "furnished_status",
                ],
                "additionalProperties": False,
            },
            "viewing_notes": {
                "type": "object",
                "properties": {
                    "check_items": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Property-specific things to inspect during viewing",
                    },
                    "questions_for_agent": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Questions to ask the letting agent",
                    },
                    "deal_breaker_tests": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Quick tests to determine deal-breakers",
                    },
                },
                "required": ["check_items", "questions_for_agent", "deal_breaker_tests"],
                "additionalProperties": False,
            },
            "highlights": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Top 3-5 positive features as 1-3 word tags",
            },
            "lowlights": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Top 1-3 concerns as 1-3 word tags",
            },
            "one_line": {
                "type": "string",
                "description": "6-12 word tagline capturing the property's character",
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
        },
        "required": [
            "listing_extraction",
            "viewing_notes",
            "highlights",
            "lowlights",
            "one_line",
            "value_for_quality",
        ],
        "additionalProperties": False,
    },
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
        desc = description[:3000] + "..." if len(description) > 3000 else description
        prompt += f"\n\n<listing_description>\n{desc}\n</listing_description>"

    prompt += "\n\nProvide your visual quality assessment using the "
    prompt += "property_visual_analysis tool."

    return prompt


def build_evaluation_prompt(
    *,
    visual_data: dict[str, Any],
    description: str | None = None,
    price_pcm: int,
    bedrooms: int,
    area_average: int,
    area_context: str | None = None,
    outcode: str | None = None,
    council_tax_band_c: int | None = None,
    crime_summary: str | None = None,
    rent_trend: str | None = None,
) -> str:
    """Build the Phase 2 evaluation prompt with Phase 1 output and property context."""
    prompt = "<visual_analysis>\n"
    prompt += json.dumps(visual_data, indent=2)
    prompt += "\n</visual_analysis>"

    diff = price_pcm - area_average
    if diff < -50:
        price_comparison = f"£{abs(diff)} below"
    elif diff > 50:
        price_comparison = f"£{diff} above"
    else:
        price_comparison = "at"

    prompt += f"\n\n<property>\nPrice: £{price_pcm:,}/month | Bedrooms: {bedrooms}"
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

    if description:
        desc = description[:3000] + "..." if len(description) > 3000 else description
        prompt += f"\n\n<listing_description>\n{desc}\n</listing_description>"

    prompt += "\n\nBased on the visual analysis observations above, provide your "
    prompt += "evaluation using the property_evaluation tool."

    return prompt


class PropertyQualityFilter:
    """Analyze property quality using Claude vision API."""

    def __init__(
        self,
        api_key: str,
        max_images: int = 20,
        *,
        enable_extended_thinking: bool = True,
        thinking_budget_tokens: int = 10000,
    ) -> None:
        """Initialize the quality filter.

        Args:
            api_key: Anthropic API key.
            max_images: Maximum number of gallery images to analyze.
            enable_extended_thinking: Enable extended thinking for deeper analysis.
            thinking_budget_tokens: Token budget for extended thinking.
        """
        self._api_key = api_key
        self._max_images = max_images
        self._enable_extended_thinking = enable_extended_thinking
        self._thinking_budget_tokens = thinking_budget_tokens
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
                if _is_valid_media_type(media_type_raw):
                    media_type: ImageMediaType = media_type_raw
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
                data = _resize_image_bytes(data)
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
            resized = _resize_image_bytes(base64.standard_b64decode(image_data))
            image_data = base64.standard_b64encode(resized).decode("utf-8")
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

    async def analyze_single_merged(
        self,
        merged: MergedProperty,
        *,
        data_dir: str | None = None,
    ) -> tuple[MergedProperty, PropertyQualityAnalysis]:
        """Analyze quality for a single pre-enriched merged property.

        Does NOT sleep between calls — caller controls pacing.
        Handles errors internally (returns minimal analysis on failure).

        Args:
            merged: Enriched merged property to analyze.
            data_dir: Data directory for image cache.

        Returns:
            Tuple of (merged_property, analysis).
        """
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
            return merged, self._create_minimal_analysis(value=value)

        # Use best description from descriptions dict
        best_description: str | None = None
        for desc in merged.descriptions.values():
            if desc and (not best_description or len(desc) > len(best_description)):
                best_description = desc

        try:
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
        except Exception:
            logger.error(
                "analyze_single_merged_failed",
                property_id=merged.unique_id,
                exc_info=True,
            )
            return merged, self._create_minimal_analysis(value=value)

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
                bathroom=analysis.bathroom,
                bedroom=analysis.bedroom,
                outdoor_space=analysis.outdoor_space,
                storage=analysis.storage,
                flooring_noise=analysis.flooring_noise,
                listing_extraction=analysis.listing_extraction,
                listing_red_flags=analysis.listing_red_flags,
                viewing_notes=analysis.viewing_notes,
                highlights=analysis.highlights,
                lowlights=analysis.lowlights,
                one_line=analysis.one_line,
                condition_concerns=analysis.condition_concerns,
                concern_severity=analysis.concern_severity,
                value=merged_value,
                overall_rating=analysis.overall_rating,
                summary=analysis.summary,
            )
            return merged, analysis
        else:
            return merged, self._create_minimal_analysis(value=value)

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
            results.append(await self.analyze_single_merged(merged, data_dir=data_dir))
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
        """Analyze a single property using two-phase chained Claude analysis.

        Phase 1 (Visual): Images + text → structured observations (extended thinking)
        Phase 2 (Evaluation): Phase 1 output + text → value/viewing/curation (text-only)

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
            gallery_cached_paths: Paths to cached gallery images on disk.
            floorplan_cached_path: Path to cached floorplan image on disk.

        Returns:
            Analysis result or None if Phase 1 failed.
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

        # Cap gallery count so total images (gallery + floorplan) stays <= max_images.
        # >20 images triggers Anthropic's stricter 2000px dimension limit.
        has_floorplan = floorplan_url is not None and is_valid_image_url(floorplan_url)
        effective_max = self._max_images - (1 if has_floorplan else 0)

        # Add gallery images with labels (up to effective_max)
        gallery_num = 0
        cached_paths = gallery_cached_paths or [None] * len(gallery_urls)
        for idx, url in enumerate(gallery_urls[:effective_max]):
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

        # ── Phase 1: Visual Analysis ──────────────────────────────────
        visual_data: dict[str, Any] | None = None
        try:
            visual_tool: ToolParam = VISUAL_ANALYSIS_TOOL  # type: ignore[assignment]

            # Build API call kwargs
            create_kwargs: dict[str, Any] = {
                "model": "claude-sonnet-4-5-20250929",
                "max_tokens": 16384,
                "system": [
                    {
                        "type": "text",
                        "text": VISUAL_ANALYSIS_SYSTEM_PROMPT,
                        "cache_control": {"type": "ephemeral"},  # Cache for 5 mins
                    }
                ],
                "messages": [{"role": "user", "content": content}],
                "tools": [visual_tool],
            }

            # Extended thinking is incompatible with forced tool use;
            # use tool_choice: auto and rely on system prompt instruction
            if self._enable_extended_thinking:
                create_kwargs["thinking"] = {
                    "type": "enabled",
                    "budget_tokens": self._thinking_budget_tokens,
                }
                create_kwargs["tool_choice"] = {"type": "auto"}
            else:
                create_kwargs["tool_choice"] = {
                    "type": "tool",
                    "name": "property_visual_analysis",
                }

            response = await client.messages.create(**create_kwargs)

            # Log cache performance for monitoring
            if hasattr(response, "usage"):
                usage = response.usage
                cache_read = getattr(usage, "cache_read_input_tokens", 0)
                cache_creation = getattr(usage, "cache_creation_input_tokens", 0)
                if cache_read or cache_creation:
                    logger.debug(
                        "prompt_cache_stats",
                        property_id=property_id,
                        phase="visual",
                        cache_read_tokens=cache_read,
                        cache_creation_tokens=cache_creation,
                    )

            # Find ToolUseBlock regardless of stop_reason
            # (thinking blocks may appear first, then tool_use)
            tool_use_block = next(
                (block for block in response.content if isinstance(block, ToolUseBlock)),
                None,
            )
            if not tool_use_block:
                logger.warning(
                    "no_tool_use_in_response",
                    property_id=property_id,
                    phase="visual",
                    stop_reason=response.stop_reason,
                )
                return None

            visual_data = tool_use_block.input

        except BadRequestError as e:
            logger.warning(
                "visual_analysis_bad_request",
                property_id=property_id,
                error=str(e),
                request_id=getattr(e, "_request_id", None),
            )
            return None

        except RateLimitError as e:
            logger.error(
                "rate_limit_exhausted",
                property_id=property_id,
                phase="visual",
                error=str(e),
                request_id=getattr(e, "_request_id", None),
            )
            return None

        except InternalServerError as e:
            logger.error(
                "server_error",
                property_id=property_id,
                phase="visual",
                error=str(e),
                request_id=getattr(e, "_request_id", None),
            )
            return None

        except APIConnectionError as e:
            logger.error(
                "connection_error",
                property_id=property_id,
                phase="visual",
                error=str(e),
            )
            return None

        except APIStatusError as e:
            logger.warning(
                "api_status_error",
                property_id=property_id,
                phase="visual",
                status_code=e.status_code,
                error=str(e),
                request_id=getattr(e, "_request_id", None),
            )
            return None

        except Exception as e:
            logger.warning(
                "visual_analysis_failed",
                property_id=property_id,
                error=str(e),
                error_type=type(e).__name__,
            )
            return None

        logger.info("visual_analysis_complete", property_id=property_id)

        # ── Phase 2: Evaluation ───────────────────────────────────────
        eval_data: dict[str, Any] = {}
        try:
            eval_tool: ToolParam = EVALUATION_TOOL  # type: ignore[assignment]

            eval_prompt = build_evaluation_prompt(
                visual_data=visual_data,
                description=description,
                price_pcm=price_pcm,
                bedrooms=bedrooms,
                area_average=effective_average,
                area_context=area_context,
                outcode=outcode,
                council_tax_band_c=council_tax_band_c,
                crime_summary=crime_summary,
                rent_trend=rent_trend,
            )

            eval_response = await client.messages.create(
                model="claude-sonnet-4-5-20250929",
                max_tokens=4096,
                system=[
                    {
                        "type": "text",
                        "text": EVALUATION_SYSTEM_PROMPT,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[{"role": "user", "content": eval_prompt}],
                tools=[eval_tool],
                tool_choice={"type": "tool", "name": "property_evaluation"},
            )

            eval_block = next(
                (block for block in eval_response.content if isinstance(block, ToolUseBlock)),
                None,
            )
            if eval_block:
                eval_data = eval_block.input
            else:
                logger.warning(
                    "no_tool_use_in_response",
                    property_id=property_id,
                    phase="evaluation",
                    stop_reason=eval_response.stop_reason,
                )

        except Exception as e:
            logger.warning(
                "evaluation_phase_failed",
                property_id=property_id,
                error=str(e),
                error_type=type(e).__name__,
            )
            # Continue with partial analysis (visual data only)

        logger.info(
            "evaluation_complete",
            property_id=property_id,
            has_eval=bool(eval_data),
        )

        # ── Merge both phases into PropertyQualityAnalysis ─────────────

        # Extract value_for_quality from Phase 2 response
        value_for_quality = eval_data.pop("value_for_quality", {})
        quality_adjusted_rating = value_for_quality.get("rating")
        quality_adjusted_note = value_for_quality.get("reasoning", "")

        # Build value analysis with LLM assessment
        value = ValueAnalysis(
            quality_adjusted_rating=quality_adjusted_rating,
            quality_adjusted_note=quality_adjusted_note,
        )

        # Build the full analysis (with validation error handling)
        try:
            # Parse Phase 1 sub-models
            bathroom = (
                BathroomAnalysis(**visual_data["bathroom"])
                if visual_data.get("bathroom")
                else None
            )
            bedroom = (
                BedroomAnalysis(**visual_data["bedroom"])
                if visual_data.get("bedroom")
                else None
            )
            outdoor_space = (
                OutdoorSpaceAnalysis(**visual_data["outdoor_space"])
                if visual_data.get("outdoor_space")
                else None
            )
            storage_analysis = (
                StorageAnalysis(**visual_data["storage"])
                if visual_data.get("storage")
                else None
            )
            flooring_noise = (
                FlooringNoiseAnalysis(**visual_data["flooring_noise"])
                if visual_data.get("flooring_noise")
                else None
            )
            listing_red_flags = (
                ListingRedFlags(**visual_data["listing_red_flags"])
                if visual_data.get("listing_red_flags")
                else None
            )

            # Parse Phase 2 sub-models
            listing_extraction = (
                ListingExtraction(**eval_data["listing_extraction"])
                if eval_data.get("listing_extraction")
                else None
            )
            viewing_notes = (
                ViewingNotes(**eval_data["viewing_notes"])
                if eval_data.get("viewing_notes")
                else None
            )

            analysis = PropertyQualityAnalysis(
                kitchen=KitchenAnalysis(**visual_data.get("kitchen", {})),
                condition=ConditionAnalysis(**visual_data.get("condition", {})),
                light_space=LightSpaceAnalysis(**visual_data.get("light_space", {})),
                space=SpaceAnalysis(**visual_data.get("space", {})),
                bathroom=bathroom,
                bedroom=bedroom,
                outdoor_space=outdoor_space,
                storage=storage_analysis,
                flooring_noise=flooring_noise,
                listing_extraction=listing_extraction,
                listing_red_flags=listing_red_flags,
                viewing_notes=viewing_notes,
                highlights=eval_data.get("highlights"),
                lowlights=eval_data.get("lowlights"),
                one_line=eval_data.get("one_line"),
                condition_concerns=visual_data.get("condition_concerns", False),
                concern_severity=visual_data.get("concern_severity"),
                value=value,
                overall_rating=visual_data.get("overall_rating"),
                summary=visual_data.get("summary", "Analysis completed"),
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
                bathroom=analysis.bathroom,
                bedroom=analysis.bedroom,
                outdoor_space=analysis.outdoor_space,
                storage=analysis.storage,
                flooring_noise=analysis.flooring_noise,
                listing_extraction=analysis.listing_extraction,
                listing_red_flags=analysis.listing_red_flags,
                viewing_notes=analysis.viewing_notes,
                highlights=analysis.highlights,
                lowlights=analysis.lowlights,
                one_line=analysis.one_line,
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

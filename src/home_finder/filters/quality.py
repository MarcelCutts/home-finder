"""Property quality analysis filter using Claude vision."""

import asyncio
import base64
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, get_args

from pydantic import BaseModel, ConfigDict

from home_finder.logging import get_logger
from home_finder.models import MergedProperty
from home_finder.utils.image_cache import read_image_bytes

if TYPE_CHECKING:
    import anthropic
    from anthropic.types import ImageBlockParam

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
# Based on Rightmove/Zoopla asking prices and ONS Private Rent data (Jan-Feb 2026)
# Format: {outcode: {bedrooms: average_rent}}
RENTAL_BENCHMARKS: dict[str, dict[int, int]] = {
    # Hackney
    "E2": {1: 1950, 2: 2400, 3: 3100},
    "E5": {1: 1800, 2: 2200, 3: 2750},
    "E8": {1: 1900, 2: 2350, 3: 3000},
    "E9": {1: 1950, 2: 2400, 3: 2950},
    "N16": {1: 1800, 2: 2300, 3: 2950},
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
    "N15": {1: 1550, 2: 1850, 3: 2400},
    "N17": {1: 1650, 2: 2000, 3: 2550},
    "N22": {1: 1600, 2: 1950, 3: 2500},
    # Tower Hamlets
    "E1": {1: 2050, 2: 2550, 3: 3300},
    "E3": {1: 1800, 2: 2150, 3: 2700},
    "E14": {1: 2100, 2: 2600, 3: 3350},
    # Newham
    "E15": {1: 1950, 2: 2250, 3: 2800},
    # Waltham Forest
    "E10": {1: 1550, 2: 1750, 3: 2400},
    "E11": {1: 1550, 2: 1900, 3: 2450},
    "E17": {1: 1700, 2: 1850, 3: 2350},
}

# Default benchmark for unknown areas (East London average)
DEFAULT_BENCHMARK: dict[int, int] = {1: 1750, 2: 2100, 3: 2650}

# Area context for LLM quality analysis — concise renter-focused summaries per outcode
# Injected into the quality analysis prompt to inform value-for-quality ratings
AREA_CONTEXT: dict[str, str] = {
    "E3": (
        "Bow offers Zone 2 value near Victoria Park with District/H&C/Central line access. "
        "Value pockets in Victorian conversions near Roman Road; Fish Island canalside commands "
        "15-25% premiums. New-build developments (Bow Green, Fish Island Village) are pushing "
        "average prices higher. Watch for A12 noise/pollution, variable estate quality, and "
        "poor EPC ratings on cheap main-road offerings."
    ),
    "E5": (
        "Clapton is 15-20% cheaper than neighbouring Dalston (E8) and Stoke Newington (N16) "
        "with similar Victorian stock. Overground from Clapton/Homerton reaches Liverpool Street "
        "in under 15 min. Best value in Upper Clapton near Stamford Hill; Chatsworth Road "
        "artisan hub commands premiums. Clapton Park Estate 1970s blocks vary significantly in "
        "quality. Liveable Neighbourhood bus gates (Aug 2025) cause congestion on boundary roads."
    ),
    "E9": (
        "Hackney Wick commands premium rents due to Creative Enterprise Zone status and Olympic "
        "Park proximity. Overground reaches Liverpool Street in 15 min. Value in older Homerton "
        "stock; warehouse conversions and waterfront new-builds cost significantly more. Note: "
        "Fish Island is technically Tower Hamlets (E3), not Hackney—different council tax. "
        "Flood risk near canals and late-night venue noise affect some locations."
    ),
    "E10": (
        "Leyton offers the best value—10% below Walthamstow and 15-25% below Stratford—with "
        "Central Line access (12 min to Liverpool Street, 21 to Oxford Circus). Francis Road "
        "area combines period character with good amenities; proximity to QE Olympic Park and "
        "Hackney Marshes adds appeal. Flood risk near River Lea; High Road Leyton has higher "
        "crime rates than borough average. Waltham Forest requires selective landlord licensing."
    ),
    "E15": (
        "Stratford commands premium rents due to Elizabeth Line connectivity and Olympic legacy "
        "regeneration—London's second-most connected transport hub. Value pockets in Victorian "
        "terraces around Stratford Village and West Ham run 15-20% below purpose-built towers. "
        "Avoid Carpenters Estate (facing demolition/regen uncertainty). New-build service charges "
        "add £150-400/month; scrutinise ground rent terms. Flood risk near Stratford High Street."
    ),
    "E17": (
        "Walthamstow shows dramatic price variation between premium Village area and affordable "
        "Higham Hill/Wood Street pockets. Victoria Line reaches King's Cross in 15 minutes. "
        "Build-to-Rent developments (e.g. The Eades) offer 8 weeks rent-free (~15% effective "
        "discount)—factor incentives into comparisons. Waltham Forest operates borough-wide "
        "selective landlord licensing. Marlowe Road estate flagged for intervention."
    ),
    "N15": (
        "South Tottenham offers 15-20% savings versus neighbouring N16 with Victoria Line access "
        "(Seven Sisters to King's Cross in ~15 min). Zone between Seven Sisters and South "
        "Tottenham stations offers best balance of price and transport. Gradual gentrification "
        "spillover from N16; new BTR developments (Vabel Lawrence, Apex Gardens) adding modern "
        "stock. Markfield Road flood risk zone; Haringey crime rate 14% above London average "
        "with violence/theft concentrating around transport hubs."
    ),
    "N16": (
        "Stoke Newington commands 15-20% premiums over N15/N17 reflecting its village feel, "
        "independent shops, and excellent schools. Church Street core has highest rents; prime "
        "houses near Clissold Park reach £1,800/week. Value pockets near Hackney Downs and "
        "Stamford Hill. Woodberry Down regeneration (5,500 homes by 2035) may ease supply. "
        "Manor House tube (Piccadilly) nearby but no direct tube; above-average property crime."
    ),
    "N17": (
        "Tottenham Hale has a stark two-tier market: Build-to-Rent developments (Heart of Hale, "
        "Hale Village, The Gessner—£2,170-2,615 for 1-beds with gym/concierge) vs older "
        "Victorian conversions at 20-30% less. Victoria Line reaches King's Cross in 12 min; "
        "direct trains to Stansted. Northumberland Park area remains challenging. Higher crime "
        "in Tottenham Hale ward; flood risk near waterways."
    ),
}


# Borough for each search outcode (for council tax / rent trend lookup)
OUTCODE_BOROUGH: dict[str, str] = {
    "E3": "Tower Hamlets",
    "E5": "Hackney",
    "E9": "Hackney",
    "E10": "Waltham Forest",
    "E15": "Newham",
    "E17": "Waltham Forest",
    "N15": "Haringey",
    "N16": "Hackney",
    "N17": "Haringey",
}

# Council tax monthly £ by borough and band (2025-26)
COUNCIL_TAX_MONTHLY: dict[str, dict[str, int]] = {
    "Tower Hamlets": {"A": 97, "B": 114, "C": 130, "D": 146},
    "Hackney": {"A": 109, "B": 127, "C": 146, "D": 164},
    "Waltham Forest": {"A": 127, "B": 148, "C": 169, "D": 190},
    "Newham": {"A": 96, "B": 112, "C": 128, "D": 144},
    "Haringey": {"A": 123, "B": 143, "C": 164, "D": 184},
}

# Crime rate per 1,000 residents (London avg = 85)
CRIME_RATES: dict[str, dict[str, Any]] = {
    "E3": {"rate": 125, "vs_london": "+47%", "risk": "medium"},
    "E5": {
        "rate": 150,
        "vs_london": "+76%",
        "risk": "medium-high",
        "note": "varies 47-354 within postcode",
    },
    "E9": {"rate": 165, "vs_london": "+94%", "risk": "medium-high"},
    "E10": {
        "rate": 110,
        "vs_london": "+29%",
        "risk": "medium",
        "note": "High Road 264 vs residential 67",
    },
    "E15": {
        "rate": 150,
        "vs_london": "+76%",
        "risk": "medium",
        "note": "retail skews to 398",
    },
    "E17": {"rate": 95, "vs_london": "+12%", "risk": "low-medium"},
    "N15": {"rate": 143, "vs_london": "+68%", "risk": "medium-high"},
    "N16": {
        "rate": 125,
        "vs_london": "+47%",
        "risk": "medium",
        "note": "Church St 170 vs High St 82",
    },
    "N17": {"rate": 143, "vs_london": "+68%", "risk": "medium-high"},
}

# YoY rent trend by borough (applied to outcodes via OUTCODE_BOROUGH)
RENT_TRENDS: dict[str, dict[str, Any]] = {
    "Tower Hamlets": {"yoy_pct": 2.6, "direction": "rising"},
    "Hackney": {"yoy_pct": 4.3, "direction": "rising"},
    "Waltham Forest": {"yoy_pct": 2.8, "direction": "rising"},
    "Newham": {"yoy_pct": 8.9, "direction": "rising strongly"},
    "Haringey": {"yoy_pct": 4.7, "direction": "rising"},
}


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

    # Overall star rating (1-5, from LLM)
    overall_rating: int | None = None

    # For notifications
    summary: str


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
        if floorplan_url and self._is_valid_image_url(floorplan_url):
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

"""Property quality analysis filter using Claude vision."""

import asyncio
import base64
import functools
import json as _json
import re as _re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from home_finder.data.area_context import (
    ACOUSTIC_PROFILES,
    DEFAULT_BENCHMARK,
    RENTAL_BENCHMARKS,
    build_property_context,
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
    PropertyHighlight,
    PropertyLowlight,
    PropertyQualityAnalysis,
    SpaceAnalysis,
    StorageAnalysis,
    ValueAnalysis,
    ViewingNotes,
)
from home_finder.models.quality import _QUALITY_SUB_MODEL_FIELDS
from home_finder.utils.image_cache import find_cached_file, is_valid_image_url, read_image_bytes
from home_finder.utils.image_processing import (
    ImageMediaType,
)
from home_finder.utils.image_processing import (
    resize_image_bytes as _resize_image_bytes,
)

if TYPE_CHECKING:
    import anthropic
    from anthropic.types import ImageBlockParam

logger = get_logger(__name__)

# Rate limit settings for Tier 2 (1,000 RPM)
# SDK handles retry automatically, we just need a small delay to avoid bursts
DELAY_BETWEEN_CALLS: Final = 0.2  # seconds (1000 RPM = 0.06s minimum, add buffer)

# SDK retry configuration
MAX_RETRIES: Final = 3
REQUEST_TIMEOUT: Final = 180.0  # 3 minutes for vision requests

# Circuit breaker: stop hammering the API after consecutive outage-indicating errors
_CIRCUIT_BREAKER_THRESHOLD: Final = 3
_CIRCUIT_BREAKER_COOLDOWN: Final = 300  # seconds (5 min)

from home_finder.utils.circuit_breaker import (  # noqa: E402
    APIUnavailableError as APIUnavailableError,
)


@dataclass
class TokenUsage:
    """Accumulated API token usage across analysis calls."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0

    def add_from_response(self, usage: Any) -> None:
        """Accumulate token counts from an Anthropic response.usage object."""
        self.input_tokens += getattr(usage, "input_tokens", 0) or 0
        self.output_tokens += getattr(usage, "output_tokens", 0) or 0
        self.cache_read_tokens += getattr(usage, "cache_read_input_tokens", 0) or 0
        self.cache_creation_tokens += getattr(usage, "cache_creation_input_tokens", 0) or 0

    @property
    def estimated_cost_usd(self) -> float:
        """Estimate cost using Sonnet pricing (per million tokens)."""
        # Sonnet: $3/MTok input, $15/MTok output, $0.30/MTok cache read, $3.75/MTok cache write
        return (
            self.input_tokens * 3.0
            + self.output_tokens * 15.0
            + self.cache_read_tokens * 0.30
            + self.cache_creation_tokens * 3.75
        ) / 1_000_000


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


from home_finder.filters.quality_prompts import (  # noqa: E402
    EVALUATION_SYSTEM_PROMPT,
    VISUAL_ANALYSIS_SYSTEM_PROMPT,
    build_evaluation_prompt,
    build_user_prompt,
)

# ── Response models for tool schema generation ──────────────────────────
# These define the strict schemas sent to the Anthropic API. They differ from
# the storage models (models.py) which allow None/defaults for backward compat.
_Forbid = ConfigDict(extra="forbid")


class _VisualAnalysisResponse(BaseModel):
    """Phase 1 tool response: visual observations from property images."""

    model_config = _Forbid

    class Kitchen(BaseModel):
        model_config = _Forbid
        overall_quality: Literal["modern", "decent", "dated", "unknown"] = Field(
            description="Overall kitchen quality/age assessment"
        )
        hob_type: Literal["gas", "electric", "induction", "unknown"] = Field(
            description="Type of hob if visible or mentioned"
        )
        has_dishwasher: Literal["yes", "no", "unknown"] = Field(
            description=(
                "Whether a dishwasher is visible or mentioned. Look for: freestanding "
                "or semi-integrated units near the sink, EU energy rating stickers "
                "(coloured A-G labels), control panels along the top edge, brand logos, "
                "or handles/panels that differ from adjacent cabinets. Fully integrated "
                "dishwashers behind matching cabinet fronts are hard to spot — use "
                '"unknown" unless you see a clear indicator or the listing mentions one.'
            ),
        )
        has_washing_machine: Literal["yes", "no", "unknown"] = Field(
            description=(
                "Whether a washing machine is visible or mentioned. Look for: "
                "freestanding units (usually in the kitchen or a utility area), "
                'porthole door, control dials, or listing mentions. Use "unknown" '
                "if no laundry appliance is visible and the listing doesn't mention one."
            ),
        )
        notes: str = Field(description="Notable kitchen features or concerns")

    class Condition(BaseModel):
        model_config = _Forbid
        overall_condition: Literal["excellent", "good", "fair", "poor", "unknown"]
        has_visible_damp: Literal["yes", "no", "unknown"] = Field(
            description=(
                "Whether visible damp is present. Look for: water stains on "
                "ceilings/walls, peeling or bubbling paint near windows, tide marks "
                "on lower walls (rising damp), discolouration around pipes. "
                "Victorian/Edwardian conversions are particularly prone to rising "
                'damp. Use "unknown" if walls/ceilings are not clearly shown.'
            ),
        )
        has_visible_mold: Literal["yes", "no", "unknown"] = Field(
            description=(
                "Whether visible mold is present. Look for: dark clusters in "
                "bathroom corners, around window frames, or on ceilings — black "
                "mold appears as dark spotty patches. Check bathroom and kitchen "
                'photos especially. Use "unknown" if wet areas are not shown.'
            ),
        )
        has_worn_fixtures: Literal["yes", "no", "unknown"] = Field(
            description=(
                "Whether fixtures look worn or dated. Look for: chipped or "
                "stained bathroom fittings, tired carpets, scuffed walls, old-style "
                'light switches/sockets, worn cupboard edges. Use "unknown" if '
                "photos are too few or angled to hide fixture condition."
            ),
        )
        maintenance_concerns: list[str] = Field(description="List of specific maintenance concerns")
        confidence: Literal["high", "medium", "low"]

    class LightSpace(BaseModel):
        model_config = _Forbid
        natural_light: Literal["excellent", "good", "fair", "poor", "unknown"]
        window_sizes: Literal["large", "medium", "small", "unknown"]
        feels_spacious: bool = Field(description="Whether the property feels spacious")
        ceiling_height: Literal["high", "standard", "low", "unknown"]
        floor_level: Literal["basement", "ground", "lower", "upper", "top", "unknown"] = Field(
            description="Estimated floor level from photos/description/floorplan"
        )
        notes: str

    class Space(BaseModel):
        model_config = _Forbid
        living_room_sqm: float | None = Field(
            description="Estimated living room size in sqm from floorplan"
        )
        total_area_sqm: float | None = Field(
            default=None,
            description=(
                "Estimated total floor area in sqm by summing all rooms from the floorplan. "
                "Include all rooms: bedrooms, living room, kitchen, bathroom, hallway, storage. "
                "Only estimate if a floorplan with dimensions is available. null if no floorplan."
            ),
        )
        is_spacious_enough: bool = Field(description="True if can fit office AND host 8+ people")
        hosting_layout: Literal["excellent", "good", "awkward", "poor", "unknown"] = Field(
            description=(
                "Layout flow for hosting: excellent = open-plan kitchen/living + accessible "
                "bathroom + practical entrance; good = mostly good flow; awkward = hosting "
                "friction (disconnected kitchen, narrow entrance, guests pass bedrooms); "
                "poor = fundamentally unsuitable (through-rooms, isolated kitchen)"
            ),
        )

    class Bathroom(BaseModel):
        model_config = _Forbid
        overall_condition: Literal["modern", "decent", "dated", "unknown"]
        has_bathtub: Literal["yes", "no", "unknown"] = Field(
            description=(
                "Whether a bathtub is visible. Many London flats are shower-only "
                "— this is documentation, not a negative. Check for bath/shower "
                'combo or standalone tub. Use "unknown" if the bathroom is not '
                "fully shown in photos."
            ),
        )
        shower_type: Literal["overhead", "separate_cubicle", "electric", "none", "unknown"] = Field(
            description=(
                "Shower type: overhead = rain or fixed head over bath/walk-in; "
                "separate_cubicle = standalone enclosed shower (not over bath); "
                "electric = wall-mounted unit with built-in heater (white box, "
                "dial — common in older UK flats, signals weak hot water system); "
                'none = no shower visible. Use "unknown" if bathroom not shown.'
            ),
        )
        is_ensuite: Literal["yes", "no", "unknown"] = Field(
            description=(
                "Whether a bathroom is ensuite (accessed from inside a bedroom). "
                "Check floorplan layout — ensuite doors open from the bedroom, "
                'not the hallway. Use "unknown" if no floorplan and photos are '
                "ambiguous."
            ),
        )
        notes: str

    class Bedroom(BaseModel):
        model_config = _Forbid
        primary_is_double: Literal["yes", "no", "unknown"] = Field(
            description=(
                "Whether the primary bedroom can fit a double bed (≥1.35m wide). "
                'Check floorplan dimensions if available — "double room" claims in '
                'listings are often dubious for rooms under 3m wide. Use "unknown" '
                "if no floorplan and photos don't show enough of the room."
            ),
        )
        has_built_in_wardrobe: Literal["yes", "no", "unknown"] = Field(
            description=(
                "Whether the primary bedroom has a built-in wardrobe. Look for: "
                "wardrobe doors (sliding or hinged) along one wall, commonly found "
                'in purpose-built and new-build flats. Use "unknown" if bedroom '
                "photos don't show all walls."
            ),
        )
        can_fit_desk: Literal["yes", "no", "unknown"] = Field(
            description=(
                "Whether a desk (~1.2m wide) could fit in any bedroom or "
                "dedicated space. Check floorplan dimensions and photos for "
                'available wall space beyond bed and wardrobe. Prefer "unknown" '
                'over "no" unless the room is clearly too small or the floorplan '
                "confirms insufficient space."
            ),
        )
        office_separation: Literal[
            "dedicated_room", "separate_area", "shared_space", "none", "unknown"
        ] = Field(
            description=(
                "Quality of work-life separation: dedicated_room = closable room for "
                "office (2-bed with non-through second room); separate_area = alcove, "
                "mezzanine, or partitioned nook; shared_space = desk in living room, "
                "no separation; none = studio or nowhere viable"
            ),
        )
        notes: str

    class OutdoorSpace(BaseModel):
        model_config = _Forbid
        has_balcony: bool
        has_garden: bool
        has_terrace: bool
        has_shared_garden: bool
        notes: str

    class Storage(BaseModel):
        model_config = _Forbid
        has_built_in_wardrobes: Literal["yes", "no", "unknown"] = Field(
            description=(
                "Whether the property has built-in wardrobes in any bedroom. "
                "Look for wardrobe doors in bedroom photos. Common in purpose-built "
                'and new-build flats. Use "unknown" if bedrooms are not fully shown.'
            ),
        )
        has_hallway_cupboard: Literal["yes", "no", "unknown"] = Field(
            description=(
                "Whether there is a hallway storage cupboard (airing cupboard, "
                "coat cupboard, or utility cupboard). Look for doors in hallway "
                "photos. Common in ex-council and purpose-built flats. Use "
                '"unknown" if hallway is not shown in photos.'
            ),
        )
        storage_rating: Literal["good", "adequate", "poor", "unknown"]

    class FlooringNoise(BaseModel):
        model_config = _Forbid
        primary_flooring: Literal["hardwood", "laminate", "carpet", "tile", "mixed", "unknown"]
        has_double_glazing: Literal["yes", "no", "unknown"] = Field(
            description=(
                "Whether windows are double-glazed. Look for: thick uPVC frames "
                "(white plastic, ~60mm deep), sealed double-pane units visible in "
                "profile, or listing mentions. Single glazing: thin timber sash "
                "frames with visible putty (common in unconverted Victorian/ "
                'Edwardian properties). Prefer "unknown" over "no" unless you '
                "can clearly see single-pane windows or the listing states it."
            ),
        )
        building_construction: Literal[
            "solid_brick", "concrete", "timber_frame", "mixed", "unknown"
        ] = Field(description="Building construction type estimated from visual cues")
        noise_indicators: list[str]
        hosting_noise_risk: Literal["low", "moderate", "high", "unknown"] = Field(
            description=(
                "Risk of disturbing neighbours when hosting: low = solid construction + "
                "carpet + top floor/detached; moderate = mixed signals; high = timber "
                "frame + hard floors + lower floor + shared walls"
            ),
        )
        notes: str

    class RedFlags(BaseModel):
        model_config = _Forbid
        missing_room_photos: list[str] = Field(
            description="Rooms not shown in photos (e.g. 'bathroom', 'kitchen')"
        )
        too_few_photos: bool
        selective_angles: bool
        description_concerns: list[str]
        red_flag_count: int = Field(description="Total number of red flags identified")

    kitchen: Kitchen
    condition: Condition
    light_space: LightSpace
    space: Space
    bathroom: Bathroom
    bedroom: Bedroom
    outdoor_space: OutdoorSpace
    storage: Storage
    flooring_noise: FlooringNoise
    listing_red_flags: RedFlags
    floorplan_detected_in_gallery: list[int] = Field(
        default_factory=list,
        description=(
            "1-based gallery image indices that appear to be floorplans or floor plan diagrams. "
            "Empty list if no gallery images look like floorplans."
        ),
    )
    overall_rating: int = Field(
        description="Overall 1-5 star rating for rental desirability (1=worst, 5=best)"
    )
    condition_concerns: bool = Field(description="True if any significant condition issues found")
    concern_severity: Literal["minor", "moderate", "serious", "none"]
    summary: str = Field(
        description=(
            "1-2 sentence property overview for notification. Focus on what "
            "it's like to live here: character, standout features, layout feel. "
            "Do NOT restate condition concerns (already listed separately)."
        ),
    )


class _EvaluationResponse(BaseModel):
    """Phase 2 tool response: evaluation and synthesis."""

    model_config = _Forbid

    class ListingExtraction(BaseModel):
        model_config = _Forbid
        epc_rating: Literal["A", "B", "C", "D", "E", "F", "G", "unknown"]
        service_charge_pcm: int | None
        deposit_weeks: int | None
        bills_included: Literal["yes", "no", "unknown"]
        pets_allowed: Literal["yes", "no", "unknown"]
        parking: Literal["dedicated", "street", "none", "unknown"]
        council_tax_band: Literal["A", "B", "C", "D", "E", "F", "G", "H", "unknown"]
        property_type: Literal[
            "victorian",
            "edwardian",
            "georgian",
            "new_build",
            "purpose_built",
            "warehouse",
            "ex_council",
            "period_conversion",
            "unknown",
        ]
        furnished_status: Literal["furnished", "unfurnished", "part_furnished", "unknown"]
        broadband_type: Literal["fttp", "fttc", "cable", "standard", "unknown"] = Field(
            description=(
                "Broadband type from listing: fttp = fibre/FTTP/FTTH/Hyperoptic/"
                "Community Fibre/full fibre/1Gbps; fttc = superfast/FTTC/up to 80Mbps; "
                "cable = Virgin Media/cable; standard = broadband alone/ADSL"
            ),
        )

    class ViewingNotes(BaseModel):
        model_config = _Forbid
        check_items: list[str] = Field(
            description="Property-specific things to inspect during viewing"
        )
        questions_for_agent: list[str] = Field(description="Questions to ask the letting agent")
        deal_breaker_tests: list[str] = Field(description="Quick tests to determine deal-breakers")

    class ValueForQuality(BaseModel):
        model_config = _Forbid
        rating: Literal["excellent", "good", "fair", "poor"] = Field(
            description="Value rating considering quality vs price"
        )
        reasoning: str = Field(
            description=(
                "Value justification: why this price is or isn't fair for "
                "what you get. Focus on price factors: stock type "
                "premium/discount, true monthly cost, area rent trajectory, "
                "service charges, incentives. Reference condition only as "
                "'condition justifies/doesn't justify price' — don't "
                "restate specific issues."
            ),
        )

    listing_extraction: ListingExtraction
    viewing_notes: ViewingNotes
    highlights: list[Literal[tuple(PropertyHighlight)]] = Field(  # type: ignore[valid-type]
        description="Top 3-5 positive features from the allowed highlight tags"
    )
    lowlights: list[Literal[tuple(PropertyLowlight)]] = Field(  # type: ignore[valid-type]
        description="Top 1-3 concerns from the allowed lowlight tags"
    )
    one_line: str = Field(description="6-12 word tagline capturing the property's character")
    value_for_quality: ValueForQuality


# ── Cross-reference: API response models ↔ storage models ────────────
# Each pair maps an API sub-model (used for JSON schema generation) to
# its storage counterpart (used for DB persistence with defaults/coercion).
#
# Intentionally unpaired:
#   - _EvaluationResponse.ValueForQuality (2 fields) vs ValueAnalysis (6 fields):
#     different structures by design — ValueAnalysis merges benchmark data with
#     LLM-assessed quality rating.
#   - _VisualAnalysisResponse.floorplan_detected_in_gallery: API-only field,
#     used during analysis but not stored as a separate model.
_MODEL_PAIRS: Final[list[tuple[type[BaseModel], type[BaseModel]]]] = [
    (_VisualAnalysisResponse.Kitchen, KitchenAnalysis),
    (_VisualAnalysisResponse.Condition, ConditionAnalysis),
    (_VisualAnalysisResponse.LightSpace, LightSpaceAnalysis),
    (_VisualAnalysisResponse.Space, SpaceAnalysis),
    (_VisualAnalysisResponse.Bathroom, BathroomAnalysis),
    (_VisualAnalysisResponse.Bedroom, BedroomAnalysis),
    (_VisualAnalysisResponse.OutdoorSpace, OutdoorSpaceAnalysis),
    (_VisualAnalysisResponse.Storage, StorageAnalysis),
    (_VisualAnalysisResponse.FlooringNoise, FlooringNoiseAnalysis),
    (_VisualAnalysisResponse.RedFlags, ListingRedFlags),
    (_EvaluationResponse.ListingExtraction, ListingExtraction),
    (_EvaluationResponse.ViewingNotes, ViewingNotes),
]


def _inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Resolve $ref/$defs in a Pydantic JSON schema and strip title fields."""
    defs = schema.pop("$defs", {})

    def _resolve(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                ref_name = node["$ref"].rsplit("/", 1)[-1]
                return _resolve(dict(defs[ref_name]))
            return {k: _resolve(v) for k, v in node.items() if k != "title"}
        if isinstance(node, list):
            return [_resolve(item) for item in node]
        return node

    result: dict[str, Any] = _resolve(schema)
    result.pop("title", None)
    return result


def _build_tool_schema(
    name: str,
    description: str,
    model: type[BaseModel],
    *,
    strict: bool = False,
) -> dict[str, Any]:
    """Build an Anthropic tool schema from a Pydantic model class.

    Schemas are generated from the API response models (``extra="forbid"``,
    all-required, plain types) rather than the storage models (defaults,
    coercion validators).  The ``_MODEL_PAIRS`` constant documents the
    correspondence between each pair, and tests in
    ``TestModelPairConsistency`` mechanically verify they stay in sync.
    """
    schema = _inline_refs(model.model_json_schema())
    schema.pop("description", None)  # Strip Pydantic docstring
    tool: dict[str, Any] = {
        "name": name,
        "description": description,
        "input_schema": schema,
    }
    if strict:
        tool["strict"] = True
    return tool


VISUAL_ANALYSIS_TOOL: Final[dict[str, Any]] = _build_tool_schema(
    "property_visual_analysis",
    "Return visual property quality analysis results from images",
    _VisualAnalysisResponse,
    # No strict=True — schema exceeds Anthropic grammar compilation limits
    # (55 properties across 10 nested objects, ~7.8KB)
)


def _attempt_json_repair(s: str) -> dict[str, Any] | list[Any] | None:
    """Best-effort repair of common LLM JSON malformations.

    Uses the ``json_repair`` library (parser-based) to handle:
    - Missing colons between key-value pairs
    - Set-literal syntax (valueless keys like ``"primary_is_double"``)
    - Trailing commas, single quotes, Python booleans, and more
    """
    from json_repair import loads as repair_loads

    try:
        parsed = repair_loads(s)
        if isinstance(parsed, (dict, list)):
            return parsed
    except Exception:
        pass
    return None


def _clean_value(val: Any) -> Any:
    """Clean a single response value — parse JSON strings that should be dicts/lists."""
    if not isinstance(val, str):
        return val
    s = val.strip()
    if (s.startswith("{") and s.endswith("}")) or (s.startswith("[") and s.endswith("]")):
        try:
            parsed = _json.loads(s)
            if isinstance(parsed, (dict, list)):
                return parsed
        except (ValueError, _json.JSONDecodeError):
            # Retry after stripping trailing commas (common LLM quirk)
            sanitized = _re.sub(r",\s*([}\]])", r"\1", s)
            try:
                parsed = _json.loads(sanitized)
                if isinstance(parsed, (dict, list)):
                    return parsed
            except (ValueError, _json.JSONDecodeError):
                pass
            # Third pass: best-effort structural repair (missing colons, etc.)
            repaired = _attempt_json_repair(s)
            if repaired is not None:
                logger.info("json_repair_succeeded", preview=s[:120])
                return repaired
    return s


def _clean_list(lst: list[Any]) -> list[Any]:
    """Clean list values and remove junk entries (bare commas, empty strings)."""
    cleaned = []
    for item in lst:
        item = _clean_value(item)
        if isinstance(item, str) and item.strip() in ("", ","):
            continue
        if isinstance(item, dict):
            item = _clean_dict(item)
        cleaned.append(item)
    return cleaned


def _clean_dict(d: dict[str, Any]) -> dict[str, Any]:
    """Clean a dict of response data — parse JSON strings, recurse nested dicts, remove junk."""
    out: dict[str, Any] = {}
    for k, v in d.items():
        if isinstance(v, list):
            out[k] = _clean_list(v)
        elif isinstance(v, dict):
            out[k] = _clean_dict(v)
        else:
            out[k] = _clean_value(v)
    return out


# Construction type → acoustic profile key mapping
_CONSTRUCTION_TO_PROFILE: dict[str, str] = {
    "timber_frame": "victorian",
    "concrete": "ex_council",
    "solid_brick": "purpose_built",
}


@functools.cache
def _eval_json_schema() -> dict[str, Any]:
    """Return the inlined JSON schema for ``_EvaluationResponse``, cached."""
    return _inline_refs(_EvaluationResponse.model_json_schema())


def _extract_eval_json(response: Any) -> dict[str, Any] | None:
    """Extract raw JSON dict from the first text content block in *response*.

    Returns ``None`` when no parseable JSON text block is found.
    """
    for block in getattr(response, "content", []):
        if getattr(block, "type", None) == "text":
            try:
                parsed = _json.loads(block.text)
                if isinstance(parsed, dict):
                    return parsed
            except (ValueError, _json.JSONDecodeError):
                continue
    return None


def _sanitize_eval_enums(raw: dict[str, Any]) -> None:
    """Filter invalid highlight/lowlight enum values from *raw* in-place."""
    valid_highlights = {v.value for v in PropertyHighlight}
    valid_lowlights = {v.value for v in PropertyLowlight}
    if "highlights" in raw:
        raw["highlights"] = [h for h in raw["highlights"] if h in valid_highlights]
    if "lowlights" in raw:
        raw["lowlights"] = [lo for lo in raw["lowlights"] if lo in valid_lowlights]


class PropertyQualityFilter:
    """Analyze property quality using Claude vision API."""

    def __init__(
        self,
        api_key: str,
        max_images: int = 20,
        *,
        enable_extended_thinking: bool = True,
    ) -> None:
        """Initialize the quality filter.

        Args:
            api_key: Anthropic API key.
            max_images: Maximum number of gallery images to analyze.
            enable_extended_thinking: Enable extended thinking for deeper analysis.
        """
        self._api_key = api_key
        self._max_images = max_images
        self._enable_extended_thinking = enable_extended_thinking
        self._client: anthropic.AsyncAnthropic | None = None
        self.token_usage = TokenUsage()
        # Circuit breaker (asyncio is single-threaded, no lock needed)
        from home_finder.utils.circuit_breaker import CircuitBreaker

        self._breaker = CircuitBreaker(
            threshold=_CIRCUIT_BREAKER_THRESHOLD,
            cooldown=_CIRCUIT_BREAKER_COOLDOWN,
            name="anthropic_api",
        )

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

    async def _build_image_block(
        self, url: str, cached_path: Path | None = None
    ) -> "ImageBlockParam | None":
        """Build an image block from disk cache.

        All images should already be cached by detail enrichment (pipeline
        step 8).  If the cache misses, the image is skipped rather than
        re-downloaded — the CDN download would face the same network
        conditions that caused the enrichment miss, and skipping 1-2 of 20
        images has negligible impact on analysis quality.

        Args:
            url: Image URL (used for media-type detection and logging).
            cached_path: Path to cached image on disk.

        Returns:
            ImageBlockParam or None if image not cached or corrupt.
        """
        # Skip non-image URLs (e.g. YouTube embeds in OpenRent galleries)
        _VIDEO_MARKERS = ("youtube.com/", "youtu.be/", "vimeo.com/")
        if any(marker in url.lower() for marker in _VIDEO_MARKERS):
            logger.debug("skipping_video_url", url=url)
            return None

        from anthropic.types import (
            Base64ImageSourceParam,
            ImageBlockParam,
        )

        if cached_path is None:
            logger.warning("image_not_cached", url=url)
            return None

        data = read_image_bytes(cached_path)
        if data is None:
            logger.warning("cached_image_unreadable", url=url, cached_path=str(cached_path))
            return None

        media_type = self._get_media_type(url)
        logger.debug(
            "image_block_from_cache",
            url=url,
            cached_path=str(cached_path),
            data_len=len(data),
            media_type=media_type,
        )

        # Validate image is decodable before sending to API
        try:
            from io import BytesIO

            from PIL import Image

            img = Image.open(BytesIO(data))
            img.load()  # Force full pixel decode
            img.close()
        except Exception:
            logger.warning(
                "cached_image_corrupt",
                url=url,
                cached_path=str(cached_path),
            )
            return None

        data = await asyncio.to_thread(_resize_image_bytes, data)
        image_data = base64.standard_b64encode(data).decode("utf-8")
        return ImageBlockParam(
            type="image",
            source=Base64ImageSourceParam(
                type="base64",
                media_type=media_type,
                data=image_data,
            ),
        )

    async def analyze_single_merged(
        self,
        merged: MergedProperty,
        *,
        data_dir: str | None = None,
    ) -> tuple[MergedProperty, PropertyQualityAnalysis | None]:
        """Analyze quality for a single pre-enriched merged property.

        Does NOT sleep between calls — caller controls pacing.
        Handles errors internally (returns minimal analysis on failure).
        Returns ``None`` analysis when images are incomplete (missing cache).

        Args:
            merged: Enriched merged property to analyze.
            data_dir: Data directory for image cache.

        Returns:
            Tuple of (merged_property, analysis_or_none).
        """
        prop = merged.canonical
        value = assess_value(prop.price_pcm, prop.postcode, prop.bedrooms)

        ctx = build_property_context(prop.postcode, prop.bedrooms)

        # Build URL lists from pre-enriched images
        gallery_urls = [str(img.url) for img in merged.images if img.image_type == "gallery"]
        floorplan_url = str(merged.floorplan.url) if merged.floorplan else None

        # Resolve cached image paths (if data_dir is set)
        gallery_cached: list[Path | None] = []
        floorplan_cached: Path | None = None
        if data_dir:
            for g_url in gallery_urls:
                gallery_cached.append(
                    find_cached_file(data_dir, merged.unique_id, g_url, "gallery")
                )
            if floorplan_url:
                floorplan_cached = find_cached_file(
                    data_dir, merged.unique_id, floorplan_url, "floorplan"
                )
        else:
            gallery_cached = [None] * len(gallery_urls)

        # Require all gallery images cached before spending API budget.
        # Missing images indicate failed enrichment or stale cache — defer
        # analysis until images are available (next enrichment run).
        if data_dir and gallery_urls:
            effective_max = self._max_images - (
                1 if floorplan_url and is_valid_image_url(floorplan_url) else 0
            )
            expected = min(len(gallery_urls), effective_max)
            cached_count = sum(1 for p in gallery_cached[:expected] if p is not None)
            if cached_count < expected:
                logger.warning(
                    "skipping_analysis_insufficient_images",
                    property_id=merged.unique_id,
                    cached=cached_count,
                    expected=expected,
                )
                return merged, None

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
                area_context=ctx.area_overview,
                outcode=ctx.outcode,
                council_tax_band_c=ctx.council_tax_band_c,
                energy_estimate=ctx.energy_estimate,
                crime_summary=ctx.crime_summary,
                rent_trend=ctx.rent_trend,
                hosting_tolerance=ctx.hosting_tolerance,
                gallery_cached_paths=gallery_cached[: self._max_images],
                floorplan_cached_path=floorplan_cached,
                data_dir=data_dir,
                floor_area_sqft=merged.floor_area_sqft,
            )
        except APIUnavailableError:
            raise  # Propagate to caller for circuit breaker handling
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
    ) -> list[tuple[MergedProperty, PropertyQualityAnalysis | None]]:
        """Analyze quality for pre-enriched merged properties.

        Properties should already have images and floorplan populated
        by the detail enrichment step.

        Args:
            properties: Enriched merged properties to analyze.
            data_dir: Data directory for image cache. When set, reads
                cached images from disk instead of downloading.

        Returns:
            List of (merged_property, analysis_or_none) tuples.
        """
        results: list[tuple[MergedProperty, PropertyQualityAnalysis | None]] = []

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
            ),
            condition_concerns=False,
            value=value,
            summary="No images available for quality analysis",
        )

    async def _run_visual_analysis(
        self,
        content: list[Any],
        property_id: str,
        *,
        data_dir: str | None = None,
    ) -> dict[str, Any] | None:
        """Run Phase 1 visual analysis API call.

        Args:
            content: Image and text content blocks for the API.
            property_id: Property ID for logging.
            data_dir: Data directory for image cache (used to clear cache on bad request).

        Returns:
            Raw visual analysis dict, or None if the call failed.

        Raises:
            APIUnavailableError: If circuit breaker trips (outage-indicating errors).
        """
        from anthropic import (
            APIConnectionError,
            APIStatusError,
            AuthenticationError,
            BadRequestError,
            InternalServerError,
            RateLimitError,
        )
        from anthropic.types import ToolParam, ToolUseBlock

        client = self._get_client()

        try:
            visual_tool: ToolParam = VISUAL_ANALYSIS_TOOL  # type: ignore[assignment]

            create_kwargs: dict[str, Any] = {
                "model": "claude-sonnet-4-6",
                # SDK enforces non-streaming cap of ~21333 tokens (10-min timeout estimate).
                # Streaming would allow 32768+ but requires a larger refactor.
                "max_tokens": 21000,
                "system": [
                    {
                        "type": "text",
                        "text": VISUAL_ANALYSIS_SYSTEM_PROMPT,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                "messages": [{"role": "user", "content": content}],
                "tools": [{**visual_tool, "cache_control": {"type": "ephemeral"}}],
            }

            if self._enable_extended_thinking:
                create_kwargs["thinking"] = {
                    "type": "adaptive",
                }
                create_kwargs["tool_choice"] = {"type": "auto"}
            else:
                create_kwargs["tool_choice"] = {
                    "type": "tool",
                    "name": "property_visual_analysis",
                }

            response = await client.messages.create(**create_kwargs)

            if hasattr(response, "usage"):
                usage = response.usage
                self.token_usage.add_from_response(usage)
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

            self._breaker.record_success()
            return tool_use_block.input

        except BadRequestError as e:
            logger.warning(
                "visual_analysis_bad_request",
                property_id=property_id,
                error=str(e),
                request_id=getattr(e, "_request_id", None),
            )
            err_msg = str(e)
            if (
                "Could not process image" in err_msg or "file format is invalid" in err_msg
            ) and data_dir:
                from home_finder.utils.image_cache import clear_image_cache

                clear_image_cache(data_dir, property_id)
                logger.warning(
                    "image_cache_cleared_on_bad_request",
                    property_id=property_id,
                )
            return None

        except AuthenticationError as e:
            self._breaker.record_failure()
            logger.critical(
                "authentication_error",
                property_id=property_id,
                phase="visual",
                error=str(e),
            )
            raise APIUnavailableError(f"Authentication failed: {e}") from e

        except (RateLimitError, InternalServerError, APIConnectionError) as e:
            self._breaker.record_failure()
            if isinstance(e, RateLimitError):
                log_event = "rate_limit_exhausted"
            elif isinstance(e, InternalServerError):
                log_event = "server_error"
            else:
                log_event = "connection_error"
            logger.error(
                log_event,
                property_id=property_id,
                phase="visual",
                error=str(e),
                error_type=type(e).__name__,
                request_id=getattr(e, "_request_id", None)
                if not isinstance(e, APIConnectionError)
                else None,
                exc_info=True,
            )
            raise APIUnavailableError(str(e)) from e

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
                exc_info=True,
            )
            return None

    async def _run_evaluation(
        self,
        visual_data: dict[str, Any],
        property_id: str,
        *,
        description: str | None,
        price_pcm: int,
        bedrooms: int,
        effective_average: int,
        area_context: str | None,
        outcode: str | None,
        council_tax_band_c: float | None,
        crime_summary: str | None,
        rent_trend: str | None,
        energy_estimate: float | None,
        hosting_tolerance: str | None,
    ) -> dict[str, Any]:
        """Run Phase 2 evaluation API call using structured JSON output.

        Uses ``messages.create()`` with ``output_config`` for JSON schema
        output, then manually extracts and validates the response. This
        approach gives us access to the raw JSON even when Pydantic
        validation fails (e.g. Claude invents a lowlight value), allowing
        graceful coercion instead of losing the entire evaluation.

        Args:
            visual_data: Phase 1 visual analysis output.
            property_id: Property ID for logging.
            description: Listing description text.
            price_pcm: Monthly rent price.
            bedrooms: Number of bedrooms.
            effective_average: Area average rent for comparison.
            area_context: Area context string.
            outcode: Property outcode.
            council_tax_band_c: Monthly council tax estimate.
            crime_summary: Crime rate summary.
            rent_trend: Rent trend string.
            energy_estimate: Energy cost estimate.
            hosting_tolerance: Hosting tolerance string.

        Returns:
            Evaluation data dict (empty if Phase 2 failed).

        Raises:
            APIUnavailableError: On authentication, rate-limit, server, or
                connection errors (mirrors Phase 1 contract).
        """
        if self._breaker.is_open():
            logger.info("circuit_breaker_open_skipping_evaluation", property_id=property_id)
            return {}

        client = self._get_client()

        # Map building_construction from Phase 1 to acoustic profile for Phase 2
        acoustic_context: str | None = None
        flooring_raw = visual_data.get("flooring_noise")
        construction: str | None = (
            flooring_raw.get("building_construction") if isinstance(flooring_raw, dict) else None
        )
        if construction:
            profile_key = _CONSTRUCTION_TO_PROFILE.get(construction)
            if profile_key:
                profile = ACOUSTIC_PROFILES.get(profile_key)
                if profile:
                    db_range = profile["airborne_insulation_db"]
                    acoustic_context = (
                        f"Building construction: {construction}\n"
                        f"Typical sound insulation: {db_range} dB airborne\n"
                        f"Hosting safety: {profile['hosting_safety']}\n"
                        f"{profile['summary']}"
                    )

        from anthropic import (
            APIConnectionError,
            AuthenticationError,
            InternalServerError,
            RateLimitError,
        )

        eval_data: dict[str, Any] = {}
        try:
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
                energy_estimate=energy_estimate,
                hosting_tolerance=hosting_tolerance,
                acoustic_context=acoustic_context,
            )

            eval_response = await client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=4096,
                system=[
                    {
                        "type": "text",
                        "text": EVALUATION_SYSTEM_PROMPT,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[{"role": "user", "content": eval_prompt}],
                output_config={
                    "format": {
                        "type": "json_schema",
                        "schema": _eval_json_schema(),
                    }
                },
            )

            if hasattr(eval_response, "usage"):
                self.token_usage.add_from_response(eval_response.usage)

            # Extract raw JSON from text content block
            raw_json = _extract_eval_json(eval_response)
            if raw_json is not None:
                # Filter any invalid enum values before Pydantic validation
                _sanitize_eval_enums(raw_json)
                try:
                    validated = _EvaluationResponse.model_validate(raw_json)
                    eval_data = validated.model_dump()
                    self._breaker.record_success()
                except ValidationError as ve:
                    # Enum filtering wasn't enough; use raw dict
                    eval_data = raw_json
                    self._breaker.record_success()
                    logger.warning(
                        "evaluation_coerced_invalid_fields",
                        property_id=property_id,
                        error=str(ve),
                    )
            else:
                logger.warning(
                    "no_json_in_evaluation_response",
                    property_id=property_id,
                    phase="evaluation",
                    stop_reason=getattr(eval_response, "stop_reason", None),
                )

        except AuthenticationError as e:
            self._breaker.record_failure()
            logger.critical(
                "authentication_error",
                property_id=property_id,
                phase="evaluation",
                error=str(e),
            )
            raise APIUnavailableError(f"Authentication failed: {e}") from e

        except (RateLimitError, InternalServerError, APIConnectionError) as e:
            self._breaker.record_failure()
            logger.warning(
                "evaluation_phase_api_unavailable",
                property_id=property_id,
                error=str(e),
                error_type=type(e).__name__,
                exc_info=True,
            )
            raise APIUnavailableError(str(e)) from e

        except Exception as e:
            logger.warning(
                "evaluation_phase_failed",
                property_id=property_id,
                error=str(e),
                error_type=type(e).__name__,
                exc_info=True,
            )

        logger.info(
            "evaluation_complete",
            property_id=property_id,
            has_eval=bool(eval_data),
        )

        return eval_data

    @staticmethod
    def _merge_analysis_results(
        visual_data: dict[str, Any],
        eval_data: dict[str, Any],
        bedrooms: int,
        property_id: str,
    ) -> PropertyQualityAnalysis | None:
        """Merge Phase 1 + Phase 2 results into a PropertyQualityAnalysis.

        Cleans response data, validates with Pydantic, and applies post-processing
        (e.g. 2+ bed space override).

        Args:
            visual_data: Phase 1 visual analysis output.
            eval_data: Phase 2 evaluation output.
            bedrooms: Number of bedrooms (for space assessment override).
            property_id: Property ID for logging.

        Returns:
            Validated analysis, or None if validation failed.
        """
        visual_data = _clean_dict(visual_data)
        eval_data = _clean_dict(eval_data)

        # Extract value_for_quality from Phase 2 response
        value_for_quality = eval_data.pop("value_for_quality", {})
        quality_adjusted_rating = value_for_quality.get("rating")
        quality_adjusted_note = value_for_quality.get("reasoning", "")

        value = ValueAnalysis(
            quality_adjusted_rating=quality_adjusted_rating,
            quality_adjusted_note=quality_adjusted_note,
        )

        try:
            merged_data = {**visual_data, **eval_data, "value": value}
            merged_data.setdefault("summary", "Analysis completed")

            # Warn if _clean_dict failed to parse any sub-model JSON strings.
            # The model validator will attempt further repair (control char
            # stripping), but this log flags upstream issues worth investigating.
            for key in _QUALITY_SUB_MODEL_FIELDS:
                if isinstance(merged_data.get(key), str):
                    logger.warning(
                        "clean_dict_missed_json_string",
                        field=key,
                        property_id=property_id,
                    )

            analysis = PropertyQualityAnalysis.model_validate(merged_data)
        except Exception as e:
            logger.warning(
                "analysis_validation_failed",
                property_id=property_id,
                error=str(e),
                exc_info=True,
            )
            return None

        return analysis

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
        council_tax_band_c: float | None = None,
        energy_estimate: float | None = None,
        crime_summary: str | None = None,
        rent_trend: str | None = None,
        hosting_tolerance: str | None = None,
        gallery_cached_paths: list[Path | None] | None = None,
        floorplan_cached_path: Path | None = None,
        data_dir: str | None = None,
        floor_area_sqft: int | None = None,
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
            hosting_tolerance: Area hosting tolerance summary string.
            gallery_cached_paths: Paths to cached gallery images on disk.
            floorplan_cached_path: Path to cached floorplan image on disk.
            data_dir: Data directory for image cache (passed to error handlers).

        Returns:
            Analysis result or None if Phase 1 failed.
        """
        from anthropic.types import ImageBlockParam, TextBlockParam

        # Fast-fail if circuit breaker is open (with half-open recovery after cooldown)
        if self._breaker.is_open():
            raise APIUnavailableError("Circuit breaker is open — API unavailable")

        # Build image content blocks
        content: list[ImageBlockParam | TextBlockParam] = []

        has_floorplan = floorplan_url is not None and is_valid_image_url(floorplan_url)
        effective_max = self._max_images - (1 if has_floorplan else 0)

        gallery_num = 0
        cached_paths = gallery_cached_paths or [None] * len(gallery_urls)
        for idx, url in enumerate(gallery_urls[:effective_max]):
            cached = cached_paths[idx] if idx < len(cached_paths) else None
            image_block = await self._build_image_block(url, cached_path=cached)
            if image_block:
                gallery_num += 1
                content.append(TextBlockParam(type="text", text=f"Gallery image {gallery_num}:"))
                content.append(image_block)

        if floorplan_url and is_valid_image_url(floorplan_url):
            floorplan_block = await self._build_image_block(
                floorplan_url, cached_path=floorplan_cached_path
            )
            if floorplan_block:
                content.append(TextBlockParam(type="text", text="Floorplan:"))
                content.append(floorplan_block)
        elif floorplan_url:
            logger.debug("skipping_pdf_floorplan", url=floorplan_url)

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
            energy_estimate=energy_estimate,
            hosting_tolerance=hosting_tolerance,
            has_labeled_floorplan=has_floorplan,
            floor_area_sqft=floor_area_sqft,
        )
        content.append(TextBlockParam(type="text", text=user_prompt))

        has_usable_floorplan = floorplan_url is not None and is_valid_image_url(floorplan_url)
        image_blocks = [c for c in content if hasattr(c, "get") and c.get("type") == "image"]
        logger.info(
            "analyzing_property",
            property_id=property_id,
            gallery_count=len(gallery_urls),
            has_floorplan=has_usable_floorplan,
            image_blocks_sent=len(image_blocks),
            total_content_blocks=len(content),
        )

        # Phase 1: Visual Analysis
        visual_data = await self._run_visual_analysis(content, property_id, data_dir=data_dir)
        if visual_data is None:
            return None

        logger.info("visual_analysis_complete", property_id=property_id)

        detected_indices = visual_data.get("floorplan_detected_in_gallery", [])
        if detected_indices:
            logger.info(
                "floorplan_detected_by_claude",
                property_id=property_id,
                indices=detected_indices,
            )

        # Phase 2: Evaluation
        eval_data = await self._run_evaluation(
            visual_data,
            property_id,
            description=description,
            price_pcm=price_pcm,
            bedrooms=bedrooms,
            effective_average=effective_average,
            area_context=area_context,
            outcode=outcode,
            council_tax_band_c=council_tax_band_c,
            crime_summary=crime_summary,
            rent_trend=rent_trend,
            energy_estimate=energy_estimate,
            hosting_tolerance=hosting_tolerance,
        )

        # Phase 3: Merge and validate
        return self._merge_analysis_results(visual_data, eval_data, bedrooms, property_id)

    async def __aenter__(self) -> "PropertyQualityFilter":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def close(self) -> None:
        """Close clients."""
        if self._client is not None:
            await self._client.close()
            self._client = None

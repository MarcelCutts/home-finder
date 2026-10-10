"""Property quality analysis filter using Claude vision."""

import asyncio
import base64
import functools
import hashlib
import json as _json
import re as _re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, Literal, NoReturn

from pydantic import BaseModel, ConfigDict, Field

from home_finder.data.area_context import (
    ACOUSTIC_PROFILES,
    DEFAULT_BENCHMARK,
    RENTAL_BENCHMARKS,
    build_property_context,
)
from home_finder.filters.analysis_contracts import (
    AnalysisOutputError,
    check_stop_reason,
    evaluation_json,
    rejected_request,
    validate_output,
)
from home_finder.filters.analysis_contracts import (
    failure as output_failure,
)
from home_finder.filters.analysis_spend import AnalysisSpendMeter, visual_input_key
from home_finder.filters.badge_consistency import reconcile_analysis_badges
from home_finder.filters.image_review import apply_image_review_coverage, reconcile_image_review
from home_finder.filters.listing_text import (
    select_listing_text,
    supported_bills_included,
    supported_council_tax_band,
)
from home_finder.filters.space_evidence import reconcile_floorplan_areas
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
    RoomArea,
    SpaceAnalysis,
    StorageAnalysis,
    ValueAnalysis,
    ViewingNotes,
)
from home_finder.models.analysis import (
    AnalysisConfig,
    AnalysisProvenance,
    ImageReviewCoverage,
    PhaseConfig,
    VisualPhaseConfig,
)
from home_finder.models.quality import _QUALITY_SUB_MODEL_FIELDS
from home_finder.models.spend import (
    NANOUSD_PER_USD,
    SONNET_STANDARD_RATES,
    BillableUsage,
    SpendDeferred,
    SpendPolicy,
    rate_card_for,
)
from home_finder.utils.image_cache import find_cached_file, is_valid_image_url, read_image_bytes
from home_finder.utils.image_processing import (
    ImageMediaType,
    detect_media_type_from_bytes,
)
from home_finder.utils.image_processing import (
    resize_image_bytes as _resize_image_bytes,
)
from home_finder.utils.listing_facts import supported_band

if TYPE_CHECKING:
    import anthropic
    import httpx2
    from anthropic.types import ImageBlockParam

    from home_finder.db.spend import SpendRepository

logger = get_logger(__name__)

# Rate limit settings for Tier 2 (1,000 RPM)
# SDK handles retry automatically, we just need a small delay to avoid bursts
DELAY_BETWEEN_CALLS: Final = 0.2  # seconds (1000 RPM = 0.06s minimum, add buffer)

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
    cache_creation_1h_tokens: int = 0
    _estimated_cost_nanousd: int = field(init=False, repr=False)

    def __post_init__(self) -> None:
        # Pre-populated counters historically represent Sonnet 4.6. New calls
        # are priced individually so mixed-model phases are not repriced.
        self._estimated_cost_nanousd = SONNET_STANDARD_RATES.cost(
            BillableUsage(
                input_tokens=self.input_tokens,
                output_tokens=self.output_tokens,
                cache_read_tokens=self.cache_read_tokens,
                cache_write_5m_tokens=self.cache_creation_tokens - self.cache_creation_1h_tokens,
                cache_write_1h_tokens=self.cache_creation_1h_tokens,
            )
        )

    def add_from_response(self, usage: Any, *, model: str = "claude-sonnet-4-6") -> None:
        """Accumulate inclusive output tokens at the actual model's rate."""
        rates = rate_card_for(model)
        breakdown = getattr(usage, "cache_creation", None)
        write_1h = getattr(breakdown, "ephemeral_1h_input_tokens", 0) or 0
        total_written = getattr(usage, "cache_creation_input_tokens", 0) or 0
        billed = BillableUsage(
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
            cache_write_5m_tokens=total_written - write_1h,
            cache_write_1h_tokens=write_1h,
        )
        self._estimated_cost_nanousd += rates.cost(billed)
        self.input_tokens += billed.input_tokens
        self.output_tokens += billed.output_tokens
        self.cache_read_tokens += billed.cache_read_tokens
        self.cache_creation_tokens += total_written
        self.cache_creation_1h_tokens += write_1h

    @property
    def estimated_cost_usd(self) -> float:
        """Estimate successful-response cost; the durable attempt ledger is authoritative."""
        return self._estimated_cost_nanousd / NANOUSD_PER_USD


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
        note = f"£{abs(difference):,} below unverified historical {outcode} reference"
    elif difference > 0:
        note = f"£{difference:,} above unverified historical {outcode} reference"
    else:
        note = f"At unverified historical {outcode} reference"

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

    class RoomArea(BaseModel):
        model_config = _Forbid
        name: str = Field(description="Room name as labelled on floorplan")
        length_m: float | None = Field(
            default=None, description="Length in metres. null if not readable."
        )
        width_m: float | None = Field(
            default=None, description="Width in metres. null if not readable."
        )
        area_sqm: float | None = Field(
            default=None,
            description=(
                "Printed room area, or length x width only for a confirmed rectangle. "
                "null for irregular rooms, maximum/bounding dimensions or unknown geometry."
            ),
        )
        area_basis: Literal["stated_on_floorplan", "rectangular_dimensions", "unknown"] = Field(
            default="unknown",
            description=(
                "Evidence for area_sqm: a printed room area or confirmed rectangular dimensions. "
                "Use unknown for maximum dimensions, irregular outlines or uncertain geometry; "
                "retain readable length/width labels but leave area_sqm null."
            ),
        )

    class Space(BaseModel):
        model_config = _Forbid
        living_room_sqm: float | None = Field(
            description=(
                "Supported usable living area, not a maximum length x width bounding box. "
                "null when irregular/uncertain or inconsistent with the printed total. "
                "Identify combined kitchen/living space in area_notes."
            )
        )
        total_area_sqm: float | None = Field(
            default=None,
            description=(
                "Prefer the printed internal total from floorplan_total_area_sqm. Otherwise "
                "sum only a complete, non-overlapping set of supported internal room areas. "
                "Do not sum maximum dimension products or guess missing rooms."
            ),
        )
        floorplan_total_area_sqm: float | None = Field(
            default=None,
            description="Internal total printed on the plan, converted to sqm; otherwise null.",
        )
        area_notes: str = Field(
            default="",
            description="Area uncertainty, conflicting labels/totals, or combined kitchen/living.",
        )
        room_areas: list["_VisualAnalysisResponse.RoomArea"] = Field(
            default_factory=list,
            description=(
                "Per-room breakdown from floorplan. Empty list if no dimensioned floorplan."
            ),
        )
        area_estimation_method: (
            Literal[
                "measured_from_floorplan",
                "stated_on_floorplan",
                "partial_dimensions",
                "estimated_from_scale",
                "estimated_from_photos",  # Reserved — not prompted yet
            ]
            | None
        ) = Field(
            default=None,
            description="How total_area_sqm was determined. null if total_area_sqm is null.",
        )
        is_spacious_enough: bool | None = Field(
            description="True if can fit office AND host 8+ people; null if size is uncertain"
        )
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
        availability: Literal["yes", "no", "unknown"] = Field(
            description="Overall outdoor access: yes if evidenced, no only if explicitly absent; "
            "otherwise unknown. All false type flags do not establish absence."
        )
        has_balcony: bool
        has_garden: bool
        has_terrace: bool = Field(description="Includes private patios and courtyards.")
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
                "Discuss advertised rent and observed trade-offs. Separate supported "
                "tenant payments from estimates and unknown bills. Do not infer service "
                "charges, fixed stock premiums, a complete monthly total or an offer "
                "discount from undated research. Acknowledge missing comparable evidence."
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
    (_VisualAnalysisResponse.RoomArea, RoomArea),
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
    strict=True,
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


def _visual_output(response: Any) -> dict[str, Any]:
    from anthropic.types import ToolUseBlock

    check_stop_reason(response, "visual")
    tools = [block for block in response.content if isinstance(block, ToolUseBlock)]
    if len(tools) != 1 or tools[0].name != "property_visual_analysis":
        raise output_failure(
            "visual",
            "malformed_output",
            response,
            diagnostics={"reason": "expected_one_visual_tool", "tool_block_count": len(tools)},
        )
    return validate_output(tools[0].input, _VisualAnalysisResponse, "visual", response)


def _evaluation_output(response: Any) -> dict[str, Any]:
    check_stop_reason(response, "evaluation")
    return validate_output(evaluation_json(response), _EvaluationResponse, "evaluation", response)


class PropertyQualityFilter:
    """Analyze property quality using Claude vision API."""

    def __init__(
        self,
        api_key: str,
        max_images: int = 20,
        *,
        enable_extended_thinking: bool = True,
        http_client: "httpx2.AsyncClient | None" = None,
        analysis_config: AnalysisConfig | None = None,
        spend_repository: "SpendRepository | None" = None,
        spend_policy: SpendPolicy | None = None,
    ) -> None:
        """Initialize the quality filter.

        Args:
            api_key: Anthropic API key.
            max_images: Maximum number of gallery images to analyze.
            enable_extended_thinking: Enable extended thinking for deeper analysis.
            http_client: Optional native SDK transport client, closed with this filter.
            analysis_config: Explicit phase settings; overrides the legacy thinking flag.
            spend_repository: Authoritative durable ledger; required for real SDK requests.
            spend_policy: Explicit caps; absent caps defer every real SDK request.
        """
        self._api_key = api_key
        self._max_images = max_images
        self._analysis_config = analysis_config or AnalysisConfig(
            visual=VisualPhaseConfig(
                reasoning="adaptive" if enable_extended_thinking else "disabled"
            )
        )
        self._client: anthropic.AsyncAnthropic | None = None
        self._http_client = http_client
        self.token_usage = TokenUsage()
        self._spend_meter = AnalysisSpendMeter(spend_repository, spend_policy)
        # Circuit breaker (asyncio is single-threaded, no lock needed)
        from home_finder.utils.circuit_breaker import CircuitBreaker

        self._breaker = CircuitBreaker(
            threshold=_CIRCUIT_BREAKER_THRESHOLD,
            cooldown=_CIRCUIT_BREAKER_COOLDOWN,
            name="anthropic_api",
        )

    @property
    def analysis_config(self) -> AnalysisConfig:
        """One immutable effective configuration for this filter's lifetime."""
        return self._analysis_config

    def _get_client(self) -> "anthropic.AsyncAnthropic":
        """Get or create the Anthropic client with optimized settings."""
        if self._client is None:
            import anthropic as _anthropic

            if self._http_client is None:
                self._http_client = _anthropic.DefaultAsyncHttpxClient(follow_redirects=False)
            else:
                # A redirected POST must not create an unmetered second send
                # inside the HTTP client beneath the SDK middleware.
                self._http_client.follow_redirects = False
            self._client = _anthropic.AsyncAnthropic(
                api_key=self._api_key,
                base_url="https://api.anthropic.com",
                middleware=[self._spend_meter],
                max_retries=self.analysis_config.visual.sdk_retries,
                http_client=self._http_client,
                timeout=_anthropic.Timeout(
                    self.analysis_config.visual.request_timeout_seconds,
                    connect=self.analysis_config.visual.connect_timeout_seconds,
                ),
            )
        return self._client

    def _phase_client(self, phase: PhaseConfig) -> "anthropic.AsyncAnthropic":
        client = self._get_client()
        if phase.sdk_retries != self.analysis_config.visual.sdk_retries:
            # SDK options copies share the base client's owned HTTP connection
            # pool. Closing the filter still closes that single transport.
            return client.with_options(max_retries=phase.sdk_retries)
        return client

    def _analysis_provenance(self) -> AnalysisProvenance:
        from anthropic import __version__ as sdk_version

        from home_finder.filters.request_budget import POLICY

        def digest(value: Any) -> str:
            text = value if isinstance(value, str) else _json.dumps(value, sort_keys=True)
            return hashlib.sha256(text.encode()).hexdigest()

        return AnalysisProvenance(
            version=3,
            configuration=self.analysis_config,
            sdk_version=sdk_version,
            max_images=self._max_images,
            visual_system_sha256=digest(VISUAL_ANALYSIS_SYSTEM_PROMPT),
            evaluation_system_sha256=digest(EVALUATION_SYSTEM_PROMPT),
            visual_output_sha256=digest(VISUAL_ANALYSIS_TOOL),
            evaluation_output_sha256=digest(_eval_json_schema()),
            input_budget_policy=POLICY
            if any(
                phase.input_token_limit is not None
                for phase in (self.analysis_config.visual, self.analysis_config.evaluation)
            )
            else None,
        )

    def _raise_spend_deferral(self, error: SpendDeferred) -> NoReturn:
        if error.reason in {
            "uncertain_provider_outcome",
            "unpriced_provider_response",
            "ledger_unavailable",
        }:
            self._breaker.record_failure()
            # Stop the current batch on provider/accounting uncertainty. The
            # middleware has already kept the durable reservation and stopped
            # the SDK's own retry loop.
            raise APIUnavailableError(error.reason) from None
        raise error

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

        url_type = self._get_media_type(url)
        detected = detect_media_type_from_bytes(data)
        media_type = detected if detected is not None else url_type
        if detected is not None and detected != url_type:
            logger.info(
                "mime_type_corrected",
                url=url,
                url_type=url_type,
                detected_type=detected,
            )
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
        Invalid required output returns an unrated, typed failure.
        Provider outages propagate to the caller's circuit-breaker handling.
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
            return merged, None

        text_inputs = select_listing_text(merged)

        try:
            analysis = await self._analyze_property(
                merged.unique_id,
                gallery_urls=gallery_urls,
                floorplan_url=floorplan_url,
                floorplan_source_declared=bool(
                    merged.floorplan and merged.floorplan.is_source_floorplan
                ),
                bedrooms=prop.bedrooms,
                price_pcm=prop.price_pcm,
                area_average=value.area_average,
                description=text_inputs.description,
                features=list(text_inputs.features) or None,
                listing_address=prop.address,
                listing_postcode=prop.postcode,
                area_context=ctx.area_overview,
                outcode=ctx.outcode,
                council_tax_band_c=ctx.council_tax_band_c,
                energy_estimate=ctx.energy_estimate,
                crime_summary=ctx.crime_summary,
                rent_trend=ctx.rent_trend,
                hosting_tolerance=ctx.hosting_tolerance,
                gallery_cached_paths=gallery_cached,
                floorplan_cached_path=floorplan_cached,
                data_dir=data_dir,
                floor_area_sqm=(
                    None if merged.floor_area_source == "estimated" else merged.floor_area_sqm
                ),
            )
        except SpendDeferred as exc:
            logger.info("analysis_spend_deferred", property_id=merged.unique_id, reason=exc.reason)
            raise
        except APIUnavailableError:
            raise  # Propagate to caller for circuit breaker handling
        except AnalysisOutputError as exc:
            logger.warning(
                "analysis_output_failed",
                property_id=merged.unique_id,
                output_diagnostics=exc.diagnostics,
                **exc.failure.model_dump(),
            )
            return merged, self._create_failed_analysis(value, exc)
        except Exception:
            logger.error(
                "analyze_single_merged_failed",
                property_id=merged.unique_id,
                exc_info=True,
            )
            return merged, self._create_failed_analysis(
                value, output_failure("assembly", "internal_error")
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
                listing_text_inputs=text_inputs,
                image_review_coverage=analysis.image_review_coverage,
                analysis_provenance=analysis.analysis_provenance or self._analysis_provenance(),
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
            return merged, self._create_failed_analysis(
                value, output_failure("assembly", "malformed_output")
            )

    def _create_failed_analysis(
        self, value: ValueAnalysis | None, error: AnalysisOutputError
    ) -> PropertyQualityAnalysis:
        incomplete = self._create_minimal_analysis(value)
        return incomplete.model_copy(
            update={
                "kitchen": KitchenAnalysis(notes="Quality analysis incomplete"),
                "light_space": LightSpaceAnalysis(notes="Quality analysis incomplete"),
                "summary": f"Quality analysis incomplete ({error})",
                "analysis_failure": error.failure,
                "analysis_provenance": self._analysis_provenance(),
            }
        )

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

    def _visual_request(self, content: list[Any]) -> dict[str, Any]:
        """Build the exact visual request used both for checkpoint identity and dispatch."""
        phase = self.analysis_config.visual
        create_kwargs: dict[str, Any] = {
            "model": phase.model,
            # SDK enforces non-streaming cap of ~21333 tokens (10-min timeout estimate).
            # Streaming would allow 32768+ but requires a larger refactor.
            "max_tokens": phase.max_output_tokens,
            "system": [
                {
                    "type": "text",
                    "text": VISUAL_ANALYSIS_SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            "messages": [{"role": "user", "content": content}],
            "tools": [{**VISUAL_ANALYSIS_TOOL, "cache_control": {"type": "ephemeral"}}],
        }

        if phase.thinking_type is not None:
            create_kwargs["thinking"] = {"type": phase.thinking_type}
            create_kwargs["tool_choice"] = {"type": "auto"}
        else:
            create_kwargs["tool_choice"] = {
                "type": "tool",
                "name": "property_visual_analysis",
            }

        if phase.effort is not None:
            create_kwargs["output_config"] = {"effort": phase.effort}

        return create_kwargs

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
            Validated visual analysis dict.

        Raises:
            AnalysisOutputError: If the request or required output is invalid.
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

        phase = self.analysis_config.visual
        client = self._phase_client(phase)

        try:
            create_kwargs = self._visual_request(content)
            provenance = self._analysis_provenance()
            input_key = visual_input_key(property_id, create_kwargs, provenance)
            cached = await self._spend_meter.get_visual_checkpoint(property_id, input_key)
            if cached is not None:
                try:
                    visual_data = validate_output(cached, _VisualAnalysisResponse, "visual", None)
                except AnalysisOutputError:
                    raise SpendDeferred("visual_checkpoint_invalid") from None
                logger.info(
                    "visual_checkpoint_reused", property_id=property_id, input_sha256=input_key
                )
                return visual_data

            from anthropic import Timeout

            with self._spend_meter.bind(
                property_id,
                "visual",
                provenance,
                _visual_output,
                visual_input_sha256=input_key,
            ):
                response = await client.messages.create(
                    **create_kwargs,
                    timeout=Timeout(
                        phase.request_timeout_seconds, connect=phase.connect_timeout_seconds
                    ),
                )

            if hasattr(response, "usage"):
                usage = response.usage
                self.token_usage.add_from_response(usage, model=phase.model)
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

            visual_data = _visual_output(response)
            self._breaker.record_success()
            return visual_data

        except SpendDeferred as exc:
            self._raise_spend_deferral(exc)
        except AnalysisOutputError:
            raise

        except BadRequestError as e:
            logger.warning(
                "visual_analysis_bad_request",
                property_id=property_id,
                error=str(e),
                request_id=getattr(e, "_request_id", None),
            )
            err_msg = str(e)
            _image_error_patterns = (
                "Could not process image",
                "file format is invalid",
                "media_type",
                "does not match",
            )
            if any(p in err_msg for p in _image_error_patterns) and data_dir:
                from home_finder.utils.image_cache import clear_image_cache

                clear_image_cache(data_dir, property_id)
                logger.warning(
                    "image_cache_cleared_on_bad_request",
                    property_id=property_id,
                )
            raise rejected_request("visual", e) from e

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
            raise rejected_request("visual", e) from e

        except Exception as e:
            logger.warning(
                "visual_analysis_failed",
                property_id=property_id,
                error=str(e),
                error_type=type(e).__name__,
                exc_info=True,
            )
            raise output_failure("visual", "internal_error") from e

    async def _run_evaluation(
        self,
        visual_data: dict[str, Any],
        property_id: str,
        *,
        description: str | None,
        features: list[str] | None = None,
        listing_address: str | None = None,
        listing_postcode: str | None = None,
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
        image_review_coverage: ImageReviewCoverage | None = None,
    ) -> dict[str, Any]:
        """Run Phase 2 evaluation API call using structured JSON output.

        Uses ``messages.create()`` with ``output_config`` for JSON schema
        output, then validates required response fields before any lenient
        historical-record loading. Invalid responses retain their usage and
        redacted diagnostics without completing an assessment.

        Args:
            visual_data: Phase 1 visual analysis output.
            property_id: Property ID for logging.
            description: Listing description text.
            listing_address: Unverified address from the canonical listing.
            listing_postcode: Unverified postcode from the canonical listing.
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
            Validated evaluation data dict.

        Raises:
            AnalysisOutputError: If the request or required output is invalid.
            APIUnavailableError: On authentication, rate-limit, server, or
                connection errors (mirrors Phase 1 contract).
        """
        if self._breaker.is_open():
            logger.info("circuit_breaker_open_skipping_evaluation", property_id=property_id)
            raise APIUnavailableError("Circuit breaker opened before evaluation")

        phase = self.analysis_config.evaluation
        client = self._phase_client(phase)

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
            APIStatusError,
            AuthenticationError,
            BadRequestError,
            InternalServerError,
            RateLimitError,
        )

        eval_data: dict[str, Any] = {}
        try:
            eval_prompt = build_evaluation_prompt(
                visual_data=visual_data,
                description=description,
                features=features,
                listing_address=listing_address,
                listing_postcode=listing_postcode,
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
                image_review_coverage=image_review_coverage,
            )

            from anthropic import Timeout

            output_config: dict[str, Any] = {
                "format": {"type": "json_schema", "schema": _eval_json_schema()}
            }
            if phase.effort is not None:
                output_config["effort"] = phase.effort
            create_kwargs: dict[str, Any] = {
                "model": phase.model,
                "max_tokens": phase.max_output_tokens,
                "system": [
                    {
                        "type": "text",
                        "text": EVALUATION_SYSTEM_PROMPT,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                "messages": [{"role": "user", "content": eval_prompt}],
                "output_config": output_config,
            }
            if phase.thinking_type is not None:
                create_kwargs["thinking"] = {"type": phase.thinking_type}
            with self._spend_meter.bind(
                property_id, "evaluation", self._analysis_provenance(), _evaluation_output
            ):
                eval_response = await client.messages.create(
                    **create_kwargs,
                    timeout=Timeout(
                        phase.request_timeout_seconds, connect=phase.connect_timeout_seconds
                    ),
                )

            if hasattr(eval_response, "usage"):
                self.token_usage.add_from_response(eval_response.usage, model=phase.model)

            eval_data = _evaluation_output(eval_response)
            self._breaker.record_success()

        except SpendDeferred as exc:
            self._raise_spend_deferral(exc)
        except AnalysisOutputError:
            raise
        except BadRequestError as e:
            raise rejected_request("evaluation", e) from e

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

        except APIStatusError as e:
            raise rejected_request("evaluation", e) from e

        except Exception as e:
            logger.warning(
                "evaluation_phase_failed",
                property_id=property_id,
                error=str(e),
                error_type=type(e).__name__,
                exc_info=True,
            )
            raise output_failure("evaluation", "internal_error") from e

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

        Live callers validate both response contracts first. Historical data
        can still use the storage model's bounded repairs here.

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
            # These fields describe the application's actual execution. Ignore
            # any model-supplied attempt to supply or invalidate that provenance.
            merged_data.pop("analysis_provenance", None)
            merged_data.pop("analysis_failure", None)
            merged_data.pop("listing_text_inputs", None)
            merged_data.pop("image_review_coverage", None)
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
        listing_address: str | None = None,
        listing_postcode: str | None = None,
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
        floor_area_sqm: float | None = None,
        floorplan_source_declared: bool = False,
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
            listing_address: Unverified address from the canonical listing.
            listing_postcode: Unverified postcode from the canonical listing.
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

        floorplan_block = None
        if floorplan_url and is_valid_image_url(floorplan_url):
            floorplan_block = await self._build_image_block(
                floorplan_url, cached_path=floorplan_cached_path
            )
        elif floorplan_url:
            logger.debug("skipping_pdf_floorplan", url=floorplan_url)

        has_extra_image = floorplan_block is not None
        has_floorplan = has_extra_image and floorplan_source_declared
        effective_max = self._max_images - (1 if has_extra_image else 0)

        gallery_num = 0
        cached_paths = gallery_cached_paths or [None] * len(gallery_urls)
        for idx, url in enumerate(gallery_urls[:effective_max]):
            cached = cached_paths[idx] if idx < len(cached_paths) else None
            image_block = await self._build_image_block(url, cached_path=cached)
            if image_block:
                gallery_num += 1
                content.append(TextBlockParam(type="text", text=f"Gallery image {gallery_num}:"))
                content.append(image_block)

        if floorplan_block:
            content.append(
                TextBlockParam(
                    type="text",
                    text=(
                        "Source-declared floorplan. Type and readable dimensions are not verified. "
                        "Read only visible dimension labels (metres or feet/inches):"
                        if floorplan_source_declared
                        else f"Gallery image {gallery_num + 1} (historical image role unverified):"
                    ),
                )
            )
            content.append(floorplan_block)

        if not gallery_num and not has_extra_image:
            raise SpendDeferred("missing_usable_images")

        # Match the actual gallery-labelled blocks, including a historical image
        # whose source never declared it to be a floorplan. Count failed/capped
        # gallery images as available but unreviewed.
        coverage = ImageReviewCoverage(
            available_gallery_count=len(gallery_urls)
            + int(bool(floorplan_url) and not floorplan_source_declared),
            reviewed_gallery_count=gallery_num
            + int(has_extra_image and not floorplan_source_declared),
        )
        effective_average = area_average or DEFAULT_BENCHMARK.get(min(bedrooms, 3), 1800)
        user_prompt = build_user_prompt(
            price_pcm,
            bedrooms,
            effective_average,
            description,
            features,
            listing_address=listing_address,
            listing_postcode=listing_postcode,
            area_context=area_context,
            outcode=outcode,
            council_tax_band_c=council_tax_band_c,
            crime_summary=crime_summary,
            rent_trend=rent_trend,
            energy_estimate=energy_estimate,
            hosting_tolerance=hosting_tolerance,
            has_labeled_floorplan=has_floorplan,
            floor_area_sqm=floor_area_sqm,
            image_review_coverage=coverage,
        )
        content.append(TextBlockParam(type="text", text=user_prompt))

        image_blocks = [c for c in content if hasattr(c, "get") and c.get("type") == "image"]
        logger.info(
            "analyzing_property",
            property_id=property_id,
            gallery_count=len(gallery_urls),
            has_floorplan=has_floorplan,
            floorplan_origin=(
                "source_declared" if has_floorplan else "unknown" if has_extra_image else None
            ),
            image_blocks_sent=len(image_blocks),
            total_content_blocks=len(content),
        )

        # Phase 1: Visual Analysis
        visual_data = await self._run_visual_analysis(content, property_id, data_dir=data_dir)
        if visual_data is None:
            raise output_failure("visual", "malformed_output")

        # Apply equally to fresh output and reused raw visual checkpoints. Never
        # alter the paid observation receipt or rewrite historical assessments.
        visual_data = reconcile_floorplan_areas(visual_data)
        visual_data, coverage = reconcile_image_review(visual_data, coverage)

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
            features=features,
            listing_address=listing_address,
            listing_postcode=listing_postcode,
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
            image_review_coverage=coverage,
        )

        if not eval_data:
            raise output_failure("evaluation", "malformed_output")

        # Phase 3: Merge and validate
        analysis = self._merge_analysis_results(visual_data, eval_data, bedrooms, property_id)
        if analysis is not None and analysis.listing_extraction is not None:
            extraction = analysis.listing_extraction
            band = supported_council_tax_band(extraction.council_tax_band, description, features)
            epc = supported_band("epc", extraction.epc_rating, description, features)
            bills = supported_bills_included(extraction.bills_included, description, features)
            if (
                band != extraction.council_tax_band
                or epc != extraction.epc_rating
                or bills != extraction.bills_included
            ):
                analysis = analysis.model_copy(
                    update={
                        "listing_extraction": extraction.model_copy(
                            update={
                                "council_tax_band": band,
                                "epc_rating": epc,
                                "bills_included": bills,
                            }
                        )
                    }
                )
        if analysis is not None:
            analysis = apply_image_review_coverage(analysis, coverage)
            analysis = reconcile_analysis_badges(analysis, property_id=property_id)
            provenance = self._analysis_provenance()
            input_key = visual_input_key(property_id, self._visual_request(content), provenance)
            analysis = analysis.model_copy(
                update={
                    "analysis_provenance": provenance.model_copy(
                        update={"visual_input_sha256": input_key}
                    )
                }
            )
        return analysis

    async def __aenter__(self) -> "PropertyQualityFilter":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def close(self) -> None:
        """Close clients."""
        if self._client is not None:
            await self._client.close()
            self._client = None
        elif self._http_client is not None:
            await self._http_client.aclose()
        self._http_client = None

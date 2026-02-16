"""Quality analysis models for property evaluation."""

import json
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator
from pydantic.functional_validators import BeforeValidator

# ---------------------------------------------------------------------------
# Shared coercion functions for backward compat with old DB data
# ---------------------------------------------------------------------------


def _coerce_bool_to_tristate(v: Any) -> Any:
    """Coerce bool/None to tri-state string for backward compat with old DB data."""
    if v is True:
        return "yes"
    if v is False:
        return "no"
    if v is None:
        return "unknown"
    return v


def _coerce_none_to_false(v: Any) -> Any:
    """Coerce None to False for backward compat with old DB data."""
    return False if v is None else v


def _coerce_none_to_unknown(v: Any) -> Any:
    """Coerce None to 'unknown' for backward compat with old DB data."""
    return "unknown" if v is None else v


# Annotated type aliases — eliminate per-model @field_validator boilerplate
TriStateBool = Annotated[Literal["yes", "no", "unknown"], BeforeValidator(_coerce_bool_to_tristate)]
CoercedBool = Annotated[bool, BeforeValidator(_coerce_none_to_false)]
_CoerceUnknown = BeforeValidator(_coerce_none_to_unknown)


class PropertyHighlight(StrEnum):
    """Constrained vocabulary for positive property features."""

    # Kitchen
    GAS_HOB = "Gas hob"
    INDUCTION_HOB = "Induction hob"
    DISHWASHER = "Dishwasher included"
    WASHING_MACHINE = "Washing machine"
    MODERN_KITCHEN = "Modern kitchen"
    # Bathroom
    MODERN_BATHROOM = "Modern bathroom"
    TWO_BATHROOMS = "Two bathrooms"
    ENSUITE = "Ensuite bathroom"
    # Light & Space
    EXCELLENT_LIGHT = "Excellent natural light"
    GOOD_LIGHT = "Good natural light"
    FLOOR_TO_CEILING_WINDOWS = "Floor-to-ceiling windows"
    HIGH_CEILINGS = "High ceilings"
    SPACIOUS_LIVING = "Spacious living room"
    OPEN_PLAN = "Open-plan layout"
    # Storage
    BUILT_IN_WARDROBES = "Built-in wardrobes"
    GOOD_STORAGE = "Good storage"
    # Outdoor
    PRIVATE_BALCONY = "Private balcony"
    PRIVATE_GARDEN = "Private garden"
    PRIVATE_TERRACE = "Private terrace"
    SHARED_GARDEN = "Shared garden"
    COMMUNAL_GARDENS = "Communal gardens"
    ROOF_TERRACE = "Roof terrace"
    # Condition
    EXCELLENT_CONDITION = "Excellent condition"
    RECENTLY_REFURBISHED = "Recently refurbished"
    PERIOD_FEATURES = "Period features"
    # Glazing
    DOUBLE_GLAZING = "Double glazing"
    # Amenities
    ON_SITE_GYM = "On-site gym"
    CONCIERGE = "Concierge"
    BIKE_STORAGE = "Bike storage"
    PARKING = "Parking included"
    # Lifestyle
    PETS_ALLOWED = "Pets allowed"
    BILLS_INCLUDED = "Bills included"
    # Views
    CANAL_VIEWS = "Canal views"
    PARK_VIEWS = "Park views"
    # Marcel-specific
    ULTRAFAST_BROADBAND = "Ultrafast broadband (FTTP)"
    DEDICATED_OFFICE = "Dedicated office room"
    SEPARATE_WORK_AREA = "Separate work area"
    GREAT_HOSTING_LAYOUT = "Great hosting layout"


class PropertyLowlight(StrEnum):
    """Constrained vocabulary for property concerns."""

    NO_DISHWASHER = "No dishwasher"
    NO_WASHING_MACHINE = "No washing machine"
    DATED_KITCHEN = "Dated kitchen"
    ELECTRIC_HOB = "Electric hob"
    COMPACT_LIVING = "Compact living room"
    SMALL_LIVING = "Small living room"
    SMALL_BEDROOM = "Small bedroom"
    COMPACT_BEDROOM = "Compact bedroom"
    POOR_STORAGE = "Poor storage"
    NO_STORAGE = "No storage"
    DATED_BATHROOM = "Dated bathroom"
    NO_OUTDOOR_SPACE = "No outdoor space"
    NO_INTERIOR_PHOTOS = "No interior photos"
    NO_BATHROOM_PHOTOS = "No bathroom photos"
    MISSING_KEY_PHOTOS = "Missing key photos"
    TRAFFIC_NOISE = "Potential traffic noise"
    NEW_BUILD_ACOUSTICS = "New-build acoustics"
    SERVICE_CHARGE_UNSTATED = "Service charge unstated"
    BALCONY_CRACKING = "Balcony cracking"
    NEEDS_UPDATING = "Needs updating"
    # Marcel-specific
    BASIC_BROADBAND = "Basic broadband only"
    NO_WORK_SEPARATION = "No work-life separation"
    POOR_HOSTING_LAYOUT = "Poor hosting layout"


class PropertyType(StrEnum):
    """Property stock type."""

    VICTORIAN = "victorian"
    EDWARDIAN = "edwardian"
    GEORGIAN = "georgian"
    NEW_BUILD = "new_build"
    PURPOSE_BUILT = "purpose_built"
    WAREHOUSE = "warehouse"
    EX_COUNCIL = "ex_council"
    PERIOD_CONVERSION = "period_conversion"
    UNKNOWN = "unknown"


class KitchenAnalysis(BaseModel):
    """Analysis of kitchen amenities and condition."""

    model_config = ConfigDict(frozen=True)

    overall_quality: Literal["modern", "decent", "dated", "unknown"] = "unknown"
    hob_type: Literal["gas", "electric", "induction", "unknown"] | None = None
    has_dishwasher: TriStateBool = "unknown"
    has_washing_machine: TriStateBool = "unknown"
    notes: str = ""


class ConditionAnalysis(BaseModel):
    """Analysis of property condition."""

    model_config = ConfigDict(frozen=True)

    overall_condition: Literal["excellent", "good", "fair", "poor", "unknown"] = "unknown"
    has_visible_damp: TriStateBool = "unknown"
    has_visible_mold: TriStateBool = "unknown"
    has_worn_fixtures: TriStateBool = "unknown"
    maintenance_concerns: list[str] = []
    confidence: Literal["high", "medium", "low"] = "medium"


class LightSpaceAnalysis(BaseModel):
    """Analysis of natural light and space feel."""

    model_config = ConfigDict(frozen=True)

    natural_light: Literal["excellent", "good", "fair", "poor", "unknown"] = "unknown"
    window_sizes: Annotated[
        Literal["large", "medium", "small", "unknown"] | None, _CoerceUnknown
    ] = None
    feels_spacious: bool | None = None  # None = unknown
    ceiling_height: Annotated[
        Literal["high", "standard", "low", "unknown"] | None, _CoerceUnknown
    ] = None
    floor_level: Annotated[
        Literal["basement", "ground", "lower", "upper", "top", "unknown"] | None, _CoerceUnknown
    ] = None
    notes: str = ""


class SpaceAnalysis(BaseModel):
    """Analysis of living room space (replaces FloorplanFilter logic)."""

    model_config = ConfigDict(frozen=True)

    living_room_sqm: float | None = None
    is_spacious_enough: bool | None = None  # None = unknown
    confidence: Literal["high", "medium", "low"] = "low"
    hosting_layout: Literal["excellent", "good", "awkward", "poor", "unknown"] = "unknown"


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


class BathroomAnalysis(BaseModel):
    """Analysis of bathroom amenities and condition."""

    model_config = ConfigDict(frozen=True)

    overall_condition: Literal["modern", "decent", "dated", "unknown"] = "unknown"
    has_bathtub: TriStateBool = "unknown"
    shower_type: Literal["overhead", "separate_cubicle", "electric", "none", "unknown"] | None = (
        None
    )
    is_ensuite: TriStateBool = "unknown"
    notes: str = ""


class BedroomAnalysis(BaseModel):
    """Analysis of bedroom space and fittings."""

    model_config = ConfigDict(frozen=True)

    primary_is_double: TriStateBool = "unknown"
    has_built_in_wardrobe: TriStateBool = "unknown"
    can_fit_desk: TriStateBool = "unknown"
    office_separation: Literal[
        "dedicated_room", "separate_area", "shared_space", "none", "unknown"
    ] = "unknown"
    notes: str = ""


class OutdoorSpaceAnalysis(BaseModel):
    """Analysis of outdoor space availability."""

    model_config = ConfigDict(frozen=True)

    has_balcony: CoercedBool = False
    has_garden: CoercedBool = False
    has_terrace: CoercedBool = False
    has_shared_garden: CoercedBool = False
    notes: str = ""


class StorageAnalysis(BaseModel):
    """Analysis of storage provision."""

    model_config = ConfigDict(frozen=True)

    has_built_in_wardrobes: TriStateBool = "unknown"
    has_hallway_cupboard: TriStateBool = "unknown"
    storage_rating: Literal["good", "adequate", "poor", "unknown"] = "unknown"


class FlooringNoiseAnalysis(BaseModel):
    """Analysis of flooring type and noise indicators."""

    model_config = ConfigDict(frozen=True)

    primary_flooring: Literal["hardwood", "laminate", "carpet", "tile", "mixed", "unknown"] = (
        "unknown"
    )
    has_double_glazing: TriStateBool = "unknown"
    building_construction: Annotated[
        Literal["solid_brick", "concrete", "timber_frame", "mixed", "unknown"] | None,
        _CoerceUnknown,
    ] = None
    noise_indicators: list[str] = []
    hosting_noise_risk: Literal["low", "moderate", "high", "unknown"] = "unknown"
    notes: str = ""


class ListingExtraction(BaseModel):
    """Structured data extracted from the listing description."""

    model_config = ConfigDict(frozen=True)

    epc_rating: Annotated[
        Literal["A", "B", "C", "D", "E", "F", "G", "unknown"] | None, _CoerceUnknown
    ] = None
    service_charge_pcm: int | None = None
    deposit_weeks: int | None = None
    bills_included: TriStateBool = "unknown"
    pets_allowed: TriStateBool = "unknown"
    parking: Literal["dedicated", "street", "none", "unknown"] | None = None
    council_tax_band: Annotated[
        Literal["A", "B", "C", "D", "E", "F", "G", "H", "unknown"] | None, _CoerceUnknown
    ] = None
    property_type: PropertyType = PropertyType.UNKNOWN
    furnished_status: Literal["furnished", "unfurnished", "part_furnished", "unknown"] | None = None
    broadband_type: Literal["fttp", "fttc", "cable", "standard", "unknown"] | None = None


class ListingRedFlags(BaseModel):
    """Red flags identified from the listing."""

    model_config = ConfigDict(frozen=True)

    missing_room_photos: list[str] = []
    too_few_photos: CoercedBool = False
    selective_angles: CoercedBool = False
    description_concerns: list[str] = []
    red_flag_count: int = 0


class ViewingNotes(BaseModel):
    """Property-specific viewing preparation notes."""

    model_config = ConfigDict(frozen=True)

    check_items: list[str] = []
    questions_for_agent: list[str] = []
    deal_breaker_tests: list[str] = []


class PropertyQualityAnalysis(BaseModel):
    """Complete quality analysis of a property."""

    model_config = ConfigDict(frozen=True)

    kitchen: KitchenAnalysis
    condition: ConditionAnalysis
    light_space: LightSpaceAnalysis
    space: SpaceAnalysis

    # New analysis dimensions (optional for backward compat with existing DB rows)
    bathroom: BathroomAnalysis | None = None
    bedroom: BedroomAnalysis | None = None
    outdoor_space: OutdoorSpaceAnalysis | None = None
    storage: StorageAnalysis | None = None
    flooring_noise: FlooringNoiseAnalysis | None = None
    listing_extraction: ListingExtraction | None = None
    listing_red_flags: ListingRedFlags | None = None
    viewing_notes: ViewingNotes | None = None

    # Card display fields (optional for backward compat)
    highlights: list[str] | None = None
    lowlights: list[str] | None = None
    one_line: str | None = None

    @field_validator("one_line", mode="before")
    @classmethod
    def unwrap_one_line(cls, v: Any) -> Any:
        """Unwrap one_line if stored as dict or JSON string like {"one_line": "text"}."""
        if isinstance(v, dict) and "one_line" in v:
            return v["one_line"]
        if isinstance(v, str) and v.startswith("{"):
            try:
                parsed = json.loads(v)
                if isinstance(parsed, dict) and "one_line" in parsed:
                    return parsed["one_line"]
            except (json.JSONDecodeError, TypeError):
                pass
        return v

    # Advisory flags (no auto-filtering)
    condition_concerns: bool = False
    concern_severity: Literal["minor", "moderate", "serious", "none"] | None = None

    @field_validator("concern_severity", mode="before")
    @classmethod
    def coerce_none_severity(cls, v: Any) -> Any:
        """Coerce None to 'none' for backward compat with old DB data."""
        return "none" if v is None else v

    # Value assessment (calculated, not from LLM)
    value: ValueAnalysis | None = None

    # Overall star rating (1-5, from LLM)
    overall_rating: int | None = None

    # For notifications
    summary: str

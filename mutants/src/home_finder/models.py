"""Pydantic models for properties and search criteria."""

import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator

# ---------------------------------------------------------------------------
# Shared field validators for backward compat with old DB data
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


class PropertySource(StrEnum):
    """Supported property listing platforms."""

    RIGHTMOVE = "rightmove"
    ZOOPLA = "zoopla"
    OPENRENT = "openrent"
    ONTHEMARKET = "onthemarket"

    @property
    def display_name(self) -> str:
        """Human-readable display name for this source."""
        return _SOURCE_META[self.value]["name"]


_SOURCE_META: Final[dict[str, dict[str, str]]] = {
    "openrent": {"name": "OpenRent", "abbr": "O", "color": "#00b4d8"},
    "rightmove": {"name": "Rightmove", "abbr": "R", "color": "#00deb6"},
    "zoopla": {"name": "Zoopla", "abbr": "Z", "color": "#8040bf"},
    "onthemarket": {"name": "OnTheMarket", "abbr": "M", "color": "#e54b4b"},
}

SOURCE_NAMES: Final[dict[str, str]] = {k: v["name"] for k, v in _SOURCE_META.items()}
SOURCE_BADGES: Final[dict[str, dict[str, str]]] = _SOURCE_META


class FurnishType(StrEnum):
    """Furnishing type for property search filters."""

    FURNISHED = "furnished"
    UNFURNISHED = "unfurnished"
    PART_FURNISHED = "part_furnished"


class TransportMode(StrEnum):
    """Transport modes for commute filtering."""

    CYCLING = "cycling"
    PUBLIC_TRANSPORT = "public_transport"
    DRIVING = "driving"
    WALKING = "walking"


class Property(BaseModel):
    """A rental property listing."""

    model_config = ConfigDict(frozen=True)

    source: PropertySource
    source_id: str = Field(description="Unique ID from the source platform")
    url: HttpUrl
    title: str
    price_pcm: int = Field(ge=0, description="Price per calendar month in GBP")
    bedrooms: int = Field(ge=0)
    address: str
    postcode: str | None = None
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    description: str | None = None
    image_url: HttpUrl | None = None
    image_hash: str | None = None  # Perceptual hash of main listing image
    available_from: datetime | None = None
    first_seen: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("postcode")
    @classmethod
    def normalize_postcode(cls, v: str | None) -> str | None:
        """Normalize postcode to uppercase with single space."""
        if v is None:
            return None
        return " ".join(v.upper().split())

    @property
    def unique_id(self) -> str:
        """Unique identifier across all sources."""
        return f"{self.source.value}:{self.source_id}"

    @model_validator(mode="after")
    def check_coordinates(self) -> Self:
        """Ensure both lat and lon are present or both are absent."""
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("Both latitude and longitude must be provided, or neither")
        return self


class SearchCriteria(BaseModel):
    """Search criteria for filtering properties."""

    model_config = ConfigDict(frozen=True)

    min_price: int = Field(default=0, ge=0)
    max_price: int = Field(ge=0)
    min_bedrooms: int = Field(default=1, ge=0)
    max_bedrooms: int = Field(ge=0)
    destination_postcode: str = Field(description="Postcode to calculate commute to")
    max_commute_minutes: int = Field(ge=1, le=120)
    transport_modes: tuple[TransportMode, ...] = Field(
        default=(TransportMode.CYCLING, TransportMode.PUBLIC_TRANSPORT)
    )

    @field_validator("destination_postcode")
    @classmethod
    def normalize_postcode(cls, v: str) -> str:
        """Normalize postcode to uppercase with single space."""
        return " ".join(v.upper().split())

    @model_validator(mode="after")
    def check_price_range(self) -> Self:
        """Ensure min_price <= max_price."""
        if self.min_price > self.max_price:
            raise ValueError("min_price must be <= max_price")
        return self

    @model_validator(mode="after")
    def check_bedroom_range(self) -> Self:
        """Ensure min_bedrooms <= max_bedrooms."""
        if self.min_bedrooms > self.max_bedrooms:
            raise ValueError("min_bedrooms must be <= max_bedrooms")
        return self

    def matches_property(self, prop: Property) -> bool:
        """Check if a property matches the basic criteria (price, bedrooms)."""
        return (
            self.min_price <= prop.price_pcm <= self.max_price
            and self.min_bedrooms <= prop.bedrooms <= self.max_bedrooms
        )


class NotificationStatus(StrEnum):
    """Status of property notification."""

    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"


class TrackedProperty(BaseModel):
    """A property being tracked in the database."""

    property: Property
    commute_minutes: int | None = None
    transport_mode: TransportMode | None = None
    notification_status: NotificationStatus = NotificationStatus.PENDING
    notified_at: datetime | None = None


class PropertyImage(BaseModel):
    """An image from a property listing."""

    model_config = ConfigDict(frozen=True)

    url: HttpUrl
    source: PropertySource
    image_type: Literal["gallery", "floorplan"]


class MergedProperty(BaseModel):
    """A property aggregated from multiple listing sources.

    When the same property is listed on multiple platforms (e.g., OpenRent and Rightmove),
    this model combines data from all sources rather than discarding duplicates.
    """

    model_config = ConfigDict(frozen=True)

    # Canonical data (from first-seen source)
    canonical: Property

    # All sources where this property was found
    sources: tuple[PropertySource, ...]

    # URLs per platform (for "Also listed on...")
    source_urls: dict[PropertySource, HttpUrl]

    # Combined images from all sources
    images: tuple[PropertyImage, ...] = ()

    # Best floorplan found (prefer highest resolution)
    floorplan: PropertyImage | None = None

    # Price range if varies across platforms
    min_price: int
    max_price: int

    # Combined descriptions (keyed by source)
    descriptions: dict[PropertySource, str] = Field(default_factory=dict)

    @property
    def unique_id(self) -> str:
        """Unique identifier based on canonical property."""
        return self.canonical.unique_id

    @property
    def price_varies(self) -> bool:
        """Whether the price differs across platforms."""
        return self.min_price != self.max_price


# ---------------------------------------------------------------------------
# Quality analysis models (used by filters, storage, notifiers, web routes)
# ---------------------------------------------------------------------------


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
    has_dishwasher: Literal["yes", "no", "unknown"] = "unknown"
    has_washing_machine: Literal["yes", "no", "unknown"] = "unknown"
    notes: str = ""

    @field_validator("has_dishwasher", "has_washing_machine", mode="before")
    @classmethod
    def coerce_bool_to_tristate(cls, v: Any) -> Any:
        return _coerce_bool_to_tristate(v)


class ConditionAnalysis(BaseModel):
    """Analysis of property condition."""

    model_config = ConfigDict(frozen=True)

    overall_condition: Literal["excellent", "good", "fair", "poor", "unknown"] = "unknown"
    has_visible_damp: Literal["yes", "no", "unknown"] = "unknown"
    has_visible_mold: Literal["yes", "no", "unknown"] = "unknown"
    has_worn_fixtures: bool = False
    maintenance_concerns: list[str] = []
    confidence: Literal["high", "medium", "low"] = "medium"

    @field_validator("has_visible_damp", "has_visible_mold", mode="before")
    @classmethod
    def coerce_bool_to_tristate(cls, v: Any) -> Any:
        return _coerce_bool_to_tristate(v)

    @field_validator("has_worn_fixtures", mode="before")
    @classmethod
    def coerce_none_to_false(cls, v: Any) -> Any:
        return _coerce_none_to_false(v)


class LightSpaceAnalysis(BaseModel):
    """Analysis of natural light and space feel."""

    model_config = ConfigDict(frozen=True)

    natural_light: Literal["excellent", "good", "fair", "poor", "unknown"] = "unknown"
    window_sizes: Literal["large", "medium", "small", "unknown"] | None = None
    feels_spacious: bool | None = None  # None = unknown
    ceiling_height: Literal["high", "standard", "low", "unknown"] | None = None
    notes: str = ""

    @field_validator("window_sizes", "ceiling_height", mode="before")
    @classmethod
    def coerce_none_to_unknown(cls, v: Any) -> Any:
        return _coerce_none_to_unknown(v)


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


class BathroomAnalysis(BaseModel):
    """Analysis of bathroom amenities and condition."""

    model_config = ConfigDict(frozen=True)

    overall_condition: Literal["modern", "decent", "dated", "unknown"] = "unknown"
    has_bathtub: bool | None = None
    shower_type: Literal["overhead", "separate_cubicle", "electric", "none", "unknown"] | None = (
        None
    )
    is_ensuite: Literal["yes", "no", "unknown"] = "unknown"
    notes: str = ""

    @field_validator("is_ensuite", mode="before")
    @classmethod
    def coerce_bool_to_tristate(cls, v: Any) -> Any:
        return _coerce_bool_to_tristate(v)


class BedroomAnalysis(BaseModel):
    """Analysis of bedroom space and fittings."""

    model_config = ConfigDict(frozen=True)

    primary_is_double: Literal["yes", "no", "unknown"] = "unknown"
    has_built_in_wardrobe: bool | None = None
    can_fit_desk: Literal["yes", "no", "unknown"] = "unknown"
    notes: str = ""

    @field_validator("primary_is_double", "can_fit_desk", mode="before")
    @classmethod
    def coerce_bool_to_tristate(cls, v: Any) -> Any:
        return _coerce_bool_to_tristate(v)


class OutdoorSpaceAnalysis(BaseModel):
    """Analysis of outdoor space availability."""

    model_config = ConfigDict(frozen=True)

    has_balcony: bool = False
    has_garden: bool = False
    has_terrace: bool = False
    has_shared_garden: bool = False
    notes: str = ""

    @field_validator("has_balcony", "has_garden", "has_terrace", "has_shared_garden", mode="before")
    @classmethod
    def coerce_none_to_false(cls, v: Any) -> Any:
        return _coerce_none_to_false(v)


class StorageAnalysis(BaseModel):
    """Analysis of storage provision."""

    model_config = ConfigDict(frozen=True)

    has_built_in_wardrobes: bool | None = None
    has_hallway_cupboard: bool | None = None
    storage_rating: Literal["good", "adequate", "poor", "unknown"] = "unknown"


class FlooringNoiseAnalysis(BaseModel):
    """Analysis of flooring type and noise indicators."""

    model_config = ConfigDict(frozen=True)

    primary_flooring: Literal["hardwood", "laminate", "carpet", "tile", "mixed", "unknown"] = (
        "unknown"
    )
    has_double_glazing: Literal["yes", "no", "unknown"] = "unknown"
    noise_indicators: list[str] = []
    notes: str = ""

    @field_validator("has_double_glazing", mode="before")
    @classmethod
    def coerce_bool_to_tristate(cls, v: Any) -> Any:
        return _coerce_bool_to_tristate(v)


class ListingExtraction(BaseModel):
    """Structured data extracted from the listing description."""

    model_config = ConfigDict(frozen=True)

    epc_rating: Literal["A", "B", "C", "D", "E", "F", "G", "unknown"] | None = None
    service_charge_pcm: int | None = None
    deposit_weeks: int | None = None
    bills_included: Literal["yes", "no", "unknown"] = "unknown"
    pets_allowed: Literal["yes", "no", "unknown"] = "unknown"
    parking: Literal["dedicated", "street", "none", "unknown"] | None = None
    council_tax_band: Literal["A", "B", "C", "D", "E", "F", "G", "H", "unknown"] | None = None
    property_type: PropertyType = PropertyType.UNKNOWN
    furnished_status: Literal["furnished", "unfurnished", "part_furnished", "unknown"] | None = None

    @field_validator("epc_rating", "council_tax_band", mode="before")
    @classmethod
    def coerce_none_to_unknown(cls, v: Any) -> Any:
        return _coerce_none_to_unknown(v)

    @field_validator("bills_included", "pets_allowed", mode="before")
    @classmethod
    def coerce_bool_to_tristate(cls, v: Any) -> Any:
        return _coerce_bool_to_tristate(v)


class ListingRedFlags(BaseModel):
    """Red flags identified from the listing."""

    model_config = ConfigDict(frozen=True)

    missing_room_photos: list[str] = []
    too_few_photos: bool = False
    selective_angles: bool = False
    description_concerns: list[str] = []
    red_flag_count: int = 0

    @field_validator("too_few_photos", "selective_angles", mode="before")
    @classmethod
    def coerce_none_to_false(cls, v: Any) -> Any:
        return _coerce_none_to_false(v)


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

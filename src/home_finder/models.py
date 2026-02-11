"""Pydantic models for properties and search criteria."""

from datetime import UTC, datetime
from enum import Enum
from typing import Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator


class PropertySource(str, Enum):
    """Supported property listing platforms."""

    RIGHTMOVE = "rightmove"
    ZOOPLA = "zoopla"
    OPENRENT = "openrent"
    ONTHEMARKET = "onthemarket"

    @property
    def display_name(self) -> str:
        """Human-readable display name for this source."""
        return _SOURCE_DISPLAY_NAMES[self.value]


_SOURCE_DISPLAY_NAMES: dict[str, str] = {
    "openrent": "OpenRent",
    "rightmove": "Rightmove",
    "zoopla": "Zoopla",
    "onthemarket": "OnTheMarket",
}

SOURCE_NAMES: Final[dict[str, str]] = {s.value: s.display_name for s in PropertySource}
assert set(SOURCE_NAMES) == {s.value for s in PropertySource}

SOURCE_BADGES: dict[str, dict[str, str]] = {
    "openrent": {"abbr": "O", "color": "#00b4d8", "name": "OpenRent"},
    "rightmove": {"abbr": "R", "color": "#00deb6", "name": "Rightmove"},
    "zoopla": {"abbr": "Z", "color": "#8040bf", "name": "Zoopla"},
    "onthemarket": {"abbr": "M", "color": "#e54b4b", "name": "OnTheMarket"},
}


class FurnishType(str, Enum):
    """Furnishing type for property search filters."""

    FURNISHED = "furnished"
    UNFURNISHED = "unfurnished"
    PART_FURNISHED = "part_furnished"


class TransportMode(str, Enum):
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


class NotificationStatus(str, Enum):
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

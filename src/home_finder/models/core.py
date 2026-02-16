"""Core property and search models."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator


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
    PENDING_ENRICHMENT = "pending_enrichment"
    PENDING_ANALYSIS = "pending_analysis"
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

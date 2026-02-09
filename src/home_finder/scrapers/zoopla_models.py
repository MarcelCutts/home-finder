"""Pydantic models for Zoopla JSON data structures."""

import re
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
)


class ZooplaListingUris(BaseModel):
    """URIs associated with a Zoopla listing."""

    model_config = ConfigDict(extra="ignore")

    detail: str = ""


class ZooplaFeature(BaseModel):
    """A feature of a Zoopla listing (e.g., bedrooms, bathrooms)."""

    model_config = ConfigDict(extra="ignore")

    icon_id: str = Field(default="", validation_alias="iconId")
    content: int | str | None = None


class ZooplaImage(BaseModel):
    """Image data for a Zoopla listing."""

    model_config = ConfigDict(extra="ignore")

    src: str = ""


class ZooplaPosition(BaseModel):
    """Geographic position of a listing."""

    model_config = ConfigDict(extra="ignore")

    lat: float | None = None
    lng: float | None = None


class ZooplaListing(BaseModel):
    """A single property listing from Zoopla's JSON data.

    Handles both RSC format and traditional Next.js format.
    """

    model_config = ConfigDict(extra="ignore")

    # Required fields
    listing_id: int = Field(validation_alias="listingId")

    # URL fields - RSC uses listingUris, older format uses detailUrl
    listing_uris: ZooplaListingUris | None = Field(default=None, validation_alias="listingUris")
    detail_url: str | None = Field(default=None, validation_alias="detailUrl")

    # Price fields - prefer unformatted, fall back to formatted
    # Note: priceUnformatted can be float for weekly prices (e.g., 357.69 pw)
    price_unformatted: float | int | None = Field(default=None, validation_alias="priceUnformatted")
    price: str = ""

    # Features - can be list (RSC) or dict (older format)
    features: list[ZooplaFeature] | dict[str, Any] = Field(default_factory=list)

    # Property details
    title: str = ""
    address: str = ""

    # Image - can be object or None
    image: ZooplaImage | None = None

    # Position
    pos: ZooplaPosition | None = None

    def get_detail_url(self) -> str | None:
        """Get the detail URL from either format."""
        if self.listing_uris and self.listing_uris.detail:
            return self.listing_uris.detail
        return self.detail_url

    def get_price_pcm(self) -> int | None:
        """Extract monthly price from available price fields."""
        if self.price_unformatted is not None:
            # price_unformatted can be float for weekly prices
            price = int(self.price_unformatted)
            # Check if it's a weekly price (needs conversion to monthly)
            if "pw" in self.price.lower():
                price = int(price * 52 / 12)
            return price

        if not self.price:
            return None

        # Parse formatted price string like "£1,900 pcm"
        match = re.search(r"£([\d,]+)", self.price)
        if not match:
            return None

        price = int(match.group(1).replace(",", ""))

        # Convert weekly to monthly if needed
        if "pw" in self.price.lower():
            price = int(price * 52 / 12)

        return price

    def get_bedrooms(self) -> int | None:
        """Extract bedroom count from features or title."""
        # Try RSC format: features as list of {iconId, content}
        if isinstance(self.features, list):
            for feature in self.features:
                if feature.icon_id == "bed" and feature.content is not None:
                    if isinstance(feature.content, int):
                        return feature.content
                    if isinstance(feature.content, str) and feature.content.isdigit():
                        return int(feature.content)

        # Try older dict format: features.beds
        if isinstance(self.features, dict):
            beds = self.features.get("beds")
            if isinstance(beds, int):
                return beds

        # Fallback: parse from title
        return self._extract_bedrooms_from_text(self.title)

    def get_image_url(self) -> str | None:
        """Get the image URL, ensuring it has a protocol."""
        if not self.image or not self.image.src:
            return None

        url = self.image.src
        if not url.startswith("http"):
            url = f"https:{url}"
        return url

    def get_address(self) -> str:
        """Get address, falling back to title if empty."""
        return self.address or self.title

    def get_title(self) -> str:
        """Get title, falling back to address if empty."""
        return self.title or self.address

    @staticmethod
    def _extract_bedrooms_from_text(text: str) -> int | None:
        """Extract bedroom count from text like '2 bed flat'."""
        if not text:
            return None

        text_lower = text.lower()

        # Handle studio
        if "studio" in text_lower:
            return 0

        # Match "1 bed", "2 bedroom", etc.
        match = re.search(r"(\d+)\s*bed(?:room)?s?", text_lower)
        return int(match.group(1)) if match else None


# TypeAdapter for parsing lists of listings directly (for RSC format)
ZooplaListingsAdapter = TypeAdapter(list[ZooplaListing])

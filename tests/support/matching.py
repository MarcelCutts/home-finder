"""Listing, aggregate, and image builders for matching contract tests."""

from datetime import UTC, datetime
from io import BytesIO
from typing import Any

from PIL import Image
from pydantic import HttpUrl

from home_finder.models import MergedProperty, Property, PropertySource


def make_listing(
    source: PropertySource,
    source_id: str,
    *,
    price_pcm: int = 1800,
    bedrooms: int = 2,
    postcode: str = "E8 3RH",
    latitude: float | None = 51.5465,
    longitude: float | None = -0.0553,
    address: str = "123 Mare Street, Hackney, London",
    first_seen: datetime | None = None,
    available_from: datetime | None = None,
) -> Property:
    return Property(
        source=source,
        source_id=source_id,
        url=HttpUrl(f"https://example.com/{source.value}/{source_id}"),
        title=f"Test Property {source_id}",
        price_pcm=price_pcm,
        bedrooms=bedrooms,
        address=address,
        postcode=postcode,
        latitude=latitude,
        longitude=longitude,
        first_seen=first_seen or datetime(2025, 1, 15, 10, 0, tzinfo=UTC),
        available_from=available_from,
    )


def as_aggregate(prop: Property, **overrides: Any) -> MergedProperty:
    defaults = {
        "canonical": prop,
        "sources": (prop.source,),
        "source_urls": {prop.source: prop.url},
        "images": (),
        "floorplan": None,
        "min_price": prop.price_pcm,
        "max_price": prop.price_pcm,
        "descriptions": {},
    }
    defaults.update(overrides)
    return MergedProperty(**defaults)  # type: ignore[arg-type]


def make_solid_jpeg(color: tuple[int, int, int] = (135, 206, 235)) -> bytes:
    """Create a simple JPEG image with the given solid color."""
    img = Image.new("RGB", (100, 100), color)
    buf = BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def make_checkerboard_jpeg() -> bytes:
    """Create a visually distinct image (checkerboard pattern).

    pHash is designed to tolerate small variations, so solid-color images
    of different colors can still match. A checkerboard pattern is structurally
    very different from a solid color.
    """
    from PIL import ImageDraw

    img = Image.new("RGB", (100, 100), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    # Draw checkerboard pattern
    for x in range(0, 100, 10):
        for y in range(0, 100, 10):
            if (x + y) % 20 == 0:
                draw.rectangle([x, y, x + 10, y + 10], fill=(0, 0, 0))
    buf = BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()

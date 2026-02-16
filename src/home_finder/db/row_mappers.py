"""Shared row-mapping utilities for database modules."""

from __future__ import annotations

import json
from collections.abc import Callable, Coroutine
from datetime import datetime
from typing import Any, TypedDict

import aiosqlite
from pydantic import HttpUrl

from home_finder.models import (
    MergedProperty,
    NotificationStatus,
    Property,
    PropertyImage,
    PropertyQualityAnalysis,
    PropertySource,
    TransportMode,
)


class PropertyListItem(TypedDict, total=False):
    """Shape of dicts returned by get_properties_paginated.

    Fields from the SQL join plus parsed JSON columns.
    All fields marked total=False because dict(row) includes the full row.
    """

    unique_id: str
    title: str
    price_pcm: int
    bedrooms: int
    address: str
    postcode: str | None
    image_url: str | None
    latitude: float | None
    longitude: float | None
    commute_minutes: int | None
    transport_mode: str | None
    min_price: int | None
    max_price: int | None
    # Quality analysis (from JOIN)
    quality_rating: int | None
    quality_concerns: bool | None
    quality_severity: str | None
    quality_summary: str
    # Parsed JSON fields
    sources_list: list[str]
    source_urls_dict: dict[str, str]
    descriptions_dict: dict[str, str]
    # Value rating from analysis
    value_rating: str | None
    # Extended quality fields (from analysis_json)
    highlights: list[str] | None
    lowlights: list[str] | None
    property_type: str | None
    one_line: str | None
    epc_rating: str | None
    # Analysis JSON for fit_score computation in routes
    analysis_json: str | None


class PropertyDetailItem(PropertyListItem, total=False):
    """Shape of dicts returned by get_property_detail.

    Extends PropertyListItem with images and parsed quality analysis.
    """

    description: str | None
    ward: str | None
    quality_analysis: PropertyQualityAnalysis | None
    gallery_images: list[PropertyImage]
    floorplan_images: list[PropertyImage]


def parse_json_fields(prop_dict: dict[str, Any]) -> None:
    """Parse common JSON-encoded fields in a property dict (mutates in place)."""
    if prop_dict.get("sources"):
        prop_dict["sources_list"] = json.loads(prop_dict["sources"])
    else:
        prop_dict["sources_list"] = [prop_dict.get("source", "")]

    if prop_dict.get("source_urls"):
        prop_dict["source_urls_dict"] = json.loads(prop_dict["source_urls"])
    else:
        prop_dict["source_urls_dict"] = {}

    if prop_dict.get("descriptions_json"):
        prop_dict["descriptions_dict"] = json.loads(prop_dict["descriptions_json"])
    else:
        prop_dict["descriptions_dict"] = {}


def row_to_property(row: aiosqlite.Row) -> Property:
    """Convert a database row to a Property.

    Args:
        row: Database row from the properties table.

    Returns:
        Property instance.
    """
    first_seen = datetime.fromisoformat(row["first_seen"])
    available_from = (
        datetime.fromisoformat(row["available_from"]) if row["available_from"] else None
    )
    return Property(
        source=PropertySource(row["source"]),
        source_id=row["source_id"],
        url=row["url"],
        title=row["title"],
        price_pcm=row["price_pcm"],
        bedrooms=row["bedrooms"],
        address=row["address"],
        postcode=row["postcode"],
        latitude=row["latitude"],
        longitude=row["longitude"],
        description=row["description"],
        image_url=row["image_url"] if row["image_url"] else None,
        available_from=available_from,
        first_seen=first_seen,
    )


async def row_to_merged_property(
    row: aiosqlite.Row,
    *,
    load_images: bool = True,
    get_property_images: (
        Callable[[str], Coroutine[Any, Any, list[PropertyImage]]] | None
    ) = None,
) -> MergedProperty:
    """Convert a database row to a MergedProperty.

    Parses multi-source JSON fields and optionally loads images.

    Args:
        row: Database row from the properties table.
        load_images: Whether to load images from property_images table.
        get_property_images: Async callable to fetch images; required when load_images=True.

    Returns:
        Reconstructed MergedProperty.
    """
    prop = row_to_property(row)

    sources_list: list[PropertySource] = []
    source_urls: dict[PropertySource, HttpUrl] = {}
    descriptions: dict[PropertySource, str] = {}

    if row["sources"]:
        for s in json.loads(row["sources"]):
            sources_list.append(PropertySource(s))
    else:
        sources_list.append(prop.source)

    if row["source_urls"]:
        for s, url in json.loads(row["source_urls"]).items():
            source_urls[PropertySource(s)] = HttpUrl(url)
    else:
        source_urls[prop.source] = prop.url

    if row["descriptions_json"]:
        for s, desc in json.loads(row["descriptions_json"]).items():
            descriptions[PropertySource(s)] = desc

    gallery: tuple[PropertyImage, ...] = ()
    floorplan_img: PropertyImage | None = None
    if load_images:
        if get_property_images is None:
            msg = "get_property_images is required when load_images=True"
            raise ValueError(msg)
        images = await get_property_images(prop.unique_id)
        gallery = tuple(img for img in images if img.image_type == "gallery")
        floorplan_img = next((img for img in images if img.image_type == "floorplan"), None)

    min_price = row["min_price"] if row["min_price"] is not None else prop.price_pcm
    max_price = row["max_price"] if row["max_price"] is not None else prop.price_pcm

    return MergedProperty(
        canonical=prop,
        sources=tuple(sources_list),
        source_urls=source_urls,
        images=gallery,
        floorplan=floorplan_img,
        min_price=min_price,
        max_price=max_price,
        descriptions=descriptions,
    )


def build_base_insert(
    prop: Property,
    *,
    commute_minutes: int | None = None,
    transport_mode: TransportMode | None = None,
    notification_status: NotificationStatus,
) -> tuple[list[str], list[Any]]:
    """Build the 18 base INSERT column names and values for a Property.

    Returns (columns, values) lists that are guaranteed to stay in sync.
    """
    columns: list[str] = [
        "unique_id",
        "source",
        "source_id",
        "url",
        "title",
        "price_pcm",
        "bedrooms",
        "address",
        "postcode",
        "latitude",
        "longitude",
        "description",
        "image_url",
        "available_from",
        "first_seen",
        "commute_minutes",
        "transport_mode",
        "notification_status",
    ]
    values: list[Any] = [
        prop.unique_id,
        prop.source.value,
        prop.source_id,
        str(prop.url),
        prop.title,
        prop.price_pcm,
        prop.bedrooms,
        prop.address,
        prop.postcode,
        prop.latitude,
        prop.longitude,
        prop.description,
        str(prop.image_url) if prop.image_url else None,
        prop.available_from.isoformat() if prop.available_from else None,
        prop.first_seen.isoformat(),
        commute_minutes,
        transport_mode.value if transport_mode else None,
        notification_status.value,
    ]
    return columns, values


def build_merged_insert_columns(
    merged: MergedProperty,
    *,
    commute_minutes: int | None = None,
    transport_mode: TransportMode | None = None,
    notification_status: NotificationStatus,
    extra: dict[str, Any] | None = None,
) -> tuple[list[str], list[Any]]:
    """Build INSERT column names and values for a MergedProperty.

    Returns (columns, values) lists that are guaranteed to stay in sync.
    Callers can add extra columns via the ``extra`` dict, then build their
    own SQL INSERT + ON CONFLICT using the returned lists.
    """
    columns, values = build_base_insert(
        merged.canonical,
        commute_minutes=commute_minutes,
        transport_mode=transport_mode,
        notification_status=notification_status,
    )
    sources_json = json.dumps([s.value for s in merged.sources])
    source_urls_json = json.dumps({s.value: str(url) for s, url in merged.source_urls.items()})
    descriptions_json = (
        json.dumps({s.value: d for s, d in merged.descriptions.items()})
        if merged.descriptions
        else None
    )
    columns.extend(["sources", "source_urls", "min_price", "max_price", "descriptions_json"])
    values.extend([
        sources_json,
        source_urls_json,
        merged.min_price,
        merged.max_price,
        descriptions_json,
    ])
    if extra:
        for col, val in extra.items():
            columns.append(col)
            values.append(val)
    return columns, values

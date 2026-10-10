"""Attach bounded source observations at the successful search-response boundary."""

from __future__ import annotations

import hashlib
from datetime import datetime
from functools import lru_cache
from importlib.resources import files
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import parse_qsl, urlsplit, urlunsplit
from uuid import uuid4

from pydantic import HttpUrl, TypeAdapter, ValidationError

from home_finder.logging import get_logger
from home_finder.models import FurnishType, Property
from home_finder.models.observations import (
    CollectionQuery,
    ObservationFields,
    ObservationParser,
    ObservationRequest,
    PublicURL,
    SourceObservation,
)

if TYPE_CHECKING:
    from home_finder.scrapers.detail_fetcher import DetailPageData

logger = get_logger(__name__)


def collection_query(
    *,
    area: str,
    min_price: int,
    max_price: int,
    min_bedrooms: int,
    max_bedrooms: int,
    furnish_types: tuple[FurnishType, ...] = (),
    min_bathrooms: int = 0,
    include_let_agreed: bool = True,
) -> CollectionQuery:
    return CollectionQuery.model_validate(
        {
            "area": area,
            "min_price": min_price,
            "max_price": max_price,
            "min_bedrooms": min_bedrooms,
            "max_bedrooms": max_bedrooms,
            "furnish_types": tuple(item.value for item in furnish_types),
            "min_bathrooms": min_bathrooms,
            "include_let_agreed": include_let_agreed,
        }
    )


def request_context(
    url: str, *, query: CollectionQuery | None = None, page_index: int | None = None
) -> ObservationRequest:
    if not 0 < len(url) <= 16_384:
        raise ValueError("Request URL is outside the supported fingerprint bound")
    return ObservationRequest(
        requested_url_sha256=hashlib.sha256(url.encode()).hexdigest(),
        query=query,
        page_index=page_index,
    )


def _listing_url(prop: Property) -> HttpUrl:
    """Remove only recognized tracking parameters from a provider listing URL.

    Unrecognized queries retain the strict contract's rejection. This function
    neither requests a URL nor qualifies a source's access/target identity.
    """
    url = urlsplit(str(prop.url))
    known = {
        "search_identifier",
        "search_id",
        "featured",
        "channel",
        "from",
        "ref",
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_content",
        "utm_term",
    }
    hosts = {
        "rightmove": {"rightmove.co.uk", "www.rightmove.co.uk"},
        "zoopla": {"zoopla.co.uk", "www.zoopla.co.uk"},
        "openrent": {"openrent.co.uk", "www.openrent.co.uk", "openrent.com", "www.openrent.com"},
        "onthemarket": {"onthemarket.com", "www.onthemarket.com"},
    }
    if url.hostname in hosts[prop.source.value] and all(
        key.casefold() in known for key, _value in parse_qsl(url.query, keep_blank_values=True)
    ):
        return HttpUrl(urlunsplit((url.scheme, url.netloc, url.path, "", "")))
    return prop.url


@lru_cache(maxsize=4)
def search_parser(source: str) -> ObservationParser:
    """Fingerprint the actual adapter, shared pagination and observation mapping."""
    modules = [f"{source}.py", "base.py", "observations.py", "parsing.py"]
    if source == "rightmove":
        modules.append("rightmove_payloads.py")
    root = files("home_finder.scrapers")
    digest = hashlib.sha256()
    for name in sorted(modules):
        digest.update(name.encode() + b"\x00" + root.joinpath(name).read_bytes() + b"\x00")
    return ObservationParser(name=f"{source}.search", version="1", code_sha256=digest.hexdigest())


def search_observations(
    properties: list[Property],
    *,
    observed_at: datetime,
    request: ObservationRequest | None = None,
) -> tuple[list[Property], int]:
    """Stamp once after receipt, before policy changes/caps; no invented body hash.

    These are normalized-only observations: legacy transports do not retain
    response bytes. A new page receipt gets a new stable in-process event prefix;
    passing its Property through pagination/persistence keeps that identity.
    """
    event_prefix = uuid4().hex
    result = []
    errors = 0
    for index, prop in enumerate(properties):
        fields: dict[str, Any] = (
            prop.listing_metadata.model_dump(mode="json", exclude_none=True)
            if prop.listing_metadata is not None
            else {}
        )
        for name in ("title", "price_pcm", "bedrooms", "address", "postcode", "description"):
            value = getattr(prop, name)
            if value is not None and (not isinstance(value, str) or value.strip()):
                fields[name] = {"value": value}
        if prop.latitude is not None and prop.longitude is not None:
            fields["coordinates"] = {
                "value": {"latitude": prop.latitude, "longitude": prop.longitude}
            }
        try:
            observation = SourceObservation(
                producer_id="local-search-v1",
                event_id=f"{event_prefix}:{index}",
                source=prop.source,
                source_id=prop.source_id,
                listing_url=_listing_url(prop),
                origin="search",
                observed_at=observed_at,
                parser=search_parser(prop.source.value),
                request=request,
                fields=ObservationFields.model_validate(fields),
            )
        except ValidationError:
            # Never fall through to a lossy unrecorded property on malformed
            # evidence. Neighbouring valid records remain available.
            errors += 1
            logger.warning("source_observation_invalid", source=prop.source.value)
            continue
        result.append(prop.model_copy(update={"source_observation": observation}))
    return result, errors


@lru_cache(maxsize=4)
def detail_parser(source: str) -> ObservationParser:
    modules = [
        "scrapers/detail_fetcher.py",
        "scrapers/detail_identity.py",
        "scrapers/observations.py",
        "models/identity.py",
        "scrapers/source_area.py",
        "models/source_area.py",
        "utils/http_evidence.py",
        "utils/listing_identity.py",
    ]
    if source == "rightmove":
        modules.append("scrapers/rightmove_payloads.py")
    root = files("home_finder")
    digest = hashlib.sha256()
    for name in sorted(modules):
        digest.update(name.encode() + b"\x00" + root.joinpath(name).read_bytes() + b"\x00")
    return ObservationParser(name=f"{source}.detail", version="1", code_sha256=digest.hexdigest())


_PUBLIC_URL = TypeAdapter(PublicURL)


def detail_observation(
    prop: Property,
    data: DetailPageData,
    *,
    observed_at: datetime,
    metadata: ObservationFields | None = None,
    request: ObservationRequest | None = None,
) -> SourceObservation | None:
    """Retain the parsed detail subset, never copied search facts or invented empties.

    Media URLs are selected by the existing parser's gallery limit. They are
    references, not complete source inventories or proof of downloaded bytes.
    Unsupported optional URL sets are explicitly marked unavailable as a whole.
    """
    fields: dict[str, Any] = metadata.model_dump(mode="json", exclude_none=True) if metadata else {}
    for name in ("description", "features", "postcode"):
        value = getattr(data, name)
        if value and (not isinstance(value, str) or value.strip()):
            fields[name] = {"value": value}
    if data.latitude is not None and data.longitude is not None:
        fields["coordinates"] = {"value": {"latitude": data.latitude, "longitude": data.longitude}}
    omissions: dict[
        Literal["gallery_urls", "floorplan_urls", "floor_area_readings"],
        Literal["unsupported_url", "unsupported_quantity"],
    ] = {}
    if data.floor_area_readings:
        fields["floor_area_readings"] = {
            "value": [item.model_dump(mode="json") for item in data.floor_area_readings]
        }
    if data.floor_area_omission:
        omissions["floor_area_readings"] = data.floor_area_omission
    media: dict[Literal["gallery_urls", "floorplan_urls"], list[str] | None] = {
        "gallery_urls": data.gallery_urls,
        "floorplan_urls": [data.floorplan_url] if data.floorplan_url else None,
    }
    for name, urls in media.items():
        if urls:
            try:
                fields[name] = {"value": [_PUBLIC_URL.validate_python(url) for url in urls]}
            except ValidationError:
                omissions[name] = "unsupported_url"
    if not fields and not omissions:
        return None  # An empty parser result cannot manufacture positive evidence.
    try:
        return SourceObservation(
            producer_id="local-detail-v1",
            event_id=uuid4().hex,
            source=prop.source,
            source_id=prop.source_id,
            listing_url=_listing_url(prop),
            origin="detail",
            observed_at=observed_at,
            parser=detail_parser(prop.source.value),
            request=request,
            fields=ObservationFields.model_validate(fields),
            normalization_omissions=omissions or None,
        )
    except ValidationError:
        raise ValueError("invalid_detail_evidence") from None

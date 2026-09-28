"""Attach bounded source observations at the successful search-response boundary."""

import hashlib
from datetime import datetime
from functools import lru_cache
from importlib.resources import files
from typing import Any
from urllib.parse import parse_qsl, urlsplit, urlunsplit
from uuid import uuid4

from pydantic import HttpUrl, ValidationError

from home_finder.logging import get_logger
from home_finder.models import Property
from home_finder.models.observations import ObservationFields, ObservationParser, SourceObservation

logger = get_logger(__name__)


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
    properties: list[Property], *, observed_at: datetime
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

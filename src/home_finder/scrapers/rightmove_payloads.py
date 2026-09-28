"""Bounded Rightmove contracts supported by retained source evidence."""

import json
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from pydantic import HttpUrl

from home_finder.models import Property, PropertySource
from home_finder.scrapers.parsing import extract_postcode

MAX_PAYLOAD_CHARACTERS = 4_000_000
MAX_SEARCH_LISTINGS = 200


@dataclass
class SearchPage:
    properties: list[Property] = field(default_factory=list)
    parse_errors: int = 0


def parse_structured_search(soup: BeautifulSoup) -> SearchPage | None:
    """None means no structured script; malformed/present data is an error page."""
    script = soup.find("script", id="__NEXT_DATA__")
    if script is None:
        return None
    raw = script.string or script.get_text()
    if len(raw) > MAX_PAYLOAD_CHARACTERS:
        return SearchPage(parse_errors=1)
    try:
        payload = json.loads(raw)["props"]["pageProps"]["searchResults"]
        listings = payload["properties"]
        if not isinstance(listings, list) or len(listings) > MAX_SEARCH_LISTINGS:
            raise ValueError("invalid_search_properties")
        # A missing, positive or malformed count cannot establish empty inventory.
        if not listings and (
            type(payload.get("resultCount")) is not int or payload["resultCount"] != 0
        ):
            raise ValueError("ambiguous_empty_search")
    except (ValueError, KeyError, TypeError, RecursionError):
        return SearchPage(parse_errors=1)
    result = SearchPage()
    for listing in listings:
        try:
            result.properties.append(_search_listing(listing))
        except (ValueError, KeyError, TypeError, AttributeError):
            result.parse_errors += 1
    return result


def _search_listing(listing: dict[str, Any]) -> Property:
    source_id = str(listing["id"])
    if not re.fullmatch(r"[1-9][0-9]*", source_id):
        raise ValueError("invalid_property_id")
    url = HttpUrl(urljoin("https://www.rightmove.co.uk", listing["propertyUrl"].split("#")[0]))
    if (
        url.scheme != "https"
        or url.host != "www.rightmove.co.uk"
        or url.port != 443
        or url.username
        or url.password
        or (url.path or "").rstrip("/") != f"/properties/{source_id}"
    ):
        raise ValueError("mismatched_property_url")
    price = listing["price"]
    amount = price["amount"]
    if type(amount) is not int or amount <= 0 or price["currencyCode"] != "GBP":
        raise ValueError("invalid_rent")
    frequency = price["frequency"]
    if frequency == "weekly":
        amount = round(amount * 52 / 12)
    elif frequency != "monthly":
        raise ValueError("unknown_rent_period")
    bedrooms = listing["bedrooms"]
    if type(bedrooms) is not int or bedrooms < 0:
        raise ValueError("unknown_bedroom_count")
    address = listing["displayAddress"]
    if not isinstance(address, str) or not address.strip():
        raise ValueError("missing_address")
    title = listing.get("heading") or f"{bedrooms} bedroom property, {address}"
    summary = listing.get("summary")
    description = (
        BeautifulSoup(summary, "html.parser").get_text(" ", strip=True) if summary else None
    )
    location = listing.get("location") or {}
    latitude, longitude = location.get("latitude"), location.get("longitude")
    if latitude is None or longitude is None:
        latitude = longitude = None
    images = listing.get("propertyImages") or {}
    return Property(
        source=PropertySource.RIGHTMOVE,
        source_id=source_id,
        url=url,
        title=title,
        price_pcm=amount,
        bedrooms=bedrooms,
        address=address,
        postcode=extract_postcode(address),
        latitude=latitude,
        longitude=longitude,
        description=description,
        image_url=images.get("mainImageSrc") or None,
    )


def legacy_detail_property(html: str, expected_id: str) -> dict[str, Any]:
    """Decode the evidenced plain JSON form; reject unqualified encoded variants.

    JSONDecoder handles braces and escapes inside strings. It does not evaluate
    JavaScript or guess the semantics of the reported reference-array format.
    """
    if len(html) > MAX_PAYLOAD_CHARACTERS:
        raise ValueError("page_model_size_limit")
    match = re.search(r"window\.(?:__)?PAGE_MODEL\s*=\s*", html)
    if match is None:
        raise ValueError("missing_page_model")
    if "__PAGE_MODEL" in match.group():
        raise ValueError("unsupported_page_model_variant")
    data, _ = json.JSONDecoder().raw_decode(html, match.end())
    if not isinstance(data, dict) or "data" in data or "encoding" in data:
        raise ValueError("unsupported_page_model_variant")
    property_data = data.get("propertyData")
    if not isinstance(property_data, dict) or not property_data:
        raise ValueError("missing_property_data")
    if "id" in property_data and str(property_data["id"]) != expected_id:
        raise ValueError("mismatched_property_id")
    return property_data

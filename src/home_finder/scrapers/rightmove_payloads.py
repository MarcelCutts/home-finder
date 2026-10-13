"""Bounded Rightmove contracts supported by retained source evidence."""

import json
import math
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from pydantic import HttpUrl

from home_finder.models import ListingTextEvidence, Property, PropertySource
from home_finder.scrapers.base import ParsedSearchPage
from home_finder.scrapers.parsing import extract_postcode

MAX_PAYLOAD_CHARACTERS = 4_000_000
MAX_SEARCH_LISTINGS = 200
MAX_DETAIL_NODES = 20_000
MAX_DETAIL_REFERENCES = 100_000
MAX_DETAIL_DEPTH = 64


@dataclass
class SearchPage(ParsedSearchPage):
    end_of_results: bool = False


def _search_integer(raw: Any) -> int:
    """The portal encodes counts and pagination values as decimal strings."""
    if isinstance(raw, str) and re.fullmatch(r"[0-9]{1,7}", raw):
        raw = int(raw)
    if type(raw) is not int or not 0 <= raw <= 1_000_000:
        raise ValueError("invalid_search_integer")
    return raw


def _empty_search_is_exhausted(payload: dict[str, Any], requested_index: int) -> bool:
    count = _search_integer(payload.get("resultCount"))
    if count == 0:
        return True
    # A positive total describes the entire search, not this page. Accept an
    # empty page beyond that inventory only when the echoed request and every
    # relevant pagination field agree; never turn a missing first page into zero.
    params, pagination = payload["searchParameters"], payload["pagination"]
    index = _search_integer(params["index"])
    size = _search_integer(params["numberOfPropertiesPerPage"])
    if not 1 <= size <= MAX_SEARCH_LISTINGS:
        return False
    total_pages = (count + size - 1) // size
    return (
        index == requested_index
        and index >= count
        and index % size == 0
        and _search_integer(pagination["total"]) == total_pages
        and _search_integer(pagination["page"]) == index // size + 1 > total_pages
        and _search_integer(pagination["last"]) == (total_pages - 1) * size
    )


def parse_structured_search(soup: BeautifulSoup, *, requested_index: int = 0) -> SearchPage | None:
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
        if not listings and not _empty_search_is_exhausted(payload, requested_index):
            raise ValueError("ambiguous_empty_search")
    except (ValueError, KeyError, TypeError, RecursionError):
        return SearchPage(parse_errors=1)
    result = SearchPage(end_of_results=not listings)
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
    features = listing.get("keyFeatures")
    if isinstance(features, list) and all(isinstance(item, dict) for item in features):
        features = [item.get("description") for item in features]
    if features is not None and (
        not isinstance(features, list) or not all(isinstance(item, str) for item in features)
    ):
        raise ValueError("invalid_search_features")
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
        search_text=ListingTextEvidence(
            description=description,
            description_origin="search",
            features=tuple(features) if features is not None else None,
            features_origin="search",
        ),
        image_url=images.get("mainImageSrc") or None,
    )


def detail_property(html: str, expected_id: str, *, exact_numbers: bool = False) -> dict[str, Any]:
    """Decode retained plain JSON and indexed-array forms without executing JS."""
    if len(html) > MAX_PAYLOAD_CHARACTERS:
        raise ValueError("page_model_size_limit")
    match = re.search(r"window\.(?:__)?PAGE_MODEL\s*=\s*", html)
    if match is None:
        raise ValueError("missing_page_model")
    decoder = json.JSONDecoder(
        parse_constant=_reject_constant, parse_float=Decimal if exact_numbers else float
    )
    data, _ = decoder.raw_decode(html, match.end())
    indexed = "__PAGE_MODEL" in match.group()
    if not isinstance(data, dict):
        raise ValueError("unsupported_page_model_variant")
    if indexed:
        if data.get("encoding") != "on" or not isinstance(data.get("data"), str):
            raise ValueError("unsupported_page_model_variant")
        data = _decode_indexed_model(decoder.decode(data["data"]))
    elif "data" in data or "encoding" in data:
        raise ValueError("unsupported_page_model_variant")
    property_data = data.get("propertyData")
    if not isinstance(property_data, dict) or not property_data:
        raise ValueError("missing_property_data")
    if indexed and "id" not in property_data:
        raise ValueError("missing_property_id")
    if "id" in property_data and str(property_data["id"]) != expected_id:
        raise ValueError("mismatched_property_id")
    return property_data


def _reject_constant(value: str) -> Any:
    raise ValueError("nonfinite_page_model_number")


def _decode_indexed_model(table: Any) -> dict[str, Any]:
    """Container values are indexes; scalar table entries are literal values.

    The retained response uses nonnegative integer references, including an
    index to a literal null. Negative sentinels and cycles have no evidenced
    semantics and fail explicitly. Memoization preserves shared containers
    without expanding a compact graph into an exponentially larger tree.
    """
    if not isinstance(table, list) or not table or len(table) > MAX_DETAIL_NODES:
        raise ValueError("page_model_node_limit")
    references = 0
    for entry in table:
        if isinstance(entry, float) and not math.isfinite(entry):
            raise ValueError("nonfinite_page_model_number")
        if not isinstance(entry, (dict, list)):
            continue
        references += len(entry)
        if references > MAX_DETAIL_REFERENCES:
            raise ValueError("page_model_reference_limit")
        for ref in entry.values() if isinstance(entry, dict) else entry:
            if type(ref) is not int or not 0 <= ref < len(table):
                raise ValueError("invalid_page_model_reference")

    decoded: dict[int, Any] = {}
    heights: dict[int, int] = {}
    active: set[int] = set()

    def resolve(index: int, depth: int) -> Any:
        if depth > MAX_DETAIL_DEPTH:
            raise ValueError("page_model_depth_limit")
        if index in active:
            raise ValueError("cyclic_page_model_reference")
        if index in decoded:
            # A cached subtree may be reached through a longer path later.
            if depth + heights[index] > MAX_DETAIL_DEPTH:
                raise ValueError("page_model_depth_limit")
            return decoded[index]
        active.add(index)
        entry = table[index]
        value: Any
        if isinstance(entry, dict):
            value = {key: resolve(ref, depth + 1) for key, ref in entry.items()}
            children = list(entry.values())
        elif isinstance(entry, list):
            value = [resolve(ref, depth + 1) for ref in entry]
            children = entry
        else:
            value = entry
            children = []
        active.remove(index)
        decoded[index] = value
        heights[index] = max((heights[ref] + 1 for ref in children), default=0)
        return value

    root = resolve(0, 0)
    if not isinstance(root, dict):
        raise ValueError("invalid_page_model_root")
    # Validate unused nodes too; accepting malformed hidden branches would make
    # the supported format depend on which fields happen to be consumed today.
    for index in range(len(table)):
        resolve(index, 0)
    return root

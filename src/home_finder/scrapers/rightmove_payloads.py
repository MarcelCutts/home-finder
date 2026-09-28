"""Bounded Rightmove contracts supported by retained source evidence."""

import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from pydantic import HttpUrl

from home_finder.models import Property, PropertySource
from home_finder.models.observations import (
    AgentReference,
    FieldChange,
    ObservationFields,
)
from home_finder.scrapers.parsing import extract_postcode

MAX_PAYLOAD_CHARACTERS = 4_000_000
MAX_SEARCH_LISTINGS = 200
MAX_DETAIL_NODES = 20_000
MAX_DETAIL_REFERENCES = 100_000
MAX_DETAIL_DEPTH = 64


@dataclass
class SearchPage:
    properties: list[Property] = field(default_factory=list)
    parse_errors: int = 0


def _portal_date(raw: Any, meaning: str, *, reason: str = "") -> dict[str, Any] | None:
    if raw is None or raw == "":
        return None
    if not isinstance(raw, str):
        raise ValueError("invalid_portal_date")
    instant = None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is not None and parsed.utcoffset() is not None:
            instant = parsed
    except ValueError:
        pass  # Keep source wording without inventing precision or a timezone.
    return {
        "value": {
            "literal": f"{reason}: {raw}" if reason else raw,
            "meaning": meaning,
            "instant": instant,
        }
    }


def _search_metadata(listing: dict[str, Any]) -> ObservationFields:
    fields: dict[str, Any] = {}
    fields["first_visible_date"] = _portal_date(listing.get("firstVisibleDate"), "first_visible")
    fields["availability_date"] = _portal_date(listing.get("letAvailableDate"), "available")
    update = listing.get("listingUpdate")
    if update:
        if not isinstance(update, dict):
            raise ValueError("invalid_listing_update")
        reason = update.get("listingUpdateReason") or "unspecified"
        if not isinstance(reason, str):
            raise ValueError("invalid_listing_update_reason")
        meaning = {"price_reduced": "reduced", "new": "listed", "added": "listed"}.get(
            reason, "unspecified"
        )
        fields["portal_date"] = _portal_date(
            update.get("listingUpdateDate"), meaning, reason=reason
        )
    count = listing.get("numberOfFloorplans")
    if count is not None:
        fields["observed_floorplan_count"] = {"value": count}
    customer = listing.get("customer")
    if customer:
        if not isinstance(customer, dict):
            raise ValueError("invalid_customer")
        branch = customer.get("branchId")
        if type(branch) is int or (isinstance(branch, str) and branch.strip()):
            fields["agent"] = FieldChange[AgentReference](
                value=AgentReference(
                    branch_id=str(branch), branch_name=customer.get("branchDisplayName") or None
                )
            )
    features = listing.get("keyFeatures")
    if features:
        fields["features"] = {"value": features}
    return ObservationFields.model_validate(fields)


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
        listing_metadata=_search_metadata(listing),
    )


def detail_property(html: str, expected_id: str) -> dict[str, Any]:
    """Decode retained plain JSON and indexed-array forms without executing JS."""
    if len(html) > MAX_PAYLOAD_CHARACTERS:
        raise ValueError("page_model_size_limit")
    match = re.search(r"window\.(?:__)?PAGE_MODEL\s*=\s*", html)
    if match is None:
        raise ValueError("missing_page_model")
    decoder = json.JSONDecoder(parse_constant=_reject_constant)
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

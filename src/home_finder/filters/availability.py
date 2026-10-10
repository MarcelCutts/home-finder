"""Conservative availability evidence scoped to one provider listing.

Supported structured paths are deliberately explicit. A new portal layout
needs a fixture; arbitrary page text and a successful HTTP status are not facts
about the target listing's availability.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any

from bs4 import BeautifulSoup

from home_finder.scrapers.onthemarket_payloads import onthemarket_redux_state
from home_finder.scrapers.rightmove_payloads import detail_property
from home_finder.utils.http_evidence import is_challenge as is_challenge
from home_finder.utils.listing_identity import listing_id as listing_id


class ListingStatus(Enum):
    ACTIVE = "active"
    REMOVED = "removed"
    LET_AGREED = "let_agreed"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class AvailabilityEvidence:
    status: ListingStatus
    reason: str


def _at(value: object, *keys: str) -> Any:
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _primary_records(source: str, soup: BeautifulSoup, target: str) -> list[dict[str, Any]]:
    records = []
    for script in soup.find_all("script"):
        text = script.string or ""
        indexed = re.search(r"window\.__PAGE_MODEL\s*=\s*", text) if source == "rightmove" else None
        if indexed is not None:
            try:
                records.append(detail_property(text[indexed.start() :], target))
            except (ValueError, RecursionError):
                # A present but untrusted primary model must not borrow a status
                # from another script, a linked offer, or the page heading.
                records.append({})
            continue
        try:
            if source == "rightmove":
                match = re.search(r"(?:window\.)?PAGE_MODEL\s*=\s*", text)
                if not match:
                    continue
                value, _ = json.JSONDecoder().raw_decode(text[match.end() :])
                record = _at(value, "propertyData")
            elif script.get("id") == "__NEXT_DATA__":
                value = json.loads(text)
                record = (
                    _at(value, "props", "pageProps", "listing")
                    if source == "zoopla"
                    else _at(onthemarket_redux_state(value), "property")
                    if source == "onthemarket"
                    else None
                )
            else:
                continue
            if isinstance(record, dict):
                records.append(record)
        except (ValueError, RecursionError):
            continue
    return records


def _status_word(value: object) -> ListingStatus | None:
    if not isinstance(value, str):
        return None
    word = re.sub(r"[\s_-]+", " ", value.strip().lower())
    if word in {"let agreed", "letting agreed", "under offer"}:
        return ListingStatus.LET_AGREED
    if word in {
        "removed",
        "unavailable",
        "no longer available",
        "off market",
        "this property is no longer available",
        "this property has been removed",
    }:
        return ListingStatus.REMOVED
    if word in {"active", "available"}:
        return ListingStatus.ACTIVE
    return None


def _record_states(record: dict[str, Any]) -> set[ListingStatus]:
    states = set()
    for key in ("status", "listingStatus", "displayStatus"):
        state = _status_word(record.get(key))
        if state:
            states.add(state)
    tags = record.get("tags")
    if isinstance(tags, list):
        states.update(state for value in tags if (state := _status_word(value)))
    for container in (record, record.get("lettings")):
        if not isinstance(container, dict):
            continue
        for key, state in (
            ("letAgreed", ListingStatus.LET_AGREED),
            ("isLetAgreed", ListingStatus.LET_AGREED),
            ("underOffer", ListingStatus.LET_AGREED),
            ("isRemoved", ListingStatus.REMOVED),
        ):
            if container.get(key) is True:
                states.add(state)
    return states


def _structured_states(source: str, soup: BeautifulSoup, target: str) -> set[ListingStatus]:
    states: set[ListingStatus] = set()
    for record in _primary_records(source, soup, target):
        ids = {str(record[key]) for key in ("id", "propertyId", "listingId") if key in record}
        if ids == {target}:
            states.update(_record_states(record))
        else:
            # A mismatched primary record cannot establish status for this URL.
            states.add(ListingStatus.UNKNOWN)
    return states


def _linked_data_states(source: str, soup: BeautifulSoup, target: str) -> set[ListingStatus]:
    states: set[ListingStatus] = set()
    words = {
        "InStock": ListingStatus.ACTIVE,
        "LimitedAvailability": ListingStatus.ACTIVE,
        "OutOfStock": ListingStatus.REMOVED,
        "SoldOut": ListingStatus.REMOVED,
        "Discontinued": ListingStatus.REMOVED,
    }
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            raw = json.loads(script.string or "")
        except (json.JSONDecodeError, RecursionError):
            continue
        nodes = raw if isinstance(raw, list) else [raw]
        for node in nodes:
            if not isinstance(node, dict):
                continue
            # Only the document's explicit graph, never arbitrary nested recommendations.
            candidates = node.get("@graph", [node])
            if not isinstance(candidates, list):
                continue
            for candidate in candidates:
                if not isinstance(candidate, dict):
                    continue
                url = candidate.get("url")
                zoopla_page_identity = (
                    source == "zoopla"
                    and candidate.get("@type") == "RealEstateListing"
                    and "mainEntityOfPage" in candidate
                )
                if zoopla_page_identity:
                    # Current Zoopla JSON-LD identifies its main listing here,
                    # with an explicit Offer but no top-level `url` field.
                    page_url = candidate["mainEntityOfPage"]
                    if (
                        not isinstance(page_url, str)
                        or listing_id(source, page_url) != target
                        or (
                            "url" in candidate
                            and (not isinstance(url, str) or listing_id(source, url) != target)
                        )
                    ):
                        states.add(ListingStatus.UNKNOWN)
                        continue
                    url = page_url
                if not isinstance(url, str) or listing_id(source, url) != target:
                    continue
                offers = candidate.get("offers", [])
                offers = offers if isinstance(offers, list) else [offers]
                for offer in offers:
                    if not isinstance(offer, dict):
                        if zoopla_page_identity:
                            states.add(ListingStatus.UNKNOWN)
                        continue
                    if zoopla_page_identity and offer.get("@type") != "Offer":
                        states.add(ListingStatus.UNKNOWN)
                        continue
                    offer_url = offer.get("url", url)
                    if not isinstance(offer_url, str) or listing_id(source, offer_url) != target:
                        states.add(ListingStatus.UNKNOWN)
                        continue
                    availability = offer.get("availability")
                    if isinstance(availability, str):
                        for prefix in ("https://schema.org/", "http://schema.org/"):
                            if (
                                availability.startswith(prefix)
                                and availability[len(prefix) :] in words
                            ):
                                states.add(words[availability[len(prefix) :]])
    return states


def _primary_heading_states(source: str, soup: BeautifulSoup, target: str) -> set[ListingStatus]:
    """Only exact status headings/badges in the canonical page's main content."""
    canonical = {link.get("href") for link in soup.select('link[rel~="canonical"]')}
    if not canonical:
        return set()
    if len(canonical) != 1 or not all(
        isinstance(url, str) and listing_id(source, url) == target for url in canonical
    ):
        return {ListingStatus.UNKNOWN}
    states = set()
    for node in soup.select(
        'main h1, main [data-testid="listing-status"], main [data-testid="property-status"]'
    ):
        excluded = False
        for parent in (node, *node.parents):
            if parent.has_attr("hidden") or parent.get("aria-hidden") == "true":
                excluded = True
                break
            if any(
                parent.get(attr) not in (None, target)
                for attr in ("data-property-id", "data-listing-id")
            ):
                excluded = True
                break
            if parent.name in {"aside", "nav", "footer"} or parent.get("role") == "complementary":
                excluded = True
                break
            labels = " ".join(str(parent.get(attr, "")) for attr in ("id", "class", "data-testid"))
            if re.search(r"recommend|similar|related|search.result", labels, re.I):
                excluded = True
                break
        if not excluded and (state := _status_word(node.get_text(" ", strip=True))):
            states.add(state)
    return states


def assess_listing_page(
    source: str,
    *,
    status_code: int,
    html: str,
    final_url: str,
    requested_url: str | None = None,
    headers: Mapping[str, str] | None = None,
) -> AvailabilityEvidence:
    """Recognise only target-bound status; generic redirects and 404s are unknown."""
    unknown = ListingStatus.UNKNOWN
    if is_challenge(html, headers):
        return AvailabilityEvidence(unknown, "challenge")
    target = listing_id(source, requested_url or final_url)
    if target is None or listing_id(source, final_url) != target:
        return AvailabilityEvidence(unknown, "target_url_unconfirmed")
    if status_code == 410:
        return AvailabilityEvidence(ListingStatus.REMOVED, "target_http_410")
    if status_code not in {200, 404}:
        return AvailabilityEvidence(unknown, f"http_{status_code}")
    soup = BeautifulSoup(html, "html.parser")
    states = (
        _structured_states(source, soup, target)
        | _linked_data_states(source, soup, target)
        | _primary_heading_states(source, soup, target)
    )
    if len(states) != 1 or unknown in states:
        return AvailabilityEvidence(unknown, "conflicting_status" if states else "no_target_status")
    state = next(iter(states))
    if status_code == 404 and state != ListingStatus.REMOVED:
        return AvailabilityEvidence(unknown, "http_404_unconfirmed")
    return AvailabilityEvidence(state, "target_structured_status")

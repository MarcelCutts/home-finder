"""Bind detail extraction to the requested advert, not merely an HTTP 200."""

from collections.abc import Mapping
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlsplit

from home_finder.models import Property
from home_finder.models.identity import SourceIdentityInput, resolve_source_identity
from home_finder.utils.listing_identity import listing_id


class DetailIdentityError(ValueError):
    """A bounded reason code; never includes response URLs or page contents."""


def requested_identity(prop: Property) -> str:
    identity = resolve_source_identity(
        SourceIdentityInput(
            unique_id=prop.unique_id,
            source=prop.source.value,
            source_id=prop.source_id,
            url=str(prop.url),
        )
    )
    if identity.provider_id is None:
        raise DetailIdentityError("unresolved_request_identity")
    return identity.provider_id


def validate_response_url(prop: Property, response_url: str) -> str:
    expected = requested_identity(prop)
    actual = listing_id(prop.source.value, response_url)
    if actual is None:
        raise DetailIdentityError("unrecognized_response_url")
    if actual != expected:
        raise DetailIdentityError("response_listing_mismatch")
    return expected


def validate_embedded_identity(
    source: str, expected: str, listing: Mapping[str, Any]
) -> tuple[str, ...]:
    """Check only the selected main record, never related adverts or image IDs."""
    keys = ("id", "listingId") if source == "zoopla" else ("id",)
    markers = []
    for key in keys:
        if key not in listing:
            continue
        value = listing[key]
        if type(value) not in {str, int} or str(value) != expected:
            raise DetailIdentityError("embedded_listing_mismatch")
        markers.append(f"listing.{key}")
    return tuple(markers)


class _PageIdentity(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_head = False
        self.urls: list[tuple[str, str | None]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "head":
            self.in_head = True
        if not self.in_head:
            return
        attributes = dict(attrs)
        if tag == "link" and "canonical" in (attributes.get("rel") or "").lower().split():
            self.urls.append(("canonical", attributes.get("href")))
        elif tag == "meta" and (attributes.get("property") or "").lower() == "og:url":
            self.urls.append(("og:url", attributes.get("content")))
        if len(self.urls) > 16:
            raise DetailIdentityError("too_many_page_identity_markers")

    def handle_endtag(self, tag: str) -> None:
        if tag == "head":
            self.in_head = False


def validate_page_identity(
    source: str,
    expected: str,
    response_url: str,
    html: str,
    *,
    listing: Mapping[str, Any] | None = None,
) -> tuple[str, ...]:
    """Require a main-record ID or page identity URL and agreement of all present markers.

    HTML/RSC fallbacks require a canonical or Open Graph URL in the page head.
    This qualifies the page, not ownership of every image or field in it.
    """
    parser = _PageIdentity()
    parser.feed(html)
    parser.close()
    markers = list(validate_embedded_identity(source, expected, listing or {}))
    urls = parser.urls
    if source == "onthemarket" and listing is not None and "canonicalUrl" in listing:
        urls.append(("listing.canonicalUrl", listing["canonicalUrl"]))
    for kind, url in urls:
        if not isinstance(url, str) or not url or any(ord(c) <= 32 or ord(c) == 127 for c in url):
            raise DetailIdentityError("invalid_page_identity_url")
        try:
            path = urlsplit(url).path
        except ValueError as exc:
            raise DetailIdentityError("invalid_page_identity_url") from exc
        if not path:
            raise DetailIdentityError("invalid_page_identity_url")
        if listing_id(source, urljoin(response_url, url)) != expected:
            raise DetailIdentityError("page_listing_mismatch")
        markers.append(kind)
    if not markers:
        raise DetailIdentityError("missing_page_identity")
    return tuple(sorted(set(markers)))

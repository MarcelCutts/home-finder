"""Resolve a provider ID only from a recognized provider's listing path."""

import re
from urllib.parse import urlsplit

_HOSTS = {
    "rightmove": {"rightmove.co.uk", "www.rightmove.co.uk"},
    "zoopla": {"zoopla.co.uk", "www.zoopla.co.uk"},
    "openrent": {"openrent.com", "www.openrent.com", "openrent.co.uk", "www.openrent.co.uk"},
    "onthemarket": {"onthemarket.com", "www.onthemarket.com"},
}
_PATHS = {
    "rightmove": re.compile(r"/properties/(\d+)/?"),
    "zoopla": re.compile(r"/(?:to-rent/)?details/(\d+)/?"),
    "onthemarket": re.compile(r"/details/(\d+)/?"),
    "openrent": re.compile(r"/(?:property-to-rent/.+/|property/)?(\d+)/?"),
}


def listing_id(source: str, url: str) -> str | None:
    """Match an explicit listing path; this is not response-target validation."""
    try:
        parsed = urlsplit(url)
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.hostname not in _HOSTS.get(source, set())
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in {None, 80 if parsed.scheme == "http" else 443}
        ):
            return None
    except ValueError:
        return None
    pattern = _PATHS.get(source)
    match = pattern.fullmatch(parsed.path) if pattern else None
    return match[1] if match else None

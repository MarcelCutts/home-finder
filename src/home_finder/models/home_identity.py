"""Internal home identity is independent of any portal advertisement or URL."""

import re
from dataclasses import dataclass

_HOME_ID = re.compile(r"h_[0-9a-f]{32}")
_LEGACY_CALLBACK_ID = re.compile(r"[A-Za-z0-9_-]+:[A-Za-z0-9_-]+")


def is_home_id(value: str) -> bool:
    return _HOME_ID.fullmatch(value) is not None


def is_callback_target(value: str) -> bool:
    """Accept new short IDs and the exact legacy callback grammar, never truncation."""
    return is_home_id(value) or _LEGACY_CALLBACK_ID.fullmatch(value) is not None


@dataclass(frozen=True)
class HomeTarget:
    home_id: str
    property_id: str

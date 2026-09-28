"""Provider identities shared by property and immutable-evidence models."""

from enum import StrEnum
from typing import Final


class PropertySource(StrEnum):
    """Supported property listing platforms."""

    RIGHTMOVE = "rightmove"
    ZOOPLA = "zoopla"
    OPENRENT = "openrent"
    ONTHEMARKET = "onthemarket"

    @property
    def display_name(self) -> str:
        """Human-readable display name for this source."""
        return SOURCE_BADGES[self.value]["name"]


SOURCE_BADGES: Final[dict[str, dict[str, str]]] = {
    "openrent": {"name": "OpenRent", "abbr": "O", "color": "#00b4d8"},
    "rightmove": {"name": "Rightmove", "abbr": "R", "color": "#00deb6"},
    "zoopla": {"name": "Zoopla", "abbr": "Z", "color": "#8040bf"},
    "onthemarket": {"name": "OnTheMarket", "abbr": "M", "color": "#e54b4b"},
}
SOURCE_NAMES: Final[dict[str, str]] = {key: value["name"] for key, value in SOURCE_BADGES.items()}

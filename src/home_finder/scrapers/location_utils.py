"""Location utilities for area detection and classification."""

import re

# Pattern to detect UK outcodes (e.g., E8, N15, SW1A, EC1)
# Outcode = 1-2 letters + 1-2 digits + optional letter
OUTCODE_PATTERN = re.compile(r"^[A-Z]{1,2}\d{1,2}[A-Z]?$", re.IGNORECASE)


def is_outcode(area: str) -> bool:
    """Check if area string is a UK postcode outcode.

    Args:
        area: Area string to check (e.g., "E8", "N15", "hackney").

    Returns:
        True if the area is a valid UK outcode format.

    Examples:
        >>> is_outcode("E8")
        True
        >>> is_outcode("N15")
        True
        >>> is_outcode("SW1A")
        True
        >>> is_outcode("hackney")
        False
    """
    return bool(OUTCODE_PATTERN.match(area.strip()))

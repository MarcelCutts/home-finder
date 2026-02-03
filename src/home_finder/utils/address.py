"""Address normalization utilities."""

import re

# Common street type abbreviations
STREET_TYPES = {
    r"\bst\b": "street",
    r"\brd\b": "road",
    r"\bave\b": "avenue",
    r"\bln\b": "lane",
    r"\bdr\b": "drive",
    r"\bct\b": "court",
    r"\bpl\b": "place",
    r"\bsq\b": "square",
    r"\bgrn\b": "green",
    r"\bgdns?\b": "gardens",
    r"\bterr?\b": "terrace",
    r"\bcres\b": "crescent",
    r"\bclse?\b": "close",
    r"\bmews\b": "mews",
    r"\bpk\b": "park",
    r"\bway\b": "way",
}

# Street type words (for extraction)
STREET_TYPE_WORDS = {
    "street",
    "road",
    "avenue",
    "lane",
    "drive",
    "court",
    "place",
    "square",
    "green",
    "gardens",
    "terrace",
    "crescent",
    "close",
    "mews",
    "park",
    "way",
    "hill",
    "rise",
    "grove",
    "walk",
}


def normalize_street_name(address: str) -> str:
    """Extract and normalize street name from address.

    Handles:
    - "Flat 2, The Towers, Mare Street" -> "mare street"
    - "2a Mare St" -> "mare street"
    - "Building Name, 123 Mare Street, London" -> "mare street"

    Args:
        address: Full address string.

    Returns:
        Normalized street name (lowercase, expanded abbreviations).
    """
    # Lowercase
    addr = address.lower()

    # Remove flat/unit numbers at start
    addr = re.sub(r"^(flat|unit|apt|apartment)\s*\d+[a-z]?\s*,?\s*", "", addr)
    addr = re.sub(r"^\d+[a-z]?\s*,?\s*", "", addr)

    # If comma-separated, find the part with a street type word
    if "," in addr:
        parts = [p.strip() for p in addr.split(",")]
        for part in parts:
            # Check if this part contains a street type
            if any(st in part.split() for st in STREET_TYPE_WORDS):
                addr = part
                break

    # Remove leading "the" (e.g., "The Towers" -> "Towers")
    addr = re.sub(r"^the\s+", "", addr)

    # Expand abbreviations
    for abbrev, full in STREET_TYPES.items():
        addr = re.sub(abbrev, full, addr, flags=re.IGNORECASE)

    # Remove postcodes
    addr = re.sub(
        r"[A-Z]{1,2}\d{1,2}[A-Z]?\s*\d?[A-Z]{0,2}",
        "",
        addr,
        flags=re.IGNORECASE,
    )

    # Remove "London", borough names
    addr = re.sub(
        r"\b(london|hackney|islington|tower hamlets|haringey|camden|"
        r"lambeth|southwark|lewisham|greenwich|newham|waltham forest)\b",
        "",
        addr,
        flags=re.IGNORECASE,
    )

    # Remove commas and normalize whitespace
    addr = re.sub(r",", " ", addr)
    addr = " ".join(addr.split()).strip()

    # Take first part - stop at common delimiters
    parts = re.split(r"\s+(?:near|off|behind|opposite)\s+", addr)
    result = parts[0] if parts else addr

    # Remove any remaining leading numbers (house numbers)
    result = re.sub(r"^\d+[a-z]?\s+", "", result)

    return result


def extract_outcode(postcode: str | None) -> str | None:
    """Extract outcode from full or partial postcode.

    Args:
        postcode: Full postcode like "E8 3RH" or partial like "E8".

    Returns:
        Outcode like "E8", or None if invalid.
    """
    if not postcode:
        return None

    # Match outcode pattern
    match = re.match(r"([A-Z]{1,2}\d{1,2}[A-Z]?)", postcode.upper().strip())
    return match.group(1) if match else None

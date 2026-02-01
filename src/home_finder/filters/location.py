"""Location-based property filtering.

This filter validates that properties are actually located in the requested
search areas, catching any "leakage" from scrapers that return properties
outside the intended geographic boundaries.
"""

import re

from home_finder.logging import get_logger
from home_finder.models import Property

logger = get_logger(__name__)

# Mapping of London boroughs to their valid outcodes
# This is used to validate that properties are in the expected area
BOROUGH_OUTCODES: dict[str, set[str]] = {
    # Central London
    "city-of-london": {"EC1", "EC2", "EC3", "EC4"},
    "westminster": {
        "SW1",
        "SW1A",
        "SW1E",
        "SW1H",
        "SW1P",
        "SW1V",
        "SW1W",
        "SW1X",
        "SW1Y",
        "W1",
        "W1B",
        "W1C",
        "W1D",
        "W1F",
        "W1G",
        "W1H",
        "W1J",
        "W1K",
        "W1S",
        "W1T",
        "W1U",
        "W1W",
        "W2",
        "WC1",
        "WC2",
        "NW1",
        "NW8",
    },
    "camden": {"NW1", "NW3", "NW5", "NW6", "WC1", "WC2", "N1", "N6", "N7", "N19"},
    "islington": {
        "N1",
        "N4",
        "N5",
        "N7",
        "N19",
        "EC1",
        "EC1A",
        "EC1M",
        "EC1N",
        "EC1R",
        "EC1V",
        "EC1Y",
    },
    # East London
    "hackney": {"E5", "E8", "E9", "E10", "N1", "N4", "N5", "N15", "N16"},
    "tower-hamlets": {"E1", "E1W", "E2", "E3", "E14"},
    "newham": {"E6", "E7", "E12", "E13", "E15", "E16"},
    "waltham-forest": {"E4", "E10", "E11", "E17"},
    "barking-dagenham": {"IG11", "RM6", "RM8", "RM9", "RM10"},
    "havering": {
        "RM1",
        "RM2",
        "RM3",
        "RM4",
        "RM5",
        "RM7",
        "RM11",
        "RM12",
        "RM13",
        "RM14",
    },
    "redbridge": {"E18", "IG1", "IG2", "IG3", "IG4", "IG5", "IG6", "IG7", "IG8"},
    # North London
    "haringey": {"N4", "N6", "N8", "N10", "N11", "N15", "N17", "N22"},
    "enfield": {
        "EN1",
        "EN2",
        "EN3",
        "EN4",
        "EN5",
        "N9",
        "N11",
        "N13",
        "N14",
        "N18",
        "N21",
    },
    "barnet": {
        "EN4",
        "EN5",
        "N2",
        "N3",
        "N11",
        "N12",
        "N14",
        "N20",
        "NW4",
        "NW7",
        "NW9",
        "NW11",
    },
    # West London
    "kensington-chelsea": {"SW3", "SW5", "SW7", "SW10", "W8", "W10", "W11", "W14"},
    "hammersmith-fulham": {"SW6", "W6", "W12", "W14"},
    "brent": {"NW2", "NW6", "NW9", "NW10", "HA0", "HA1", "HA3", "HA9"},
    "ealing": {"W3", "W5", "W7", "W13", "UB1", "UB2", "UB5", "UB6"},
    "hounslow": {"TW3", "TW4", "TW5", "TW7", "TW8", "TW13", "TW14", "W4"},
    "hillingdon": {
        "UB3",
        "UB4",
        "UB7",
        "UB8",
        "UB9",
        "UB10",
        "UB11",
        "HA4",
        "HA5",
        "HA6",
    },
    "harrow": {"HA1", "HA2", "HA3", "HA5", "HA7"},
    # South London
    "lambeth": {
        "SE1",
        "SE5",
        "SE11",
        "SE21",
        "SE24",
        "SE27",
        "SW2",
        "SW4",
        "SW8",
        "SW9",
        "SW12",
        "SW16",
    },
    "southwark": {"SE1", "SE5", "SE15", "SE16", "SE17", "SE21", "SE22", "SE24"},
    "lewisham": {"SE4", "SE6", "SE8", "SE12", "SE13", "SE14", "SE23", "SE26"},
    "greenwich": {"SE2", "SE3", "SE7", "SE9", "SE10", "SE18", "SE28"},
    "bromley": {
        "BR1",
        "BR2",
        "BR3",
        "BR4",
        "BR5",
        "BR6",
        "BR7",
        "SE6",
        "SE9",
        "SE12",
        "SE20",
    },
    "bexley": {
        "DA1",
        "DA5",
        "DA6",
        "DA7",
        "DA8",
        "DA14",
        "DA15",
        "DA16",
        "DA17",
        "DA18",
        "SE2",
        "SE9",
        "SE18",
        "SE28",
    },
    "croydon": {"CR0", "CR2", "CR5", "CR7", "CR8", "SE19", "SE25", "SW16"},
    "sutton": {"SM1", "SM2", "SM3", "SM4", "SM5", "SM6", "SM7"},
    "merton": {"CR4", "SM4", "SW19", "SW20"},
    "wandsworth": {"SW4", "SW8", "SW11", "SW12", "SW15", "SW17", "SW18", "SW19"},
    "kingston-thames": {"KT1", "KT2", "KT3", "KT4", "KT5", "KT6", "KT9"},
    "richmond-thames": {
        "TW1",
        "TW2",
        "TW9",
        "TW10",
        "TW11",
        "TW12",
        "SW13",
        "SW14",
        "SW15",
    },
}

# Outcode aliases for flexible matching
OUTCODE_ALIASES: dict[str, str] = {
    # Common variations
    "tower hamlets": "tower-hamlets",
    "waltham forest": "waltham-forest",
    "barking and dagenham": "barking-dagenham",
    "kensington and chelsea": "kensington-chelsea",
    "hammersmith and fulham": "hammersmith-fulham",
    "kingston upon thames": "kingston-thames",
    "richmond upon thames": "richmond-thames",
    "city of london": "city-of-london",
}


def extract_outcode(postcode: str | None) -> str | None:
    """Extract the outcode (first part) from a UK postcode.

    Args:
        postcode: Full or partial UK postcode.

    Returns:
        The outcode portion (e.g., "E8" from "E8 3RH"), or None.
    """
    if not postcode:
        return None

    # Match outcode pattern: 1-2 letters + 1-2 digits + optional letter
    match = re.match(r"^([A-Z]{1,2}\d{1,2}[A-Z]?)", postcode.upper().strip())
    return match.group(1) if match else None


def normalize_area(area: str) -> str:
    """Normalize an area name for lookup.

    Args:
        area: Area name or outcode.

    Returns:
        Normalized area name.
    """
    normalized = area.lower().strip()
    return OUTCODE_ALIASES.get(normalized, normalized)


class LocationFilter:
    """Filter properties by geographic location.

    This filter validates that scraped properties are actually in the
    requested search areas, preventing "location leakage" where scrapers
    return properties from outside the intended area.
    """

    def __init__(self, search_areas: list[str], strict: bool = True) -> None:
        """Initialize the location filter.

        Args:
            search_areas: List of borough names or outcodes to accept.
            strict: If True, reject properties without postcodes.
                   If False, allow properties without postcodes through.
        """
        self.search_areas = [normalize_area(a) for a in search_areas]
        self.strict = strict

        # Build set of valid outcodes from search areas
        self.valid_outcodes: set[str] = set()
        for area in self.search_areas:
            # Check if area is an outcode itself
            if re.match(r"^[a-z]{1,2}\d{1,2}[a-z]?$", area, re.IGNORECASE):
                self.valid_outcodes.add(area.upper())
            # Otherwise look up borough outcodes
            elif area in BOROUGH_OUTCODES:
                self.valid_outcodes.update(BOROUGH_OUTCODES[area])

        logger.debug(
            "location_filter_initialized",
            search_areas=self.search_areas,
            valid_outcodes=sorted(self.valid_outcodes),
        )

    def is_valid_location(self, prop: Property) -> bool:
        """Check if a property is in a valid location.

        Args:
            prop: Property to check.

        Returns:
            True if property is in a valid location, False otherwise.
        """
        outcode = extract_outcode(prop.postcode)

        if not outcode:
            # No postcode - allow through if not strict
            return not self.strict

        return outcode in self.valid_outcodes

    def filter_properties(self, properties: list[Property]) -> list[Property]:
        """Filter properties by location.

        Args:
            properties: List of properties to filter.

        Returns:
            List of properties in valid locations.
        """
        valid = []
        rejected = []

        for prop in properties:
            if self.is_valid_location(prop):
                valid.append(prop)
            else:
                rejected.append(prop)

        if rejected:
            # Log rejected properties for debugging
            rejected_outcodes: dict[str, int] = {}
            for prop in rejected:
                outcode = extract_outcode(prop.postcode) or "NO_POSTCODE"
                rejected_outcodes[outcode] = rejected_outcodes.get(outcode, 0) + 1

            logger.info(
                "location_filter_rejected",
                total_rejected=len(rejected),
                rejected_outcodes=rejected_outcodes,
            )

        logger.info(
            "location_filter_complete",
            total_properties=len(properties),
            valid=len(valid),
            rejected=len(rejected),
            search_areas=self.search_areas,
        )

        return valid

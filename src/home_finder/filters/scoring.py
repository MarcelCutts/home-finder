"""Pure scoring functions for property deduplication matching."""

import math
import re
from dataclasses import dataclass
from enum import Enum
from typing import Final

from home_finder.models import Property
from home_finder.utils.address import extract_outcode, normalize_street_name
from home_finder.utils.image_hash import hashes_match

# Price tolerance for fuzzy matching (3% - tighter than before)
PRICE_TOLERANCE: Final = 0.03

# Maximum distance in meters for coordinate-based matching
COORDINATE_DISTANCE_METERS: Final = 50

# Regex to detect full UK postcodes (outcode + incode)
FULL_POSTCODE_PATTERN: Final = re.compile(
    r"^[A-Z]{1,2}[0-9][0-9A-Z]?\s+[0-9][A-Z]{2}$", re.IGNORECASE
)

# Weighted scoring constants
SCORE_IMAGE_HASH: Final = 40
SCORE_FULL_POSTCODE: Final = 40
SCORE_COORDINATES: Final = 40
SCORE_STREET_NAME: Final = 20
SCORE_OUTCODE: Final = 10
SCORE_PRICE: Final = 15

# Minimum score to consider a match (raised from 55 to account for graduated
# scoring giving partial credit where binary gave 0)
MATCH_THRESHOLD: Final = 60

# Minimum number of contributing signals (prevents single-signal false positives)
MINIMUM_SIGNALS: Final = 2


class MatchConfidence(Enum):
    """Confidence level of a property match."""

    HIGH = "high"  # >= 80 points, 3+ signals - very confident
    MEDIUM = "medium"  # 60-79 points, 2+ signals - confident
    LOW = "low"  # 40-59 points - potential match, needs review
    NONE = "none"  # < 40 points - no match


@dataclass
class MatchScore:
    """Breakdown of match score between two properties."""

    image_hash: float = 0.0
    full_postcode: float = 0.0
    coordinates: float = 0.0
    street_name: float = 0.0
    outcode: float = 0.0
    price: float = 0.0

    @property
    def total(self) -> float:
        return (
            self.image_hash
            + self.full_postcode
            + self.coordinates
            + self.street_name
            + self.outcode
            + self.price
        )

    @property
    def signal_count(self) -> int:
        """Number of signals that contributed to the score."""
        return sum(
            [
                self.image_hash > 0,
                self.full_postcode > 0,
                self.coordinates > 0,
                self.street_name > 0,
                self.outcode > 0,
                self.price > 0,
            ]
        )

    @property
    def confidence(self) -> MatchConfidence:
        """Determine confidence level of match."""
        if self.total >= 80 and self.signal_count >= 3:
            return MatchConfidence.HIGH
        elif self.total >= MATCH_THRESHOLD and self.signal_count >= MINIMUM_SIGNALS:
            return MatchConfidence.MEDIUM
        elif self.total >= 40:
            return MatchConfidence.LOW
        return MatchConfidence.NONE

    @property
    def is_match(self) -> bool:
        """Whether this score constitutes a match."""
        return self.total >= MATCH_THRESHOLD and self.signal_count >= MINIMUM_SIGNALS

    def to_dict(self) -> dict[str, float | int | str]:
        """Convert to dict for logging."""
        return {
            "image_hash": self.image_hash,
            "full_postcode": self.full_postcode,
            "coordinates": self.coordinates,
            "street_name": self.street_name,
            "outcode": self.outcode,
            "price": self.price,
            "total": self.total,
            "signal_count": self.signal_count,
            "confidence": self.confidence.value,
        }


def is_full_postcode(postcode: str | None) -> bool:
    """Check if postcode includes both outcode and incode (e.g., 'E3 4AB' not just 'E3')."""
    if not postcode:
        return False
    normalized = " ".join(postcode.upper().split())
    return bool(FULL_POSTCODE_PATTERN.match(normalized))


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate distance between two coordinates in meters.

    Args:
        lat1, lon1: First coordinate.
        lat2, lon2: Second coordinate.

    Returns:
        Distance in meters.
    """
    R = 6371000  # Earth's radius in meters

    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return R * c


def coordinates_match(
    prop1: Property, prop2: Property, max_meters: float = COORDINATE_DISTANCE_METERS
) -> bool:
    """Check if two properties are within max_meters of each other.

    Args:
        prop1: First property.
        prop2: Second property.
        max_meters: Maximum distance in meters.

    Returns:
        True if both have coordinates and are within distance, False otherwise.
    """
    if (
        prop1.latitude is None
        or prop1.longitude is None
        or prop2.latitude is None
        or prop2.longitude is None
    ):
        return False

    distance = haversine_distance(prop1.latitude, prop1.longitude, prop2.latitude, prop2.longitude)
    return distance <= max_meters


def prices_match(price1: int, price2: int, tolerance: float = PRICE_TOLERANCE) -> bool:
    """Check if two prices are within tolerance.

    Args:
        price1: First price.
        price2: Second price.
        tolerance: Maximum relative difference (default 3%).

    Returns:
        True if prices are within tolerance.
    """
    if price1 == price2:
        return True
    if price1 == 0 or price2 == 0:  # pragma: no mutate (defensive; math gives same result)
        return False
    diff = abs(price1 - price2)
    avg = (price1 + price2) / 2
    return diff / avg <= tolerance


def graduated_coordinate_score(
    prop1: Property, prop2: Property, max_meters: float = COORDINATE_DISTANCE_METERS
) -> float:
    """Graduated coordinate proximity score.

    Returns 1.0 at 0m, 0.5 at max_meters (50m), 0.0 at 2*max_meters (100m+).

    Args:
        prop1: First property.
        prop2: Second property.
        max_meters: Reference distance for half-score.

    Returns:
        Score in [0.0, 1.0], or 0.0 if either property lacks coordinates.
    """
    if (
        prop1.latitude is None
        or prop1.longitude is None
        or prop2.latitude is None
        or prop2.longitude is None
    ):
        return 0.0

    distance = haversine_distance(prop1.latitude, prop1.longitude, prop2.latitude, prop2.longitude)

    if distance <= max_meters:
        return 1.0 - (distance / max_meters) * 0.5
    elif distance <= max_meters * 2:
        return 0.5 - ((distance - max_meters) / max_meters) * 0.5
    else:
        return 0.0


def graduated_price_score(price1: int, price2: int, tolerance: float = PRICE_TOLERANCE) -> float:
    """Graduated price proximity score.

    Returns 1.0 at exact match, 0.5 at tolerance (3%), 0.0 at 2*tolerance (6%+).

    Args:
        price1: First price.
        price2: Second price.
        tolerance: Reference percentage for half-score.

    Returns:
        Score in [0.0, 1.0], or 0.0 if either price is zero.
    """
    if price1 == price2:
        return 1.0
    if price1 == 0 or price2 == 0:  # pragma: no mutate (defensive; math gives same result)
        return 0.0

    diff = abs(price1 - price2)
    avg = (price1 + price2) / 2
    pct = diff / avg

    if pct <= tolerance:
        return 1.0 - (pct / tolerance) * 0.5
    elif pct <= tolerance * 2:
        return 0.5 - ((pct - tolerance) / tolerance) * 0.5
    else:
        return 0.0


def calculate_match_score(
    prop1: Property,
    prop2: Property,
    image_hashes: dict[str, str] | None = None,
) -> MatchScore:
    """Calculate weighted match score between two properties.

    Bedrooms must match for any comparison to happen.

    Args:
        prop1: First property.
        prop2: Second property.
        image_hashes: Optional dict mapping unique_id to image hash.

    Returns:
        MatchScore with breakdown of all signals.
    """
    score = MatchScore()

    # Gate: bedrooms must match
    if prop1.bedrooms != prop2.bedrooms:
        return score

    # Image hash (strong signal)
    if image_hashes:
        hash1 = image_hashes.get(prop1.unique_id)
        hash2 = image_hashes.get(prop2.unique_id)
        if hashes_match(hash1, hash2):
            score.image_hash = SCORE_IMAGE_HASH

    # Full postcode match
    if (
        is_full_postcode(prop1.postcode)
        and is_full_postcode(prop2.postcode)
        and prop1.postcode
        and prop2.postcode
        and prop1.postcode.upper() == prop2.postcode.upper()
    ):
        score.full_postcode = SCORE_FULL_POSTCODE

    # Coordinate proximity (graduated)
    coord_value = graduated_coordinate_score(prop1, prop2)
    if coord_value > 0:
        score.coordinates = SCORE_COORDINATES * coord_value

    # Street name match
    street1 = normalize_street_name(prop1.address)
    street2 = normalize_street_name(prop2.address)
    if street1 and street2 and street1 == street2:
        score.street_name = SCORE_STREET_NAME

    # Outcode match
    out1 = extract_outcode(prop1.postcode)
    out2 = extract_outcode(prop2.postcode)
    if out1 and out2 and out1 == out2:
        score.outcode = SCORE_OUTCODE

    # Price match (graduated)
    price_value = graduated_price_score(prop1.price_pcm, prop2.price_pcm)
    if price_value > 0:
        score.price = SCORE_PRICE * price_value

    return score

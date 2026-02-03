# Weighted Deduplication with Image Hashing

## Context

The current deduplication system requires full postcodes to match properties across platforms. However, Rightmove only provides outcodes (e.g., "E8" not "E8 3RH") and no coordinates, making it impossible to match Rightmove listings with other platforms.

**Current state after this session:**
- `MergedProperty` model added for combining data from multiple sources
- Full postcode requirement added (partial postcodes skip cross-platform matching)
- Coordinate confirmation added (if both have coords, must be within 50m)
- Price tolerance tightened to 3%
- Images are collected and persisted to database

## Problem

With current matching:
- OpenRent ↔ Zoopla ↔ OnTheMarket can match (all have full postcodes + coords)
- **Rightmove can't match with anyone** (only has outcode, no coordinates)

## Solution: Weighted Scoring System

Instead of requiring specific signals, use a point-based system where multiple weaker signals can combine to reach a confidence threshold. This follows the established [Fellegi-Sunter model](https://en.wikipedia.org/wiki/Record_linkage#Probabilistic_record_linkage) for probabilistic record linkage.

### Scoring Table

| Signal | Points | Notes |
|--------|--------|-------|
| Image hash match | +40 | Strong but not definitive (agents may reuse photos) |
| Full postcode match | +40 | Strong - "E8 3RH" = "E8 3RH" |
| Coordinates within 50m | +40 | Strong - precise location |
| Normalized street match | +20 | Medium - requires normalization |
| Outcode match | +10 | Weak alone - "E8" = "E8" |
| Price within 3% | +15 | Medium |
| Bedrooms match | Required | Gate - must match to even consider |

**Threshold: 55 points to merge**
**Minimum signals: 2** (prevents single-signal false positives)

### Why These Weights?

- **Image hash at 40 (not 50)**: Agents reuse photos across properties, landlords use same photos for different flats in a building. Research shows perceptual hashing alone has false positive risks with real estate imagery due to watermarks, crops, and stock photos.
- **2-signal minimum**: Forces corroboration. Image + outcode (50 pts) alone won't merge, but image + outcode + price (65 pts with 3 signals) will.
- **55 threshold**: Ensures meaningful signal combinations while still allowing Rightmove matching via image + street + outcode + price.

### Confidence Tiers

For rollout monitoring, matches are categorized by confidence:

| Tier | Score | Signals | Action |
|------|-------|---------|--------|
| HIGH | >= 80 | 3+ | Auto-merge |
| MEDIUM | 55-79 | 2+ | Merge with detailed logging |
| LOW | 40-54 | - | Log only (potential match, needs more signals) |
| NONE | < 40 | - | No match |

### Example Combinations

| Signals | Score | Signals | Merge? |
|---------|-------|---------|--------|
| Full postcode + price | 55 | 2 | ✓ |
| Coordinates + price | 55 | 2 | ✓ |
| Image hash + outcode | 50 | 2 | ✗ (below threshold) |
| Image hash + outcode + price | 65 | 3 | ✓ |
| Image hash + street + outcode | 70 | 3 | ✓ |
| Street + outcode + price | 45 | 3 | ✗ (below threshold) |
| Full postcode + coords + price | 95 | 3 | ✓ (HIGH confidence) |

---

## Implementation Plan

### Step 1: Add Image Hash Infrastructure

**File:** `pyproject.toml`

Add dependency:
```toml
dependencies = [
    # ... existing deps
    "imagehash>=4.3.1",
    "Pillow>=10.0.0",  # Required by imagehash
]
```

**File:** `src/home_finder/models.py`

Add `image_hash` field to Property:
```python
class Property(BaseModel):
    # ... existing fields
    image_url: HttpUrl | None = None
    image_hash: str | None = None  # Perceptual hash of main listing image
```

### Step 2: Create Image Hashing Utility

**File:** `src/home_finder/utils/image_hash.py`

```python
"""Image hashing utilities for property deduplication."""

import asyncio
import io
from typing import TYPE_CHECKING

import httpx
import imagehash
from PIL import Image

from home_finder.logging import get_logger

if TYPE_CHECKING:
    from home_finder.models import Property

logger = get_logger(__name__)

# Hamming distance threshold for considering images "same"
# Start conservative (8), tune up based on false negative rate.
# Research suggests 10-12 for 64-bit pHash, but real estate images
# have watermarks/crops that increase variance.
HASH_DISTANCE_THRESHOLD = 8


async def fetch_and_hash_image(url: str, timeout: float = 10.0) -> str | None:
    """Fetch image from URL and compute perceptual hash.

    Args:
        url: Image URL to fetch.
        timeout: Request timeout in seconds.

    Returns:
        Hex string of perceptual hash, or None if failed.
    """
    try:
        async with httpx.AsyncClient() as client:
            # Handle protocol-relative URLs
            if url.startswith("//"):
                url = "https:" + url

            response = await client.get(url, timeout=timeout, follow_redirects=True)
            response.raise_for_status()

            # Load image and compute hash
            image = Image.open(io.BytesIO(response.content))
            phash = imagehash.phash(image)
            return str(phash)

    except Exception as e:
        logger.debug("image_hash_failed", url=url, error=str(e))
        return None


def hashes_match(hash1: str | None, hash2: str | None) -> bool:
    """Check if two image hashes are similar enough to be the same image.

    Args:
        hash1: First hash (hex string).
        hash2: Second hash (hex string).

    Returns:
        True if hashes are within threshold distance.
    """
    if not hash1 or not hash2:
        return False

    try:
        h1 = imagehash.hex_to_hash(hash1)
        h2 = imagehash.hex_to_hash(hash2)
        distance = h1 - h2  # Hamming distance
        return distance <= HASH_DISTANCE_THRESHOLD
    except Exception:
        return False


async def fetch_image_hashes_batch(
    properties: list["Property"],
    max_concurrent: int = 10,
) -> dict[str, str]:
    """Fetch image hashes for multiple properties with controlled concurrency.

    Args:
        properties: Properties to fetch hashes for.
        max_concurrent: Maximum concurrent HTTP requests.

    Returns:
        Dict mapping property unique_id to hash string.
    """
    semaphore = asyncio.Semaphore(max_concurrent)

    async def fetch_one(prop: "Property") -> tuple[str, str | None]:
        async with semaphore:
            if prop.image_url:
                hash_val = await fetch_and_hash_image(str(prop.image_url))
                return (prop.unique_id, hash_val)
            return (prop.unique_id, None)

    tasks = [fetch_one(p) for p in properties]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    hashes = {}
    for result in results:
        if isinstance(result, tuple) and result[1] is not None:
            hashes[result[0]] = result[1]

    logger.info(
        "image_hashes_fetched",
        total=len(properties),
        successful=len(hashes),
    )

    return hashes
```

### Step 3: Add Street Name Normalization

**File:** `src/home_finder/utils/address.py`

```python
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
    "street", "road", "avenue", "lane", "drive", "court", "place",
    "square", "green", "gardens", "terrace", "crescent", "close",
    "mews", "park", "way", "hill", "rise", "grove", "walk",
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
```

### Step 4: Implement Weighted Scoring

**File:** `src/home_finder/filters/deduplication.py`

Add new scoring logic (keep existing functions, add new ones):

```python
from dataclasses import dataclass, field
from enum import Enum

from home_finder.utils.address import extract_outcode, normalize_street_name
from home_finder.utils.image_hash import hashes_match

# Scoring weights
SCORE_IMAGE_HASH = 40
SCORE_FULL_POSTCODE = 40
SCORE_COORDINATES = 40
SCORE_STREET_NAME = 20
SCORE_OUTCODE = 10
SCORE_PRICE = 15

# Minimum score to consider a match
MATCH_THRESHOLD = 55

# Minimum number of contributing signals (prevents single-signal false positives)
MINIMUM_SIGNALS = 2


class MatchConfidence(Enum):
    """Confidence level of a property match."""

    HIGH = "high"  # >= 80 points, 3+ signals - very confident
    MEDIUM = "medium"  # 55-79 points, 2+ signals - confident
    LOW = "low"  # 40-54 points - potential match, needs review
    NONE = "none"  # < 40 points - no match


@dataclass
class MatchScore:
    """Breakdown of match score between two properties."""

    image_hash: int = 0
    full_postcode: int = 0
    coordinates: int = 0
    street_name: int = 0
    outcode: int = 0
    price: int = 0

    @property
    def total(self) -> int:
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
        return sum([
            self.image_hash > 0,
            self.full_postcode > 0,
            self.coordinates > 0,
            self.street_name > 0,
            self.outcode > 0,
            self.price > 0,
        ])

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
        return (
            self.total >= MATCH_THRESHOLD
            and self.signal_count >= MINIMUM_SIGNALS
        )

    def to_dict(self) -> dict:
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

    # Coordinate proximity
    if coordinates_match(prop1, prop2):
        score.coordinates = SCORE_COORDINATES

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

    # Price match
    if prices_match(prop1.price_pcm, prop2.price_pcm):
        score.price = SCORE_PRICE

    return score
```

### Step 5: Update Deduplicator Class

**File:** `src/home_finder/filters/deduplication.py`

Add async method that uses weighted scoring:

```python
class Deduplicator:
    """Deduplicate and optionally merge properties across platforms."""

    def __init__(
        self,
        *,
        enable_cross_platform: bool = False,
        enable_image_hashing: bool = False,
    ) -> None:
        """Initialize the deduplicator.

        Args:
            enable_cross_platform: If True, attempt to dedupe same property
                listed on multiple platforms.
            enable_image_hashing: If True, fetch and compare image hashes
                for properties that might match.
        """
        self.enable_cross_platform = enable_cross_platform
        self.enable_image_hashing = enable_image_hashing

    async def deduplicate_and_merge_async(
        self,
        properties: list[Property],
    ) -> list[MergedProperty]:
        """Deduplicate and merge properties using weighted scoring.

        This async version supports image hash fetching for improved
        cross-platform matching (especially for Rightmove which lacks
        full postcodes).

        Args:
            properties: List of properties to deduplicate and merge.

        Returns:
            List of merged properties.
        """
        if not properties:
            return []

        # Stage 1: Dedupe by unique_id (same source + same ID)
        by_unique_id: dict[str, Property] = {}
        for prop in properties:
            if (
                prop.unique_id not in by_unique_id
                or prop.first_seen < by_unique_id[prop.unique_id].first_seen
            ):
                by_unique_id[prop.unique_id] = prop

        unique_props = list(by_unique_id.values())

        logger.debug(
            "stage1_dedup_complete",
            original_count=len(properties),
            after_unique_id=len(unique_props),
        )

        if not self.enable_cross_platform:
            merged = [self._single_to_merged(p) for p in unique_props]
            logger.info(
                "deduplication_merge_complete",
                original_count=len(properties),
                merged_count=len(merged),
                cross_platform=False,
            )
            return merged

        # Stage 2: Group by outcode + bedrooms (blocking for efficiency)
        # This reduces O(n²) comparisons to smaller groups
        candidates_by_block: dict[str, list[Property]] = defaultdict(list)
        no_outcode: list[Property] = []

        for prop in unique_props:
            outcode = extract_outcode(prop.postcode)
            if outcode:
                block_key = f"{outcode}:{prop.bedrooms}"
                candidates_by_block[block_key].append(prop)
            else:
                no_outcode.append(prop)

        # Stage 3: Fetch image hashes for blocks with multiple candidates
        image_hashes: dict[str, str] = {}
        if self.enable_image_hashing:
            # Only fetch for properties in blocks that need comparison
            props_needing_hashes = [
                p
                for candidates in candidates_by_block.values()
                if len(candidates) > 1
                for p in candidates
                if p.image_url
            ]
            if props_needing_hashes:
                image_hashes = await fetch_image_hashes_batch(props_needing_hashes)

        # Stage 4: Score and merge within each block
        merged_results: list[MergedProperty] = []

        for block_key, candidates in candidates_by_block.items():
            groups = self._group_by_weighted_score(candidates, image_hashes)
            for group in groups:
                if len(group) == 1:
                    merged_results.append(self._single_to_merged(group[0]))
                else:
                    merged_results.append(self._merge_properties(group))
                    logger.info(
                        "properties_merged",
                        block=block_key,
                        source_count=len(group),
                        sources=[p.source.value for p in group],
                        unique_ids=[p.unique_id for p in group],
                    )

        # Add properties without outcode (can't cross-platform match)
        for prop in no_outcode:
            merged_results.append(self._single_to_merged(prop))

        logger.info(
            "deduplication_merge_complete",
            original_count=len(properties),
            after_unique_id=len(unique_props),
            merged_count=len(merged_results),
            cross_platform=True,
            image_hashing=self.enable_image_hashing,
            image_hashes_fetched=len(image_hashes),
        )

        return merged_results

    def _group_by_weighted_score(
        self,
        candidates: list[Property],
        image_hashes: dict[str, str],
    ) -> list[list[Property]]:
        """Group properties by weighted match score.

        Uses union-find to transitively group matching properties.

        Args:
            candidates: Properties in same outcode+bedrooms block.
            image_hashes: Dict mapping unique_id to image hash.

        Returns:
            List of groups where each group contains matching properties.
        """
        if len(candidates) <= 1:
            return [candidates] if candidates else []

        # Union-find for transitive matching
        parent: dict[int, int] = {i: i for i in range(len(candidates))}

        def find(x: int) -> int:
            if parent[x] != x:
                parent[x] = find(parent[x])
            return parent[x]

        def union(x: int, y: int) -> None:
            px, py = find(x), find(y)
            if px != py:
                parent[px] = py

        # Compare all pairs and log scores
        for i in range(len(candidates)):
            for j in range(i + 1, len(candidates)):
                prop_i, prop_j = candidates[i], candidates[j]

                score = calculate_match_score(prop_i, prop_j, image_hashes)

                # Log all non-trivial scores for tuning
                if score.total >= 40:
                    logger.debug(
                        "match_score_calculated",
                        prop1=prop_i.unique_id,
                        prop2=prop_j.unique_id,
                        score=score.to_dict(),
                        is_match=score.is_match,
                    )

                if score.is_match:
                    union(i, j)

        # Build groups from union-find
        groups_dict: dict[int, list[Property]] = defaultdict(list)
        for i, prop in enumerate(candidates):
            groups_dict[find(i)].append(prop)

        return list(groups_dict.values())
```

### Step 6: Update Pipeline

**File:** `src/home_finder/main.py`

Change to use async deduplication:

```python
# Create deduplicator with image hashing enabled
deduplicator = Deduplicator(
    enable_cross_platform=True,
    enable_image_hashing=config.ENABLE_IMAGE_HASH_MATCHING,  # Feature flag
)

# Step 3: Deduplicate and merge (async for image fetching)
merged_properties = await deduplicator.deduplicate_and_merge_async(filtered)
```

**File:** `src/home_finder/config.py`

Add feature flag:

```python
class Settings(BaseSettings):
    # ... existing fields

    # Feature flags
    ENABLE_IMAGE_HASH_MATCHING: bool = Field(
        default=False,
        description="Enable image hash comparison for cross-platform deduplication",
    )
```

### Step 7: Add Tests

**File:** `tests/test_filters/test_deduplication_weighted.py`

```python
"""Tests for weighted scoring deduplication."""

import pytest

from home_finder.filters.deduplication import (
    MATCH_THRESHOLD,
    MINIMUM_SIGNALS,
    MatchConfidence,
    MatchScore,
    calculate_match_score,
)
from home_finder.models import Property, PropertySource
from home_finder.utils.address import extract_outcode, normalize_street_name


class TestMatchScore:
    """Tests for MatchScore dataclass."""

    def test_total_calculation(self):
        score = MatchScore(image_hash=40, outcode=10, price=15)
        assert score.total == 65

    def test_signal_count(self):
        score = MatchScore(image_hash=40, outcode=10, price=15)
        assert score.signal_count == 3

    def test_confidence_high(self):
        score = MatchScore(image_hash=40, full_postcode=40, price=15)
        assert score.confidence == MatchConfidence.HIGH
        assert score.is_match is True

    def test_confidence_medium(self):
        score = MatchScore(full_postcode=40, price=15)
        assert score.confidence == MatchConfidence.MEDIUM
        assert score.is_match is True

    def test_confidence_low_below_threshold(self):
        score = MatchScore(image_hash=40, outcode=10)  # 50 points, 2 signals
        assert score.total == 50
        assert score.signal_count == 2
        assert score.confidence == MatchConfidence.LOW
        assert score.is_match is False  # Below 55 threshold

    def test_confidence_low_single_signal(self):
        score = MatchScore(image_hash=40, price=15)  # 55 points but only 2 signals
        assert score.total == 55
        # Actually this is 2 signals, so it should be MEDIUM
        assert score.is_match is True

    def test_single_signal_not_enough(self):
        """Single signal alone should not be a match."""
        score = MatchScore(full_postcode=40)
        assert score.signal_count == 1
        assert score.is_match is False

    def test_image_hash_alone_not_enough(self):
        """Image hash alone (40 pts, 1 signal) should not match."""
        score = MatchScore(image_hash=40)
        assert score.total == 40
        assert score.signal_count == 1
        assert score.is_match is False


class TestStreetNormalization:
    """Tests for street name normalization."""

    def test_basic_abbreviation(self):
        assert normalize_street_name("Mare St") == "mare street"

    def test_with_flat_number(self):
        assert normalize_street_name("Flat 2, Mare Street") == "mare street"

    def test_with_building_name(self):
        result = normalize_street_name("The Towers, 123 Mare Street, London")
        assert result == "mare street"

    def test_road_abbreviation(self):
        assert normalize_street_name("Victoria Rd") == "victoria road"

    def test_with_postcode(self):
        result = normalize_street_name("Mare Street, E8 3RH")
        assert "e8" not in result
        assert result == "mare street"

    def test_removes_london(self):
        result = normalize_street_name("Mare Street, Hackney, London")
        assert "london" not in result
        assert "hackney" not in result


class TestExtractOutcode:
    """Tests for outcode extraction."""

    def test_full_postcode(self):
        assert extract_outcode("E8 3RH") == "E8"

    def test_partial_postcode(self):
        assert extract_outcode("E8") == "E8"

    def test_longer_outcode(self):
        assert extract_outcode("SW1A 1AA") == "SW1A"

    def test_none_input(self):
        assert extract_outcode(None) is None

    def test_invalid_input(self):
        assert extract_outcode("invalid") is None


class TestCalculateMatchScore:
    """Tests for match score calculation."""

    @pytest.fixture
    def base_property(self):
        return Property(
            source=PropertySource.OPENRENT,
            source_id="123",
            url="https://openrent.com/123",
            title="2 bed flat",
            price_pcm=1500,
            bedrooms=2,
            address="Flat 1, 123 Mare Street, London",
            postcode="E8 3RH",
            latitude=51.5,
            longitude=-0.05,
        )

    def test_different_bedrooms_no_match(self, base_property):
        prop2 = base_property.model_copy(
            update={"source_id": "456", "bedrooms": 3}
        )
        score = calculate_match_score(base_property, prop2)
        assert score.total == 0

    def test_full_postcode_and_price_match(self, base_property):
        prop2 = base_property.model_copy(
            update={
                "source": PropertySource.ZOOPLA,
                "source_id": "456",
                "price_pcm": 1530,  # Within 3%
            }
        )
        score = calculate_match_score(base_property, prop2)
        assert score.full_postcode == 40
        assert score.price == 15
        assert score.is_match is True

    def test_coordinates_match(self, base_property):
        prop2 = base_property.model_copy(
            update={
                "source": PropertySource.ZOOPLA,
                "source_id": "456",
                "latitude": 51.5001,  # ~11m away
                "longitude": -0.0501,
            }
        )
        score = calculate_match_score(base_property, prop2)
        assert score.coordinates == 40

    def test_rightmove_scenario(self, base_property):
        """Rightmove with outcode only can match via image + street + price."""
        rightmove = Property(
            source=PropertySource.RIGHTMOVE,
            source_id="RM123",
            url="https://rightmove.co.uk/RM123",
            title="2 bed flat",
            price_pcm=1500,
            bedrooms=2,
            address="123 Mare Street",
            postcode="E8",  # Only outcode
            latitude=None,
            longitude=None,
        )

        # Without image hash, only street (20) + outcode (10) + price (15) = 45
        score = calculate_match_score(base_property, rightmove)
        assert score.street_name == 20
        assert score.outcode == 10
        assert score.price == 15
        assert score.total == 45
        assert score.is_match is False  # Below 55

        # With image hash: 40 + 20 + 10 + 15 = 85
        image_hashes = {
            base_property.unique_id: "a" * 16,
            rightmove.unique_id: "a" * 16,  # Same hash
        }
        score_with_image = calculate_match_score(
            base_property, rightmove, image_hashes
        )
        assert score_with_image.image_hash == 40
        assert score_with_image.total == 85
        assert score_with_image.is_match is True
        assert score_with_image.confidence == MatchConfidence.HIGH
```

---

## Migration Notes

1. **Backwards compatible**: Existing `deduplicate_and_merge()` method preserved
2. **Feature flagged**: Image hashing disabled by default via `ENABLE_IMAGE_HASH_MATCHING`
3. **Database unchanged**: Image hashes are transient (fetched during dedup)

## Testing Strategy

1. **Unit tests**: Each scoring component, street normalization, outcode extraction
2. **Integration test**: Fixture properties from all 4 scrapers
3. **Manual validation**: `--dry-run` with logging to verify merge quality
4. **Threshold tuning**: Review `match_score_calculated` logs for false positives/negatives

## Rollout Plan

1. **Phase 1**: Deploy with `ENABLE_IMAGE_HASH_MATCHING=false` (current behavior)
2. **Phase 2**: Enable in dev with verbose logging, review merge decisions
3. **Phase 3**: Tune `HASH_DISTANCE_THRESHOLD` and score weights based on data
4. **Phase 4**: Enable in production

## Tuning Guide

### If seeing false positives (wrong properties merged):
- Decrease `HASH_DISTANCE_THRESHOLD` (try 6)
- Increase `MATCH_THRESHOLD` (try 60)
- Increase `MINIMUM_SIGNALS` (try 3)

### If seeing false negatives (same property not merged):
- Increase `HASH_DISTANCE_THRESHOLD` (try 10)
- Decrease `MATCH_THRESHOLD` (try 50, but keep signal requirement)
- Check street normalization for edge cases

## References

- [Ben Hoyt: Duplicate image detection with perceptual hashing](https://benhoyt.com/writings/duplicate-image-detection/)
- [imagededup library](https://github.com/idealo/imagededup)
- [Fellegi-Sunter probabilistic record linkage](https://en.wikipedia.org/wiki/Record_linkage#Probabilistic_record_linkage)
- [Entity Resolution survey](https://arxiv.org/pdf/1905.06167)

"""Property deduplication and merging logic."""

import math
import re
from collections import defaultdict
from dataclasses import dataclass
from enum import Enum
from typing import Final

from home_finder.logging import get_logger
from home_finder.models import MergedProperty, Property, PropertyImage, PropertySource
from home_finder.utils.address import extract_outcode, normalize_street_name
from home_finder.utils.image_hash import fetch_image_hashes_batch, hashes_match
from home_finder.utils.union_find import UnionFind

logger = get_logger(__name__)

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
    if not (prop1.latitude and prop1.longitude and prop2.latitude and prop2.longitude):
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
    if price1 == 0 or price2 == 0:
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
    if not (prop1.latitude and prop1.longitude and prop2.latitude and prop2.longitude):
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
    if price1 == 0 or price2 == 0:
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


def _dedupe_by_unique_id(properties: list[Property]) -> list[Property]:
    """Deduplicate properties by unique_id, keeping earliest first_seen.

    Args:
        properties: List of properties, possibly with duplicate unique_ids.

    Returns:
        Deduplicated list keeping the earliest first_seen for each unique_id.
    """
    by_unique_id: dict[str, Property] = {}
    for prop in properties:
        if (
            prop.unique_id not in by_unique_id
            or prop.first_seen < by_unique_id[prop.unique_id].first_seen
        ):
            by_unique_id[prop.unique_id] = prop
    return list(by_unique_id.values())


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
                listed on multiple platforms (based on postcode + price + beds).
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
        unique_props = _dedupe_by_unique_id(properties)

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

    def properties_to_merged(self, properties: list[Property]) -> list[MergedProperty]:
        """Wrap each Property as a single-source MergedProperty.

        Used to convert raw Properties for the enrichment pipeline before
        cross-platform deduplication.

        Args:
            properties: List of properties to wrap.

        Returns:
            List of single-source MergedProperty objects.
        """
        return [self._single_to_merged(p) for p in _dedupe_by_unique_id(properties)]

    async def deduplicate_merged_async(
        self,
        merged_properties: list[MergedProperty],
    ) -> list[MergedProperty]:
        """Deduplicate enriched MergedProperty objects using weighted scoring.

        This operates on already-enriched single-source MergedProperties
        (after detail fetching), comparing their canonical properties and
        combining enrichment data (images, descriptions, floorplans) when
        merging duplicates.

        Args:
            merged_properties: Enriched single-source MergedProperty objects.

        Returns:
            List of merged properties with duplicates combined.
        """
        if not merged_properties:
            return []

        if not self.enable_cross_platform:
            logger.info(
                "deduplication_merge_complete",
                original_count=len(merged_properties),
                merged_count=len(merged_properties),
                cross_platform=False,
            )
            return merged_properties

        # Stage 1: Group by outcode + bedrooms (blocking for efficiency)
        candidates_by_block: dict[str, list[MergedProperty]] = defaultdict(list)
        no_outcode: list[MergedProperty] = []

        for mp in merged_properties:
            outcode = extract_outcode(mp.canonical.postcode)
            if outcode:
                block_key = f"{outcode}:{mp.canonical.bedrooms}"
                candidates_by_block[block_key].append(mp)
            else:
                no_outcode.append(mp)

        # Stage 2: Fetch image hashes for hero images
        image_hashes: dict[str, str] = {}
        if self.enable_image_hashing:
            props_needing_hashes = [
                mp.canonical
                for candidates in candidates_by_block.values()
                if len(candidates) > 1
                for mp in candidates
                if mp.canonical.image_url
            ]
            if props_needing_hashes:
                image_hashes = await fetch_image_hashes_batch(props_needing_hashes)

        # Stage 3: Score and merge within each block
        merged_results: list[MergedProperty] = []

        for block_key, candidates in candidates_by_block.items():
            groups = self._group_merged_by_weighted_score(candidates, image_hashes)
            for group in groups:
                if len(group) == 1:
                    merged_results.append(group[0])
                else:
                    merged_results.append(self._merge_merged_properties(group))
                    logger.info(
                        "enriched_properties_merged",
                        block=block_key,
                        source_count=len(group),
                        sources=[s.value for mp in group for s in mp.sources],
                    )

        # Add properties without outcode (can't cross-platform match)
        merged_results.extend(no_outcode)

        multi_source = sum(1 for m in merged_results if len(m.sources) > 1)
        logger.info(
            "deduplication_merge_complete",
            original_count=len(merged_properties),
            merged_count=len(merged_results),
            multi_source_count=multi_source,
            cross_platform=True,
            image_hashing=self.enable_image_hashing,
        )

        return merged_results

    def _group_merged_by_weighted_score(
        self,
        candidates: list[MergedProperty],
        image_hashes: dict[str, str],
    ) -> list[list[MergedProperty]]:
        """Group MergedProperties by weighted match score on their canonicals.

        Args:
            candidates: MergedProperties in same outcode+bedrooms block.
            image_hashes: Dict mapping unique_id to image hash.

        Returns:
            List of groups where each group contains matching properties.
        """
        if len(candidates) <= 1:
            return [candidates] if candidates else []

        uf = UnionFind(len(candidates))

        for i in range(len(candidates)):
            for j in range(i + 1, len(candidates)):
                mp_i, mp_j = candidates[i], candidates[j]
                prop_i, prop_j = mp_i.canonical, mp_j.canonical

                score = calculate_match_score(prop_i, prop_j, image_hashes)

                if score.total >= 40:
                    logger.debug(
                        "merged_match_score_calculated",
                        prop1=prop_i.unique_id,
                        prop2=prop_j.unique_id,
                        score=score.to_dict(),
                        is_match=score.is_match,
                    )

                if score.is_match and prop_i.source != prop_j.source:
                    uf.union(i, j)

        return [[candidates[i] for i in members] for members in uf.groups().values()]

    def _merge_merged_properties(self, merged_list: list[MergedProperty]) -> MergedProperty:
        """Combine multiple enriched MergedProperty objects into one.

        Merges sources, URLs, images, floorplans, and descriptions from
        all input MergedProperties.

        Args:
            merged_list: MergedProperties to combine.

        Returns:
            Single MergedProperty with combined data from all inputs.
        """
        # Sort by canonical first_seen — earliest is the new canonical
        sorted_mps = sorted(merged_list, key=lambda m: m.canonical.first_seen)
        canonical = sorted_mps[0].canonical

        # Combine sources and URLs (dedup by source)
        all_sources: list[PropertySource] = []
        all_source_urls = dict(sorted_mps[0].source_urls)
        for src in sorted_mps[0].sources:
            all_sources.append(src)
        for mp in sorted_mps[1:]:
            for src in mp.sources:
                if src not in all_source_urls:
                    all_sources.append(src)
                    all_source_urls[src] = mp.source_urls[src]

        # Combine descriptions
        all_descriptions: dict[PropertySource, str] = {}
        for mp in sorted_mps:
            all_descriptions.update(mp.descriptions)

        # Combine images (dedup by URL)
        seen_image_urls: set[str] = set()
        all_images: list[PropertyImage] = []
        for mp in sorted_mps:
            for img in mp.images:
                url_str = str(img.url)
                if url_str not in seen_image_urls:
                    seen_image_urls.add(url_str)
                    all_images.append(img)

        # Pick first available floorplan
        floorplan = None
        for mp in sorted_mps:
            if mp.floorplan:
                floorplan = mp.floorplan
                break

        # Price range across all sources
        prices = [mp.min_price for mp in sorted_mps] + [mp.max_price for mp in sorted_mps]

        return MergedProperty(
            canonical=canonical,
            sources=tuple(all_sources),
            source_urls=all_source_urls,
            images=tuple(all_images),
            floorplan=floorplan,
            min_price=min(prices),
            max_price=max(prices),
            descriptions=all_descriptions,
        )

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

        uf = UnionFind(len(candidates))

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

                if score.is_match and prop_i.source != prop_j.source:
                    uf.union(i, j)

        # Build groups from union-find
        return [[candidates[i] for i in members] for members in uf.groups().values()]

    def _single_to_merged(self, prop: Property) -> MergedProperty:
        """Wrap a single property as a MergedProperty.

        Args:
            prop: Property to wrap.

        Returns:
            MergedProperty with single source.
        """
        descriptions: dict[PropertySource, str] = {}
        if prop.description:
            descriptions[prop.source] = prop.description

        return MergedProperty(
            canonical=prop,
            sources=(prop.source,),
            source_urls={prop.source: prop.url},
            images=(),
            floorplan=None,
            min_price=prop.price_pcm,
            max_price=prop.price_pcm,
            descriptions=descriptions,
        )

    def _merge_properties(self, props: list[Property]) -> MergedProperty:
        """Merge multiple properties into one.

        Args:
            props: Properties to merge (should be same listing on different platforms).

        Returns:
            Combined MergedProperty.
        """
        # Sort by first_seen - earliest is canonical
        sorted_props = sorted(props, key=lambda p: p.first_seen)
        canonical = sorted_props[0]

        # Collect all sources and URLs
        sources = tuple(p.source for p in sorted_props)
        source_urls = {p.source: p.url for p in sorted_props}

        # Collect all descriptions
        descriptions: dict[PropertySource, str] = {}
        for p in sorted_props:
            if p.description:
                descriptions[p.source] = p.description

        # Calculate price range
        prices = [p.price_pcm for p in sorted_props]
        min_price = min(prices)
        max_price = max(prices)

        return MergedProperty(
            canonical=canonical,
            sources=sources,
            source_urls=source_urls,
            images=(),  # Populated later by quality filter
            floorplan=None,  # Populated later by quality filter
            min_price=min_price,
            max_price=max_price,
            descriptions=descriptions,
        )

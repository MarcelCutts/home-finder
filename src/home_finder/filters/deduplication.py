"""Property deduplication and merging logic."""

import math
import re
from collections import defaultdict

from home_finder.logging import get_logger
from home_finder.models import MergedProperty, Property, PropertySource

logger = get_logger(__name__)

# Price tolerance for fuzzy matching (3% - tighter than before)
PRICE_TOLERANCE = 0.03

# Maximum distance in meters for coordinate-based matching
COORDINATE_DISTANCE_METERS = 50

# Regex to detect full UK postcodes (outcode + incode)
FULL_POSTCODE_PATTERN = re.compile(
    r"^[A-Z]{1,2}[0-9][0-9A-Z]?\s+[0-9][A-Z]{2}$", re.IGNORECASE
)


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

    distance = haversine_distance(
        prop1.latitude, prop1.longitude, prop2.latitude, prop2.longitude
    )
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


class Deduplicator:
    """Deduplicate and optionally merge properties across platforms."""

    def __init__(self, *, enable_cross_platform: bool = False) -> None:
        """Initialize the deduplicator.

        Args:
            enable_cross_platform: If True, attempt to dedupe same property
                listed on multiple platforms (based on postcode + price + beds).
        """
        self.enable_cross_platform = enable_cross_platform

    def deduplicate(self, properties: list[Property]) -> list[Property]:
        """Remove duplicate properties (original behavior, discards duplicates).

        Args:
            properties: List of properties to deduplicate.

        Returns:
            List of unique properties.
        """
        if not properties:
            return []

        # First pass: dedupe by unique_id (same source + same ID)
        seen_unique_ids: dict[str, Property] = {}
        for prop in properties:
            if prop.unique_id in seen_unique_ids:
                existing = seen_unique_ids[prop.unique_id]
                # Keep the one seen first
                if prop.first_seen < existing.first_seen:
                    seen_unique_ids[prop.unique_id] = prop
            else:
                seen_unique_ids[prop.unique_id] = prop

        unique_by_id = list(seen_unique_ids.values())

        if not self.enable_cross_platform:
            logger.info(
                "deduplication_complete",
                original_count=len(properties),
                deduplicated_count=len(unique_by_id),
                cross_platform=False,
            )
            return unique_by_id

        # Second pass: cross-platform dedup based on postcode + price + bedrooms
        seen_signatures: dict[str, Property] = {}
        result: list[Property] = []

        for prop in unique_by_id:
            signature = self._get_cross_platform_signature_legacy(prop)

            if signature is None:
                # Can't generate signature (missing postcode), keep as unique
                result.append(prop)
                continue

            if signature in seen_signatures:
                existing = seen_signatures[signature]
                # Keep the one seen first
                if prop.first_seen < existing.first_seen:
                    seen_signatures[signature] = prop
                    # Replace in result
                    result = [p for p in result if p.unique_id != existing.unique_id]
                    result.append(prop)
            else:
                seen_signatures[signature] = prop
                result.append(prop)

        logger.info(
            "deduplication_complete",
            original_count=len(properties),
            after_unique_id=len(unique_by_id),
            deduplicated_count=len(result),
            cross_platform=True,
        )

        return result

    def deduplicate_and_merge(
        self, properties: list[Property]
    ) -> list[MergedProperty]:
        """Deduplicate and merge properties from multiple sources.

        Unlike deduplicate(), this method preserves data from all sources
        by creating MergedProperty objects that combine information.

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

        logger.debug(
            "stage1_dedup_complete",
            original_count=len(properties),
            after_unique_id=len(by_unique_id),
        )

        if not self.enable_cross_platform:
            # No cross-platform merging, wrap each as single-source MergedProperty
            merged = [self._single_to_merged(p) for p in by_unique_id.values()]
            logger.info(
                "deduplication_merge_complete",
                original_count=len(properties),
                merged_count=len(merged),
                cross_platform=False,
            )
            return merged

        # Stage 2: Group potential cross-platform duplicates
        # Key: (postcode, bedrooms), Value: list of properties
        potential_matches: dict[str, list[Property]] = defaultdict(list)
        no_signature: list[Property] = []

        for prop in by_unique_id.values():
            sig = self._get_cross_platform_signature(prop)
            if sig:
                potential_matches[sig].append(prop)
            else:
                no_signature.append(prop)

        # Stage 3: Merge groups with matching prices and location confirmation
        merged_results: list[MergedProperty] = []

        for sig, candidates in potential_matches.items():
            groups = self._group_by_price_and_location(candidates)
            for group in groups:
                if len(group) == 1:
                    merged_results.append(self._single_to_merged(group[0]))
                else:
                    merged_results.append(self._merge_properties(group))
                    logger.info(
                        "properties_merged",
                        signature=sig,
                        source_count=len(group),
                        sources=[p.source.value for p in group],
                    )

        # Add properties without signatures (no postcode)
        for prop in no_signature:
            merged_results.append(self._single_to_merged(prop))

        logger.info(
            "deduplication_merge_complete",
            original_count=len(properties),
            after_unique_id=len(by_unique_id),
            merged_count=len(merged_results),
            cross_platform=True,
        )

        return merged_results

    def _get_cross_platform_signature_legacy(self, prop: Property) -> str | None:
        """Generate a signature for cross-platform deduplication (legacy exact match).

        Properties with the same postcode, price, and bedrooms are likely
        the same listing on different platforms.

        Args:
            prop: Property to generate signature for.

        Returns:
            Signature string, or None if signature can't be generated.
        """
        if not prop.postcode:
            return None

        # Normalize postcode (uppercase, single space)
        postcode = " ".join(prop.postcode.upper().split())

        return f"{postcode}:{prop.price_pcm}:{prop.bedrooms}"

    def _get_cross_platform_signature(self, prop: Property) -> str | None:
        """Generate a signature for grouping potential duplicates.

        CONSERVATIVE: Only generates signature if property has a FULL postcode
        (e.g., "E3 4AB" not just "E3"). This prevents false merges in areas
        where many similar properties exist.

        Args:
            prop: Property to generate signature for.

        Returns:
            Signature string, or None if signature can't be generated.
        """
        if not prop.postcode:
            return None

        # Normalize postcode (uppercase, single space)
        postcode = " ".join(prop.postcode.upper().split())

        # CONSERVATIVE: Only match on full postcodes to avoid false positives
        # Properties with only outcode (e.g., "E3") won't be cross-platform matched
        if not is_full_postcode(postcode):
            logger.debug(
                "skipping_cross_platform_match",
                property_id=prop.unique_id,
                postcode=postcode,
                reason="partial_postcode",
            )
            return None

        return f"{postcode}:{prop.bedrooms}"

    def _group_by_price_and_location(
        self, candidates: list[Property]
    ) -> list[list[Property]]:
        """Group properties by similar prices AND location confirmation.

        CONSERVATIVE: Requires price match AND either:
        - Coordinates within 50m (high confidence), OR
        - Same full postcode (moderate confidence, already filtered)

        Args:
            candidates: List of properties with same full postcode/bedrooms.

        Returns:
            List of groups where each group has matching prices and locations.
        """
        if len(candidates) <= 1:
            return [candidates] if candidates else []

        # Use union-find to group properties that match
        parent: dict[int, int] = {i: i for i in range(len(candidates))}

        def find(x: int) -> int:
            if parent[x] != x:
                parent[x] = find(parent[x])
            return parent[x]

        def union(x: int, y: int) -> None:
            px, py = find(x), find(y)
            if px != py:
                parent[px] = py

        # Compare all pairs
        for i in range(len(candidates)):
            for j in range(i + 1, len(candidates)):
                prop_i, prop_j = candidates[i], candidates[j]

                # Must have matching prices
                if not prices_match(prop_i.price_pcm, prop_j.price_pcm):
                    continue

                # If both have coordinates, require them to be close
                if (
                    prop_i.latitude
                    and prop_i.longitude
                    and prop_j.latitude
                    and prop_j.longitude
                ):
                    if coordinates_match(prop_i, prop_j):
                        union(i, j)
                    # else: coordinates don't match, don't merge even with same postcode
                else:
                    # No coordinates to verify - rely on full postcode match
                    # (already filtered to only full postcodes)
                    union(i, j)

        # Build groups from union-find
        groups_dict: dict[int, list[Property]] = defaultdict(list)
        for i, prop in enumerate(candidates):
            groups_dict[find(i)].append(prop)

        return list(groups_dict.values())

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

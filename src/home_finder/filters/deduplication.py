"""Property deduplication and merging logic."""

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from home_finder.filters.scoring import calculate_match_score, is_full_postcode
from home_finder.logging import get_logger
from home_finder.models import MergedProperty, Property, PropertyImage, PropertySource
from home_finder.utils.address import extract_outcode
from home_finder.utils.image_cache import find_cached_file
from home_finder.utils.image_hash import (
    fetch_image_hashes_batch,
    hash_cached_gallery,
    hash_from_disk,
    hashes_match,
)

logger = get_logger(__name__)

# Source priority for image quality (higher = better).
# Zoopla typically has the highest resolution CDN images.
SOURCE_IMAGE_PRIORITY: Final[dict[PropertySource, int]] = {
    PropertySource.ZOOPLA: 4,
    PropertySource.RIGHTMOVE: 3,
    PropertySource.ONTHEMARKET: 2,
    PropertySource.OPENRENT: 1,
}

# Maximum number of properties in a single merged group (one per platform).
_MAX_GROUP_SIZE: Final = 4


@dataclass(frozen=True, order=True)
class _ScoredPair:
    """A scored pair of candidate indices for greedy matching.

    Ordered by score descending (negated), then by (min_idx, max_idx) for
    deterministic tie-breaking.
    """

    neg_score: float  # negated so higher scores sort first
    min_idx: int
    max_idx: int


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
        data_dir: str = "",
    ) -> None:
        """Initialize the deduplicator.

        Args:
            enable_cross_platform: If True, attempt to dedupe same property
                listed on multiple platforms (based on postcode + price + beds).
            enable_image_hashing: If True, fetch and compare image hashes
                for properties that might match.
            data_dir: Base data directory for reading cached gallery images.
                When set, gallery images are hashed from disk instead of
                fetching hero thumbnails from the network.
        """
        self.enable_cross_platform = enable_cross_platform
        self.enable_image_hashing = enable_image_hashing
        self.data_dir = data_dir

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

        # Wrap as single-source MergedProperties, then use unified dedup
        merged = [self._single_to_merged(p) for p in unique_props]
        result = await self._deduplicate_merged_items(merged)

        logger.info(
            "deduplication_merge_complete",
            original_count=len(properties),
            after_unique_id=len(unique_props),
            merged_count=len(result),
            cross_platform=self.enable_cross_platform,
            image_hashing=self.enable_image_hashing,
        )

        return result

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

        result = await self._deduplicate_merged_items(merged_properties)

        multi_source = sum(1 for m in result if len(m.sources) > 1)
        logger.info(
            "deduplication_merge_complete",
            original_count=len(merged_properties),
            merged_count=len(result),
            multi_source_count=multi_source,
            cross_platform=self.enable_cross_platform,
            image_hashing=self.enable_image_hashing,
        )

        return result

    async def _deduplicate_merged_items(
        self,
        items: list[MergedProperty],
    ) -> list[MergedProperty]:
        """Core dedup: block by outcode+bedrooms, score canonical properties, merge groups.

        Args:
            items: MergedProperty objects to deduplicate.

        Returns:
            Deduplicated list with cross-platform matches merged.
        """
        if not self.enable_cross_platform:
            return items

        # Group by outcode + bedrooms (blocking for efficiency)
        candidates_by_block: dict[str, list[MergedProperty]] = defaultdict(list)
        no_outcode: list[MergedProperty] = []

        for mp in items:
            outcode = extract_outcode(mp.canonical.postcode)
            if outcode:
                block_key = f"{outcode}:{mp.canonical.bedrooms}"
                candidates_by_block[block_key].append(mp)
            else:
                no_outcode.append(mp)

        # Build gallery image hashes for dedup comparison
        image_hashes: dict[str, list[str]] = {}
        if self.enable_image_hashing:
            candidate_ids = [
                mp.canonical.unique_id
                for candidates in candidates_by_block.values()
                if len(candidates) > 1
                for mp in candidates
            ]

            # Primary: hash cached gallery images from disk (no network)
            if self.data_dir and candidate_ids:
                image_hashes = await hash_cached_gallery(candidate_ids, self.data_dir)

            # Fallback: fetch hero thumbnails for properties without cached galleries
            ids_without_gallery = {uid for uid in candidate_ids if uid not in image_hashes}
            if ids_without_gallery:
                props_needing_fetch = [
                    mp.canonical
                    for candidates in candidates_by_block.values()
                    if len(candidates) > 1
                    for mp in candidates
                    if mp.canonical.unique_id in ids_without_gallery
                    and mp.canonical.image_url
                ]
                if props_needing_fetch:
                    hero_hashes = await fetch_image_hashes_batch(props_needing_fetch)
                    # Wrap single hashes as lists for interface compatibility
                    for uid, h in hero_hashes.items():
                        image_hashes[uid] = [h]

        # Score and merge within each block
        results: list[MergedProperty] = []

        for block_key, candidates in candidates_by_block.items():
            groups = _group_items_greedy(candidates, image_hashes)
            for group in groups:
                if len(group) == 1:
                    results.append(group[0])
                else:
                    results.append(self._merge_merged_properties(group))
                    logger.info(
                        "properties_merged",
                        block=block_key,
                        source_count=len(group),
                        sources=[s.value for mp in group for s in mp.sources],
                    )

        # Add properties without outcode (can't cross-platform match)
        results.extend(no_outcode)

        return results

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
        canonical = _build_best_canonical(sorted_mps)

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

        # Perceptual dedup: remove visually identical photos from different CDNs.
        # Pass all unique_ids so images cached under any source's directory are found.
        if self.data_dir and len(all_images) > 1:
            all_unique_ids = [mp.canonical.unique_id for mp in sorted_mps]
            all_images = _perceptual_dedup_images(
                all_images, self.data_dir, all_unique_ids
            )

        # Pick best floorplan by source priority
        floorplan = _select_best_floorplan(sorted_mps)

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


def _build_best_canonical(sorted_mps: list[MergedProperty]) -> Property:
    """Build the best canonical Property by upgrading fields from all sources.

    Starts from the earliest first_seen property and backfills:
    - postcode: prefer full postcode over outcode
    - coordinates: prefer non-null over null
    - available_from: pick earliest non-null date

    Identity fields (source, source_id, url, title, first_seen) are never changed.

    Args:
        sorted_mps: MergedProperties sorted by canonical.first_seen (earliest first).

    Returns:
        Best canonical Property with upgraded fields.
    """
    base = sorted_mps[0].canonical
    updates: dict[str, object] = {}

    # Postcode: prefer full postcode from any source
    if not is_full_postcode(base.postcode):
        for mp in sorted_mps[1:]:
            if is_full_postcode(mp.canonical.postcode):
                updates["postcode"] = mp.canonical.postcode
                break

    # Coordinates: prefer non-null from any source
    if base.latitude is None:
        for mp in sorted_mps[1:]:
            if mp.canonical.latitude is not None:
                updates["latitude"] = mp.canonical.latitude
                updates["longitude"] = mp.canonical.longitude
                break

    # Available from: pick earliest non-null date across all sources
    dates = [
        mp.canonical.available_from
        for mp in sorted_mps
        if mp.canonical.available_from is not None
    ]
    if dates:
        earliest = min(dates)
        if base.available_from is None or earliest < base.available_from:
            updates["available_from"] = earliest

    if not updates:
        return base
    return base.model_copy(update=updates)


def _find_cached_across_ids(
    data_dir: str, unique_ids: list[str], url: str, image_type: str
) -> Path | None:
    """Find a cached image file by trying each unique_id's cache directory.

    During merge, images from different sources are still cached under their
    original unique_ids (e.g. ``image_cache/openrent_100/`` vs
    ``image_cache/zoopla_456/``).  This helper tries each directory so
    perceptual dedup can find cross-source images before cache consolidation.
    """
    for uid in unique_ids:
        cached = find_cached_file(data_dir, uid, url, image_type)
        if cached is not None:
            return cached
    return None


def _perceptual_dedup_images(
    images: list[PropertyImage],
    data_dir: str,
    all_unique_ids: list[str],
) -> list[PropertyImage]:
    """Remove visually identical photos served from different CDN URLs.

    Hashes each image from disk cache and removes duplicates, keeping the
    version from the higher-priority source.

    Args:
        images: Combined gallery images from all sources.
        data_dir: Base data directory for image cache.
        all_unique_ids: All unique_ids in the merge group, so images cached
            under any source's directory can be found.

    Returns:
        Deduplicated image list.
    """
    if len(images) <= 1:
        return images

    # Hash all images that have cached files
    hashed: list[tuple[PropertyImage, str | None]] = []
    for img in images:
        cached = _find_cached_across_ids(
            data_dir, all_unique_ids, str(img.url), "gallery"
        )
        if cached is not None:
            h = hash_from_disk(cached)
            hashed.append((img, h))
        else:
            hashed.append((img, None))

    # Find duplicates by comparing hashes
    keep: list[bool] = [True] * len(hashed)
    for i in range(len(hashed)):
        if not keep[i]:
            continue
        img_i, hash_i = hashed[i]
        if hash_i is None:
            continue
        for j in range(i + 1, len(hashed)):
            if not keep[j]:
                continue
            img_j, hash_j = hashed[j]
            if hash_j is None:
                continue
            if hashes_match(hash_i, hash_j):
                # Keep the one from the higher-priority source
                pri_i = SOURCE_IMAGE_PRIORITY.get(img_i.source, 0)
                pri_j = SOURCE_IMAGE_PRIORITY.get(img_j.source, 0)
                if pri_j > pri_i:
                    keep[i] = False
                    break  # i is removed, stop comparing it
                else:
                    keep[j] = False

    return [img for img, kept in zip(images, keep, strict=True) if kept]


def _select_best_floorplan(sorted_mps: list[MergedProperty]) -> PropertyImage | None:
    """Select the best floorplan by source priority.

    Args:
        sorted_mps: MergedProperties sorted by canonical.first_seen.

    Returns:
        Best floorplan PropertyImage, or None if none available.
    """
    candidates = [mp.floorplan for mp in sorted_mps if mp.floorplan is not None]
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    # Sort by source priority descending, pick best
    return max(candidates, key=lambda fp: SOURCE_IMAGE_PRIORITY.get(fp.source, 0))


def _group_items_greedy(
    items: list[MergedProperty],
    image_hashes: dict[str, list[str]],
) -> list[list[MergedProperty]]:
    """Group items by greedy pairwise matching with same-source collision guard.

    Algorithm:
    1. Score all cross-source pairs (skip same-source pairs entirely)
    2. Collect qualifying pairs (score.is_match)
    3. Sort by score descending (ties broken by index pair for determinism)
    4. Greedily build groups with two invariants:
       - No same-source collision (a group never has 2 properties from the same source)
       - Max group size = 4 (one per platform maximum)

    Args:
        items: MergedProperty candidates within a single blocking group.
        image_hashes: Dict mapping unique_id to list of gallery hash strings.

    Returns:
        List of groups where each group contains matching items.
    """
    if len(items) <= 1:
        return [items] if items else []

    # Score all cross-source pairs and collect qualifying ones
    scored_pairs: list[_ScoredPair] = []
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            prop_i = items[i].canonical
            prop_j = items[j].canonical

            # Skip same-source pairs entirely
            if prop_i.source == prop_j.source:
                continue

            score = calculate_match_score(prop_i, prop_j, image_hashes)

            if score.total >= 40:
                logger.debug(
                    "match_score_calculated",
                    prop1=prop_i.unique_id,
                    prop2=prop_j.unique_id,
                    score=score.to_dict(),
                    is_match=score.is_match,
                )

            if score.is_match:
                scored_pairs.append(_ScoredPair(-score.total, min(i, j), max(i, j)))

    # Sort: best scores first, then by index pair for determinism
    scored_pairs.sort()

    # Greedy grouping: track which group each item belongs to
    # group_id[i] = index into `groups` list, or -1 if ungrouped
    group_id: list[int] = [-1] * len(items)
    groups: list[list[int]] = []  # each is a list of item indices

    for pair in scored_pairs:
        i, j = pair.min_idx, pair.max_idx
        gi, gj = group_id[i], group_id[j]

        if gi == -1 and gj == -1:
            # Neither is in a group — create a new group
            new_gid = len(groups)
            groups.append([i, j])
            group_id[i] = new_gid
            group_id[j] = new_gid

        elif gi >= 0 and gj == -1:
            # i is in a group, try to add j
            group = groups[gi]
            if len(group) >= _MAX_GROUP_SIZE:
                continue
            # Check same-source collision
            j_sources = set(items[j].sources)
            if any(j_sources & set(items[k].sources) for k in group):
                continue
            group.append(j)
            group_id[j] = gi

        elif gi == -1 and gj >= 0:
            # j is in a group, try to add i
            group = groups[gj]
            if len(group) >= _MAX_GROUP_SIZE:
                continue
            i_sources = set(items[i].sources)
            if any(i_sources & set(items[k].sources) for k in group):
                continue
            group.append(i)
            group_id[i] = gj

        else:
            # Both in groups — skip (don't merge groups to avoid transitive chains)
            continue

    # Build result: grouped items + singletons
    result: list[list[MergedProperty]] = []
    grouped_indices: set[int] = set()
    for group in groups:
        result.append([items[idx] for idx in group])
        grouped_indices.update(group)

    # Add singletons
    for i, item in enumerate(items):
        if i not in grouped_indices:
            result.append([item])

    return result

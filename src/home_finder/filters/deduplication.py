"""Property deduplication and merging logic."""

from collections import defaultdict
from collections.abc import Callable
from typing import TypeVar

from home_finder.filters.scoring import calculate_match_score
from home_finder.logging import get_logger
from home_finder.models import MergedProperty, Property, PropertyImage, PropertySource
from home_finder.utils.address import extract_outcode
from home_finder.utils.image_hash import fetch_image_hashes_batch
from home_finder.utils.union_find import UnionFind

logger = get_logger(__name__)

_T = TypeVar("_T")


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

        # Fetch image hashes for hero images
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

        # Score and merge within each block
        results: list[MergedProperty] = []

        for block_key, candidates in candidates_by_block.items():
            groups = _group_items_by_weighted_score(
                candidates, lambda mp: mp.canonical, image_hashes
            )
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


def _group_items_by_weighted_score(
    items: list[_T],
    get_prop: Callable[[_T], Property],
    image_hashes: dict[str, str],
) -> list[list[_T]]:
    """Group items by weighted match score using union-find.

    Args:
        items: Items to group (Property or MergedProperty).
        get_prop: Accessor to get the Property from each item.
        image_hashes: Dict mapping unique_id to image hash.

    Returns:
        List of groups where each group contains matching items.
    """
    if len(items) <= 1:
        return [items] if items else []

    uf = UnionFind(len(items))

    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            prop_i = get_prop(items[i])
            prop_j = get_prop(items[j])

            score = calculate_match_score(prop_i, prop_j, image_hashes)

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

    return [[items[i] for i in members] for members in uf.groups().values()]

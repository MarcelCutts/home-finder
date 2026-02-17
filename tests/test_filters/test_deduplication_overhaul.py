"""Tests for the deduplication overhaul: greedy matching, best-of canonical, perceptual dedup."""

from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st
from PIL import Image
from pydantic import HttpUrl

from home_finder.filters.deduplication import (
    Deduplicator,
    _build_best_canonical,
    _group_items_greedy,
    _perceptual_dedup_images,
    _select_best_floorplan,
)
from home_finder.models import MergedProperty, Property, PropertyImage, PropertySource
from home_finder.utils.image_cache import get_cache_dir, save_image_bytes, url_to_filename

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_prop(
    source: PropertySource,
    source_id: str,
    *,
    price_pcm: int = 1800,
    bedrooms: int = 2,
    postcode: str = "E8 3RH",
    latitude: float | None = 51.5465,
    longitude: float | None = -0.0553,
    address: str = "123 Mare Street, Hackney, London",
    first_seen: datetime | None = None,
    available_from: datetime | None = None,
) -> Property:
    return Property(
        source=source,
        source_id=source_id,
        url=HttpUrl(f"https://example.com/{source.value}/{source_id}"),
        title=f"Test Property {source_id}",
        price_pcm=price_pcm,
        bedrooms=bedrooms,
        address=address,
        postcode=postcode,
        latitude=latitude,
        longitude=longitude,
        first_seen=first_seen or datetime(2025, 1, 15, 10, 0, tzinfo=UTC),
        available_from=available_from,
    )


def _wrap_merged(prop: Property, **overrides: Any) -> MergedProperty:
    defaults = {
        "canonical": prop,
        "sources": (prop.source,),
        "source_urls": {prop.source: prop.url},
        "images": (),
        "floorplan": None,
        "min_price": prop.price_pcm,
        "max_price": prop.price_pcm,
        "descriptions": {},
    }
    defaults.update(overrides)
    return MergedProperty(**defaults)


def _make_image_bytes(color: tuple[int, int, int] = (135, 206, 235)) -> bytes:
    """Create a simple JPEG image with the given solid color."""
    img = Image.new("RGB", (100, 100), color)
    buf = BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _make_distinct_image_bytes() -> bytes:
    """Create a visually distinct image (checkerboard pattern).

    pHash is designed to tolerate small variations, so solid-color images
    of different colors can still match. A checkerboard pattern is structurally
    very different from a solid color.
    """
    from PIL import ImageDraw

    img = Image.new("RGB", (100, 100), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    # Draw checkerboard pattern
    for x in range(0, 100, 10):
        for y in range(0, 100, 10):
            if (x + y) % 20 == 0:
                draw.rectangle([x, y, x + 10, y + 10], fill=(0, 0, 0))
    buf = BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# C1. TestFalsePositiveCascadePrevention
# ---------------------------------------------------------------------------


class TestFalsePositiveCascadePrevention:
    """Verify the greedy algorithm prevents transitive cascade false positives."""

    async def test_six_flats_same_building_stay_separate(self) -> None:
        """3 RM + 3 ZP + 1 OTM at same postcode/coords/street with different prices.

        No group should have duplicate sources.
        """
        items = []
        for i in range(3):
            items.append(
                _wrap_merged(
                    _make_prop(
                        PropertySource.RIGHTMOVE,
                        f"rm{i}",
                        price_pcm=1800 + i * 200,
                    )
                )
            )
        for i in range(3):
            items.append(
                _wrap_merged(
                    _make_prop(
                        PropertySource.ZOOPLA,
                        f"zp{i}",
                        price_pcm=1800 + i * 200,
                    )
                )
            )
        items.append(
            _wrap_merged(
                _make_prop(PropertySource.ONTHEMARKET, "otm0", price_pcm=1800)
            )
        )

        groups = _group_items_greedy(items, {})

        for group in groups:
            sources = [mp.canonical.source for mp in group]
            assert len(sources) == len(set(sources)), (
                f"Group has duplicate sources: {sources}"
            )

    async def test_same_price_same_building_max_one_per_source(self) -> None:
        """2 RM with identical data -> 2 separate groups (never merge same-source)."""
        items = [
            _wrap_merged(_make_prop(PropertySource.RIGHTMOVE, "rm1")),
            _wrap_merged(_make_prop(PropertySource.RIGHTMOVE, "rm2")),
        ]

        groups = _group_items_greedy(items, {})

        # Should be 2 singletons — same-source pairs are never matched
        assert len(groups) == 2
        assert all(len(g) == 1 for g in groups)

    async def test_no_transitive_chain_through_intermediary(self) -> None:
        """A(OR) matches B(ZP), B(ZP) matches C(OR) -> A and C never in same group."""
        a = _wrap_merged(
            _make_prop(PropertySource.OPENRENT, "a", price_pcm=1800)
        )
        b = _wrap_merged(
            _make_prop(PropertySource.ZOOPLA, "b", price_pcm=1800)
        )
        c = _wrap_merged(
            _make_prop(PropertySource.OPENRENT, "c", price_pcm=1800)
        )

        groups = _group_items_greedy([a, b, c], {})

        # A and C are both OpenRent — they can't be in the same group
        for group in groups:
            sources = [mp.canonical.source for mp in group]
            assert len(sources) == len(set(sources))

    async def test_cascade_limited_to_four(self) -> None:
        """6 properties that would all chain, but max group size is 4."""
        sources = [
            PropertySource.OPENRENT,
            PropertySource.ZOOPLA,
            PropertySource.RIGHTMOVE,
            PropertySource.ONTHEMARKET,
            PropertySource.OPENRENT,
            PropertySource.ZOOPLA,
        ]
        items = [
            _wrap_merged(_make_prop(src, f"p{i}"))
            for i, src in enumerate(sources)
        ]

        groups = _group_items_greedy(items, {})

        for group in groups:
            assert len(group) <= 4
            sources_in_group = [mp.canonical.source for mp in group]
            assert len(sources_in_group) == len(set(sources_in_group))


# ---------------------------------------------------------------------------
# C2. TestSameSourceCollisionGuard
# ---------------------------------------------------------------------------


class TestSameSourceCollisionGuard:
    async def test_two_rightmove_never_merge(self) -> None:
        items = [
            _wrap_merged(_make_prop(PropertySource.RIGHTMOVE, "rm1")),
            _wrap_merged(_make_prop(PropertySource.RIGHTMOVE, "rm2")),
        ]

        groups = _group_items_greedy(items, {})

        assert len(groups) == 2
        assert all(len(g) == 1 for g in groups)

    async def test_four_platforms_merge_into_one(self) -> None:
        """Same property on all 4 platforms -> 1 group of 4."""
        items = [
            _wrap_merged(_make_prop(PropertySource.OPENRENT, "or1")),
            _wrap_merged(_make_prop(PropertySource.ZOOPLA, "zp1")),
            _wrap_merged(_make_prop(PropertySource.RIGHTMOVE, "rm1")),
            _wrap_merged(_make_prop(PropertySource.ONTHEMARKET, "otm1")),
        ]

        groups = _group_items_greedy(items, {})

        assert len(groups) == 1
        assert len(groups[0]) == 4

    async def test_greedy_best_score_wins(self) -> None:
        """OR_A, ZP_B (strong match), ZP_C (weak match to A) -> A+B merge, C separate."""
        a = _wrap_merged(
            _make_prop(PropertySource.OPENRENT, "a", price_pcm=1800)
        )
        # B: very close match to A
        b = _wrap_merged(
            _make_prop(PropertySource.ZOOPLA, "b", price_pcm=1800)
        )
        # C: also Zoopla but slightly different price (still matching)
        c = _wrap_merged(
            _make_prop(PropertySource.ZOOPLA, "c", price_pcm=1810)
        )

        groups = _group_items_greedy([a, b, c], {})

        # A and B should merge (best match). C stays separate (same source as B).
        merged_group = [g for g in groups if len(g) > 1]
        assert len(merged_group) == 1
        merged_sources = {mp.canonical.source for mp in merged_group[0]}
        assert PropertySource.OPENRENT in merged_sources
        assert PropertySource.ZOOPLA in merged_sources


# ---------------------------------------------------------------------------
# C3. TestBestOfCanonicalFields
# ---------------------------------------------------------------------------


class TestBestOfCanonicalFields:
    def test_full_postcode_from_secondary_source(self) -> None:
        """Canonical has outcode "E8", other has "E8 3RH" -> result has "E8 3RH"."""
        p1 = _make_prop(
            PropertySource.RIGHTMOVE, "rm1", postcode="E8",
            first_seen=datetime(2025, 1, 1, tzinfo=UTC),
        )
        p2 = _make_prop(
            PropertySource.ZOOPLA, "zp1", postcode="E8 3RH",
            first_seen=datetime(2025, 1, 2, tzinfo=UTC),
        )
        sorted_mps = [_wrap_merged(p1), _wrap_merged(p2)]

        result = _build_best_canonical(sorted_mps)

        assert result.postcode == "E8 3RH"
        # Identity fields stay from first
        assert result.source == PropertySource.RIGHTMOVE
        assert result.source_id == "rm1"

    def test_coordinates_backfill_from_other_source(self) -> None:
        """Canonical has None coords, other has coords -> result has coords."""
        p1 = _make_prop(
            PropertySource.RIGHTMOVE, "rm1",
            latitude=None, longitude=None,
            first_seen=datetime(2025, 1, 1, tzinfo=UTC),
        )
        p2 = _make_prop(
            PropertySource.ZOOPLA, "zp1",
            latitude=51.5465, longitude=-0.0553,
            first_seen=datetime(2025, 1, 2, tzinfo=UTC),
        )
        sorted_mps = [_wrap_merged(p1), _wrap_merged(p2)]

        result = _build_best_canonical(sorted_mps)

        assert result.latitude == 51.5465
        assert result.longitude == -0.0553

    def test_keeps_existing_full_postcode(self) -> None:
        """Canonical already has full postcode -> kept unchanged."""
        p1 = _make_prop(
            PropertySource.RIGHTMOVE, "rm1", postcode="E8 3RH",
            first_seen=datetime(2025, 1, 1, tzinfo=UTC),
        )
        p2 = _make_prop(
            PropertySource.ZOOPLA, "zp1", postcode="E8 4XY",
            first_seen=datetime(2025, 1, 2, tzinfo=UTC),
        )
        sorted_mps = [_wrap_merged(p1), _wrap_merged(p2)]

        result = _build_best_canonical(sorted_mps)

        assert result.postcode == "E8 3RH"

    def test_keeps_existing_coordinates(self) -> None:
        """Canonical already has coords -> kept unchanged."""
        p1 = _make_prop(
            PropertySource.RIGHTMOVE, "rm1",
            latitude=51.0, longitude=-0.1,
            first_seen=datetime(2025, 1, 1, tzinfo=UTC),
        )
        p2 = _make_prop(
            PropertySource.ZOOPLA, "zp1",
            latitude=51.9, longitude=-0.9,
            first_seen=datetime(2025, 1, 2, tzinfo=UTC),
        )
        sorted_mps = [_wrap_merged(p1), _wrap_merged(p2)]

        result = _build_best_canonical(sorted_mps)

        assert result.latitude == 51.0
        assert result.longitude == -0.1

    def test_available_from_earliest_selected(self) -> None:
        """Picks earliest non-null available_from date."""
        p1 = _make_prop(
            PropertySource.RIGHTMOVE, "rm1",
            first_seen=datetime(2025, 1, 1, tzinfo=UTC),
            available_from=datetime(2025, 3, 1, tzinfo=UTC),
        )
        p2 = _make_prop(
            PropertySource.ZOOPLA, "zp1",
            first_seen=datetime(2025, 1, 2, tzinfo=UTC),
            available_from=datetime(2025, 2, 15, tzinfo=UTC),
        )
        sorted_mps = [_wrap_merged(p1), _wrap_merged(p2)]

        result = _build_best_canonical(sorted_mps)

        assert result.available_from == datetime(2025, 2, 15, tzinfo=UTC)

    def test_available_from_null_filled_from_other(self) -> None:
        """Null available_from -> gets date from other source."""
        p1 = _make_prop(
            PropertySource.RIGHTMOVE, "rm1",
            first_seen=datetime(2025, 1, 1, tzinfo=UTC),
            available_from=None,
        )
        p2 = _make_prop(
            PropertySource.ZOOPLA, "zp1",
            first_seen=datetime(2025, 1, 2, tzinfo=UTC),
            available_from=datetime(2025, 2, 15, tzinfo=UTC),
        )
        sorted_mps = [_wrap_merged(p1), _wrap_merged(p2)]

        result = _build_best_canonical(sorted_mps)

        assert result.available_from == datetime(2025, 2, 15, tzinfo=UTC)

    def test_all_null_available_from_stays_null(self) -> None:
        p1 = _make_prop(
            PropertySource.RIGHTMOVE, "rm1",
            first_seen=datetime(2025, 1, 1, tzinfo=UTC),
            available_from=None,
        )
        p2 = _make_prop(
            PropertySource.ZOOPLA, "zp1",
            first_seen=datetime(2025, 1, 2, tzinfo=UTC),
            available_from=None,
        )
        sorted_mps = [_wrap_merged(p1), _wrap_merged(p2)]

        result = _build_best_canonical(sorted_mps)

        assert result.available_from is None

    def test_identity_fields_never_change(self) -> None:
        """source, source_id, url, first_seen always from earliest MP."""
        p1 = _make_prop(
            PropertySource.RIGHTMOVE, "rm1", postcode="E8",
            latitude=None, longitude=None,
            first_seen=datetime(2025, 1, 1, tzinfo=UTC),
        )
        p2 = _make_prop(
            PropertySource.ZOOPLA, "zp1", postcode="E8 3RH",
            latitude=51.5, longitude=-0.05,
            first_seen=datetime(2025, 1, 2, tzinfo=UTC),
        )
        sorted_mps = [_wrap_merged(p1), _wrap_merged(p2)]

        result = _build_best_canonical(sorted_mps)

        assert result.source == PropertySource.RIGHTMOVE
        assert result.source_id == "rm1"
        assert result.url == p1.url
        assert result.first_seen == datetime(2025, 1, 1, tzinfo=UTC)
        assert result.title == p1.title


# ---------------------------------------------------------------------------
# C4. TestPerceptualImageDedup
# ---------------------------------------------------------------------------


class TestPerceptualImageDedup:
    def test_same_image_different_cdns_deduplicated(self, tmp_path: Path) -> None:
        """Identical bytes, different URLs -> one survives."""
        uid = "openrent:100"
        data_dir = str(tmp_path)
        same_bytes = _make_image_bytes((100, 100, 100))

        img1 = PropertyImage(
            url=HttpUrl("https://cdn-a.com/photo.jpg"),
            source=PropertySource.OPENRENT,
            image_type="gallery",
        )
        img2 = PropertyImage(
            url=HttpUrl("https://cdn-b.com/photo.jpg"),
            source=PropertySource.ZOOPLA,
            image_type="gallery",
        )

        # Cache both images under same uid (single-source scenario)
        for i, img in enumerate([img1, img2]):
            fname = url_to_filename(str(img.url), "gallery", i)
            cache_dir = get_cache_dir(data_dir, uid)
            cache_dir.mkdir(parents=True, exist_ok=True)
            save_image_bytes(cache_dir / fname, same_bytes)

        result = _perceptual_dedup_images([img1, img2], data_dir, [uid])

        assert len(result) == 1

    def test_different_images_both_kept(self, tmp_path: Path) -> None:
        uid = "openrent:100"
        data_dir = str(tmp_path)

        img1 = PropertyImage(
            url=HttpUrl("https://cdn-a.com/photo1.jpg"),
            source=PropertySource.OPENRENT,
            image_type="gallery",
        )
        img2 = PropertyImage(
            url=HttpUrl("https://cdn-b.com/photo2.jpg"),
            source=PropertySource.ZOOPLA,
            image_type="gallery",
        )

        # Cache visually distinct images — use patterns, not just solid colors,
        # because pHash is designed to ignore small color differences.
        cache_dir = get_cache_dir(data_dir, uid)
        cache_dir.mkdir(parents=True, exist_ok=True)
        fname1 = url_to_filename(str(img1.url), "gallery", 0)
        fname2 = url_to_filename(str(img2.url), "gallery", 1)
        save_image_bytes(cache_dir / fname1, _make_image_bytes((255, 0, 0)))
        save_image_bytes(cache_dir / fname2, _make_distinct_image_bytes())

        result = _perceptual_dedup_images([img1, img2], data_dir, [uid])

        assert len(result) == 2

    def test_source_priority_keeps_zoopla_over_openrent(self, tmp_path: Path) -> None:
        """Zoopla version kept when deduping identical images."""
        uid = "openrent:100"
        data_dir = str(tmp_path)
        same_bytes = _make_image_bytes((100, 100, 100))

        img_or = PropertyImage(
            url=HttpUrl("https://cdn-or.com/photo.jpg"),
            source=PropertySource.OPENRENT,
            image_type="gallery",
        )
        img_zp = PropertyImage(
            url=HttpUrl("https://cdn-zp.com/photo.jpg"),
            source=PropertySource.ZOOPLA,
            image_type="gallery",
        )

        cache_dir = get_cache_dir(data_dir, uid)
        cache_dir.mkdir(parents=True, exist_ok=True)
        for i, img in enumerate([img_or, img_zp]):
            fname = url_to_filename(str(img.url), "gallery", i)
            save_image_bytes(cache_dir / fname, same_bytes)

        result = _perceptual_dedup_images([img_or, img_zp], data_dir, [uid])

        assert len(result) == 1
        assert result[0].source == PropertySource.ZOOPLA

    def test_uncached_images_always_kept(self, tmp_path: Path) -> None:
        """Can't hash -> don't drop."""
        uid = "openrent:100"
        data_dir = str(tmp_path)

        img1 = PropertyImage(
            url=HttpUrl("https://cdn.com/no-cache.jpg"),
            source=PropertySource.OPENRENT,
            image_type="gallery",
        )
        img2 = PropertyImage(
            url=HttpUrl("https://cdn.com/also-no-cache.jpg"),
            source=PropertySource.ZOOPLA,
            image_type="gallery",
        )

        # Don't cache anything
        result = _perceptual_dedup_images([img1, img2], data_dir, [uid])

        assert len(result) == 2

    def test_empty_data_dir_skips_dedup(self) -> None:
        """When data_dir is empty, dedup shouldn't be called (guarded in caller)."""
        img1 = PropertyImage(
            url=HttpUrl("https://cdn.com/a.jpg"),
            source=PropertySource.OPENRENT,
            image_type="gallery",
        )
        img2 = PropertyImage(
            url=HttpUrl("https://cdn.com/b.jpg"),
            source=PropertySource.ZOOPLA,
            image_type="gallery",
        )

        # Empty data_dir — images won't be found in cache
        result = _perceptual_dedup_images([img1, img2], "", ["uid"])

        assert len(result) == 2

    def test_single_image_no_dedup_needed(self, tmp_path: Path) -> None:
        uid = "openrent:100"
        data_dir = str(tmp_path)
        img = PropertyImage(
            url=HttpUrl("https://cdn.com/only.jpg"),
            source=PropertySource.OPENRENT,
            image_type="gallery",
        )

        result = _perceptual_dedup_images([img], data_dir, [uid])

        assert len(result) == 1
        assert result[0] is img

    def test_cross_source_images_found_in_separate_cache_dirs(
        self, tmp_path: Path
    ) -> None:
        """Images cached under different unique_ids are found and deduplicated.

        This simulates the real pipeline: OpenRent images are cached under
        ``image_cache/openrent_100/`` and Zoopla images under
        ``image_cache/zoopla_456/``. Perceptual dedup must search both dirs.
        """
        uid_or = "openrent:100"
        uid_zp = "zoopla:456"
        data_dir = str(tmp_path)
        same_bytes = _make_image_bytes((100, 100, 100))

        img_or = PropertyImage(
            url=HttpUrl("https://cdn-or.com/photo.jpg"),
            source=PropertySource.OPENRENT,
            image_type="gallery",
        )
        img_zp = PropertyImage(
            url=HttpUrl("https://cdn-zp.com/photo.jpg"),
            source=PropertySource.ZOOPLA,
            image_type="gallery",
        )

        # Cache each image under its own source's directory (real pipeline flow)
        cache_or = get_cache_dir(data_dir, uid_or)
        cache_or.mkdir(parents=True, exist_ok=True)
        save_image_bytes(
            cache_or / url_to_filename(str(img_or.url), "gallery", 0), same_bytes
        )

        cache_zp = get_cache_dir(data_dir, uid_zp)
        cache_zp.mkdir(parents=True, exist_ok=True)
        save_image_bytes(
            cache_zp / url_to_filename(str(img_zp.url), "gallery", 0), same_bytes
        )

        result = _perceptual_dedup_images(
            [img_or, img_zp], data_dir, [uid_or, uid_zp]
        )

        assert len(result) == 1
        # Zoopla has higher source priority
        assert result[0].source == PropertySource.ZOOPLA

    def test_cross_source_distinct_images_both_kept(self, tmp_path: Path) -> None:
        """Different images in separate cache dirs are both kept."""
        uid_or = "openrent:100"
        uid_zp = "zoopla:456"
        data_dir = str(tmp_path)

        img_or = PropertyImage(
            url=HttpUrl("https://cdn-or.com/photo.jpg"),
            source=PropertySource.OPENRENT,
            image_type="gallery",
        )
        img_zp = PropertyImage(
            url=HttpUrl("https://cdn-zp.com/photo.jpg"),
            source=PropertySource.ZOOPLA,
            image_type="gallery",
        )

        cache_or = get_cache_dir(data_dir, uid_or)
        cache_or.mkdir(parents=True, exist_ok=True)
        save_image_bytes(
            cache_or / url_to_filename(str(img_or.url), "gallery", 0),
            _make_image_bytes((255, 0, 0)),
        )

        cache_zp = get_cache_dir(data_dir, uid_zp)
        cache_zp.mkdir(parents=True, exist_ok=True)
        save_image_bytes(
            cache_zp / url_to_filename(str(img_zp.url), "gallery", 0),
            _make_distinct_image_bytes(),
        )

        result = _perceptual_dedup_images(
            [img_or, img_zp], data_dir, [uid_or, uid_zp]
        )

        assert len(result) == 2


# ---------------------------------------------------------------------------
# C5. TestFloorplanQualitySelection
# ---------------------------------------------------------------------------


class TestFloorplanQualitySelection:
    def test_zoopla_preferred_over_openrent(self) -> None:
        fp_or = PropertyImage(
            url=HttpUrl("https://or.com/floor.jpg"),
            source=PropertySource.OPENRENT,
            image_type="floorplan",
        )
        fp_zp = PropertyImage(
            url=HttpUrl("https://zp.com/floor.jpg"),
            source=PropertySource.ZOOPLA,
            image_type="floorplan",
        )

        p1 = _make_prop(
            PropertySource.OPENRENT, "or1",
            first_seen=datetime(2025, 1, 1, tzinfo=UTC),
        )
        p2 = _make_prop(
            PropertySource.ZOOPLA, "zp1",
            first_seen=datetime(2025, 1, 2, tzinfo=UTC),
        )

        sorted_mps = [
            _wrap_merged(p1, floorplan=fp_or),
            _wrap_merged(p2, floorplan=fp_zp),
        ]

        result = _select_best_floorplan(sorted_mps)

        assert result is not None
        assert result.source == PropertySource.ZOOPLA

    def test_only_available_floorplan_selected(self) -> None:
        fp = PropertyImage(
            url=HttpUrl("https://rm.com/floor.jpg"),
            source=PropertySource.RIGHTMOVE,
            image_type="floorplan",
        )

        p1 = _make_prop(PropertySource.OPENRENT, "or1")
        p2 = _make_prop(PropertySource.RIGHTMOVE, "rm1")

        sorted_mps = [
            _wrap_merged(p1),  # no floorplan
            _wrap_merged(p2, floorplan=fp),
        ]

        result = _select_best_floorplan(sorted_mps)

        assert result is not None
        assert result.source == PropertySource.RIGHTMOVE

    def test_no_floorplans_returns_none(self) -> None:
        p1 = _make_prop(PropertySource.OPENRENT, "or1")
        p2 = _make_prop(PropertySource.ZOOPLA, "zp1")

        sorted_mps = [_wrap_merged(p1), _wrap_merged(p2)]

        result = _select_best_floorplan(sorted_mps)

        assert result is None


# ---------------------------------------------------------------------------
# C6. TestRegressionLegitMerges
# ---------------------------------------------------------------------------


class TestRegressionLegitMerges:
    async def test_identical_listing_two_platforms(self) -> None:
        """Same property OR + ZP still merges."""
        items = [
            _wrap_merged(_make_prop(PropertySource.OPENRENT, "or1")),
            _wrap_merged(_make_prop(PropertySource.ZOOPLA, "zp1")),
        ]

        groups = _group_items_greedy(items, {})

        assert len(groups) == 1
        assert len(groups[0]) == 2

    async def test_three_platforms_genuine_same_property(self) -> None:
        """OTM + ZP + OR -> 1 group."""
        items = [
            _wrap_merged(_make_prop(PropertySource.ONTHEMARKET, "otm1")),
            _wrap_merged(_make_prop(PropertySource.ZOOPLA, "zp1")),
            _wrap_merged(_make_prop(PropertySource.OPENRENT, "or1")),
        ]

        groups = _group_items_greedy(items, {})

        assert len(groups) == 1
        assert len(groups[0]) == 3

    async def test_four_platforms_genuine_same_property(self) -> None:
        """All 4 -> 1 group."""
        items = [
            _wrap_merged(_make_prop(PropertySource.OPENRENT, "or1")),
            _wrap_merged(_make_prop(PropertySource.ZOOPLA, "zp1")),
            _wrap_merged(_make_prop(PropertySource.RIGHTMOVE, "rm1")),
            _wrap_merged(_make_prop(PropertySource.ONTHEMARKET, "otm1")),
        ]

        groups = _group_items_greedy(items, {})

        assert len(groups) == 1
        assert len(groups[0]) == 4

    async def test_mixed_batch_correct_grouping(self) -> None:
        """7 properties (3 genuine pairs + 1 singleton) -> 4 groups.

        Each pair has a distinct address+postcode+coords+price so they don't
        cross-match with other pairs.
        """
        # Pair 1: same property on OR + ZP (E8 3RH, Mare St)
        pair1_a = _wrap_merged(
            _make_prop(
                PropertySource.OPENRENT, "pair1-or", price_pcm=1800,
                postcode="E8 3RH", address="123 Mare Street",
                latitude=51.5465, longitude=-0.0553,
            )
        )
        pair1_b = _wrap_merged(
            _make_prop(
                PropertySource.ZOOPLA, "pair1-zp", price_pcm=1800,
                postcode="E8 3RH", address="123 Mare Street",
                latitude=51.5465, longitude=-0.0553,
            )
        )

        # Pair 2: same property on RM + OTM (N1 5AB, Upper St — different area)
        pair2_a = _wrap_merged(
            _make_prop(
                PropertySource.RIGHTMOVE, "pair2-rm", price_pcm=2500,
                postcode="N1 5AB", address="45 Upper Street",
                latitude=51.5380, longitude=-0.1030,
            )
        )
        pair2_b = _wrap_merged(
            _make_prop(
                PropertySource.ONTHEMARKET, "pair2-otm", price_pcm=2500,
                postcode="N1 5AB", address="45 Upper Street",
                latitude=51.5380, longitude=-0.1030,
            )
        )

        # Pair 3: same property on OR + RM (E2 9PQ, Bethnal Green Rd)
        pair3_a = _wrap_merged(
            _make_prop(
                PropertySource.OPENRENT, "pair3-or", price_pcm=2200,
                postcode="E2 9PQ", address="78 Bethnal Green Road",
                latitude=51.5240, longitude=-0.0680,
            )
        )
        pair3_b = _wrap_merged(
            _make_prop(
                PropertySource.RIGHTMOVE, "pair3-rm", price_pcm=2200,
                postcode="E2 9PQ", address="78 Bethnal Green Road",
                latitude=51.5240, longitude=-0.0680,
            )
        )

        # Singleton: unique property (SW1 area, completely different)
        singleton = _wrap_merged(
            _make_prop(
                PropertySource.ZOOPLA, "singleton-zp", price_pcm=3500,
                postcode="SW1A 1AA", address="10 Downing Street",
                latitude=51.5034, longitude=-0.1276,
            )
        )

        items = [pair1_a, pair1_b, pair2_a, pair2_b, pair3_a, pair3_b, singleton]
        groups = _group_items_greedy(items, {})

        assert len(groups) == 4

        # Verify all items appear exactly once
        all_ids = {mp.canonical.source_id for group in groups for mp in group}
        assert len(all_ids) == 7


# ---------------------------------------------------------------------------
# C7. TestGreedyInvariants (hypothesis property-based)
# ---------------------------------------------------------------------------


_SOURCE_STRATEGY = st.sampled_from(list(PropertySource))


@st.composite
def _merged_property_st(draw: st.DrawFn) -> MergedProperty:
    """Generate a random MergedProperty for property-based testing."""
    source = draw(_SOURCE_STRATEGY)
    source_id = draw(st.text(min_size=1, max_size=8, alphabet="abcdefghijklmnop0123456789"))
    price = draw(st.integers(min_value=1000, max_value=3000))
    lat = draw(st.floats(min_value=51.54, max_value=51.56))
    lon = draw(st.floats(min_value=-0.06, max_value=-0.04))
    postcode = draw(st.sampled_from(["E8 3RH", "E8 4AB", "E8 3RH"]))

    prop = Property(
        source=source,
        source_id=source_id,
        url=HttpUrl(f"https://example.com/{source.value}/{source_id}"),
        title=f"Test {source_id}",
        price_pcm=price,
        bedrooms=2,
        address="123 Mare Street",
        postcode=postcode,
        latitude=lat,
        longitude=lon,
    )
    return MergedProperty(
        canonical=prop,
        sources=(source,),
        source_urls={source: prop.url},
        images=(),
        floorplan=None,
        min_price=price,
        max_price=price,
        descriptions={},
    )


class TestGreedyInvariants:
    @given(items=st.lists(_merged_property_st(), min_size=0, max_size=12))
    @settings(max_examples=50)
    def test_no_same_source_collision_invariant(
        self, items: list[MergedProperty]
    ) -> None:
        """No group should have duplicate sources."""
        # Deduplicate by unique_id first (hypothesis may generate same source_id)
        seen: dict[str, MergedProperty] = {}
        for mp in items:
            uid = mp.canonical.unique_id
            if uid not in seen:
                seen[uid] = mp
        unique_items = list(seen.values())

        groups = _group_items_greedy(unique_items, {})

        for group in groups:
            sources = [mp.canonical.source for mp in group]
            assert len(sources) == len(set(sources)), (
                f"Same-source collision: {[s.value for s in sources]}"
            )

    @given(items=st.lists(_merged_property_st(), min_size=0, max_size=12))
    @settings(max_examples=50)
    def test_max_group_size_invariant(self, items: list[MergedProperty]) -> None:
        """No group should exceed 4 members."""
        seen: dict[str, MergedProperty] = {}
        for mp in items:
            uid = mp.canonical.unique_id
            if uid not in seen:
                seen[uid] = mp
        unique_items = list(seen.values())

        groups = _group_items_greedy(unique_items, {})

        for group in groups:
            assert len(group) <= 4

    @given(items=st.lists(_merged_property_st(), min_size=1, max_size=12))
    @settings(max_examples=50)
    def test_no_property_lost_invariant(self, items: list[MergedProperty]) -> None:
        """Every input unique_id appears in exactly one output group."""
        seen: dict[str, MergedProperty] = {}
        for mp in items:
            uid = mp.canonical.unique_id
            if uid not in seen:
                seen[uid] = mp
        unique_items = list(seen.values())
        input_ids = {mp.canonical.unique_id for mp in unique_items}

        groups = _group_items_greedy(unique_items, {})

        output_ids: set[str] = set()
        for group in groups:
            for mp in group:
                uid = mp.canonical.unique_id
                assert uid not in output_ids, f"Duplicate output: {uid}"
                output_ids.add(uid)

        assert input_ids == output_ids

    @given(items=st.lists(_merged_property_st(), min_size=2, max_size=6))
    @settings(max_examples=30)
    def test_scoring_symmetry_preserved(self, items: list[MergedProperty]) -> None:
        """score(A,B) == score(B,A) for all pairs."""
        from home_finder.filters.scoring import calculate_match_score

        seen: dict[str, MergedProperty] = {}
        for mp in items:
            uid = mp.canonical.unique_id
            if uid not in seen:
                seen[uid] = mp
        unique_items = list(seen.values())

        for i in range(len(unique_items)):
            for j in range(i + 1, len(unique_items)):
                a = unique_items[i].canonical
                b = unique_items[j].canonical
                score_ab = calculate_match_score(a, b, {})
                score_ba = calculate_match_score(b, a, {})
                assert abs(score_ab.total - score_ba.total) < 0.01, (
                    f"Asymmetric: {score_ab.total} vs {score_ba.total}"
                )


# ---------------------------------------------------------------------------
# Integration: Deduplicator.deduplicate_merged_async with greedy algorithm
# ---------------------------------------------------------------------------


class TestDeduplicatorIntegration:
    async def test_end_to_end_greedy_merge(self) -> None:
        """Full pipeline: properties from different sources merge correctly."""
        dedup = Deduplicator(enable_cross_platform=True)

        # Same property on OR and ZP (same postcode, coords, price)
        mp1 = _wrap_merged(_make_prop(PropertySource.OPENRENT, "or1"))
        mp2 = _wrap_merged(_make_prop(PropertySource.ZOOPLA, "zp1"))

        # Different property (different outcode entirely — won't match)
        mp3 = _wrap_merged(
            _make_prop(
                PropertySource.RIGHTMOVE, "rm1", price_pcm=3000,
                postcode="N1 5AB", address="45 Upper Street",
                latitude=51.57, longitude=-0.08,
            )
        )

        result = await dedup.deduplicate_merged_async([mp1, mp2, mp3])

        assert len(result) == 2
        multi = [r for r in result if len(r.sources) > 1]
        assert len(multi) == 1
        assert set(multi[0].sources) == {PropertySource.OPENRENT, PropertySource.ZOOPLA}

    async def test_best_of_canonical_in_merge(self) -> None:
        """Merged properties should have best-of canonical fields.

        Both properties share the same full postcode, address, and price
        so they score high enough to merge (postcode 40 + street 20 + outcode 10
        + price 15 = 85). The RM property has outcode-only postcode and no coords,
        but we give it a full postcode match to ensure the merge actually happens.
        """
        dedup = Deduplicator(enable_cross_platform=True)

        # RM has full postcode but no coords
        p1 = _make_prop(
            PropertySource.RIGHTMOVE, "rm1", postcode="E8 3RH",
            latitude=None, longitude=None,
            first_seen=datetime(2025, 1, 1, tzinfo=UTC),
        )
        p2 = _make_prop(
            PropertySource.ZOOPLA, "zp1", postcode="E8 3RH",
            latitude=51.5465, longitude=-0.0553,
            first_seen=datetime(2025, 1, 2, tzinfo=UTC),
        )

        mp1 = _wrap_merged(p1)
        mp2 = _wrap_merged(p2)

        result = await dedup.deduplicate_merged_async([mp1, mp2])

        assert len(result) == 1
        canon = result[0].canonical
        # Should have coordinates from ZP (backfilled)
        assert canon.latitude == 51.5465
        assert canon.longitude == -0.0553
        # Identity stays from RM (earlier first_seen)
        assert canon.source == PropertySource.RIGHTMOVE

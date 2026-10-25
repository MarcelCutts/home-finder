"""Image evidence distinguishes homes and removes repeated gallery images."""

from pathlib import Path

from pydantic import HttpUrl

from home_finder.matching.deduplication import (
    _group_items_greedy,
    _perceptual_dedup_images,
)
from home_finder.models import PropertyImage, PropertySource
from home_finder.utils.image_cache import (
    find_cached_file,
    get_cache_dir,
    save_image_bytes,
    url_to_filename,
)
from home_finder.utils.image_hash import hash_from_disk
from tests.support.matching import (
    as_aggregate,
    make_checkerboard_jpeg,
    make_listing,
    make_solid_jpeg,
)


class TestPerceptualImageDedup:
    def test_same_image_different_cdns_deduplicated(self, tmp_path: Path) -> None:
        """Identical bytes, different URLs -> one survives."""
        uid = "openrent:100"
        data_dir = str(tmp_path)
        same_bytes = make_solid_jpeg((100, 100, 100))

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

        # Verify caching + hashing works (catches flaky PIL/imagehash failures)
        for img in [img1, img2]:
            cached = find_cached_file(data_dir, uid, str(img.url), "gallery")
            assert cached is not None, f"Cache miss for {img.url}"
            assert hash_from_disk(cached) is not None, f"Hash failed for {cached}"

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
        save_image_bytes(cache_dir / fname1, make_solid_jpeg((255, 0, 0)))
        save_image_bytes(cache_dir / fname2, make_checkerboard_jpeg())

        result = _perceptual_dedup_images([img1, img2], data_dir, [uid])

        assert len(result) == 2

    def test_source_priority_keeps_zoopla_over_openrent(self, tmp_path: Path) -> None:
        """Zoopla version kept when deduping identical images."""
        uid = "openrent:100"
        data_dir = str(tmp_path)
        same_bytes = make_solid_jpeg((100, 100, 100))

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

        # Verify caching + hashing works (catches flaky PIL/imagehash failures)
        for img in [img_or, img_zp]:
            cached = find_cached_file(data_dir, uid, str(img.url), "gallery")
            assert cached is not None, f"Cache miss for {img.url}"
            assert hash_from_disk(cached) is not None, f"Hash failed for {cached}"

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

    def test_cross_source_images_found_in_separate_cache_dirs(self, tmp_path: Path) -> None:
        """Images cached under different unique_ids are found and deduplicated.

        This simulates the real pipeline: OpenRent images are cached under
        ``image_cache/openrent_100/`` and Zoopla images under
        ``image_cache/zoopla_456/``. Perceptual dedup must search both dirs.
        """
        uid_or = "openrent:100"
        uid_zp = "zoopla:456"
        data_dir = str(tmp_path)
        same_bytes = make_solid_jpeg((100, 100, 100))

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
        save_image_bytes(cache_or / url_to_filename(str(img_or.url), "gallery", 0), same_bytes)

        cache_zp = get_cache_dir(data_dir, uid_zp)
        cache_zp.mkdir(parents=True, exist_ok=True)
        save_image_bytes(cache_zp / url_to_filename(str(img_zp.url), "gallery", 0), same_bytes)

        result = _perceptual_dedup_images([img_or, img_zp], data_dir, [uid_or, uid_zp])

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
            make_solid_jpeg((255, 0, 0)),
        )

        cache_zp = get_cache_dir(data_dir, uid_zp)
        cache_zp.mkdir(parents=True, exist_ok=True)
        save_image_bytes(
            cache_zp / url_to_filename(str(img_zp.url), "gallery", 0),
            make_checkerboard_jpeg(),
        )

        result = _perceptual_dedup_images([img_or, img_zp], data_dir, [uid_or, uid_zp])

        assert len(result) == 2


class TestImageEvidenceRequirement:
    """Verify MEDIUM matches without image evidence are rejected."""

    async def test_same_building_different_flat_rejected(self) -> None:
        """Two properties at same coords/street/outcode, similar price, but
        different gallery images (both have hashes, zero matches) → stay separate.

        This is the "same building, different flat" false positive class.
        Rightmove only has an outcode (no full postcode match):
        Score: coords ~31 + street 20 + outcode 10 + price 15 = ~76 (MEDIUM).
        """
        from home_finder.matching.scoring import calculate_match_score

        # Rightmove with outcode only (realistic — RM rarely has full postcodes)
        p_rm = make_listing(
            PropertySource.RIGHTMOVE,
            "rm1",
            postcode="E8",
            latitude=51.5465,
            longitude=-0.0553,
            price_pcm=1800,
        )
        # Zoopla with full postcode
        p_zp = make_listing(
            PropertySource.ZOOPLA,
            "zp1",
            postcode="E8 3RH",
            latitude=51.5467,
            longitude=-0.0551,
            price_pcm=1800,
        )

        # Guard test assumptions: score must be a MEDIUM match (60-79).
        # If scoring constants change, this will catch drift before the
        # grouping assertion becomes meaningless.
        image_hashes: dict[str, list[str]] = {
            "rightmove:rm1": ["abcdef1234567890"],
            "zoopla:zp1": ["9876543210fedcba"],
        }
        score = calculate_match_score(p_rm, p_zp, image_hashes)
        assert score.is_match, f"Test assumption: should be a match, got {score.total}"
        assert score.total < 80, f"Test assumption: should be MEDIUM, got {score.total}"

        items = [as_aggregate(p_rm), as_aggregate(p_zp)]

        groups = _group_items_greedy(items, image_hashes)

        # Should stay separate: both have galleries, zero image matches,
        # and total score is < 80
        assert len(groups) == 2
        assert all(len(g) == 1 for g in groups)

    async def test_no_gallery_hashes_still_merges(self) -> None:
        """Same scenario but neither property has gallery hashes → still merges.

        Can't penalize for missing data — no galleries means we don't know.
        """
        p_rm = make_listing(
            PropertySource.RIGHTMOVE,
            "rm1",
            latitude=51.5465,
            longitude=-0.0553,
            price_pcm=1800,
        )
        p_zp = make_listing(
            PropertySource.ZOOPLA,
            "zp1",
            latitude=51.5467,
            longitude=-0.0551,
            price_pcm=1800,
        )

        items = [as_aggregate(p_rm), as_aggregate(p_zp)]

        # Empty image_hashes — no galleries available
        groups = _group_items_greedy(items, {})

        # Should still merge (can't penalize for missing data)
        assert len(groups) == 1
        assert len(groups[0]) == 2

    async def test_one_has_gallery_other_doesnt_still_merges(self) -> None:
        """Only one property has gallery hashes → still merges.

        The "both_have_galleries" check requires BOTH to have hashes.
        """
        p_rm = make_listing(
            PropertySource.RIGHTMOVE,
            "rm1",
            latitude=51.5465,
            longitude=-0.0553,
            price_pcm=1800,
        )
        p_zp = make_listing(
            PropertySource.ZOOPLA,
            "zp1",
            latitude=51.5467,
            longitude=-0.0551,
            price_pcm=1800,
        )

        items = [as_aggregate(p_rm), as_aggregate(p_zp)]

        # Only RM has gallery hashes
        image_hashes = {
            "rightmove:rm1": ["abcdef1234567890"],
        }

        groups = _group_items_greedy(items, image_hashes)

        # Should still merge (only one has gallery)
        assert len(groups) == 1
        assert len(groups[0]) == 2

    async def test_image_match_present_still_merges(self) -> None:
        """Both have galleries AND images match → merges normally."""
        p_rm = make_listing(
            PropertySource.RIGHTMOVE,
            "rm1",
            latitude=51.5465,
            longitude=-0.0553,
            price_pcm=1800,
        )
        p_zp = make_listing(
            PropertySource.ZOOPLA,
            "zp1",
            latitude=51.5467,
            longitude=-0.0551,
            price_pcm=1800,
        )

        items = [as_aggregate(p_rm), as_aggregate(p_zp)]

        # Same hash → image match will be detected by scoring
        same_hash = "abcdef1234567890"
        image_hashes = {
            "rightmove:rm1": [same_hash, "extra_hash_1"],
            "zoopla:zp1": [same_hash, "extra_hash_2"],
        }

        groups = _group_items_greedy(items, image_hashes)

        # Should merge (image evidence present)
        assert len(groups) == 1
        assert len(groups[0]) == 2

    async def test_empty_gallery_hashes_rejected(self) -> None:
        """Both have hash keys but empty lists → treated as "galleries checked,
        no images hashed". This is the edge case where hashing was attempted
        but produced nothing (e.g. all images failed to download/hash).

        both_have_galleries=True (keys present), count_gallery_hash_matches=0
        (empty lists), so the MEDIUM match is rejected.
        """
        p_rm = make_listing(
            PropertySource.RIGHTMOVE,
            "rm1",
            postcode="E8",
            latitude=51.5465,
            longitude=-0.0553,
            price_pcm=1800,
        )
        p_zp = make_listing(
            PropertySource.ZOOPLA,
            "zp1",
            postcode="E8 3RH",
            latitude=51.5467,
            longitude=-0.0551,
            price_pcm=1800,
        )

        items = [as_aggregate(p_rm), as_aggregate(p_zp)]

        # Both keys present but empty lists — hashing attempted, nothing produced
        image_hashes: dict[str, list[str]] = {
            "rightmove:rm1": [],
            "zoopla:zp1": [],
        }

        groups = _group_items_greedy(items, image_hashes)

        # Should stay separate: both_have_galleries=True, no image matches,
        # score is MEDIUM (<80)
        assert len(groups) == 2
        assert all(len(g) == 1 for g in groups)

    async def test_high_score_without_images_rejected_when_galleries_present(self) -> None:
        """Score >= 80 with galleries but no image match → rejected.

        Full postcode (40) + coords (40) + street (20) + outcode (10) + price (15)
        = 125 at exact match. Despite the high location score, zero matching
        images means these could be different flats in the same building.
        Location signals are perfectly correlated for same-building properties,
        so they're uninformative — only image evidence can disambiguate.
        """
        p_rm = make_listing(
            PropertySource.RIGHTMOVE,
            "rm1",
            price_pcm=1800,
        )
        p_zp = make_listing(
            PropertySource.ZOOPLA,
            "zp1",
            price_pcm=1800,
        )

        items = [as_aggregate(p_rm), as_aggregate(p_zp)]

        # Both have galleries, no image matches, score is very high
        image_hashes = {
            "rightmove:rm1": ["aaaa1111bbbb2222"],
            "zoopla:zp1": ["cccc3333dddd4444"],
        }

        groups = _group_items_greedy(items, image_hashes)

        # Should stay separate: image evidence says "no match", location
        # score is uninformative for same-building disambiguation
        assert len(groups) == 2
        assert all(len(g) == 1 for g in groups)

    async def test_same_building_different_flat_rejected_gallery_mismatch(self) -> None:
        """Two properties at the same address with different galleries → separate.

        Simulates the real-world false positive: identical postcode, coords,
        street, price (~125 points) but completely different gallery images.
        """
        p_rm = make_listing(
            PropertySource.RIGHTMOVE,
            "rm1",
            price_pcm=1750,
            postcode="E3 4RB",
            latitude=51.5310,
            longitude=-0.0240,
            address="15 Copperfield Road, London",
        )
        p_or = make_listing(
            PropertySource.OPENRENT,
            "or1",
            price_pcm=1750,
            postcode="E3 4RB",
            latitude=51.5310,
            longitude=-0.0240,
            address="15 Copperfield Road, London",
        )

        items = [as_aggregate(p_rm), as_aggregate(p_or)]

        # Both have gallery hashes, zero overlap
        image_hashes = {
            "rightmove:rm1": ["1111111111111111", "2222222222222222", "3333333333333333"],
            "openrent:or1": ["aaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbb", "cccccccccccccccc"],
        }

        groups = _group_items_greedy(items, image_hashes)

        assert len(groups) == 2
        assert all(len(g) == 1 for g in groups)

    async def test_same_flat_with_matching_images_still_merges(self) -> None:
        """Two properties at the same address with overlapping galleries → merged.

        When gallery images confirm it's the same flat, the match proceeds.
        """
        p_rm = make_listing(
            PropertySource.RIGHTMOVE,
            "rm1",
            price_pcm=1750,
            postcode="E3 4RB",
            latitude=51.5310,
            longitude=-0.0240,
            address="15 Copperfield Road, London",
        )
        p_zp = make_listing(
            PropertySource.ZOOPLA,
            "zp1",
            price_pcm=1750,
            postcode="E3 4RB",
            latitude=51.5310,
            longitude=-0.0240,
            address="15 Copperfield Road, London",
        )

        items = [as_aggregate(p_rm), as_aggregate(p_zp)]

        # Both have gallery hashes, two shared image hashes (need 2+ for full credit)
        shared_hash = "abcdef1234567890"
        shared_hash_2 = "abcdef1234567891"
        image_hashes = {
            "rightmove:rm1": ["1111111111111111", shared_hash, shared_hash_2],
            "zoopla:zp1": ["aaaaaaaaaaaaaaaa", shared_hash, shared_hash_2],
        }

        groups = _group_items_greedy(items, image_hashes)

        assert len(groups) == 1
        assert len(groups[0]) == 2

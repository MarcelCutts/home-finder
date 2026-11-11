"""Image evidence distinguishes homes, and a merged home keeps one copy of each photo."""

from pathlib import Path

import pytest
from pydantic import HttpUrl

from home_finder.matching import deduplication
from home_finder.matching.deduplication import (
    Deduplicator,
    _group_items_greedy,
    _perceptual_dedup_images,
)
from home_finder.matching.scoring import MatchConfidence, calculate_match_score
from home_finder.models import MergedProperty, Property, PropertyImage, PropertySource
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


def _gallery_image(url: str, source: PropertySource) -> PropertyImage:
    return PropertyImage(url=HttpUrl(url), source=source, image_type="gallery")


def _cache_gallery(data_dir: Path, unique_id: str, photos: list[bytes]) -> None:
    """Write gallery files where enrichment caches them for ``unique_id``."""
    cache_dir = get_cache_dir(str(data_dir), unique_id)
    for index, photo in enumerate(photos):
        save_image_bytes(cache_dir / f"gallery_{index:03d}_{index:08x}.jpg", photo)


class TestPerceptualImageDedup:
    def test_distinct_photos_are_both_kept(self, tmp_path: Path) -> None:
        uid = "openrent:100"
        data_dir = str(tmp_path)
        img1 = _gallery_image("https://cdn-a.com/photo1.jpg", PropertySource.OPENRENT)
        img2 = _gallery_image("https://cdn-b.com/photo2.jpg", PropertySource.ZOOPLA)

        # pHash tolerates colour changes, so the distinct photo needs a different pattern.
        cache_dir = get_cache_dir(data_dir, uid)
        save_image_bytes(
            cache_dir / url_to_filename(str(img1.url), "gallery", 0), make_solid_jpeg((255, 0, 0))
        )
        save_image_bytes(
            cache_dir / url_to_filename(str(img2.url), "gallery", 1), make_checkerboard_jpeg()
        )

        result = _perceptual_dedup_images([img1, img2], data_dir, [uid])

        assert len(result) == 2

    def test_repeated_photo_keeps_the_zoopla_copy_over_openrent(self, tmp_path: Path) -> None:
        uid = "openrent:100"
        data_dir = str(tmp_path)
        same_bytes = make_solid_jpeg((100, 100, 100))
        img_or = _gallery_image("https://cdn-or.com/photo.jpg", PropertySource.OPENRENT)
        img_zp = _gallery_image("https://cdn-zp.com/photo.jpg", PropertySource.ZOOPLA)

        cache_dir = get_cache_dir(data_dir, uid)
        for i, img in enumerate([img_or, img_zp]):
            save_image_bytes(cache_dir / url_to_filename(str(img.url), "gallery", i), same_bytes)

        # Fail here, not in the dedup assertion, if caching or hashing itself breaks.
        for img in [img_or, img_zp]:
            cached = find_cached_file(data_dir, uid, str(img.url), "gallery")
            assert cached is not None, f"Cache miss for {img.url}"
            assert hash_from_disk(cached) is not None, f"Hash failed for {cached}"

        result = _perceptual_dedup_images([img_or, img_zp], data_dir, [uid])

        assert result == [img_zp]

    def test_photos_missing_from_the_cache_are_kept(self, tmp_path: Path) -> None:
        img1 = _gallery_image("https://cdn.com/no-cache.jpg", PropertySource.OPENRENT)
        img2 = _gallery_image("https://cdn.com/also-no-cache.jpg", PropertySource.ZOOPLA)

        result = _perceptual_dedup_images([img1, img2], str(tmp_path), ["openrent:100"])

        assert len(result) == 2

    def test_single_photo_is_returned_unchanged(self, tmp_path: Path) -> None:
        img = _gallery_image("https://cdn.com/only.jpg", PropertySource.OPENRENT)

        result = _perceptual_dedup_images([img], str(tmp_path), ["openrent:100"])

        assert len(result) == 1
        assert result[0] is img

    def test_repeated_photo_is_found_across_each_listing_cache(self, tmp_path: Path) -> None:
        """Before cache consolidation each listing's photos sit under its own unique_id.

        The Zoopla copy comes first here, so the higher-priority photo is the one
        compared against later duplicates.
        """
        uid_or = "openrent:100"
        uid_zp = "zoopla:456"
        data_dir = str(tmp_path)
        same_bytes = make_solid_jpeg((100, 100, 100))
        img_zp = _gallery_image("https://cdn-zp.com/photo.jpg", PropertySource.ZOOPLA)
        img_or = _gallery_image("https://cdn-or.com/photo.jpg", PropertySource.OPENRENT)

        save_image_bytes(
            get_cache_dir(data_dir, uid_or) / url_to_filename(str(img_or.url), "gallery", 0),
            same_bytes,
        )
        save_image_bytes(
            get_cache_dir(data_dir, uid_zp) / url_to_filename(str(img_zp.url), "gallery", 0),
            same_bytes,
        )

        result = _perceptual_dedup_images([img_zp, img_or], data_dir, [uid_or, uid_zp])

        assert result == [img_zp]

    def test_distinct_photos_in_separate_caches_are_both_kept(self, tmp_path: Path) -> None:
        uid_or = "openrent:100"
        uid_zp = "zoopla:456"
        data_dir = str(tmp_path)
        img_or = _gallery_image("https://cdn-or.com/photo.jpg", PropertySource.OPENRENT)
        img_zp = _gallery_image("https://cdn-zp.com/photo.jpg", PropertySource.ZOOPLA)

        save_image_bytes(
            get_cache_dir(data_dir, uid_or) / url_to_filename(str(img_or.url), "gallery", 0),
            make_solid_jpeg((255, 0, 0)),
        )
        save_image_bytes(
            get_cache_dir(data_dir, uid_zp) / url_to_filename(str(img_zp.url), "gallery", 0),
            make_checkerboard_jpeg(),
        )

        result = _perceptual_dedup_images([img_or, img_zp], data_dir, [uid_or, uid_zp])

        assert len(result) == 2


def _same_building_pair() -> list[MergedProperty]:
    """Rightmove (outcode only) and Zoopla 26 m apart with the same street and rent.

    Without images the pair scores about 74 points: a MEDIUM-confidence match.
    """
    rightmove = make_listing(
        PropertySource.RIGHTMOVE, "rm1", postcode="E8", latitude=51.5465, longitude=-0.0553
    )
    zoopla = make_listing(
        PropertySource.ZOOPLA, "zp1", postcode="E8 3RH", latitude=51.5467, longitude=-0.0551
    )
    return [as_aggregate(rightmove), as_aggregate(zoopla)]


def _assert_location_alone_groups(items: list[MergedProperty]) -> None:
    """Control: without gallery hashes the pair is grouped, so images decide the outcome."""
    groups = _group_items_greedy(items, {})
    assert [len(group) for group in groups] == [2]


class TestGalleryEvidenceRequirement:
    """Galleries that are present on both sides and share no photo veto a match."""

    async def test_disagreeing_galleries_veto_a_medium_confidence_match(self) -> None:
        items = _same_building_pair()
        image_hashes = {"rightmove:rm1": ["abcdef1234567890"], "zoopla:zp1": ["9876543210fedcba"]}
        score = calculate_match_score(items[0].canonical, items[1].canonical, image_hashes)
        assert score.confidence is MatchConfidence.MEDIUM
        assert score.image_hash == 0
        _assert_location_alone_groups(items)

        groups = _group_items_greedy(items, image_hashes)

        assert [len(group) for group in groups] == [1, 1]

    async def test_disagreeing_galleries_veto_a_high_confidence_match(self) -> None:
        """Same address, postcode, coordinates and rent, but no shared photo.

        Location signals agree for every flat in one building, so they cannot tell
        two flats apart; only the galleries can.
        """
        rightmove = make_listing(
            PropertySource.RIGHTMOVE,
            "rm1",
            price_pcm=1750,
            postcode="E3 4RB",
            latitude=51.5310,
            longitude=-0.0240,
            address="15 Copperfield Road, London",
        )
        openrent = make_listing(
            PropertySource.OPENRENT,
            "or1",
            price_pcm=1750,
            postcode="E3 4RB",
            latitude=51.5310,
            longitude=-0.0240,
            address="15 Copperfield Road, London",
        )
        items = [as_aggregate(rightmove), as_aggregate(openrent)]
        image_hashes = {
            "rightmove:rm1": ["1111111111111111", "2222222222222222", "3333333333333333"],
            "openrent:or1": ["aaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbb", "cccccccccccccccc"],
        }
        score = calculate_match_score(rightmove, openrent, image_hashes)
        assert score.confidence is MatchConfidence.HIGH
        assert score.image_hash == 0
        _assert_location_alone_groups(items)

        groups = _group_items_greedy(items, image_hashes)

        assert [len(group) for group in groups] == [1, 1]

    async def test_empty_hash_lists_count_as_galleries_without_a_shared_photo(self) -> None:
        """Hashing was attempted for both listings but produced no hashes."""
        items = _same_building_pair()
        _assert_location_alone_groups(items)

        groups = _group_items_greedy(items, {"rightmove:rm1": [], "zoopla:zp1": []})

        assert [len(group) for group in groups] == [1, 1]

    async def test_missing_galleries_keep_a_location_match(self) -> None:
        """No galleries means no evidence either way, so the match stands."""
        groups = _group_items_greedy(_same_building_pair(), {})

        assert [len(group) for group in groups] == [2]

    async def test_gallery_on_one_side_only_keeps_a_location_match(self) -> None:
        groups = _group_items_greedy(_same_building_pair(), {"rightmove:rm1": ["abcdef1234567890"]})

        assert [len(group) for group in groups] == [2]

    async def test_one_shared_photo_keeps_a_location_match(self) -> None:
        shared = "abcdef1234567890"
        image_hashes = {
            "rightmove:rm1": [shared, "0000000000000000"],
            "zoopla:zp1": [shared, "ffffffffffffffff"],
        }

        groups = _group_items_greedy(_same_building_pair(), image_hashes)

        assert [len(group) for group in groups] == [2]


# Outcode-only listings modelled on a real pair that only gallery images could join:
# street (20) + outcode (10) + rent (15) = 45 points without images.
APEX_ONTHEMARKET = make_listing(
    PropertySource.ONTHEMARKET,
    "18751817",
    address="Apex Gardens, Forster Road, Tottenham",
    postcode="N15",
    latitude=None,
    longitude=None,
)
APEX_RIGHTMOVE = make_listing(
    PropertySource.RIGHTMOVE,
    "172249586",
    address="Apex Gardens, Forster Road, Tottenham",
    postcode="N15",
    latitude=51.53,  # wrong area, so coordinates earn nothing
    longitude=-0.23,
)


def _with_hero(listing: Property, url: str) -> Property:
    return listing.model_copy(update={"image_url": HttpUrl(url)})


class TestGalleryHashAcquisition:
    """The deduplicator hashes cached galleries, falling back to hero thumbnails."""

    async def test_shared_cached_photos_merge_an_outcode_only_pair(self, tmp_path: Path) -> None:
        photos = [make_solid_jpeg((100, 100, 100)), make_checkerboard_jpeg()]
        _cache_gallery(tmp_path, APEX_ONTHEMARKET.unique_id, photos)
        _cache_gallery(tmp_path, APEX_RIGHTMOVE.unique_id, photos)
        deduplicator = Deduplicator(
            enable_cross_platform=True, enable_image_hashing=True, data_dir=str(tmp_path)
        )

        result = await deduplicator.deduplicate_merged_async(
            [as_aggregate(APEX_ONTHEMARKET), as_aggregate(APEX_RIGHTMOVE)]
        )

        assert len(result) == 1
        assert set(result[0].sources) == {PropertySource.ONTHEMARKET, PropertySource.RIGHTMOVE}

    async def test_distinct_cached_photos_leave_an_outcode_only_pair_apart(
        self, tmp_path: Path
    ) -> None:
        _cache_gallery(tmp_path, APEX_ONTHEMARKET.unique_id, [make_solid_jpeg((255, 0, 0))])
        _cache_gallery(tmp_path, APEX_RIGHTMOVE.unique_id, [make_checkerboard_jpeg()])
        deduplicator = Deduplicator(
            enable_cross_platform=True, enable_image_hashing=True, data_dir=str(tmp_path)
        )

        result = await deduplicator.deduplicate_merged_async(
            [as_aggregate(APEX_ONTHEMARKET), as_aggregate(APEX_RIGHTMOVE)]
        )

        assert len(result) == 2

    async def test_outcode_only_pair_without_any_image_stays_apart(self, tmp_path: Path) -> None:
        deduplicator = Deduplicator(
            enable_cross_platform=True, enable_image_hashing=True, data_dir=str(tmp_path)
        )

        result = await deduplicator.deduplicate_merged_async(
            [as_aggregate(APEX_ONTHEMARKET), as_aggregate(APEX_RIGHTMOVE)]
        )

        assert len(result) == 2

    async def test_disabled_image_hashing_ignores_shared_cached_photos(
        self, tmp_path: Path
    ) -> None:
        photos = [make_solid_jpeg((100, 100, 100)), make_checkerboard_jpeg()]
        _cache_gallery(tmp_path, APEX_ONTHEMARKET.unique_id, photos)
        _cache_gallery(tmp_path, APEX_RIGHTMOVE.unique_id, photos)
        deduplicator = Deduplicator(
            enable_cross_platform=True, enable_image_hashing=False, data_dir=str(tmp_path)
        )

        result = await deduplicator.deduplicate_merged_async(
            [as_aggregate(APEX_ONTHEMARKET), as_aggregate(APEX_RIGHTMOVE)]
        )

        assert len(result) == 2

    async def test_matching_hero_thumbnails_merge_listings_without_cached_galleries(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        requested: list[str] = []

        async def fetch_hero_hashes(properties: list[Property]) -> dict[str, str]:
            requested.extend(prop.unique_id for prop in properties)
            return {prop.unique_id: "abcdef1234567890" for prop in properties}

        monkeypatch.setattr(deduplication, "fetch_image_hashes_batch", fetch_hero_hashes)
        onthemarket = _with_hero(APEX_ONTHEMARKET, "https://media.onthemarket.com/hero.jpg")
        rightmove = _with_hero(APEX_RIGHTMOVE, "https://media.rightmove.co.uk/hero.jpg")
        deduplicator = Deduplicator(enable_cross_platform=True, enable_image_hashing=True)

        result = await deduplicator.deduplicate_merged_async(
            [as_aggregate(onthemarket), as_aggregate(rightmove)]
        )

        assert sorted(requested) == sorted([onthemarket.unique_id, rightmove.unique_id])
        assert len(result) == 1
        assert set(result[0].sources) == {PropertySource.ONTHEMARKET, PropertySource.RIGHTMOVE}

    async def test_hero_fallback_fetches_only_uncached_candidates_with_a_hero_image(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Of four listings, only the Rightmove one needs a hero thumbnail.

        OnTheMarket has a cached gallery, the Zoopla listing in the same block has
        no hero image, and the OpenRent listing has no other listing to match.
        """
        requested: list[str] = []

        async def fetch_hero_hashes(properties: list[Property]) -> dict[str, str]:
            requested.extend(prop.unique_id for prop in properties)
            return {}

        monkeypatch.setattr(deduplication, "fetch_image_hashes_batch", fetch_hero_hashes)
        _cache_gallery(tmp_path, APEX_ONTHEMARKET.unique_id, [make_solid_jpeg((100, 100, 100))])
        rightmove = _with_hero(APEX_RIGHTMOVE, "https://media.rightmove.co.uk/hero.jpg")
        zoopla_without_hero = make_listing(PropertySource.ZOOPLA, "zp-no-hero", postcode="N15")
        lone_openrent = _with_hero(
            make_listing(PropertySource.OPENRENT, "or-lone", postcode="E8 3RH"),
            "https://media.openrent.co.uk/hero.jpg",
        )
        deduplicator = Deduplicator(
            enable_cross_platform=True, enable_image_hashing=True, data_dir=str(tmp_path)
        )

        await deduplicator.deduplicate_merged_async(
            [
                as_aggregate(APEX_ONTHEMARKET),
                as_aggregate(rightmove),
                as_aggregate(zoopla_without_hero),
                as_aggregate(lone_openrent),
            ]
        )

        assert requested == [rightmove.unique_id]

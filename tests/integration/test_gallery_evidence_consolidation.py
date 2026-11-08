"""Cached gallery images decide whether a candidate joins a stored anchor.

Boundary: images in the on-disk cache, perceptual hashing of both galleries, and
consolidation against the anchor stored by an earlier run. Location signals are
identical for flats in one building, so only the galleries can separate them.
The grouping rule itself is tested with synthetic hashes in
``tests/test_matching/test_image_evidence.py``.
"""

import io
from collections.abc import Callable
from pathlib import Path

from PIL import Image, ImageDraw
from pydantic import SecretStr

from home_finder.config import Settings
from home_finder.db import PropertyStorage
from home_finder.matching.deduplication import Deduplicator
from home_finder.models import Property, PropertyQualityAnalysis, PropertySource
from home_finder.pipeline.candidates import _consolidate_and_admit
from home_finder.pipeline.matching import consolidate_candidates
from home_finder.utils.image_cache import get_cache_dir, save_image_bytes
from tests.support.matching import as_aggregate
from tests.support.pipeline_run import save_notified_anchor, stored_sources


def _jpeg(image: Image.Image) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format="JPEG")
    return buf.getvalue()


def _solid(colour: str) -> bytes:
    return _jpeg(Image.new("RGB", (100, 100), colour))


def _checkerboard(cell: int) -> bytes:
    # pHash tolerates colour changes, so a different flat needs a different structure.
    image = Image.new("RGB", (100, 100), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    for x in range(0, 100, cell):
        for y in range(0, 100, cell):
            if (x + y) % (cell * 2) == 0:
                draw.rectangle([x, y, x + cell, y + cell], fill=(0, 0, 0))
    return _jpeg(image)


# Solid colours hash alike, so two caches of these read as the same flat's photos.
SAME_FLAT_GALLERY = [_solid("red"), _solid("blue"), _solid("green")]
OTHER_FLAT_GALLERY = [_checkerboard(5), _checkerboard(10), _checkerboard(15)]


def _cache_gallery(data_dir: Path, unique_id: str, gallery: list[bytes]) -> None:
    cache_dir = get_cache_dir(str(data_dir), unique_id)
    for index, image in enumerate(gallery):
        save_image_bytes(cache_dir / f"gallery_{index:03d}_{index:08x}.jpg", image)


def _same_building(make_property: Callable[..., Property]) -> tuple[Property, Property]:
    """Two portals' listings with identical postcode, coordinates, price and size."""
    anchor = make_property(source=PropertySource.OPENRENT, price_pcm=1800, bedrooms=2)
    candidate = make_property(source=PropertySource.ZOOPLA, price_pcm=1800, bedrooms=2)
    return anchor, candidate


def _hashing_matcher(data_dir: Path) -> Deduplicator:
    return Deduplicator(
        enable_cross_platform=True, enable_image_hashing=True, data_dir=str(data_dir)
    )


async def test_different_galleries_keep_a_same_building_flat_separate(
    storage: PropertyStorage,
    make_property: Callable[..., Property],
    make_quality_analysis: Callable[..., PropertyQualityAnalysis],
    tmp_path: Path,
):
    anchor, candidate = _same_building(make_property)
    await save_notified_anchor(storage, anchor, make_quality_analysis())
    _cache_gallery(tmp_path, anchor.unique_id, SAME_FLAT_GALLERY)
    _cache_gallery(tmp_path, candidate.unique_id, OTHER_FLAT_GALLERY)

    result = await consolidate_candidates(
        _hashing_matcher(tmp_path),
        [as_aggregate(candidate)],
        storage,
        set(),
        data_dir=str(tmp_path),
    )

    assert result.anchors_updated == 0
    assert [home.unique_id for home in result.unmatched_candidates] == [candidate.unique_id]


async def test_matching_galleries_merge_the_same_flat_from_another_portal(
    storage: PropertyStorage,
    make_property: Callable[..., Property],
    make_quality_analysis: Callable[..., PropertyQualityAnalysis],
    tmp_path: Path,
):
    anchor, candidate = _same_building(make_property)
    await save_notified_anchor(storage, anchor, make_quality_analysis())
    _cache_gallery(tmp_path, anchor.unique_id, SAME_FLAT_GALLERY)
    _cache_gallery(tmp_path, candidate.unique_id, SAME_FLAT_GALLERY)

    result = await consolidate_candidates(
        _hashing_matcher(tmp_path),
        [as_aggregate(candidate)],
        storage,
        set(),
        data_dir=str(tmp_path),
    )

    assert result.anchors_updated == 1
    assert result.unmatched_candidates == []
    assert await stored_sources(storage, anchor.unique_id) == {"openrent", "zoopla"}


async def test_admission_compares_galleries_even_with_hash_matching_disabled(
    storage: PropertyStorage,
    make_property: Callable[..., Property],
    make_quality_analysis: Callable[..., PropertyQualityAnalysis],
    tmp_path: Path,
):
    """Disabling hash matching for speed must not merge different flats across runs."""
    settings = Settings(
        telegram_bot_token=SecretStr("fake:token"),
        telegram_chat_id=0,
        # data_dir, and so the image cache, is the database's directory.
        database_path=str(tmp_path / "home_finder.db"),
        search_areas="e8",
        min_price=1500,
        max_price=2500,
        min_bedrooms=1,
        max_bedrooms=2,
        enable_image_hash_matching=False,
        require_floorplan=False,
    )
    anchor, candidate = _same_building(make_property)
    await save_notified_anchor(storage, anchor, make_quality_analysis())
    _cache_gallery(tmp_path, anchor.unique_id, SAME_FLAT_GALLERY)
    _cache_gallery(tmp_path, candidate.unique_id, OTHER_FLAT_GALLERY)

    admission = await _consolidate_and_admit([as_aggregate(candidate)], storage, settings, set())

    assert admission.anchors_updated == 0
    assert [home.unique_id for home in admission.candidates] == [candidate.unique_id]


async def test_without_cached_galleries_location_signals_still_merge(
    storage: PropertyStorage,
    make_property: Callable[..., Property],
    make_quality_analysis: Callable[..., PropertyQualityAnalysis],
    tmp_path: Path,
):
    """Anchors stored before image caching existed have no cached gallery to compare."""
    anchor, candidate = _same_building(make_property)
    await save_notified_anchor(storage, anchor, make_quality_analysis())

    result = await consolidate_candidates(
        _hashing_matcher(tmp_path),
        [as_aggregate(candidate)],
        storage,
        set(),
        data_dir=str(tmp_path),
    )

    assert result.anchors_updated == 1
    assert result.unmatched_candidates == []
    assert await stored_sources(storage, anchor.unique_id) == {"openrent", "zoopla"}

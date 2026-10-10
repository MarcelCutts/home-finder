"""Conservative migration and round-trip contracts for floorplan evidence."""

from unittest.mock import patch

import pytest
from pydantic import HttpUrl

from home_finder.db.migrations import MIGRATIONS
from home_finder.db.storage import PropertyStorage
from home_finder.filters.deduplication import _select_best_floorplan
from home_finder.filters.detail_enrichment import _load_cached_property, filter_by_floorplan
from home_finder.models import PropertyImage, PropertySource


def _image(origin="unknown", role="floorplan", source=PropertySource.RIGHTMOVE):
    return PropertyImage(
        url=HttpUrl("https://example.com/legacy-image.jpg"),
        source=source,
        image_type=role,
        floorplan_origin=origin,
    )


async def test_legacy_upgrade_preserves_images_and_owner_state(
    tmp_path, make_merged_property, make_quality_analysis
):
    path = str(tmp_path / "legacy.db")
    storage = PropertyStorage(path)
    with patch("home_finder.db.migrations.MIGRATIONS", MIGRATIONS[:9]):
        await storage.initialize()
    merged = make_merged_property(sources=(PropertySource.RIGHTMOVE,))
    await storage.save_merged_property(merged)
    await storage.save_quality_analysis(merged.unique_id, make_quality_analysis())
    await storage.mark_notified(merged.unique_id)
    async with storage._transaction() as conn:
        await conn.execute(
            "INSERT INTO property_images(property_unique_id,source,url,image_type) VALUES(?,?,?,?)",
            (merged.unique_id, "rightmove", str(_image().url), "floorplan"),
        )
        await conn.execute(
            "UPDATE properties SET user_status='interested' WHERE unique_id=?", (merged.unique_id,)
        )
    await storage.close()

    reopened = PropertyStorage(path)
    await reopened.initialize()
    try:
        images = await reopened.get_property_images(merged.unique_id)
        assert images == [_image()]
        loaded = await _load_cached_property(merged, reopened)
        assert loaded.floorplan == _image()
        assert filter_by_floorplan([loaded]) == []
        detail = await reopened.web.get_property_detail(merged.unique_id)
        assert detail["floorplan_images"] == images
        assert detail["user_status"] == "interested"
        assert detail["notification_status"] == "sent"
        assert detail["quality_analysis"].overall_rating == 4
        assert await reopened.get_quality_analysis(merged.unique_id) is not None
    finally:
        await reopened.close()


@pytest.mark.parametrize("previous_role", ["gallery", "floorplan"])
async def test_fresh_source_confirmation_upgrades_same_url_and_survives_reload(
    tmp_path, make_merged_property, previous_role
):
    storage = PropertyStorage(str(tmp_path / "properties.db"))
    await storage.initialize()
    try:
        merged = make_merged_property(sources=(PropertySource.RIGHTMOVE,))
        await storage.save_merged_property(merged)
        await storage.save_property_images(merged.unique_id, [_image(role=previous_role)])
        declared = _image("source_declared")
        await storage.save_property_images(merged.unique_id, [declared])
        # An older caller/cache must not erase the source confirmation.
        await storage.save_property_images(merged.unique_id, [_image()])
        await storage.save_property_images(merged.unique_id, [_image(role="gallery")])
        assert await storage.get_property_images(merged.unique_id) == [declared]
        loaded = await _load_cached_property(merged, storage)
        assert loaded.floorplan == declared
        assert filter_by_floorplan([loaded]) == [loaded]
        images, _, _ = await storage.get_property_images_and_row(merged.unique_id)
        assert images == [declared]
    finally:
        await storage.close()


async def test_source_refresh_can_restore_legacy_image_to_gallery(tmp_path, make_merged_property):
    storage = PropertyStorage(str(tmp_path / "properties.db"))
    await storage.initialize()
    try:
        merged = make_merged_property(sources=(PropertySource.RIGHTMOVE,))
        await storage.save_merged_property(merged)
        await storage.save_property_images(merged.unique_id, [_image()])
        gallery = _image(role="gallery")
        await storage.save_property_images(merged.unique_id, [gallery])
        loaded = await _load_cached_property(merged, storage)
        assert loaded.floorplan is None
        assert loaded.images == (gallery,)
    finally:
        await storage.close()


def test_declared_floorplan_wins_over_unknown_higher_priority_source(make_merged_property):
    unknown = make_merged_property(
        sources=(PropertySource.RIGHTMOVE,), floorplan=_image(source=PropertySource.RIGHTMOVE)
    )
    declared = make_merged_property(
        sources=(PropertySource.OPENRENT,),
        floorplan=_image("source_declared", source=PropertySource.OPENRENT),
    )
    assert _select_best_floorplan([unknown, declared]) == declared.floorplan


def test_unknown_plan_does_not_bypass_gate_but_existing_photo_exemption_remains(
    make_merged_property,
):
    merged = make_merged_property(
        sources=(PropertySource.RIGHTMOVE,), floorplan=_image(), images=(_image(role="gallery"),)
    )
    assert filter_by_floorplan([merged]) == []
    assert filter_by_floorplan([merged], min_gallery_for_photo_inference=1) == [merged]

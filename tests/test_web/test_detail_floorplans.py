"""Floorplans on the detail page: the plan is a tile among the photos, and repeated
plans are set aside, never hidden."""

import io
from pathlib import Path

import pytest
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw
from pydantic import HttpUrl

from home_finder.config import Settings
from home_finder.db.storage import PropertyStorage
from home_finder.models import MergedProperty, PropertyImage, PropertySource
from home_finder.utils.image_cache import (
    get_cache_dir,
    safe_dir_name,
    save_image_bytes,
    url_to_filename,
)
from home_finder.utils.image_hash import hash_from_disk
from home_finder.web import routes


def _floorplan_png(*, rooms: list[tuple[int, int, int, int]], size: tuple[int, int]) -> bytes:
    """Draw a plan of black-outlined rooms, scaled to the requested size."""
    plan = Image.new("L", (400, 300), 255)
    draw = ImageDraw.Draw(plan)
    for room in rooms:
        draw.rectangle(room, outline=0, width=8)
    buf = io.BytesIO()
    plan.resize(size).save(buf, format="PNG")
    return buf.getvalue()


async def test_repeated_plan_from_another_portal_is_shown_once(
    client: TestClient,
    settings: Settings,
    storage: PropertyStorage,
    merged_a: MergedProperty,
) -> None:
    flat = [(10, 10, 390, 150), (10, 150, 200, 290), (200, 150, 390, 290)]
    upper_floor = [(10, 10, 130, 290), (130, 10, 390, 120), (130, 120, 390, 290)]
    plans = {
        # The same drawing from two portals at different resolutions.
        "https://example.com/zoopla-plan.png": (PropertySource.ZOOPLA, flat, (800, 600)),
        "https://example.com/rightmove-plan.png": (PropertySource.RIGHTMOVE, flat, (400, 300)),
        "https://example.com/upper-floor.png": (
            PropertySource.RIGHTMOVE,
            upper_floor,
            (400, 300),
        ),
    }
    cache_dir = get_cache_dir(settings.data_dir, merged_a.unique_id)
    images = []
    for index, (url, (source, rooms, size)) in enumerate(plans.items()):
        save_image_bytes(
            cache_dir / url_to_filename(url, "floorplan", index),
            _floorplan_png(rooms=rooms, size=size),
        )
        images.append(
            PropertyImage(
                source=source,
                url=HttpUrl(url),
                image_type="floorplan",
                floorplan_origin=(
                    "source_declared" if source == PropertySource.RIGHTMOVE else "unknown"
                ),
            )
        )
    await storage.save_merged_property(merged_a)
    await storage.save_property_images(merged_a.unique_id, images)

    soup = BeautifulSoup(client.get(f"/property/{merged_a.unique_id}").text, "html.parser")
    safe_id = safe_dir_name(merged_a.unique_id)
    distinct = [
        f"/images/{safe_id}/{url_to_filename(url, 'floorplan', index)}"
        for index, url in enumerate(plans)
        if index != 0
    ]
    repeat = f"/images/{safe_id}/{url_to_filename(next(iter(plans)), 'floorplan', 0)}"
    # The first distinct plan is the last tile of the photo strip and opens the lightbox.
    tile = soup.select("#section-photos .hero-thumbs > .hero-thumb")[-1]
    assert tile["id"] == "section-floorplan"
    assert [img["src"] for img in tile.select("img")] == distinct[:1]
    assert tile.a["data-lightbox"] == "floorplan"
    # Its source, and the way to the other plans, sit under the photos.
    notes = soup.select_one(".floorplan-notes")
    caption = notes.select_one(f"#{tile.a['aria-describedby']}")
    assert "See all 3 plans" in caption.get_text()
    variants = notes.select_one("details.floorplan-repeats")
    assert variants.summary.get_text() == "Additional floorplan versions (1)"
    assert "label wasn't recorded" in variants.get_text()
    assert variants.img.parent["data-lightbox"] == "floorplan"
    assert soup.select_one("#gallery-tab-floorplan").get_text() == "Floorplan 3"
    assert [img["src"] for img in soup.select(".gallery-floorplan-img")] == [*distinct, repeat]


async def test_uncached_plan_from_another_portal_is_set_aside_not_hidden(
    client: TestClient,
    settings: Settings,
    storage: PropertyStorage,
    merged_a: MergedProperty,
) -> None:
    """Without a local copy there is nothing to compare, so it is collapsed instead."""
    cached_url = "https://example.com/rightmove-plan.png"
    save_image_bytes(
        get_cache_dir(settings.data_dir, merged_a.unique_id)
        / url_to_filename(cached_url, "floorplan", 0),
        _floorplan_png(rooms=[(10, 10, 390, 290)], size=(400, 300)),
    )
    same_site_url = "https://example.com/rightmove-upper.png"
    other_site_url = "https://example.com/zoopla-plan.png"
    await storage.save_merged_property(merged_a)
    await storage.save_property_images(
        merged_a.unique_id,
        [
            PropertyImage(source=source, url=HttpUrl(url), image_type="floorplan")
            for source, url in [
                (PropertySource.ZOOPLA, other_site_url),
                (PropertySource.RIGHTMOVE, cached_url),
                (PropertySource.RIGHTMOVE, same_site_url),
            ]
        ],
    )

    soup = BeautifulSoup(client.get(f"/property/{merged_a.unique_id}").text, "html.parser")
    assert [img["src"] for img in soup.select(".gallery-floorplan-img")] == [
        f"/images/{safe_dir_name(merged_a.unique_id)}/"
        f"{url_to_filename(cached_url, 'floorplan', 0)}",
        same_site_url,
        other_site_url,
    ]
    repeats = soup.select_one(".floorplan-notes details.floorplan-repeats")
    assert repeats.summary.get_text() == "Additional floorplan versions (1)"
    assert [img["src"] for img in repeats.select("img")] == [other_site_url]
    assert repeats.img.parent["data-lightbox"] == "floorplan"


def test_changed_dimension_with_identical_perceptual_hash_is_retained(
    settings: Settings, merged_a: MergedProperty
) -> None:
    """A text change can leave the pHash identical, even at distance zero."""
    images = []
    hashes = []
    encoded = []
    for index, dimension in enumerate(["4.2m x 3.1m", "3.2m x 3.1m"]):
        plan = Image.new("L", (800, 600), 255)
        draw = ImageDraw.Draw(plan)
        for room in [(20, 20, 780, 300), (20, 300, 400, 580), (400, 300, 780, 580)]:
            draw.rectangle(room, outline=0, width=8)
        draw.text((80, 100), dimension, fill=0)
        buf = io.BytesIO()
        plan.save(buf, format="PNG")
        encoded.append(buf.getvalue())
        url = f"https://example.com/plan-{index}.png"
        path = get_cache_dir(settings.data_dir, merged_a.unique_id) / url_to_filename(
            url, "floorplan", index
        )
        save_image_bytes(path, encoded[-1])
        hashes.append(hash_from_disk(path))
        images.append(
            PropertyImage(
                source=[PropertySource.RIGHTMOVE, PropertySource.ZOOPLA][index],
                url=HttpUrl(url),
                image_type="floorplan",
                floorplan_origin="source_declared",
            )
        )

    assert encoded[0] != encoded[1]
    assert hashes[0] is not None and hashes[0] == hashes[1]
    shown, variants = routes._split_floorplans(settings.data_dir, merged_a.unique_id, images)
    assert shown == images[:1]
    assert variants == images[1:]


def test_cache_removed_during_lookup_preserves_plan(
    settings: Settings, merged_a: MergedProperty, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing_path = Path(settings.data_dir) / "evicted.png"
    monkeypatch.setattr(routes, "find_cached_file", lambda *args: missing_path)
    image = PropertyImage(
        source=PropertySource.RIGHTMOVE,
        url=HttpUrl("https://example.com/plan.png"),
        image_type="floorplan",
    )
    assert routes._split_floorplans(settings.data_dir, merged_a.unique_id, [image]) == (
        [image],
        [],
    )

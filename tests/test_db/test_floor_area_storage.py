"""A home's listed floor area and its source survive storage and reach the dashboard query."""

import pytest
from pydantic import HttpUrl

from home_finder.web.filters import PropertyFilter


async def _stored_area(storage, unique_id):
    async with storage._read_connection() as conn:
        cursor = await conn.execute(
            "SELECT floor_area_sqm, floor_area_source FROM properties WHERE unique_id = ?",
            (unique_id,),
        )
        row = await cursor.fetchone()
    assert row is not None
    return tuple(row)


@pytest.mark.parametrize(
    ("floor_area_sqm", "floor_area_source"),
    [(69.7, "zoopla"), (None, None)],
    ids=["known", "unknown"],
)
async def test_floor_area_and_source_are_saved(
    storage, make_merged_property, floor_area_sqm, floor_area_source
):
    home = make_merged_property(floor_area_sqm=floor_area_sqm, floor_area_source=floor_area_source)

    await storage.save_merged_property(home)

    assert await _stored_area(storage, home.unique_id) == (floor_area_sqm, floor_area_source)


async def test_dashboard_page_includes_floor_area(storage, make_merged_property):
    # The dashboard query only lists homes with an image.
    home = make_merged_property(
        image_url=HttpUrl("https://example.com/img.jpg"),
        floor_area_sqm=60.4,
        floor_area_source="rightmove",
    )
    await storage.save_merged_property(home)

    properties, total = await storage.web.get_properties_paginated(
        PropertyFilter(), sort="newest", page=1, per_page=10
    )

    assert total == 1
    assert (properties[0]["floor_area_sqm"], properties[0]["floor_area_source"]) == (
        60.4,
        "rightmove",
    )

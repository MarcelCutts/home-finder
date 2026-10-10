"""Image evidence writes shared by individual and bulk property transactions."""

from collections.abc import Sequence

import aiosqlite

from home_finder.models import PropertyImage


async def upsert_property_images(
    conn: aiosqlite.Connection, unique_id: str, images: Sequence[PropertyImage]
) -> None:
    """Preserve source declarations inside the caller's existing transaction."""
    if not images:
        return
    await conn.executemany(
        """INSERT INTO property_images
           (property_unique_id, source, url, image_type, floorplan_origin)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(property_unique_id, url) DO UPDATE SET
               source = excluded.source,
               image_type = excluded.image_type,
               floorplan_origin = excluded.floorplan_origin
           WHERE (excluded.image_type = 'floorplan'
                  AND excluded.floorplan_origin = 'source_declared')
              OR (property_images.image_type = 'floorplan'
                  AND property_images.floorplan_origin = 'unknown'
                  AND excluded.image_type = 'gallery')""",
        [
            (unique_id, img.source.value, str(img.url), img.image_type, img.floorplan_origin)
            for img in images
        ],
    )

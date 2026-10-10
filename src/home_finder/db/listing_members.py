"""Load every current listing representation in the caller's consistent snapshot."""

from __future__ import annotations

import aiosqlite

from home_finder.db.listing_text import text_from_source_row
from home_finder.models.listing_member import ListingMember


def member_from_row(row: aiosqlite.Row) -> ListingMember:
    return ListingMember(
        source=row["source"],
        source_unique_id=row["unique_id"],
        source_id=row["source_id"],
        url=row["url"],
        price_pcm=row["price_pcm"],
        text=text_from_source_row(row),
        is_backfilled=bool(row["is_backfilled"]),
        is_off_market=bool(row["is_off_market"]) if row["is_off_market"] is not None else None,
    )


async def load_source_rows(
    conn: aiosqlite.Connection, property_ids: list[str]
) -> dict[str, list[aiosqlite.Row]]:
    result: dict[str, list[aiosqlite.Row]] = {key: [] for key in property_ids}
    for start in range(0, len(property_ids), 100):
        batch = property_ids[start : start + 100]
        placeholders = ",".join("?" for _ in batch)
        async with conn.execute(
            f"SELECT * FROM source_listings WHERE merged_id IN ({placeholders}) "
            "ORDER BY source, unique_id",
            batch,
        ) as cursor:
            for row in await cursor.fetchall():
                result[row["merged_id"]].append(row)
    return result

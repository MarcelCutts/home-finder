"""Shared source_listings operations used by both storage and pipeline_repo."""

from __future__ import annotations

import aiosqlite
from pydantic import HttpUrl

from home_finder.db.membership_ops import link_memberships
from home_finder.models import PropertySource


async def link_source_listings_by_url(
    conn: aiosqlite.Connection,
    merged_id: str,
    source_urls: dict[PropertySource, HttpUrl],
) -> None:
    """Link unowned URL matches through the ownership journal.

    When in-run dedup merges two new properties, only the canonical
    source_listing gets ``merged_id`` set by the normal upsert.  This
    helper catches the non-canonical sources by matching their URL.
    """
    urls = [str(url) for url in source_urls.values()]
    if not urls:
        return
    placeholders = ",".join("?" * len(urls))
    async with conn.execute(
        f"SELECT unique_id FROM source_listings "
        f"WHERE url IN ({placeholders}) AND merged_id IS NULL ORDER BY unique_id",
        urls,
    ) as cursor:
        ids = [row[0] for row in await cursor.fetchall()]
    await link_memberships(conn, [(uid, merged_id) for uid in ids], reason="in_run_url_linkage")

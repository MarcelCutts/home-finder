"""Shared source_listings operations used by both storage and pipeline_repo."""

from __future__ import annotations

import aiosqlite
from pydantic import HttpUrl

from home_finder.models import PropertySource


async def link_source_listings_by_url(
    conn: aiosqlite.Connection,
    merged_id: str,
    source_urls: dict[PropertySource, HttpUrl],
) -> None:
    """Link unlinked source_listings to golden record by URL match.

    When in-run dedup merges two new properties, only the canonical
    source_listing gets ``merged_id`` set by the normal upsert.  This
    helper catches the non-canonical sources by matching their URL.
    """
    urls = [str(url) for url in source_urls.values()]
    if not urls:
        return
    placeholders = ",".join("?" * len(urls))
    await conn.execute(
        f"UPDATE source_listings SET merged_id = ? "
        f"WHERE url IN ({placeholders}) AND merged_id IS NULL",
        [merged_id, *urls],
    )

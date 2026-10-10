"""Shared source_listings operations used by both storage and pipeline_repo."""

from __future__ import annotations

import aiosqlite
from pydantic import HttpUrl

from home_finder.db.merge_safety import MergeBlockedError, ensure_staging_merge
from home_finder.models import PropertySource


async def link_source_listings_by_url(
    conn: aiosqlite.Connection,
    merged_id: str,
    source_urls: dict[PropertySource, HttpUrl],
    *,
    absorb_pending: bool = False,
) -> None:
    """Link unlinked source_listings to golden record by URL match.

    When in-run dedup merges two new properties, only the canonical
    source_listing gets ``merged_id`` set by the normal upsert.  This
    helper catches the non-canonical sources by matching their URL.
    """
    requested = {(source.value, str(url)) for source, url in source_urls.items()}
    if not requested:
        return
    donors: set[str] = set()
    if absorb_pending:
        for source, url in requested:
            async with conn.execute(
                "SELECT DISTINCT merged_id FROM source_listings "
                "WHERE source = ? AND url = ? AND merged_id IS NOT NULL AND merged_id != ?",
                (source, url, merged_id),
            ) as cur:
                donors.update(row[0] for row in await cur.fetchall())
        if donors:
            await ensure_staging_merge(conn, merged_id, sorted(donors))
            # A same-platform collision can hide one of a donor's source URLs.
            # Refuse that partial group instead of retiring unrepresented work.
            for donor in sorted(donors):
                async with conn.execute(
                    "SELECT source, url FROM source_listings WHERE merged_id = ?", (donor,)
                ) as cur:
                    if any((row[0], row[1]) not in requested for row in await cur.fetchall()):
                        raise MergeBlockedError({donor: ["unrepresented_source_listing"]})
            await conn.executemany(
                "UPDATE source_listings SET merged_id = ? WHERE merged_id = ?",
                [(merged_id, donor) for donor in sorted(donors)],
            )
            await conn.executemany(
                "DELETE FROM properties WHERE unique_id = ?", [(donor,) for donor in sorted(donors)]
            )
    await conn.executemany(
        "UPDATE source_listings SET merged_id = ? "
        "WHERE source = ? AND url = ? AND merged_id IS NULL",
        [(merged_id, source, url) for source, url in sorted(requested)],
    )

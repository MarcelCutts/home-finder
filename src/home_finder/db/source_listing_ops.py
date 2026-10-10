"""Shared source_listings operations used by both storage and pipeline_repo."""

from __future__ import annotations

import aiosqlite
from pydantic import HttpUrl

from home_finder.db.home_groups import read_routes
from home_finder.db.homes import ensure_home
from home_finder.db.membership_ops import link_memberships
from home_finder.db.merge_safety import MergeBlockedError, ensure_staging_merge
from home_finder.models import ListingMember, PropertySource
from home_finder.models.home_group import HomeRoute


async def link_source_listings_by_url(
    conn: aiosqlite.Connection,
    merged_id: str,
    source_urls: dict[PropertySource, HttpUrl],
    *,
    members: tuple[ListingMember, ...] | None = None,
    absorb_pending: bool = False,
) -> None:
    """Link unowned URL matches through the ownership journal.

    When in-run dedup merges two new properties, only the canonical
    source_listing gets ``merged_id`` set by the normal upsert.  This
    helper catches the non-canonical sources by matching their URL.
    """
    if members is not None:
        ids = []
        donors: set[str] = set()
        for member in members:
            if member.source_unique_id is None:
                # A legacy URL is evidence, not authority to claim every matching row.
                continue
            async with conn.execute(
                "SELECT merged_id, url FROM source_listings WHERE unique_id = ? "
                "AND source = ? AND source_id = ?",
                (member.source_unique_id, member.source.value, member.source_id),
            ) as cursor:
                row = await cursor.fetchone()
            if row is None:
                raise ValueError("A listing member must be ingested before linking it")
            if not member.has_equivalent_url(row["url"]):
                raise ValueError("Listing member URL conflicts with its retained identity")
            if row[0] is None:
                ids.append(member.source_unique_id)
            elif row[0] != merged_id:
                # Never let a routine save reclaim a reviewed member from another home.
                if not absorb_pending:
                    raise MergeBlockedError(
                        {member.source_unique_id: ["membership_review_required"]}
                    )
                donors.add(row[0])
                ids.append(member.source_unique_id)
        routes = {}
        if donors:
            # Two enrichment retries may become one new group. Apply the same
            # preservation gate as cross-run absorption, then retire the donor
            # routes atomically so they cannot return in the next retry batch.
            await ensure_staging_merge(conn, merged_id, sorted(donors))
            member_ids = {m.source_unique_id for m in members}
            donor_homes = set()
            for donor in sorted(donors):
                async with conn.execute(
                    "SELECT unique_id FROM source_listings WHERE merged_id = ?", (donor,)
                ) as cursor:
                    if any(row[0] not in member_ids for row in await cursor.fetchall()):
                        raise MergeBlockedError({donor: ["incomplete_staging_members"]})
                donor_homes.add(await ensure_home(conn, donor))
            target = await ensure_home(conn, merged_id)
            routes = {
                home: HomeRoute(home_id=home, target_home_id=target)
                for home, route in (await read_routes(conn)).items()
                if home in donor_homes or route.target_home_id in donor_homes
            }
        await link_memberships(
            conn,
            [(uid, merged_id) for uid in ids],
            operation="staging_link" if donors else "initial_link",
            reason="exact_member_linkage",
            routes=routes,
        )
        return
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

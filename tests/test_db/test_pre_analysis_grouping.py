"""Saving a matched group before analysis refuses to absorb members it must not retire.

``save_pre_analysis_properties`` retires the stored rows of a group's other
members. A member the owner has acted on, or one that represents a source listing
the group does not, must survive unchanged.
"""

from home_finder.models import PropertySource
from tests.support.matching import as_aggregate


async def test_group_cannot_absorb_a_pending_member_with_owner_state(storage, make_property):
    first = make_property(source=PropertySource.OPENRENT)
    second = make_property(source=PropertySource.ZOOPLA)
    await storage.processing.save_unenriched_property(as_aggregate(first))
    await storage.processing.save_unenriched_property(as_aggregate(second))
    async with storage._transaction() as conn:
        await conn.execute(
            "UPDATE properties SET user_status='interested' WHERE unique_id=?", (second.unique_id,)
        )
    group = as_aggregate(
        first,
        sources=(first.source, second.source),
        source_urls={first.source: first.url, second.source: second.url},
    )

    result = await storage.processing.save_pre_analysis_properties([group], {})

    assert result.accepted == []
    assert "user_state" in result.refused[group.unique_id][second.unique_id]
    assert len(await storage.processing.get_unenriched_properties()) == 2
    assert await storage.processing.get_pending_analysis_properties() == []


async def test_group_cannot_retire_a_member_linked_to_an_unrepresented_listing(
    storage, make_property
):
    first = make_property(source=PropertySource.OPENRENT)
    second = make_property(source=PropertySource.ZOOPLA)
    extra = make_property(source=PropertySource.RIGHTMOVE)
    await storage.processing.save_unenriched_property(as_aggregate(second))
    await storage.upsert_source_listings([first, extra])
    await storage.link_source_listings([(extra.unique_id, second.unique_id)])
    group = as_aggregate(
        first,
        sources=(first.source, second.source),
        source_urls={first.source: first.url, second.source: second.url},
    )

    result = await storage.processing.save_pre_analysis_properties([group], {})

    assert result.accepted == []
    assert result.refused[group.unique_id][second.unique_id] == ["unrepresented_source_listing"]
    assert await storage.get_property(second.unique_id) is not None
    assert await storage.get_property(first.unique_id) is None
    async with storage._read_connection() as conn:
        cursor = await conn.execute(
            "SELECT merged_id FROM source_listings WHERE unique_id=?", (extra.unique_id,)
        )
        assert (await cursor.fetchone())[0] == second.unique_id

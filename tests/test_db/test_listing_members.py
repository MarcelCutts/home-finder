"""Every advertisement keeps its own evidence through grouping and all reload paths."""

import asyncio
from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from home_finder.db import PropertyStorage
from home_finder.db.merge_safety import MergeBlockedError
from home_finder.filters.deduplication import Deduplicator
from home_finder.filters.detail_enrichment import _enrich_single, _load_cached_property
from home_finder.filters.listing_text import select_listing_text
from home_finder.models import ListingMember, ListingTextEvidence, MergedProperty, PropertySource
from home_finder.notifiers.telegram import _build_inline_keyboard, format_merged_property_message
from home_finder.scrapers.detail_fetcher import DetailFetcher, DetailPageData
from tests.test_db.test_membership import REVIEW, snapshot


def explicit_group(props):
    return MergedProperty(
        canonical=props[0],
        members=tuple(ListingMember.from_property(prop) for prop in props),
        sources=tuple(dict.fromkeys(prop.source for prop in props)),
        source_urls={prop.source: prop.url for prop in props},
        min_price=min(prop.price_pcm for prop in props),
        max_price=max(prop.price_pcm for prop in props),
    )


@pytest.fixture
def adverts(make_property):
    return [
        make_property(
            source=PropertySource.RIGHTMOVE,
            source_id=str(identifier),
            url=f"https://www.rightmove.co.uk/properties/{identifier}",
            description=description,
            price_pcm=price,
        )
        for identifier, description, price in (
            (100001, "First advert: gas hob; electricity included", 1900),
            (100002, "Second advert: induction hob; electricity excluded", 1950),
        )
    ]


@pytest.mark.parametrize("loader", ["dedup", "analysis", "unenriched", "reanalysis", "cached"])
async def test_same_portal_members_survive_every_queue_loader(
    tmp_path, adverts, sample_quality_analysis, loader
):
    async with PropertyStorage(str(tmp_path / "members.db")) as db:
        for prop in adverts:
            single = explicit_group([prop])
            if loader == "analysis":
                await db.pipeline.save_pre_analysis_properties([single], {})
            elif loader == "unenriched":
                await db.pipeline.save_unenriched_property(single)
            else:
                await db.save_merged_property(single)
        targets = [await db.homes.resolve(prop.unique_id) for prop in adverts]
        await db.memberships.apply(
            await db.memberships.prepare_merge(targets[0].home_id, (targets[1].home_id,), **REVIEW)
        )
        if loader == "analysis":
            loaded = await db.pipeline.get_pending_analysis_properties()
        elif loader == "unenriched":
            loaded = await db.pipeline.get_unenriched_properties()
        elif loader == "reanalysis":
            await db.save_quality_analysis(adverts[0].unique_id, sample_quality_analysis)
            await db.pipeline.request_reanalysis([adverts[0].unique_id])
            loaded = await db.pipeline.get_reanalysis_queue()
        elif loader == "cached":
            loaded = [await _load_cached_property(explicit_group([adverts[0]]), db)]
        else:
            loaded = await db.get_recent_properties_for_dedup(days=None)
        assert len(loaded) == 1
        current = loaded[0]
        assert {m.source_unique_id for m in current.members} == {p.unique_id for p in adverts}
        assert {str(url) for url in current.listing_urls} == {str(p.url) for p in adverts}
        selected = select_listing_text(current)
        assert selected.policy_version == 3
        assert set(selected.source_hashes) == {p.unique_id for p in adverts}
        assert selected.source_urls == {p.unique_id: str(p.url) for p in adverts}
        assert "[Rightmove 1]" in selected.description
        assert "[Rightmove 2]" in selected.description
        assert all(p.description in selected.description for p in adverts)
        # Neither the old portal cache nor a canonical field establishes member text.
        assert current.text_by_source == {}
        assert (await db.memberships.audit()).valid


@pytest.mark.parametrize("operation", ["split", "reversal"])
async def test_departing_member_cannot_leak_text_or_links_from_a_stale_cache(
    tmp_path, adverts, operation
):
    async with PropertyStorage(str(tmp_path / "members.db")) as db:
        for prop in adverts:
            await db.save_merged_property(explicit_group([prop]))
        targets = [await db.homes.resolve(prop.unique_id) for prop in adverts]
        merge = await db.memberships.prepare_merge(
            targets[0].home_id, (targets[1].home_id,), **REVIEW
        )
        await db.memberships.apply(merge)
        # A current-read consumer must ignore even a deliberately stale legacy cache.
        async with db._transaction() as conn:
            await conn.execute(
                "UPDATE properties SET description = ?, descriptions_json = ?, "
                "source_urls = ? WHERE unique_id = ?",
                (
                    "Stale donor text",
                    '{"rightmove":"Stale donor text"}',
                    '{"rightmove":"https://www.rightmove.co.uk/properties/100002"}',
                    adverts[0].unique_id,
                ),
            )
        decision = (
            await db.memberships.prepare_split(
                targets[0].home_id, {adverts[1].unique_id: targets[1].home_id}, **REVIEW
            )
            if operation == "split"
            else await db.memberships.prepare_reversal(merge.decision_id, **REVIEW)
        )
        await db.memberships.apply(decision)
        loaded = {m.unique_id: m for m in await db.get_recent_properties_for_dedup(days=None)}
        for prop in adverts:
            selected = select_listing_text(loaded[prop.unique_id])
            assert selected.description == prop.description
            assert set(selected.source_urls) == {prop.unique_id}
            assert "Stale donor text" not in selected.description
        before = await snapshot(db)
        assert (await db.memberships.apply(merge)).replayed
        assert await snapshot(db) == before


async def test_enrichment_requests_every_advert_and_persists_text_only_to_its_member(
    tmp_path, adverts
):
    group = explicit_group(adverts)
    received = []

    async def fetch(prop):
        received.append((prop.unique_id, str(prop.url)))
        return DetailPageData(
            description=f"Detail for {prop.source_id}",
            features=[f"Feature for {prop.source_id}"],
            gallery_urls=[f"https://fixture.test/{prop.source_id}.jpg"],
        )

    async with PropertyStorage(str(tmp_path / "members.db")) as db:
        await db.upsert_source_listings(adverts)
        async with DetailFetcher() as fetcher:
            with patch.object(fetcher, "fetch_detail_page", side_effect=fetch):
                enriched = await _enrich_single(group, fetcher, asyncio.Semaphore(1), storage=db)
        await db.pipeline.save_pre_analysis_properties([enriched], {})
        assert received == [(p.unique_id, str(p.url)) for p in adverts]
        current = (await db.pipeline.get_pending_analysis_properties())[0]
        assert len(current.members) == 2
        for member in current.members:
            assert member.text.description == f"Detail for {member.source_id}"
            assert member.text.features == (f"Feature for {member.source_id}",)
        cleared = ListingTextEvidence.observed(
            observed_at=datetime(2030, 1, 1, tzinfo=UTC),
            origin="correction",
            description="",
        )
        await db.update_source_text(adverts[0].unique_id, cleared)
        current = (await db.pipeline.get_pending_analysis_properties())[0]
        by_id = {m.source_unique_id: m for m in current.members}
        assert by_id[adverts[0].unique_id].text.description == ""
        assert by_id[adverts[1].unique_id].text.description == "Detail for 100002"


async def test_missing_or_foreign_members_refuse_the_whole_save(tmp_path, adverts):
    async with PropertyStorage(str(tmp_path / "members.db")) as db:
        before = await snapshot(db)
        with pytest.raises(ValueError, match="ingested before linking"):
            await db.save_merged_property(explicit_group(adverts))
        assert await snapshot(db) == before
        for prop in adverts:
            await db.save_property(prop)
        before = await snapshot(db)
        with pytest.raises(MergeBlockedError) as exc:
            await db.save_merged_property(explicit_group(adverts))
        assert exc.value.blockers == {adverts[1].unique_id: ["membership_review_required"]}
        assert await snapshot(db) == before


@pytest.mark.parametrize("alias", ["?channel=RES_LET", "?view=map"])
async def test_exact_provider_url_alias_preserves_the_retained_representation(
    tmp_path, adverts, alias
):
    from pydantic import HttpUrl

    async with PropertyStorage(str(tmp_path / "aliases.db")) as db:
        original = adverts[0]
        await db.save_merged_property(explicit_group([original]))
        incoming = original.model_copy(update={"url": HttpUrl(str(original.url) + alias)})
        await db.save_merged_property(explicit_group([incoming]))
        current = (await db.get_recent_properties_for_dedup(days=None))[0]
        assert len(current.members) == 1
        assert current.members[0].source_unique_id == original.unique_id
        assert current.members[0].url == original.url
        wrong = original.model_copy(update={"url": adverts[1].url})
        before = await snapshot(db)
        with pytest.raises(ValueError, match="URL conflicts"):
            await db.save_merged_property(explicit_group([wrong]))
        assert await snapshot(db) == before


async def test_automatic_same_portal_guard_is_unchanged(adverts):
    dedup = Deduplicator(enable_cross_platform=True)
    groups = await dedup.deduplicate_and_merge_async(adverts)
    assert len(groups) == 2
    assert {m.source_unique_id for group in groups for m in group.members} == {
        p.unique_id for p in adverts
    }


async def test_cached_fields_and_members_share_one_snapshot_during_a_review(tmp_path, adverts):
    path = str(tmp_path / "concurrent.db")
    async with PropertyStorage(path) as reader, PropertyStorage(path) as writer:
        for prop in adverts:
            await writer.save_property(prop)
        targets = [await writer.homes.resolve(prop.unique_id) for prop in adverts]
        decision = await writer.memberships.prepare_merge(
            targets[0].home_id, (targets[1].home_id,), **REVIEW
        )
        original = reader.get_property_images_and_row

        async def change_members_after_reading_fields(*args, **kwargs):
            result = await original(*args, **kwargs)
            await writer.memberships.apply(decision)
            return result

        with patch.object(
            reader, "get_property_images_and_row", change_members_after_reading_fields
        ):
            _, prop, _, members = await reader.get_enrichment_cache(adverts[0].unique_id)
        assert prop.unique_id == adverts[0].unique_id
        assert {m.source_unique_id for m in members} == {adverts[0].unique_id}
        refreshed = await reader.get_current_listing_members([adverts[0].unique_id])
        assert {m.source_unique_id for m in refreshed[adverts[0].unique_id]} == {
            p.unique_id for p in adverts
        }


def test_telegram_links_keep_both_advertisements(adverts):
    group = explicit_group(adverts)
    message = format_merged_property_message(group)
    assert all(str(prop.url) in message for prop in adverts)
    keyboard = _build_inline_keyboard(group)
    links = {button.text: button.url for row in keyboard.inline_keyboard for button in row}
    assert links["Rightmove 1"] == str(adverts[0].url)
    assert links["Rightmove 2"] == str(adverts[1].url)


def test_legacy_urls_have_no_invented_id_and_large_groups_keep_selection_bounded(adverts):
    base = explicit_group(adverts)
    members = tuple(
        ListingMember(source=PropertySource.RIGHTMOVE, url=f"https://fixture.test/{i}", text=m.text)
        for i in range(400)
        for m in base.members[:1]
    )
    group = base.model_copy(update={"members": members})
    selected = select_listing_text(group)
    assert len(selected.description or "") <= 3000
    assert len(selected.features) <= 15
    assert len(selected.source_hashes) == 400
    assert all(key.startswith("legacy:rightmove:") for key in selected.source_hashes)
    assert selected.omitted_description_keys

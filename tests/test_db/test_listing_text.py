"""Source text survives refresh, correction, persistence and retry paths."""

from datetime import UTC, datetime, timedelta

import pytest

from home_finder.db import PropertyStorage
from home_finder.filters.detail_enrichment import _load_cached_property
from home_finder.models import ListingTextEvidence, MergedProperty, UserStatus

OBSERVED = datetime(2026, 9, 28, 10, tzinfo=UTC)


@pytest.mark.parametrize("save_path", ["merged", "pre_analysis", "unenriched", "dropped"])
async def test_all_save_paths_retain_detail_text_across_sparse_search_and_rebuild(
    tmp_path, make_merged_property, save_path
) -> None:
    original = make_merged_property()
    source = original.canonical.source
    evidence = ListingTextEvidence.observed(
        observed_at=OBSERVED, description="Detail: café kitchen", features=["Gas hob", "Garden"]
    )
    rich = original.model_copy(update={"source_text": {source: evidence}})
    async with PropertyStorage(str(tmp_path / "text.db")) as db:
        await db.upsert_source_listings([original.canonical])
        if save_path == "merged":
            await db.save_merged_property(rich)
        elif save_path == "pre_analysis":
            await db.pipeline.save_pre_analysis_properties([rich], {})
        elif save_path == "unenriched":
            await db.pipeline.save_unenriched_property(rich)
        else:
            await db.pipeline.save_dropped_properties([rich], {})
        await db.upsert_source_listings(
            [original.canonical.model_copy(update={"description": None})]
        )
        await db.update_merged_sources(original.unique_id, original)
        await db.save_property(original.canonical.model_copy(update={"description": ""}))
        _, prop, text = await db.get_property_images_and_row(original.unique_id)
        assert text[source] == evidence
        assert prop.description == evidence.description
        cached = await _load_cached_property(original, db)
        assert cached.source_text[source] == evidence
        assert cached.canonical.description == evidence.description


async def test_partial_observations_stale_arrival_and_explicit_clear(
    tmp_path, make_merged_property
) -> None:
    merged = make_merged_property()
    source = merged.canonical.source
    first = ListingTextEvidence.observed(
        observed_at=OBSERVED, description="Original description", features=["Gas hob"]
    )
    async with PropertyStorage(str(tmp_path / "text.db")) as db:
        await db.save_merged_property(merged.model_copy(update={"source_text": {source: first}}))
        await db.update_user_status(
            merged.unique_id, UserStatus.INTERESTED, note="Keep my decision"
        )
        await db.detect_and_record_price_change(merged.unique_id, merged.min_price - 50)
        await db.save_viewing_message(merged.unique_id, "Keep my message")
        assert await db.update_source_text(
            merged.unique_id,
            ListingTextEvidence.observed(
                observed_at=OBSERVED + timedelta(hours=2), features=["Induction hob"]
            ),
        )
        # An independent description arrived between the two feature observations.
        assert await db.update_source_text(
            merged.unique_id,
            ListingTextEvidence.observed(
                observed_at=OBSERVED + timedelta(hours=1), description="Corrected description"
            ),
        )
        assert not await db.update_source_text(merged.unique_id, first)
        assert not await db.update_source_text(
            merged.unique_id,
            ListingTextEvidence.observed(
                observed_at=OBSERVED + timedelta(hours=3), description="", features=[]
            ),
        )
        _, _, text = await db.get_property_images_and_row(merged.unique_id)
        assert text[source].description == "Corrected description"
        assert text[source].features == ("Induction hob",)
        assert text[source].description_observed_at == OBSERVED + timedelta(hours=1)
        assert text[source].features_observed_at == OBSERVED + timedelta(hours=2)

        clear = ListingTextEvidence.observed(
            observed_at=OBSERVED + timedelta(hours=4),
            description="",
            features=[],
            origin="correction",
        )
        assert await db.update_source_text(merged.unique_id, clear)
        assert not await db.update_source_text(merged.unique_id, first)
        await db.update_merged_sources(merged.unique_id, merged)
        await db.upsert_source_listings(
            [
                merged.canonical.model_copy(
                    update={"description": "Search text cannot undo the clear"}
                )
            ]
        )
        _, prop, text = await db.get_property_images_and_row(merged.unique_id)
        assert prop.description == ""
        assert text[source].features == ()
        assert text[source].description_origin == "correction"
        assert await db.get_viewing_message(merged.unique_id) == "Keep my message"
        assert len(await db.get_price_history(merged.unique_id)) == 1
        assert (await db.get_status_history(merged.unique_id))[0]["note"] == "Keep my decision"
        assert not await db.update_source_text("missing:provider-id", first)

    # A restart preserves the deliberate empty, its origin and its timestamp.
    async with PropertyStorage(str(tmp_path / "text.db")) as db:
        reloaded = (await db.get_recent_properties_for_dedup(days=None))[0]
        assert reloaded.source_text[source] == clear
        assert reloaded.descriptions[source] == ""


async def test_other_canonical_with_same_url_is_not_modified(tmp_path, make_property) -> None:
    first = make_property(source_id="first", description="First home")
    other = first.model_copy(update={"source_id": "conflicting-id", "description": "Other home"})
    incoming = ListingTextEvidence.observed(observed_at=OBSERVED, description="New first home text")
    merged = MergedProperty(
        canonical=first,
        sources=(first.source,),
        source_urls={first.source: first.url},
        min_price=first.price_pcm,
        max_price=first.price_pcm,
        source_text={first.source: incoming},
    )
    async with PropertyStorage(str(tmp_path / "text.db")) as db:
        await db.save_property(first)
        await db.save_property(other)
        await db.save_merged_property(merged)
        assert (await db.get_property(other.unique_id)).property.description == "Other home"
        assert (await db.get_property(first.unique_id)).property.description == incoming.description


async def test_legacy_projection_survives_a_source_row_with_missing_text(
    tmp_path, make_merged_property
) -> None:
    merged = make_merged_property()
    source = merged.canonical.source
    async with PropertyStorage(str(tmp_path / "text.db")) as db:
        await db.save_merged_property(merged)
        async with db._transaction() as conn:
            await conn.execute(
                "UPDATE properties SET descriptions_json = ?, source_text_json = NULL",
                ('{"' + source.value + '": "Retained historical text"}',),
            )
            await conn.execute("UPDATE source_listings SET description = NULL, text_json = NULL")
        await db.update_merged_sources(merged.unique_id, merged)
        reloaded = (await db.get_recent_properties_for_dedup(days=None))[0]
        assert reloaded.descriptions[source] == "Retained historical text"
        assert reloaded.source_text[source].description_observed_at is None
        assert reloaded.source_text[source].features is None


async def test_search_receipts_refresh_current_text_but_cannot_overwrite_detail(make_property):
    from home_finder.scrapers.openrent import OpenRentScraper

    prop = make_property(description="First summary")
    async with PropertyStorage(":memory:") as db:

        async def receive(item):
            async def page(index):
                return [item] if index == 0 else []

            return (await OpenRentScraper()._paginate(page, max_pages=2)).properties[0]

        first = await receive(prop)
        assert first.search_text.description_observed_at is not None
        await db.save_property(first)
        fresh = await receive(prop.model_copy(update={"description": "Changed summary"}))
        await db.upsert_source_listings([fresh])
        current = await db.get_property_images_and_row(prop.unique_id)
        assert current[2][prop.source].description == "Changed summary"

        # A detail request that started earlier still outranks a later summary.
        detail = ListingTextEvidence.observed(
            observed_at=OBSERVED, description="Complete detail text", features=["Garden"]
        )
        assert await db.update_source_text(prop.unique_id, detail)
        await db.upsert_source_listings([await receive(prop)])
        current = await db.get_property_images_and_row(prop.unique_id)
        assert current[2][prop.source] == detail

        # Reloading legacy objects must not turn an old undated summary into a new receipt.
        await db.upsert_source_listings([prop])
        current = await db.get_property_images_and_row(prop.unique_id)
        assert current[2][prop.source] == detail


async def test_detail_text_survives_cancelled_image_work_for_each_provider(make_property, tmp_path):
    import asyncio
    from unittest.mock import AsyncMock

    from home_finder.filters.detail_enrichment import _enrich_single
    from home_finder.models import PropertySource
    from home_finder.scrapers.detail_fetcher import DetailFetcher, DetailPageData
    from home_finder.scrapers.detail_identity import requested_identity

    first = make_property(
        source=PropertySource.RIGHTMOVE,
        source_id="100001",
        url="https://www.rightmove.co.uk/properties/100001",
    )
    second = make_property(
        source=PropertySource.ZOOPLA,
        source_id="100002",
        url="https://www.zoopla.co.uk/to-rent/details/100002/",
    )
    merged = MergedProperty(
        canonical=first,
        sources=(first.source, second.source),
        source_urls={first.source: first.url, second.source: second.url},
        min_price=first.price_pcm,
        max_price=first.price_pcm,
    )
    async with PropertyStorage(":memory:") as db, DetailFetcher() as fetcher:
        await db.upsert_source_listings([first, second])
        await db.pipeline.save_unenriched_property(merged)
        seen = []

        async def fetch(prop):
            seen.append(requested_identity(prop))
            return DetailPageData(
                description=f"Detail for {prop.source_id}",
                gallery_urls=["https://example.com/image.jpg"]
                if prop.source == second.source
                else None,
            )

        fetcher.fetch_detail_page = AsyncMock(side_effect=fetch)
        fetcher.download_image_bytes = AsyncMock(side_effect=asyncio.CancelledError)
        with pytest.raises(asyncio.CancelledError):
            await _enrich_single(merged, fetcher, asyncio.Semaphore(1), str(tmp_path), db)
        assert seen == ["100001", "100002"]
        _, _, retained = await db.get_property_images_and_row(merged.unique_id)
        assert retained[first.source].description == "Detail for 100001"
        assert retained[second.source].description == "Detail for 100002"

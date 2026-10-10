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

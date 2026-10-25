"""Promoting queued discovery preserves enriched evidence across process restarts."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from home_finder.config import Settings
from home_finder.db import PropertyStorage
from home_finder.enrichment.details import enrich_merged_properties
from home_finder.models import PropertySource, TransportMode, UserStatus
from home_finder.pipeline.assessment import process_initial_assessments
from home_finder.pipeline.candidates import _consolidate_and_admit, _prepare_enrichment_batch
from home_finder.pipeline.contracts import CandidateBatch
from home_finder.scrapers.detail_fetcher import DetailFetcher, DetailPageData
from home_finder.web.filters import PropertyFilter
from tests.support.spend import policy, spec


async def property_row(storage, uid):
    async with storage._read_connection() as conn:
        row = await (
            await conn.execute("SELECT * FROM properties WHERE unique_id=?", (uid,))
        ).fetchone()
        return dict(row)


@pytest.mark.parametrize("complete", [False, True])
async def test_scraped_area_survives_prequeue_enrichment_and_analysis_restart(
    tmp_path, make_merged_property, monkeypatch, complete
):
    settings = Settings(
        _env_file=None,
        database_path=str(tmp_path / "area.db"),
        data_dir="",
        enable_quality_filter=False,
    )
    raw = make_merged_property(sources=(PropertySource.RIGHTMOVE,))
    fetcher = AsyncMock(spec=DetailFetcher)
    fetcher.fetch_detail_page.return_value = DetailPageData(
        gallery_urls=["https://example.com/gallery.jpg"],
        floor_area_sqm=57.3,
        floor_area_source="rightmove",
    )
    monkeypatch.setattr("home_finder.pipeline.assessment._lookup_wards", AsyncMock())
    async with PropertyStorage(settings.database_path) as storage:
        selected = await _prepare_enrichment_batch([raw], [], storage, settings)
        result = await enrich_merged_properties(selected, fetcher, storage=storage)
        assert not result.without_usable_media
        assert result.with_usable_media[0].floor_area_sqm == 57.3
        if complete:
            assessment = await process_initial_assessments(
                CandidateBatch(candidates=result.with_usable_media),
                settings,
                storage,
                AsyncMock(),
            )
            count, _ = assessment.processed_count, assessment.token_usage
            assert count == 1
        else:
            await storage.processing.save_pre_analysis_properties(result.with_usable_media, {})

    async with PropertyStorage(settings.database_path) as storage:
        row = await property_row(storage, raw.unique_id)
        assert row["floor_area_sqm"] == 57.3
        assert row["floor_area_source"] == "rightmove"
        if not complete:
            pending = await storage.processing.get_pending_analysis_properties()
            assert [(m.floor_area_sqm, m.floor_area_source) for m in pending] == [
                (57.3, "rightmove")
            ]
        cards, count = await storage.web.get_properties_paginated(
            PropertyFilter(min_floor_area_sqm=55)
        )
        assert count == 1
        assert cards[0]["unique_id"] == raw.unique_id
        assert cards[0]["floor_area_sqm"] == 57.3


async def test_dropped_merged_home_recovers_every_accepted_source_and_enriched_field(
    tmp_path, make_merged_property
):
    settings = Settings(
        _env_file=None,
        database_path=str(tmp_path / "dropped.db"),
        data_dir="",
        require_floorplan=True,
    )
    first_seen = datetime.now(UTC) - timedelta(days=1)
    raw = [
        make_merged_property(
            sources=(source,),
            price_pcm=price,
            postcode="E8",
            latitude=None,
            longitude=None,
            first_seen=first_seen + timedelta(seconds=index),
            available_from=available,
        )
        for index, (source, price, available) in enumerate(
            [
                (PropertySource.RIGHTMOVE, 1900, None),
                (PropertySource.ZOOPLA, 1800, first_seen + timedelta(days=7)),
            ]
        )
    ]
    anchor = raw[0]
    fetcher = AsyncMock(spec=DetailFetcher)
    fetcher.fetch_detail_page.side_effect = lambda prop: DetailPageData(
        gallery_urls=[f"https://example.com/{prop.source.value}.jpg"],
        description=f"Details from {prop.source.value}",
        latitude=51.5465,
        longitude=-0.0553,
        postcode="E8 3RH",
        floor_area_sqm=57.3,
        floor_area_source=prop.source.value,
    )
    async with PropertyStorage(settings.database_path) as storage:
        selected = await _prepare_enrichment_batch(raw, [], storage, settings)
        enriched = await enrich_merged_properties(selected, fetcher, storage=storage)
        assert len(enriched.with_usable_media) == 2
        admission = await _consolidate_and_admit(
            enriched.with_usable_media,
            storage,
            settings,
            set(),
            commute_lookup={anchor.unique_id: (24, TransportMode.CYCLING)},
        )
        assert admission.candidates == []  # Too few photos to pass without a floorplan.
        assert await storage.get_property(raw[1].unique_id) is None

    async with PropertyStorage(settings.database_path) as storage:
        row = await property_row(storage, anchor.unique_id)
        assert row["notification_status"] == "dropped"
        assert (row["commute_minutes"], row["transport_mode"]) == (24, "cycling")
        assert await storage.processing.request_enrichment_recovery(
            [anchor.unique_id], reason="Sources now have floorplans"
        ) == {anchor.unique_id: "requested"}
        recovered = (await storage.processing.get_unenriched_properties())[0]
        assert recovered.source_urls == {
            source: url for merged in raw for source, url in merged.source_urls.items()
        }
        assert recovered.sources == (PropertySource.RIGHTMOVE, PropertySource.ZOOPLA)
        assert (recovered.min_price, recovered.max_price) == (1800, 1900)
        assert (recovered.floor_area_sqm, recovered.floor_area_source) == (57.3, "zoopla")
        assert recovered.canonical.postcode == "E8 3RH"
        assert (recovered.canonical.latitude, recovered.canonical.longitude) == (51.5465, -0.0553)
        assert recovered.canonical.available_from == raw[1].canonical.available_from
        fetcher.reset_mock()
        await enrich_merged_properties([recovered], fetcher, storage=storage)
        assert {call.args[0].url for call in fetcher.fetch_detail_page.await_args_list} == {
            url for merged in raw for url in merged.source_urls.values()
        }


@pytest.mark.parametrize("writer", ["save_pre_analysis_properties", "save_dropped_properties"])
async def test_sparse_promotion_preserves_enrichment_owner_history_and_spend(
    tmp_path, make_merged_property, writer
):
    rich = make_merged_property(
        sources=(PropertySource.RIGHTMOVE,),
        floor_area_sqm=57.3,
        floor_area_source="rightmove",
        description="Retained detailed description",
        available_from=datetime(2026, 11, 1, tzinfo=UTC),
    )
    sparse = rich.model_copy(
        update={
            "canonical": rich.canonical.model_copy(
                update={
                    "latitude": None,
                    "longitude": None,
                    "postcode": None,
                    "available_from": None,
                    "description": None,
                }
            ),
            "floor_area_sqm": None,
            "floor_area_source": None,
        }
    )
    async with PropertyStorage(str(tmp_path / "sparse.db")) as storage:
        await storage.processing.save_unenriched_property(
            rich, commute_minutes=24, transport_mode=TransportMode.CYCLING
        )
        await storage.update_user_status(rich.unique_id, UserStatus.INTERESTED, note="Keep this")
        await storage.save_viewing_message(rich.unique_id, "My saved message")
        await storage.spend.admit(
            spec().model_copy(update={"property_id": rich.unique_id}), policy()
        )
        before = await property_row(storage, rich.unique_id)
        async with storage._read_connection() as conn:
            history = {
                table: [
                    tuple(row)
                    for row in await (await conn.execute(f"SELECT * FROM {table}")).fetchall()
                ]
                for table in ("status_events", "viewing_messages", "paid_attempts")
            }
        result = await getattr(storage.processing, writer)([sparse], {})
        assert result.accepted == [sparse]
        after = await property_row(storage, rich.unique_id)
        for field in (
            "floor_area_sqm",
            "floor_area_source",
            "latitude",
            "longitude",
            "postcode",
            "available_from",
            "description",
            "commute_minutes",
            "transport_mode",
            "user_status",
            "first_seen",
            "enrichment_attempts",
            "analysis_attempts",
            "notified_at",
        ):
            assert after[field] == before[field], field
        async with storage._read_connection() as conn:
            for table, original in history.items():
                assert [
                    tuple(row)
                    for row in await (await conn.execute(f"SELECT * FROM {table}")).fetchall()
                ] == original


@pytest.mark.parametrize("writer", ["save_pre_analysis_properties", "save_dropped_properties"])
@pytest.mark.parametrize(
    "area,source,expected",
    [(65.0, None, (65.0, None)), (None, "zoopla", (57.3, "rightmove"))],
)
async def test_promotion_keeps_floor_area_and_its_provenance_together(
    make_merged_property, writer, area, source, expected
):
    original = make_merged_property(
        sources=(PropertySource.RIGHTMOVE,), floor_area_sqm=57.3, floor_area_source="rightmove"
    )
    incoming = original.model_copy(update={"floor_area_sqm": area, "floor_area_source": source})
    async with PropertyStorage(":memory:") as storage:
        await storage.processing.save_unenriched_property(original, attempted=False)
        result = await getattr(storage.processing, writer)([incoming], {})
        assert result.accepted == [incoming]
        row = await property_row(storage, original.unique_id)
        assert (row["floor_area_sqm"], row["floor_area_source"]) == expected

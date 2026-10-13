"""Existing work progresses without discovery and incomplete analysis never delivers."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import HttpUrl, SecretStr

from home_finder.config import Settings
from home_finder.db import PropertyStorage
from home_finder.filters.quality import APIUnavailableError, PropertyQualityFilter, TokenUsage
from home_finder.main import _make_notify_callback, _run_pipeline_with_storage
from home_finder.models import PropertyImage, PropertySource
from home_finder.models.analysis import AnalysisFailure
from home_finder.models.notifications import NotificationPolicy
from home_finder.models.spend import SpendDeferred, SpendPolicy
from home_finder.pipeline.analysis import _run_quality_and_save
from home_finder.pipeline.stages import PreAnalysisResult, _run_pre_analysis_pipeline
from home_finder.utils.image_cache import get_cached_image_path, save_image_bytes
from tests.notifier_helpers import mock_durable_notifier


@pytest.fixture
async def storage():
    async with PropertyStorage(":memory:") as db:
        yield db


@pytest.fixture
def settings(tmp_path):
    return Settings(
        _env_file=None,
        database_path=":memory:",
        data_dir=str(tmp_path),
        search_areas="e8",
        min_price=1500,
        max_price=2500,
        min_bedrooms=1,
        max_bedrooms=2,
        require_floorplan=False,
        traveltime_app_id="",
        traveltime_api_key=SecretStr(""),
        anthropic_api_key=SecretStr("fixture"),
        enable_quality_filter=True,
        notification_policy=NotificationPolicy(min_fit_score=0),
    )


@pytest.fixture
def boundaries(make_quality_analysis):
    notifier, construct_notifier = mock_durable_notifier()
    quality = AsyncMock()
    quality.__aenter__.return_value = quality
    quality.token_usage = TokenUsage()
    quality.analyze_single_merged.side_effect = lambda merged, **kw: (
        merged,
        make_quality_analysis(),
    )
    with (
        patch("home_finder.main.TelegramNotifier", side_effect=construct_notifier),
        patch("home_finder.pipeline.scraping.scrape_all_platforms", return_value=([], [])),
        patch("home_finder.pipeline.analysis.PropertyQualityFilter", return_value=quality),
        patch("home_finder.pipeline.analysis._lookup_wards", new_callable=AsyncMock),
        patch(
            "home_finder.pipeline.analysis._re_enrich_incomplete", side_effect=lambda q, *a: q
        ) as cache,
    ):
        yield notifier, quality, cache


def gallery():
    return (
        PropertyImage(
            url=HttpUrl("https://example.com/image.jpg"),
            source=PropertySource.OPENRENT,
            image_type="gallery",
        ),
    )


@pytest.mark.parametrize("cache_state", ["missing_floorplan", "corrupt_floorplan", "no_urls"])
async def test_no_usable_images_remain_pending_without_spend_or_failure_penalty(
    storage, settings, make_merged_property, cache_state
):
    floorplan = PropertyImage(
        url=HttpUrl("https://example.com/floorplan.jpg"),
        source=PropertySource.OPENRENT,
        image_type="floorplan",
    )
    merged = make_merged_property(floorplan=None if cache_state == "no_urls" else floorplan)
    if cache_state == "corrupt_floorplan":
        path = get_cached_image_path(
            settings.data_dir, merged.unique_id, str(floorplan.url), "floorplan", 0
        )
        save_image_bytes(path, b"not an image")
    await storage.pipeline.save_pre_analysis_properties([merged], {})
    on_result = AsyncMock()
    with (
        patch("home_finder.pipeline.analysis._lookup_wards", new_callable=AsyncMock),
        patch(
            "home_finder.pipeline.analysis.DetailFetcher.download_image_bytes",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch.object(PropertyQualityFilter, "_run_visual_analysis", new_callable=AsyncMock) as api,
    ):
        for _ in range(4):
            count, _ = await _run_quality_and_save(
                PreAnalysisResult(), settings, storage, on_result
            )
            assert count == 0
        api.assert_not_awaited()
    on_result.assert_not_awaited()
    assert [p.unique_id for p in await storage.pipeline.get_pending_analysis_properties()] == [
        merged.unique_id
    ]
    async with (
        storage._read_connection() as conn,
        conn.execute("SELECT analysis_attempts FROM properties") as cursor,
    ):
        assert [row[0] for row in await cursor.fetchall()] == [0]
    assert await storage.get_quality_analysis(merged.unique_id) is None
    report = await storage.spend.report()
    assert report["attempts"] == report["held_nanousd"] == report["lifetime_accounted_nanousd"] == 0


async def test_zero_discovery_drains_pending_analysis_reanalysis_and_price_delivery(
    storage, settings, boundaries, make_merged_property, make_quality_analysis
):
    notifier, quality, _ = boundaries
    pending = make_merged_property(images=gallery())
    reanalysis = make_merged_property(images=gallery())
    drop = make_merged_property(price_pcm=2000)
    await storage.pipeline.save_pre_analysis_properties([pending, reanalysis], {})
    await storage.pipeline.complete_analysis(reanalysis.unique_id, make_quality_analysis())
    await storage.mark_notified(reanalysis.unique_id)
    await storage.pipeline.request_reanalysis([reanalysis.unique_id])
    await storage.save_merged_property(drop)
    await storage.mark_notified(drop.unique_id)
    await storage.detect_and_record_price_change(drop.unique_id, 1900, source="openrent")

    await _run_pipeline_with_storage(settings, storage)

    assert {call.args[0].unique_id for call in quality.analyze_single_merged.await_args_list} == {
        pending.unique_id,
        reanalysis.unique_id,
    }
    notifier.send_merged_property_notification.assert_awaited_once()
    notifier.send_price_drop_notification.assert_not_awaited()
    report = await storage.deliveries.report()
    assert report["counts"]["complete"] == 2
    assert not await storage.pipeline.get_reanalysis_queue()
    assert not await storage.get_unsent_price_drops()
    assert (await storage.get_property(pending.unique_id)).notification_status.value == "sent"
    latest = await storage.pipeline.get_last_pipeline_run()
    assert latest["scraped_count"] == 0
    assert latest["analyzed_count"] == latest["notified_count"] == 1


@pytest.mark.parametrize("filtered_discovery", [False, True])
async def test_pending_enrichment_runs_with_no_eligible_discovery(
    storage, settings, boundaries, make_merged_property, make_property, filtered_discovery
):
    retry = make_merged_property()
    await storage.pipeline.save_unenriched_property(retry)
    discovered = [make_property(price_pcm=5000)] if filtered_discovery else []

    async def enrich(properties, *args, **kwargs):
        assert [m.unique_id for m in properties] == [retry.unique_id]
        return [m.model_copy(update={"images": gallery()}) for m in properties]

    with (
        patch("home_finder.pipeline.scraping.scrape_all_platforms", return_value=(discovered, [])),
        patch("home_finder.pipeline.stages._run_enrichment", side_effect=enrich) as enrichment,
    ):
        result = await _run_pre_analysis_pipeline(settings, storage)
    enrichment.assert_awaited_once()
    assert result is not None
    assert [m.unique_id for m in result.merged_to_process] == [retry.unique_id]
    assert result.new_property_count == 0
    assert result.enriched_count == 1


async def test_missing_cache_defers_without_attempt_or_delivery_then_recovers(
    storage, settings, boundaries, make_merged_property, make_quality_analysis
):
    notifier, quality, cache = boundaries
    pending = make_merged_property(images=gallery())
    await storage.pipeline.save_pre_analysis_properties([pending], {})
    quality.analyze_single_merged.side_effect = lambda m, **kw: (m, None)
    await _run_pipeline_with_storage(settings, storage)
    tracked = await storage.get_property(pending.unique_id)
    assert tracked.notification_status.value == "pending_analysis"
    assert (await storage.pipeline.get_last_pipeline_run())["analyzed_count"] == 0
    notifier.send_merged_property_notification.assert_not_awaited()
    async with storage._read_connection() as conn:
        row = await (
            await conn.execute(
                "SELECT analysis_attempts FROM properties WHERE unique_id = ?", (pending.unique_id,)
            )
        ).fetchone()
        assert row[0] == 0
        event = await (
            await conn.execute(
                "SELECT metadata_json FROM property_events WHERE event_type = 'analysis_deferred'"
            )
        ).fetchone()
        assert "missing_cached_images" in event[0]
    quality.analyze_single_merged.side_effect = lambda m, **kw: (m, make_quality_analysis())
    await _run_pipeline_with_storage(settings, storage)
    assert (await storage.get_property(pending.unique_id)).notification_status.value == "sent"
    notifier.send_merged_property_notification.assert_awaited_once()
    assert cache.await_count == 2


@pytest.mark.parametrize("failure", ["exception", "incomplete", "timeout"])
async def test_failed_initial_analysis_has_bounded_attempts_and_no_delivery(
    storage, settings, boundaries, make_merged_property, make_quality_analysis, failure
):
    notifier, quality, _ = boundaries
    pending = make_merged_property(images=gallery())
    await storage.pipeline.save_pre_analysis_properties([pending], {})
    if failure == "exception":
        quality.analyze_single_merged.side_effect = RuntimeError("fixture failure")
    elif failure == "incomplete":
        qa = make_quality_analysis().model_copy(update={"overall_rating": None})
        quality.analyze_single_merged.side_effect = lambda m, **kw: (m, qa)
    else:
        # Bypass settings' operational minimum only to run a deterministic short test.
        settings = settings.model_copy(update={"property_analysis_timeout": 0.02})

        async def wait(*args, **kwargs):
            await asyncio.Future()

        quality.analyze_single_merged.side_effect = wait

    for _ in range(4):
        await _run_pipeline_with_storage(settings, storage)
    assert quality.analyze_single_merged.await_count == 3
    assert not await storage.pipeline.get_pending_analysis_properties()
    assert (
        await storage.get_property(pending.unique_id)
    ).notification_status.value == "pending_analysis"
    assert await storage.get_quality_analysis(pending.unique_id) is None
    detail = await storage.web.get_property_detail(pending.unique_id)
    assert detail["analysis_exhausted_reason"] == "initial_failure_limit"
    assert detail["processing_state"] == "analysis_exhausted"
    assert (await storage.web.get_processing_counts())["analysis_exhausted"] == 1
    notifier.send_merged_property_notification.assert_not_awaited()
    notifier.send_property_notification.assert_not_awaited()


@pytest.mark.parametrize("failure", ["api_unavailable", "request_rejected", "unsupported_schema"])
async def test_provider_failures_leave_homes_retryable_after_multiple_runs(
    storage, settings, boundaries, make_merged_property, make_quality_analysis, failure
):
    notifier, quality, _ = boundaries
    settings.quality_concurrency = 1
    pending = [make_merged_property(images=gallery()) for _ in range(3)]
    await storage.pipeline.save_pre_analysis_properties(pending, {})
    if failure == "api_unavailable":
        quality.analyze_single_merged.side_effect = APIUnavailableError("provider unavailable")
    else:
        qa = make_quality_analysis().model_copy(
            update={
                "overall_rating": None,
                "analysis_failure": AnalysisFailure(phase="visual", kind=failure),
            }
        )
        quality.analyze_single_merged.side_effect = lambda m, **kw: (m, qa)

    for _ in range(4):
        await _run_pipeline_with_storage(settings, storage)
    expected_calls = 12 if failure == "request_rejected" else 4
    assert quality.analyze_single_merged.await_count == expected_calls
    assert len(await storage.pipeline.get_pending_analysis_properties()) == 3
    async with (
        storage._read_connection() as conn,
        conn.execute("SELECT analysis_attempts FROM properties") as cur,
    ):
        assert [row[0] for row in await cur.fetchall()] == [0, 0, 0]
    notifier.send_merged_property_notification.assert_not_awaited()

    quality.analyze_single_merged.side_effect = lambda m, **kw: (m, make_quality_analysis())
    await _run_pipeline_with_storage(settings, storage)
    assert not await storage.pipeline.get_pending_analysis_properties()
    assert notifier.send_merged_property_notification.await_count == 3


async def test_one_rejected_request_does_not_starve_healthy_homes(
    storage, settings, boundaries, make_merged_property, make_quality_analysis
):
    _, quality, _ = boundaries
    settings.quality_concurrency = 1
    items = [make_merged_property() for _ in range(3)]
    rejected = make_quality_analysis().model_copy(
        update={
            "overall_rating": None,
            "analysis_failure": AnalysisFailure(phase="visual", kind="request_rejected"),
        }
    )
    quality.analyze_single_merged.side_effect = lambda merged, **kw: (
        merged,
        rejected if merged.unique_id == items[0].unique_id else make_quality_analysis(),
    )
    count, _ = await _run_quality_and_save(
        PreAnalysisResult(merged_to_process=items), settings, storage, AsyncMock()
    )
    assert count == 2
    assert [
        item.unique_id for item in await storage.pipeline.get_pending_analysis_properties()
    ] == [items[0].unique_id]


async def test_pipeline_queues_all_selected_homes_within_spending_concurrency(
    storage, settings, boundaries, make_merged_property, make_quality_analysis
):
    _, quality, _ = boundaries
    settings.quality_concurrency = 15
    settings.analysis_spend_policy = SpendPolicy(
        per_call_usd="7", daily_usd="20", monthly_usd="20", max_in_flight=1
    )
    active = 0

    async def analyze(merged, **kwargs):
        nonlocal active
        active += 1
        try:
            if active > 1:
                raise SpendDeferred("concurrency_limit")
            await asyncio.sleep(0)
            return merged, make_quality_analysis()
        finally:
            active -= 1

    quality.analyze_single_merged.side_effect = analyze
    fresh = [make_merged_property() for _ in range(6)]
    callback = AsyncMock()
    count, _ = await _run_quality_and_save(
        PreAnalysisResult(merged_to_process=fresh), settings, storage, callback
    )
    assert count == callback.await_count == 6
    assert not await storage.pipeline.get_pending_analysis_properties()


async def test_legacy_sent_fallback_recovers_without_another_notification(
    storage, settings, boundaries, make_merged_property, make_quality_analysis
):
    notifier, _, _ = boundaries
    merged = make_merged_property(images=gallery())
    await storage.pipeline.save_pre_analysis_properties([merged], {})
    fallback = make_quality_analysis().model_copy(update={"overall_rating": None})
    await storage.pipeline.complete_analysis(merged.unique_id, fallback)
    await storage.mark_notified(merged.unique_id)
    await _run_pipeline_with_storage(settings, storage)
    assert (await storage.get_quality_analysis(merged.unique_id)).overall_rating == 4
    assert (await storage.get_property(merged.unique_id)).notification_status.value == "sent"
    notifier.send_merged_property_notification.assert_not_awaited()
    notifier.send_property_notification.assert_not_awaited()


async def test_failed_reanalysis_keeps_request_and_previous_analysis(
    storage, settings, boundaries, make_merged_property, make_quality_analysis
):
    _, quality, _ = boundaries
    merged = make_merged_property(images=gallery())
    await storage.pipeline.save_pre_analysis_properties([merged], {})
    await storage.pipeline.complete_analysis(merged.unique_id, make_quality_analysis(rating=5))
    await storage.mark_notified(merged.unique_id)
    await storage.pipeline.request_reanalysis([merged.unique_id])
    fallback = make_quality_analysis().model_copy(update={"overall_rating": None})
    quality.analyze_single_merged.side_effect = lambda m, **kw: (m, fallback)
    await _run_pipeline_with_storage(settings, storage)
    assert (await storage.get_quality_analysis(merged.unique_id)).overall_rating == 5
    assert [m.unique_id for m in await storage.pipeline.get_reanalysis_queue()] == [
        merged.unique_id
    ]


async def test_retries_and_fresh_discovery_share_initial_analysis_slots(
    storage, settings, boundaries, make_merged_property
):
    settings.max_analysis_per_run = 2
    _, quality, _ = boundaries
    retries = [make_merged_property() for _ in range(3)]
    fresh = [make_merged_property() for _ in range(3)]
    await storage.pipeline.save_pre_analysis_properties(retries, {})
    processed, _ = await _run_quality_and_save(
        PreAnalysisResult(merged_to_process=fresh), settings, storage, AsyncMock()
    )
    assert processed == 2
    assert {c.args[0].unique_id for c in quality.analyze_single_merged.await_args_list} == {
        retries[0].unique_id,
        fresh[0].unique_id,
    }
    assert len(await storage.pipeline.get_pending_analysis_properties()) == 4


async def test_missing_notification_images_are_not_marked_sent(storage, make_merged_property):
    merged = make_merged_property()
    await storage.save_merged_property(merged)
    notifier = AsyncMock()
    callback = _make_notify_callback(notifier, storage, [0])
    await callback(merged, None, None)
    assert (await storage.get_property(merged.unique_id)).notification_status.value == "pending"
    notifier.send_merged_property_notification.assert_not_awaited()


@pytest.mark.parametrize(
    "reason",
    [
        "attempt_limit",
        "daily_limit",
        "concurrency_limit",
        "operation_unresolved",
        "total_attempt_limit",
    ],
)
async def test_only_property_operation_ceiling_becomes_terminal(
    storage, settings, boundaries, make_merged_property, reason
):
    _, quality, _ = boundaries
    merged = make_merged_property(images=gallery())
    await storage.pipeline.save_pre_analysis_properties([merged], {})
    quality.analyze_single_merged.side_effect = SpendDeferred(reason)
    for _ in range(3):
        await _run_pipeline_with_storage(settings, storage)
    terminal = reason == "attempt_limit"
    assert quality.analyze_single_merged.await_count == (1 if terminal else 3)
    async with storage._read_connection() as conn:
        row = await (
            await conn.execute("SELECT * FROM properties WHERE unique_id=?", (merged.unique_id,))
        ).fetchone()
        deliveries = await (await conn.execute("SELECT * FROM notification_deliveries")).fetchall()
    assert row["analysis_exhausted_reason"] == (
        "paid_operation_attempt_limit" if terminal else None
    )
    assert bool(row["analysis_exhausted_at"]) == terminal
    assert row["analysis_attempts"] == 0
    assert row["notification_status"] == "pending_analysis"
    assert row["user_status"] == "new"
    assert deliveries == []
    detail = await storage.web.get_property_detail(merged.unique_id)
    assert detail["processing_state"] == (
        "analysis_exhausted" if terminal else "awaiting_assessment"
    )
    if terminal:
        # Re-presenting the same work cannot bypass its recorded stop.
        result = await storage.pipeline.save_pre_analysis_properties([merged], {})
        assert not result.accepted
        assert result.refused[merged.unique_id][merged.unique_id] == ["analysis_attempts_exhausted"]
        assert await storage.pipeline.get_pending_analysis_properties() == []


async def test_exhausted_refresh_preserves_delivered_assessment_and_request(
    storage, settings, boundaries, make_merged_property, make_quality_analysis
):
    notifier, quality, _ = boundaries
    merged = make_merged_property(images=gallery())
    await storage.pipeline.save_pre_analysis_properties([merged], {})
    analysis = make_quality_analysis(rating=5)
    await storage.pipeline.complete_analysis(merged.unique_id, analysis)
    await storage.mark_notified(merged.unique_id)
    await storage.pipeline.request_reanalysis([merged.unique_id])
    async with storage._read_connection() as conn:
        before = dict(await (await conn.execute("SELECT * FROM quality_analyses")).fetchone())
    quality.analyze_single_merged.side_effect = SpendDeferred("attempt_limit")
    for _ in range(3):
        await _run_pipeline_with_storage(settings, storage)
    assert quality.analyze_single_merged.await_count == 1
    async with storage._read_connection() as conn:
        after = dict(await (await conn.execute("SELECT * FROM quality_analyses")).fetchone())
    assert after == before
    assert (await storage.get_quality_analysis(merged.unique_id)).overall_rating == 5
    assert (await storage.get_property(merged.unique_id)).notification_status.value == "sent"
    assert await storage.pipeline.get_reanalysis_queue() == []
    detail = await storage.web.get_property_detail(merged.unique_id)
    assert detail["processing_state"] == "analysis_exhausted"
    assert detail["processing_label"] == "Assessment refresh stopped"
    assert detail["reanalysis_requested_at"] is not None
    notifier.send_merged_property_notification.assert_not_awaited()

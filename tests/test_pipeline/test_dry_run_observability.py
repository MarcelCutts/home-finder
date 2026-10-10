"""Run accounting with real in-memory storage and synthetic work/provider boundaries."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import structlog
from pydantic import SecretStr

from home_finder.config import Settings
from home_finder.db import PropertyStorage
from home_finder.filters.quality import TokenUsage
from home_finder.main import _run_pipeline_with_storage
from home_finder.pipeline.event_recorder import PropertyEvent
from home_finder.pipeline.scraping import ScraperMetrics
from home_finder.pipeline.stages import PreAnalysisResult


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
        enable_quality_filter=False,
        anthropic_api_key=SecretStr(""),
    )


@pytest.fixture
def boundaries(storage):
    notifier = AsyncMock()
    notifier.__aenter__.return_value = notifier
    notifier.deliver_pending.return_value = 0
    with (
        patch("home_finder.main.TelegramNotifier", return_value=notifier) as factory,
        patch(
            "home_finder.main._run_pre_analysis_pipeline", return_value=PreAnalysisResult()
        ) as pre,
        patch("home_finder.main._drain_reanalysis_queue", return_value=0) as reanalysis,
        patch("home_finder.pipeline.analysis._lookup_wards", new_callable=AsyncMock),
        patch.object(storage.deliveries, "recover", wraps=storage.deliveries.recover) as recover,
        patch.object(
            storage.pipeline, "cleanup_old_events", wraps=storage.pipeline.cleanup_old_events
        ) as prune,
    ):
        yield SimpleNamespace(
            factory=factory,
            notifier=notifier,
            pre=pre,
            reanalysis=reanalysis,
            recover=recover,
            prune=prune,
        )


@pytest.fixture
def logs():
    with structlog.testing.capture_logs(
        processors=[structlog.contextvars.merge_contextvars]
    ) as captured:
        yield captured


async def rows(storage, table):
    async with storage._read_connection() as conn:
        return [
            dict(row) for row in await (await conn.execute(f"SELECT * FROM {table}")).fetchall()
        ]


def phase_outcomes(logs):
    return {
        entry["phase"]: entry["outcome"]
        for entry in logs
        if entry["event"] == "pipeline_phase_finished"
    }


def assert_no_telegram(boundaries):
    boundaries.factory.assert_not_called()
    boundaries.notifier.deliver_pending.assert_not_awaited()
    boundaries.notifier.send_merged_property_notification.assert_not_awaited()
    boundaries.recover.assert_not_awaited()
    boundaries.prune.assert_not_awaited()


async def test_dry_run_saves_analysis_events_costs_and_pending_delivery(
    settings, storage, boundaries, logs, make_merged_property, make_quality_analysis
):
    merged = make_merged_property()
    quality = make_quality_analysis()
    usage = TokenUsage(input_tokens=80, output_tokens=12, cache_read_tokens=7)
    quality_filter = AsyncMock()
    quality_filter.__aenter__.return_value = quality_filter
    quality_filter.analyze_single_merged.return_value = merged, quality
    quality_filter.token_usage = usage
    settings.enable_quality_filter = True
    settings.anthropic_api_key = SecretStr("synthetic-test-key")
    boundaries.pre.return_value = PreAnalysisResult(
        merged_to_process=[merged], scraped_count=1, enriched_count=1
    )

    with patch("home_finder.pipeline.analysis.PropertyQualityFilter", return_value=quality_filter):
        await _run_pipeline_with_storage(settings, storage, dry_run=True)

    run = await storage.pipeline.get_last_pipeline_run()
    assert run["status"] == "completed"
    assert run["scraped_count"] == run["analyzed_count"] == 1
    assert run["notified_count"] == 0
    assert run["notification_seconds"] is None
    assert run["analysis_seconds"] >= 0
    assert run["total_input_tokens"] == usage.input_tokens
    assert run["total_output_tokens"] == usage.output_tokens
    assert run["total_cache_read_tokens"] == usage.cache_read_tokens
    assert run["estimated_cost_usd"] == usage.estimated_cost_usd
    events = await rows(storage, "property_events")
    assert [(event["run_id"], event["property_id"], event["event_type"]) for event in events] == [
        (run["id"], merged.unique_id, "analyzed")
    ]
    prop = await storage.get_property(merged.unique_id)
    assert prop.notification_status.value == "pending"
    delivery = await storage.deliveries.get(f"discovery:{merged.unique_id}")
    assert delivery["state"] == "queued"
    assert_no_telegram(boundaries)
    outcomes = phase_outcomes(logs)
    assert outcomes["notification_recovery"] == outcomes["notification_delivery"] == "skipped"
    assert outcomes["quality_analysis_and_save"] == outcomes["reanalysis"] == "completed"
    terminal = [entry for entry in logs if entry["event"] == "pipeline_complete"]
    assert len(terminal) == 1
    assert terminal[0]["dry_run"] is True
    assert terminal[0]["run_id"] == run["id"]


async def test_completed_phase_keeps_deferred_property_visible(
    settings, storage, boundaries, logs, make_merged_property
):
    merged = make_merged_property()
    settings.enable_quality_filter = True
    settings.anthropic_api_key = SecretStr("synthetic-test-key")
    boundaries.pre.return_value = PreAnalysisResult(merged_to_process=[merged])
    quality_filter = AsyncMock()
    quality_filter.__aenter__.return_value = quality_filter
    quality_filter.analyze_single_merged.return_value = merged, None
    quality_filter.token_usage = TokenUsage()
    with patch("home_finder.pipeline.analysis.PropertyQualityFilter", return_value=quality_filter):
        await _run_pipeline_with_storage(settings, storage, dry_run=True)

    run = await storage.pipeline.get_last_pipeline_run()
    assert run["status"] == "completed"
    assert run["analyzed_count"] == 0
    assert phase_outcomes(logs)["quality_analysis_and_save"] == "completed"
    assert (
        await storage.get_property(merged.unique_id)
    ).notification_status.value == "pending_analysis"
    assert await storage.deliveries.get(f"discovery:{merged.unique_id}") is None
    assert [row["event_type"] for row in await rows(storage, "property_events")] == [
        "analysis_deferred"
    ]
    assert_no_telegram(boundaries)


@pytest.mark.parametrize(
    ("healthy", "found", "status"),
    [(True, 0, "completed"), (False, 0, "failed"), (False, 2, "partial")],
)
async def test_dry_run_preserves_collection_outcomes_and_scraper_metrics(
    settings, storage, boundaries, logs, healthy, found, status
):
    boundaries.pre.return_value = PreAnalysisResult(
        scraper_metrics=[
            ScraperMetrics(
                scraper_name="openrent",
                started_at="2026-10-10T00:00:00+00:00",
                areas_attempted=1,
                pages_fetched=int(healthy or found > 0),
                pages_failed=int(not healthy),
                properties_found=found,
                is_healthy=healthy,
            )
        ],
    )
    await _run_pipeline_with_storage(settings, storage, dry_run=True)
    run = await storage.pipeline.get_last_pipeline_run()
    assert run["status"] == status
    assert run["completed_at"] is not None
    assert run["scraped_count"] == found
    assert run["total_input_tokens"] is None
    assert run["estimated_cost_usd"] is None
    metrics = (await rows(storage, "scraper_runs"))[0]
    assert metrics["pipeline_run_id"] == run["id"]
    assert metrics["is_healthy"] == healthy
    assert phase_outcomes(logs)["pre_analysis"] == status
    assert_no_telegram(boundaries)


@pytest.mark.parametrize("dry_run", [False, True])
async def test_run_logs_restore_caller_context(settings, storage, boundaries, logs, dry_run):
    with structlog.contextvars.bound_contextvars(
        trial_id="synthetic-trial", run_id=912, dry_run="caller-mode"
    ):
        before = structlog.contextvars.get_contextvars()
        await _run_pipeline_with_storage(settings, storage, dry_run=dry_run)
        assert structlog.contextvars.get_contextvars() == before
    run = await storage.pipeline.get_last_pipeline_run()
    phase_logs = [entry for entry in logs if entry["event"] == "pipeline_phase_finished"]
    assert phase_logs
    assert all(entry["trial_id"] == "synthetic-trial" for entry in phase_logs)
    assert all(entry["run_id"] == run["id"] for entry in phase_logs)
    assert all(entry["dry_run"] is dry_run for entry in phase_logs)
    assert all(entry["elapsed_seconds"] >= 0 for entry in phase_logs)
    if dry_run:
        assert_no_telegram(boundaries)
    else:
        boundaries.prune.assert_awaited_once()
        boundaries.recover.assert_awaited_once()


@pytest.mark.parametrize(
    ("phase", "target"),
    [
        ("pre_analysis", "_run_pre_analysis_pipeline"),
        ("quality_analysis_and_save", "_run_quality_and_save"),
        ("reanalysis", "_drain_reanalysis_queue"),
    ],
)
async def test_phase_failures_are_terminal_and_restore_context(
    settings, storage, boundaries, logs, phase, target
):
    with (
        structlog.contextvars.bound_contextvars(trial_id="failure-trial", run_id=913),
        patch(f"home_finder.main.{target}", side_effect=RuntimeError("synthetic phase failure")),
    ):
        before = structlog.contextvars.get_contextvars()
        with pytest.raises(RuntimeError, match="synthetic phase failure"):
            await _run_pipeline_with_storage(settings, storage, dry_run=True)
        assert structlog.contextvars.get_contextvars() == before
    run = await storage.pipeline.get_last_pipeline_run()
    assert run["status"] == "failed"
    assert run["error_message"] == "synthetic phase failure"
    assert phase_outcomes(logs)[phase] == "failed"
    assert [entry["finalized"] for entry in logs if entry["event"] == "pipeline_failed"] == [True]
    assert not any(entry["event"] == "pipeline_complete" for entry in logs)
    assert_no_telegram(boundaries)


async def test_returned_usage_survives_reanalysis_failure(settings, storage, boundaries):
    usage = TokenUsage(input_tokens=50, output_tokens=4)
    boundaries.reanalysis.side_effect = RuntimeError("synthetic reanalysis failure")
    with (
        patch("home_finder.main._run_quality_and_save", return_value=(2, usage)),
        pytest.raises(RuntimeError, match="synthetic reanalysis failure"),
    ):
        await _run_pipeline_with_storage(settings, storage, dry_run=True)
    run = await storage.pipeline.get_last_pipeline_run()
    assert run["status"] == "failed"
    assert run["analyzed_count"] == 2
    assert run["total_input_tokens"] == 50
    assert run["estimated_cost_usd"] == usage.estimated_cost_usd


@pytest.mark.parametrize(
    ("operation", "phase"),
    [
        ("create_pipeline_run", "setup"),
        ("insert_property_events", "pre_analysis"),
        ("complete_pipeline_run", "finalization"),
    ],
)
async def test_repeated_cancellation_settles_one_audit_insert_and_finalizes(
    settings, storage, boundaries, logs, operation, phase
):
    entered, release = asyncio.Event(), asyncio.Event()
    original = getattr(storage.pipeline, operation)

    async def blocked(*args, **kwargs):
        # Creation has committed but the caller has not received its ID yet.
        if operation == "create_pipeline_run":
            result = await original(*args, **kwargs)
            entered.set()
            await release.wait()
            return result
        if not release.is_set():
            entered.set()
            await release.wait()
        return await original(*args, **kwargs)

    async def pre(*args, recorder, progress, **kwargs):
        recorder.record(
            PropertyEvent("openrent:synthetic", "openrent", "criteria_passed", "criteria")
        )
        await recorder.flush()
        return progress

    boundaries.pre.side_effect = pre

    async def run():
        with structlog.contextvars.bound_contextvars(trial_id="cancel-trial", run_id=915):
            before = structlog.contextvars.get_contextvars()
            try:
                await _run_pipeline_with_storage(settings, storage, dry_run=True)
            finally:
                assert structlog.contextvars.get_contextvars() == before

    with patch.object(storage.pipeline, operation, side_effect=blocked) as operation_mock:
        task = asyncio.create_task(run())
        try:
            await asyncio.wait_for(entered.wait(), 2)
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
    run = await storage.pipeline.get_last_pipeline_run()
    assert run["status"] == "cancelled"
    assert run["completed_at"] is not None
    assert phase_outcomes(logs)[phase] == "cancelled"
    assert [entry["finalized"] for entry in logs if entry["event"] == "pipeline_cancelled"] == [
        True
    ]
    assert not any(entry["event"] == "pipeline_complete" for entry in logs)
    if operation == "insert_property_events":
        operation_mock.assert_awaited_once()
        assert len(await rows(storage, "property_events")) == 1
    elif operation == "create_pipeline_run":
        operation_mock.assert_awaited_once()
        boundaries.pre.assert_not_awaited()
    assert_no_telegram(boundaries)


async def test_repeated_cancel_during_cancelled_run_finalization(
    settings, storage, boundaries, logs
):
    working, finalizing, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original = storage.pipeline.complete_pipeline_run

    async def pre(*args, **kwargs):
        working.set()
        await asyncio.Future()

    async def complete(*args, **kwargs):
        finalizing.set()
        await release.wait()
        await original(*args, **kwargs)

    boundaries.pre.side_effect = pre
    with patch.object(storage.pipeline, "complete_pipeline_run", side_effect=complete) as finish:
        task = asyncio.create_task(_run_pipeline_with_storage(settings, storage, dry_run=True))
        try:
            await asyncio.wait_for(working.wait(), 2)
            task.cancel()
            await asyncio.wait_for(finalizing.wait(), 2)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
    finish.assert_awaited_once()
    assert (await storage.pipeline.get_last_pipeline_run())["status"] == "cancelled"
    assert [entry["finalized"] for entry in logs if entry["event"] == "pipeline_cancelled"] == [
        True
    ]


@pytest.mark.parametrize("failure_point", ["construction", "entry", "exit"])
async def test_notifier_setup_or_cleanup_failure_never_reports_success(
    settings, storage, boundaries, logs, failure_point
):
    failure = RuntimeError("synthetic notifier failure")
    if failure_point == "construction":
        boundaries.factory.side_effect = failure
    elif failure_point == "entry":
        boundaries.notifier.__aenter__.side_effect = failure
    else:
        boundaries.notifier.__aexit__.side_effect = failure
    with pytest.raises(RuntimeError, match="synthetic notifier failure"):
        await _run_pipeline_with_storage(settings, storage)
    assert (await storage.pipeline.get_last_pipeline_run())["status"] == "failed"
    assert not any(entry["event"] == "pipeline_complete" for entry in logs)
    if failure_point != "exit":
        assert phase_outcomes(logs)["setup"] == "failed"


async def test_exit_flush_failure_is_visible_without_replaying_batch(
    settings, storage, boundaries, logs
):
    async def pre(*args, recorder, progress, **kwargs):
        recorder.record(
            PropertyEvent("openrent:synthetic", "openrent", "criteria_passed", "criteria")
        )
        return progress

    boundaries.pre.side_effect = pre
    with (
        patch.object(
            storage.pipeline,
            "insert_property_events",
            side_effect=RuntimeError("audit unavailable"),
        ) as insert,
        pytest.raises(RuntimeError, match="audit unavailable"),
    ):
        await _run_pipeline_with_storage(settings, storage, dry_run=True)
    insert.assert_awaited_once()
    assert (await storage.pipeline.get_last_pipeline_run())["status"] == "failed"
    assert not any(entry["event"] == "pipeline_complete" for entry in logs)


async def test_failed_success_finalization_is_recorded_as_failure(
    settings, storage, boundaries, logs
):
    original = storage.pipeline.complete_pipeline_run

    async def fail_success(run_id, status, **kwargs):
        if status == "completed":
            raise RuntimeError("synthetic finalization failure")
        await original(run_id, status, **kwargs)

    with (
        patch.object(storage.pipeline, "complete_pipeline_run", side_effect=fail_success),
        pytest.raises(RuntimeError, match="synthetic finalization failure"),
    ):
        await _run_pipeline_with_storage(settings, storage, dry_run=True)
    assert (await storage.pipeline.get_last_pipeline_run())["status"] == "failed"
    assert phase_outcomes(logs)["finalization"] == "failed"
    assert not any(entry["event"] == "pipeline_complete" for entry in logs)


async def test_unavailable_finalization_preserves_original_failure(
    settings, storage, boundaries, logs
):
    boundaries.pre.side_effect = RuntimeError("original pipeline failure")
    with (
        patch.object(
            storage.pipeline,
            "complete_pipeline_run",
            side_effect=RuntimeError("database unavailable"),
        ),
        pytest.raises(RuntimeError, match="original pipeline failure"),
    ):
        await _run_pipeline_with_storage(settings, storage, dry_run=True)
    assert (await rows(storage, "pipeline_runs"))[0]["status"] == "running"
    assert any(entry["event"] == "pipeline_finalization_failed" for entry in logs)
    assert [entry["finalized"] for entry in logs if entry["event"] == "pipeline_failed"] == [False]


async def test_run_creation_failure_restores_context_without_provider_work(
    settings, storage, boundaries, logs
):
    with (
        structlog.contextvars.bound_contextvars(trial_id="setup-trial", run_id=914),
        patch.object(
            storage.pipeline, "create_pipeline_run", side_effect=RuntimeError("creation failed")
        ),
    ):
        before = structlog.contextvars.get_contextvars()
        with pytest.raises(RuntimeError, match="creation failed"):
            await _run_pipeline_with_storage(settings, storage, dry_run=True)
        assert structlog.contextvars.get_contextvars() == before
    assert await rows(storage, "pipeline_runs") == []
    assert phase_outcomes(logs)["setup"] == "failed"
    boundaries.pre.assert_not_awaited()
    assert_no_telegram(boundaries)


@pytest.mark.parametrize("primary_error", [RuntimeError, asyncio.CancelledError])
async def test_internally_cancelled_terminal_write_does_not_claim_finalization(
    settings, storage, boundaries, logs, primary_error
):
    boundaries.pre.side_effect = primary_error("original failure")
    with (
        patch.object(
            storage.pipeline, "complete_pipeline_run", side_effect=asyncio.CancelledError
        ) as finish,
        pytest.raises(primary_error, match="original failure"),
    ):
        await _run_pipeline_with_storage(settings, storage, dry_run=True)
    finish.assert_awaited_once()
    assert (await rows(storage, "pipeline_runs"))[0]["status"] == "running"
    assert any(entry["event"] == "pipeline_finalization_cancelled" for entry in logs)
    terminal = [
        entry
        for entry in logs
        if entry["event"] in {"pipeline_failed", "pipeline_cancelled", "pipeline_complete"}
    ]
    assert len(terminal) == 1
    assert terminal[0]["finalized"] is False

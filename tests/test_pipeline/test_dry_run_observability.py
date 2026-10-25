"""Run accounting with real in-memory storage and synthetic work/provider boundaries."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import structlog
from pydantic import SecretStr

from home_finder.analysis.analyzer import TokenUsage
from home_finder.config import Settings
from home_finder.db import PropertyStorage
from home_finder.models.notifications import NotificationPolicy
from home_finder.notifiers.telegram import TelegramNotifier
from home_finder.pipeline.contracts import (
    CandidateBatch,
    CandidateProgress,
    InitialAssessmentSummary,
    ReanalysisSummary,
    ScraperMetrics,
)
from home_finder.pipeline.event_recorder import PropertyEvent
from home_finder.pipeline.flow import _run_pipeline_with_storage
from home_finder.pipeline.reanalysis import process_reanalysis_queue


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
        notification_policy=NotificationPolicy(min_fit_score=0),
    )


@pytest.fixture
def boundaries(storage):
    notifier = AsyncMock()
    notifier.__aenter__.return_value = notifier
    notifier.deliver_pending.return_value = 0
    with (
        patch("home_finder.pipeline.flow.TelegramNotifier", return_value=notifier) as factory,
        patch("home_finder.pipeline.flow.prepare_candidates", return_value=CandidateBatch()) as pre,
        patch(
            "home_finder.pipeline.flow.process_reanalysis_queue", return_value=ReanalysisSummary()
        ) as reanalysis,
        patch("home_finder.pipeline.assessment._lookup_wards", new_callable=AsyncMock),
        patch.object(storage.deliveries, "recover", wraps=storage.deliveries.recover) as recover,
        patch.object(
            storage.runs, "cleanup_old_events", wraps=storage.runs.cleanup_old_events
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
    analyzer = AsyncMock()
    analyzer.__aenter__.return_value = analyzer
    analyzer.analyze_single_merged.return_value = merged, quality
    analyzer.token_usage = usage
    settings.enable_quality_filter = True
    settings.anthropic_api_key = SecretStr("synthetic-test-key")
    boundaries.pre.return_value = CandidateBatch(
        candidates=[merged],
        progress=CandidateProgress(
            observed_listing_count=1, media_ready_count=1, prepared_candidate_count=1
        ),
    )

    with patch("home_finder.pipeline.assessment.PropertyQualityAnalyzer", return_value=analyzer):
        await _run_pipeline_with_storage(settings, storage, dry_run=True)

    run = await storage.runs.get_last_pipeline_run()
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
        (run["id"], merged.unique_id, "assessment_completed")
    ]
    prop = await storage.get_property(merged.unique_id)
    assert prop.notification_status.value == "pending"
    delivery = await storage.deliveries.get(f"discovery:{merged.unique_id}")
    assert delivery["state"] == "queued"
    assert_no_telegram(boundaries)
    outcomes = phase_outcomes(logs)
    assert outcomes["delivery_recovery"] == outcomes["delivery_drain"] == "skipped"
    assert outcomes["initial_assessment"] == outcomes["reanalysis"] == "completed"
    terminal = [entry for entry in logs if entry["event"] == "pipeline_complete"]
    assert len(terminal) == 1
    assert terminal[0]["dry_run"] is True
    assert terminal[0]["run_id"] == run["id"]
    assert terminal[0]["final_drain_completed_deliveries"] == 0
    assert "queued_deliveries" not in terminal[0]


async def test_full_run_applies_independent_initial_and_reanalysis_limits(
    settings, storage, boundaries, logs, make_merged_property, make_quality_analysis
):
    initial = [make_merged_property() for _ in range(2)]
    repeated = [make_merged_property() for _ in range(2)]
    quality = make_quality_analysis()
    for merged in repeated:
        await storage.processing.save_pre_analysis_properties([merged], {})
        await storage.processing.complete_analysis(merged.unique_id, quality)
        await storage.processing.request_reanalysis([merged.unique_id])

    settings.enable_quality_filter = True
    settings.anthropic_api_key = SecretStr("synthetic-test-key")
    settings.max_initial_analyses_per_run = 1
    settings.max_reanalyses_per_run = 1
    boundaries.pre.return_value = CandidateBatch(candidates=initial)
    boundaries.reanalysis.side_effect = process_reanalysis_queue
    analyzer = AsyncMock()
    analyzer.__aenter__.return_value = analyzer
    analyzer.token_usage = TokenUsage()
    analyzer.analyze_single_merged.side_effect = lambda merged, **kwargs: (merged, quality)
    with (
        patch("home_finder.pipeline.assessment.PropertyQualityAnalyzer", return_value=analyzer),
        patch(
            "home_finder.pipeline.image_recovery.restore_missing_analysis_images",
            side_effect=lambda queue, *_: queue,
        ),
    ):
        await _run_pipeline_with_storage(settings, storage, dry_run=True)

    calls = [call.args[0].unique_id for call in analyzer.analyze_single_merged.await_args_list]
    assert len(calls) == 2
    assert len(set(calls) & {m.unique_id for m in initial}) == 1
    assert len(set(calls) & {m.unique_id for m in repeated}) == 1
    assert len(await storage.processing.get_pending_analysis_properties()) == 1
    assert len(await storage.processing.get_reanalysis_queue()) == 1
    phases = {
        entry["phase"]: entry for entry in logs if entry["event"] == "pipeline_phase_finished"
    }
    assert phases["initial_assessment"]["assessed"] == 1
    assert phases["initial_assessment"]["deferred"] == 1
    assert phases["reanalysis"]["processed"] == 1
    assert phases["reanalysis"]["deferred"] == 1
    assert_no_telegram(boundaries)


@pytest.mark.parametrize("analysis_enabled", [True, False])
async def test_assessment_event_precedes_delivery_and_distinguishes_skipped_analysis(
    settings,
    storage,
    boundaries,
    logs,
    make_merged_property,
    make_quality_analysis,
    analysis_enabled,
):
    merged = make_merged_property(image_url="https://example.com/fixture.jpg")
    quality = make_quality_analysis()
    settings.enable_quality_filter = analysis_enabled
    settings.anthropic_api_key = SecretStr("synthetic-test-key")
    boundaries.pre.return_value = CandidateBatch(candidates=[merged])
    boundaries.notifier.send_merged_property_notification.return_value = True
    analyzer = AsyncMock()
    analyzer.__aenter__.return_value = analyzer
    analyzer.analyze_single_merged.return_value = merged, quality
    analyzer.token_usage = TokenUsage()
    with (
        patch("home_finder.pipeline.assessment.PropertyQualityAnalyzer", return_value=analyzer),
        patch("home_finder.pipeline.flow.asyncio.sleep", new_callable=AsyncMock),
    ):
        await _run_pipeline_with_storage(settings, storage)

    events = await rows(storage, "property_events")
    assert [event["event_type"] for event in events] == [
        "assessment_completed" if analysis_enabled else "analysis_skipped",
        "notified",
    ]
    terminal = next(entry for entry in logs if entry["event"] == "pipeline_complete")
    assert terminal["assessed"] == int(analysis_enabled)
    assert terminal["analysis_skipped"] == int(not analysis_enabled)
    assert terminal["initial_assessments_processed"] == 1


async def test_terminal_delivery_count_only_includes_final_drain_completions(
    settings, storage, boundaries, logs, make_merged_property, make_quality_analysis
):
    recovered = make_merged_property(image_url="https://example.com/recovered.jpg")
    final = make_merged_property(image_url="https://example.com/final.jpg")
    deferred = make_merged_property()

    async def enqueue(merged):
        await storage.processing.save_pre_analysis_properties([merged], {})
        await storage.processing.complete_analysis(
            merged.unique_id,
            make_quality_analysis(),
            notification_policy=settings.notification_policy,
        )

    await enqueue(recovered)

    async def reanalyze(*args):
        await enqueue(final)
        await enqueue(deferred)
        return ReanalysisSummary(completed=2)

    boundaries.reanalysis.side_effect = reanalyze
    sender = TelegramNotifier(
        bot_token="123456:fixture", chat_id=123, delivery_repository=storage.deliveries
    )
    bot = AsyncMock()
    bot.send_photo.return_value = SimpleNamespace(message_id=11, chat=SimpleNamespace(id=123))
    sender._bot = bot
    boundaries.factory.return_value = sender

    with patch("home_finder.notifiers.telegram.asyncio.sleep", new_callable=AsyncMock):
        await _run_pipeline_with_storage(settings, storage)

    assert bot.send_photo.await_count == 2
    for merged in (recovered, final):
        delivery = await storage.deliveries.get(f"discovery:{merged.unique_id}")
        assert delivery["state"] == "complete"
        assert delivery["receipts_json"] == "[[11]]"
    pending = await storage.deliveries.get(f"discovery:{deferred.unique_id}")
    assert pending["state"] == "queued"
    assert pending["reason"] == "no_images"
    recovery = next(entry for entry in logs if entry["event"] == "delivery_recovery_complete")
    assert recovery["completed"] == 1
    terminal = next(entry for entry in logs if entry["event"] == "pipeline_complete")
    assert terminal["final_drain_completed_deliveries"] == 1
    assert terminal["callback_deliveries_completed"] == 0
    assert "queued_deliveries" not in terminal


async def test_completed_phase_keeps_deferred_property_visible(
    settings, storage, boundaries, logs, make_merged_property
):
    merged = make_merged_property()
    settings.enable_quality_filter = True
    settings.anthropic_api_key = SecretStr("synthetic-test-key")
    boundaries.pre.return_value = CandidateBatch(candidates=[merged])
    analyzer = AsyncMock()
    analyzer.__aenter__.return_value = analyzer
    analyzer.analyze_single_merged.return_value = merged, None
    analyzer.token_usage = TokenUsage()
    with patch("home_finder.pipeline.assessment.PropertyQualityAnalyzer", return_value=analyzer):
        await _run_pipeline_with_storage(settings, storage, dry_run=True)

    run = await storage.runs.get_last_pipeline_run()
    assert run["status"] == "completed"
    assert run["analyzed_count"] == 0
    assert phase_outcomes(logs)["initial_assessment"] == "completed"
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
    boundaries.pre.return_value = CandidateBatch(
        progress=CandidateProgress(
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
    )
    await _run_pipeline_with_storage(settings, storage, dry_run=True)
    run = await storage.runs.get_last_pipeline_run()
    assert run["status"] == status
    assert run["completed_at"] is not None
    assert run["scraped_count"] == found
    assert run["total_input_tokens"] is None
    assert run["estimated_cost_usd"] is None
    metrics = (await rows(storage, "scraper_runs"))[0]
    assert metrics["pipeline_run_id"] == run["id"]
    assert metrics["is_healthy"] == healthy
    assert phase_outcomes(logs)["candidate_preparation"] == status
    assert_no_telegram(boundaries)


@pytest.mark.parametrize("dry_run", [False, True])
async def test_run_logs_restore_caller_context(settings, storage, boundaries, logs, dry_run):
    with structlog.contextvars.bound_contextvars(
        trial_id="synthetic-trial", run_id=912, dry_run="caller-mode"
    ):
        before = structlog.contextvars.get_contextvars()
        await _run_pipeline_with_storage(settings, storage, dry_run=dry_run)
        assert structlog.contextvars.get_contextvars() == before
    run = await storage.runs.get_last_pipeline_run()
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
        ("candidate_preparation", "prepare_candidates"),
        ("initial_assessment", "process_initial_assessments"),
        ("reanalysis", "process_reanalysis_queue"),
    ],
)
async def test_phase_failures_are_terminal_and_restore_context(
    settings, storage, boundaries, logs, phase, target
):
    with (
        structlog.contextvars.bound_contextvars(trial_id="failure-trial", run_id=913),
        patch(
            f"home_finder.pipeline.flow.{target}",
            side_effect=RuntimeError("synthetic phase failure"),
        ),
    ):
        before = structlog.contextvars.get_contextvars()
        with pytest.raises(RuntimeError, match="synthetic phase failure"):
            await _run_pipeline_with_storage(settings, storage, dry_run=True)
        assert structlog.contextvars.get_contextvars() == before
    run = await storage.runs.get_last_pipeline_run()
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
        patch(
            "home_finder.pipeline.flow.process_initial_assessments",
            return_value=InitialAssessmentSummary(assessed=2, token_usage=usage),
        ),
        pytest.raises(RuntimeError, match="synthetic reanalysis failure"),
    ):
        await _run_pipeline_with_storage(settings, storage, dry_run=True)
    run = await storage.runs.get_last_pipeline_run()
    assert run["status"] == "failed"
    assert run["analyzed_count"] == 2
    assert run["total_input_tokens"] == 50
    assert run["estimated_cost_usd"] == usage.estimated_cost_usd


@pytest.mark.parametrize(
    ("operation", "phase"),
    [
        ("create_pipeline_run", "setup"),
        ("insert_property_events", "candidate_preparation"),
        ("complete_pipeline_run", "finalization"),
    ],
)
async def test_repeated_cancellation_settles_one_audit_insert_and_finalizes(
    settings, storage, boundaries, logs, operation, phase
):
    entered, release = asyncio.Event(), asyncio.Event()
    original = getattr(storage.runs, operation)

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
        return CandidateBatch(progress=progress)

    boundaries.pre.side_effect = pre

    async def run():
        with structlog.contextvars.bound_contextvars(trial_id="cancel-trial", run_id=915):
            before = structlog.contextvars.get_contextvars()
            try:
                await _run_pipeline_with_storage(settings, storage, dry_run=True)
            finally:
                assert structlog.contextvars.get_contextvars() == before

    with patch.object(storage.runs, operation, side_effect=blocked) as operation_mock:
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
    run = await storage.runs.get_last_pipeline_run()
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
    original = storage.runs.complete_pipeline_run

    async def pre(*args, **kwargs):
        working.set()
        await asyncio.Future()

    async def complete(*args, **kwargs):
        finalizing.set()
        await release.wait()
        await original(*args, **kwargs)

    boundaries.pre.side_effect = pre
    with patch.object(storage.runs, "complete_pipeline_run", side_effect=complete) as finish:
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
    assert (await storage.runs.get_last_pipeline_run())["status"] == "cancelled"
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
    assert (await storage.runs.get_last_pipeline_run())["status"] == "failed"
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
        return CandidateBatch(progress=progress)

    boundaries.pre.side_effect = pre
    with (
        patch.object(
            storage.runs,
            "insert_property_events",
            side_effect=RuntimeError("audit unavailable"),
        ) as insert,
        pytest.raises(RuntimeError, match="audit unavailable"),
    ):
        await _run_pipeline_with_storage(settings, storage, dry_run=True)
    insert.assert_awaited_once()
    assert (await storage.runs.get_last_pipeline_run())["status"] == "failed"
    assert not any(entry["event"] == "pipeline_complete" for entry in logs)


async def test_failed_success_finalization_is_recorded_as_failure(
    settings, storage, boundaries, logs
):
    original = storage.runs.complete_pipeline_run

    async def fail_success(run_id, status, **kwargs):
        if status == "completed":
            raise RuntimeError("synthetic finalization failure")
        await original(run_id, status, **kwargs)

    with (
        patch.object(storage.runs, "complete_pipeline_run", side_effect=fail_success),
        pytest.raises(RuntimeError, match="synthetic finalization failure"),
    ):
        await _run_pipeline_with_storage(settings, storage, dry_run=True)
    assert (await storage.runs.get_last_pipeline_run())["status"] == "failed"
    assert phase_outcomes(logs)["finalization"] == "failed"
    assert not any(entry["event"] == "pipeline_complete" for entry in logs)


async def test_unavailable_finalization_preserves_original_failure(
    settings, storage, boundaries, logs
):
    boundaries.pre.side_effect = RuntimeError("original pipeline failure")
    with (
        patch.object(
            storage.runs,
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
            storage.runs, "create_pipeline_run", side_effect=RuntimeError("creation failed")
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
            storage.runs, "complete_pipeline_run", side_effect=asyncio.CancelledError
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


@pytest.mark.parametrize("primary_type", [asyncio.CancelledError, ValueError, None])
@pytest.mark.parametrize(
    ("phase", "operation"),
    [
        ("candidate_preparation", "candidate_preparation_accounting"),
        ("initial_assessment", "initial_assessment_accounting"),
        ("reanalysis", "reanalysis_accounting"),
        ("delivery_drain", "notification_accounting"),
    ],
)
async def test_phase_accounting_failure_preserves_primary_exception(
    settings, storage, boundaries, logs, primary_type, phase, operation
):
    primary = primary_type("original phase failure") if primary_type is not None else None
    audit_error = RuntimeError("secondary accounting failure")
    original_update = storage.runs.update_pipeline_run

    async def update(run_id, **counts):
        target = (
            (phase == "candidate_preparation" and "scraped_count" in counts)
            or (phase == "initial_assessment" and "notified_count" in counts)
            or (phase == "reanalysis" and set(counts) == {"analysis_seconds"})
            or (phase == "delivery_drain" and "notification_seconds" in counts)
        )
        if target:
            raise audit_error
        await original_update(run_id, **counts)

    if phase == "candidate_preparation":
        boundaries.pre.side_effect = primary
    elif phase == "reanalysis":
        boundaries.reanalysis.side_effect = primary
    elif phase == "delivery_drain":
        boundaries.notifier.deliver_pending.side_effect = [0, primary if primary else 0]
    dry_run = phase != "delivery_drain"
    expected = primary if primary is not None else audit_error
    with (
        structlog.contextvars.bound_contextvars(trial_id="accounting-trial", run_id=916),
        patch(
            "home_finder.pipeline.flow.process_initial_assessments",
            return_value=InitialAssessmentSummary(),
            side_effect=primary if phase == "initial_assessment" else None,
        ),
        patch.object(storage.runs, "update_pipeline_run", side_effect=update),
    ):
        before = structlog.contextvars.get_contextvars()
        with pytest.raises(type(expected)) as raised:
            await _run_pipeline_with_storage(settings, storage, dry_run=dry_run)
        assert raised.value is expected
        assert structlog.contextvars.get_contextvars() == before

    status = "cancelled" if primary_type is asyncio.CancelledError else "failed"
    run = await storage.runs.get_last_pipeline_run()
    assert run["status"] == status
    assert run["error_message"] == (None if status == "cancelled" else str(expected))
    assert phase_outcomes(logs)[phase] == status
    secondary_logs = [entry for entry in logs if entry["event"] == "cleanup_failed"]
    if primary is not None:
        assert len(secondary_logs) == 1
        assert secondary_logs[0]["operation"] == operation
        assert secondary_logs[0]["error_type"] == "RuntimeError"
        assert secondary_logs[0]["primary_error_type"] == primary_type.__name__
    else:
        assert secondary_logs == []
    assert not any(entry["event"] == "pipeline_complete" for entry in logs)
    if dry_run:
        assert_no_telegram(boundaries)


@pytest.mark.parametrize("primary_type", [asyncio.CancelledError, ValueError, None])
@pytest.mark.parametrize("resource", ["event_flush", "notifier_exit"])
async def test_resource_cleanup_failure_preserves_primary_exception(
    settings, storage, boundaries, logs, primary_type, resource
):
    primary = primary_type("original body failure") if primary_type is not None else None
    cleanup_error = RuntimeError("secondary resource cleanup failure")

    async def pre(*args, recorder, progress, **kwargs):
        recorder.record(
            PropertyEvent("openrent:synthetic", "openrent", "criteria_passed", "criteria")
        )
        if primary is not None:
            raise primary
        return CandidateBatch(progress=progress)

    boundaries.pre.side_effect = pre
    if resource == "notifier_exit":
        boundaries.notifier.__aexit__.side_effect = cleanup_error
    expected = primary if primary is not None else cleanup_error
    with patch.object(
        storage.runs,
        "insert_property_events",
        wraps=storage.runs.insert_property_events,
        side_effect=cleanup_error if resource == "event_flush" else None,
    ) as insert:
        with pytest.raises(type(expected)) as raised:
            await _run_pipeline_with_storage(settings, storage, dry_run=resource == "event_flush")
        assert raised.value is expected
    insert.assert_awaited_once()
    status = "cancelled" if primary_type is asyncio.CancelledError else "failed"
    run = await storage.runs.get_last_pipeline_run()
    assert run["status"] == status
    assert run["error_message"] == (None if status == "cancelled" else str(expected))
    secondary_logs = [entry for entry in logs if entry["event"] == "cleanup_failed"]
    if primary is not None:
        assert len(secondary_logs) == 1
        assert secondary_logs[0]["operation"] == (
            "property_event_flush" if resource == "event_flush" else "pipeline_resources"
        )
        assert secondary_logs[0]["error_type"] == "RuntimeError"
        assert secondary_logs[0]["primary_error_type"] == primary_type.__name__
    else:
        assert secondary_logs == []
    assert not any(entry["event"] == "pipeline_complete" for entry in logs)


@pytest.mark.parametrize("cancel, cleanup_fails", [(True, False), (False, True), (True, True)])
@pytest.mark.parametrize(
    "boundary",
    [
        "candidate_preparation",
        "quality_analysis",
        "reanalysis",
        "notification",
        "event_insert",
        "event_exit",
        "notifier_exit",
        "run_creation",
        "run_completion",
    ],
)
async def test_first_cancellation_during_cleanup_survives_later_write_failure(
    settings, storage, boundaries, logs, boundary, cancel, cleanup_fails
):
    entered, release = asyncio.Event(), asyncio.Event()
    cleanup_calls = 0
    audit_error = RuntimeError("later cleanup failure")

    async def cleanup():
        nonlocal cleanup_calls
        cleanup_calls += 1
        entered.set()
        await release.wait()
        if cleanup_fails:
            raise audit_error

    original_update = storage.runs.update_pipeline_run
    original_insert = storage.runs.insert_property_events
    original_create = storage.runs.create_pipeline_run
    original_complete = storage.runs.complete_pipeline_run

    async def update(run_id, **counts):
        if (
            (boundary == "candidate_preparation" and "scraped_count" in counts)
            or (boundary == "quality_analysis" and "notified_count" in counts)
            or (boundary == "reanalysis" and set(counts) == {"analysis_seconds"})
            or (boundary == "notification" and "notification_seconds" in counts)
        ):
            await cleanup()
        await original_update(run_id, **counts)

    async def insert(*args, **kwargs):
        if boundary in {"event_insert", "event_exit"}:
            await cleanup()
        await original_insert(*args, **kwargs)

    async def create():
        if boundary == "run_creation":
            await cleanup()
        return await original_create()

    async def complete(run_id, status, **kwargs):
        if boundary == "run_completion" and status == "completed":
            await cleanup()
        await original_complete(run_id, status, **kwargs)

    async def pre(*args, recorder, progress, **kwargs):
        if boundary in {"event_insert", "event_exit"}:
            recorder.record(
                PropertyEvent("openrent:synthetic", "openrent", "criteria_passed", "criteria")
            )
        if boundary == "event_insert":
            await recorder.flush()
        return CandidateBatch(progress=progress)

    async def exit_notifier(*args):
        await cleanup()

    boundaries.pre.side_effect = pre
    if boundary == "notifier_exit":
        boundaries.notifier.__aexit__.side_effect = exit_notifier
    dry_run = boundary not in {"notification", "notifier_exit"}

    async def run():
        with structlog.contextvars.bound_contextvars(trial_id="cleanup-race", run_id=917):
            before = structlog.contextvars.get_contextvars()
            try:
                await _run_pipeline_with_storage(settings, storage, dry_run=dry_run)
            finally:
                assert structlog.contextvars.get_contextvars() == before

    with (
        patch(
            "home_finder.pipeline.flow.process_initial_assessments",
            return_value=InitialAssessmentSummary(),
        ),
        patch.object(storage.runs, "update_pipeline_run", side_effect=update),
        patch.object(storage.runs, "insert_property_events", side_effect=insert),
        patch.object(storage.runs, "create_pipeline_run", side_effect=create),
        patch.object(storage.runs, "complete_pipeline_run", side_effect=complete),
    ):
        task = asyncio.create_task(run())
        try:
            await asyncio.wait_for(entered.wait(), 2)
            if cancel:
                task.cancel("first cancellation during cleanup")
                await asyncio.sleep(0)
                task.cancel("repeated cancellation")
                await asyncio.sleep(0)
                assert not task.done()
        finally:
            release.set()
        if cancel:
            with pytest.raises(asyncio.CancelledError, match="first cancellation during cleanup"):
                await asyncio.wait_for(task, 2)
        else:
            with pytest.raises(RuntimeError) as raised:
                await asyncio.wait_for(task, 2)
            assert raised.value is audit_error

    assert task.cancelled() is cancel
    assert cleanup_calls == 1
    run_row = await storage.runs.get_last_pipeline_run()
    if boundary == "run_creation" and cleanup_fails:
        assert run_row is None
        assert await rows(storage, "pipeline_runs") == []
    else:
        assert run_row["status"] == ("cancelled" if cancel else "failed")
    secondary_logs = [entry for entry in logs if entry["event"] == "cleanup_failed"]
    assert len(secondary_logs) == int(cancel and cleanup_fails)
    if secondary_logs:
        assert secondary_logs[0]["error_type"] == "RuntimeError"
        assert secondary_logs[0]["primary_error_type"] == "CancelledError"
    assert not any(entry["event"] == "pipeline_complete" for entry in logs)
    if dry_run:
        assert_no_telegram(boundaries)

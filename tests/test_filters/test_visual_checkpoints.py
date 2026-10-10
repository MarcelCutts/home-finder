"""Restart and crash boundaries for paid visual observations, using the real SDK."""

import asyncio
import base64
import copy
import json
from io import BytesIO
from unittest.mock import AsyncMock, patch

import httpx2
import pytest
from PIL import Image
from pydantic import SecretStr

from home_finder.config import Settings
from home_finder.db import PropertyStorage
from home_finder.filters.analysis_spend import visual_input_key
from home_finder.filters.quality import APIUnavailableError, PropertyQualityFilter
from home_finder.models.analysis import AnalysisConfig
from home_finder.models.spend import ConfirmedCost, SpendDeferred, SpendPolicy, nanousd, usd_text
from home_finder.pipeline.analysis import _run_quality_and_save
from home_finder.pipeline.spend_admin import administer
from home_finder.pipeline.stages import PreAnalysisResult
from tests.test_filters.test_quality_respx import (
    PHASE1_VISUAL_RESPONSE,
    PHASE2_EVALUATION_RESPONSE,
)

CONTENT = [{"type": "text", "text": "Visual checkpoint fixture"}]


def config():
    phase = {"sdk_retries": 0, "input_token_limit": 140000, "max_output_tokens": 8192}
    return AnalysisConfig.model_validate({"visual": phase, "evaluation": phase})


def policy(**changes):
    return SpendPolicy.model_validate(
        {"per_call_usd": "1", "daily_usd": "10", "monthly_usd": "10", **changes}
    )


def unavailable():
    return httpx2.Response(
        503, json={"type": "error", "error": {"type": "api_error", "message": "fixture"}}
    )


def quality(storage, respond, *, settings=None, budget=None):
    return PropertyQualityFilter(
        "fixture",
        analysis_config=settings or config(),
        spend_repository=storage.spend,
        spend_policy=budget or policy(),
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(respond)),
    )


async def checkpoint_rows(storage):
    async with storage._read_connection() as conn:
        cursor = await conn.execute("SELECT * FROM visual_checkpoints")
        return [dict(row) for row in await cursor.fetchall()]


@pytest.mark.parametrize("refresh", [False, True])
async def test_two_evaluation_outages_then_restart_uses_one_visual_call(
    tmp_path, cached_analysis_property, make_quality_analysis, refresh
):
    merged, images = cached_analysis_property
    database = str(tmp_path / "restart.db")
    calls = {"visual": 0, "evaluation": 0}

    def respond(request):
        phase = "visual" if "tools" in json.loads(request.content) else "evaluation"
        calls[phase] += 1
        if phase == "visual":
            return httpx2.Response(200, json=PHASE1_VISUAL_RESPONSE)
        if calls[phase] < 3:
            return unavailable()
        return httpx2.Response(200, json=PHASE2_EVALUATION_RESPONSE)

    async with PropertyStorage(database) as storage:
        await storage.pipeline.save_pre_analysis_properties([merged], {})
        if refresh:
            await storage.pipeline.complete_analysis(merged.unique_id, make_quality_analysis())
            await storage.mark_notified(merged.unique_id)
            await storage.pipeline.request_reanalysis([merged.unique_id])
    for attempt in range(3):
        async with PropertyStorage(database) as storage:
            async with quality(storage, respond) as model:
                if attempt < 2:
                    with pytest.raises(APIUnavailableError):
                        await model.analyze_single_merged(merged, data_dir=images)
                    assert len(await checkpoint_rows(storage)) == 1
                    checkpoint_report = (await storage.spend.report())["visual_checkpoints"]
                    assert len(checkpoint_report) == 1
                    assert set(checkpoint_report[0]) == {
                        "property_id",
                        "input_sha256",
                        "attempt_id",
                        "created_at",
                    }
                else:
                    _, analysis = await model.analyze_single_merged(merged, data_dir=images)
                    assert analysis and analysis.overall_rating == 4
                    assert analysis.analysis_provenance.visual_input_sha256
                    if refresh:
                        await storage.pipeline.complete_reanalysis(merged.unique_id, analysis)
                    else:
                        await storage.pipeline.complete_analysis(merged.unique_id, analysis)
                    assert await checkpoint_rows(storage) == []
                    detail = await storage.web.get_property_detail(merged.unique_id)
                    assert detail["notification_status"] == ("sent" if refresh else "pending")
            report = await storage.spend.report()
    assert calls == {"visual": 1, "evaluation": 3}
    assert report["attempts"] == 4 and report["held_nanousd"] == 0


@pytest.mark.parametrize("confirmed_amount", ["unchanged", "0", "0.25"])
async def test_reconciled_visual_receipt_recovers_pending_analysis_after_restart(
    tmp_path, cached_analysis_property, confirmed_amount
):
    merged, _ = cached_analysis_property
    database = tmp_path / "reconciled-restart.db"
    settings = Settings(
        _env_file=None,
        database_path=str(database),
        anthropic_api_key=SecretStr("fixture"),
        enable_quality_filter=True,
        analysis_config=config(),
        analysis_spend_policy=policy(),
    )
    calls = {"visual": 0, "evaluation": 0}
    on_result = AsyncMock()

    def respond(request):
        phase = "visual" if "tools" in json.loads(request.content) else "evaluation"
        calls[phase] += 1
        if phase == "visual":
            return httpx2.Response(200, json=PHASE1_VISUAL_RESPONSE)
        if calls[phase] == 1:
            return unavailable()
        return httpx2.Response(200, json=PHASE2_EVALUATION_RESPONSE)

    def make_filter(**kwargs):
        return PropertyQualityFilter(
            **kwargs, http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(respond))
        )

    with (
        patch("home_finder.pipeline.analysis.PropertyQualityFilter", side_effect=make_filter),
        patch("home_finder.pipeline.analysis._lookup_wards", new_callable=AsyncMock),
    ):
        async with PropertyStorage(str(database)) as storage:
            count, _ = await _run_quality_and_save(
                PreAnalysisResult(merged_to_process=[merged]), settings, storage, on_result
            )
            assert count == 0
            on_result.assert_not_awaited()
            checkpoint = (await checkpoint_rows(storage))[0]
            async with storage._read_connection() as conn:
                receipt = await storage.spend._attempt(conn, checkpoint["attempt_id"])
            original_settlement = receipt["settlement_json"]
            amount = (
                usd_text(receipt["accounted_nanousd"])
                if confirmed_amount == "unchanged"
                else confirmed_amount
            )

        # Use the supported operator command between database lifetimes, then
        # let the pipeline reconstruct the pending home and consume its checkpoint.
        confirmed = ConfirmedCost(amount_usd=amount, evidence_ref="invoice:visual-fixture")
        await administer(database, checkpoint["attempt_id"], confirmed=confirmed)
        async with PropertyStorage(str(database)) as storage:
            count, _ = await _run_quality_and_save(
                PreAnalysisResult(), settings, storage, on_result
            )
            assert count == 1
            on_result.assert_awaited_once()
            analysis = await storage.get_quality_analysis(merged.unique_id)
            assert analysis.overall_rating == 4
            assert analysis.analysis_provenance.visual_input_sha256 == checkpoint["input_sha256"]
            assert not await storage.pipeline.get_pending_analysis_properties()
            assert await checkpoint_rows(storage) == []
            assert (await storage.get_property(merged.unique_id)).notification_status == "pending"
            async with storage._read_connection() as conn:
                receipt = await storage.spend._attempt(conn, checkpoint["attempt_id"])
            assert receipt["settlement_json"] == original_settlement
            assert receipt["cost_basis"] == "operator_confirmed"
            assert receipt["accounted_nanousd"] == nanousd(confirmed.amount_usd)
            report = await storage.spend.report()
            assert report["attempts"] == 3 and report["held_nanousd"] == 0
            assert report["lifetime_confirmed_nanousd"] == nanousd(confirmed.amount_usd)
    assert calls == {"visual": 1, "evaluation": 2}


async def test_billed_evaluation_failure_still_hits_its_attempt_limit(
    tmp_path, cached_analysis_property
):
    merged, images = cached_analysis_property
    calls = {"visual": 0, "evaluation": 0}

    def respond(request):
        phase = "visual" if "tools" in json.loads(request.content) else "evaluation"
        calls[phase] += 1
        result = (
            PHASE1_VISUAL_RESPONSE
            if phase == "visual"
            else {**PHASE2_EVALUATION_RESPONSE, "stop_reason": "max_tokens"}
        )
        return httpx2.Response(200, json=result)

    async with PropertyStorage(str(tmp_path / "limits.db")) as storage:
        for _ in range(2):
            async with quality(storage, respond) as model:
                _, analysis = await model.analyze_single_merged(merged, data_dir=images)
                assert analysis.analysis_failure.phase == "evaluation"
                assert analysis.analysis_failure.kind == "truncated"
        async with quality(storage, respond) as model:
            with pytest.raises(SpendDeferred, match="attempt_limit"):
                await model.analyze_single_merged(merged, data_dir=images)
        assert calls == {"visual": 1, "evaluation": 2}
        assert len(await checkpoint_rows(storage)) == 1
        assert (await storage.spend.report())["attempts"] == 3


@pytest.mark.parametrize("hold", ["uncertain_evaluation", "budget"])
async def test_reusing_visual_output_cannot_bypass_evaluation_holds(
    tmp_path, cached_analysis_property, hold
):
    merged, images = cached_analysis_property
    calls = []

    def respond(request):
        calls.append(json.loads(request.content))
        if "tools" in calls[-1]:
            return httpx2.Response(200, json=PHASE1_VISUAL_RESPONSE)
        if hold == "uncertain_evaluation":
            raise httpx2.ReadTimeout("fixture", request=request)
        return unavailable()

    async with PropertyStorage(str(tmp_path / "holds.db")) as storage:
        async with quality(storage, respond) as model:
            with pytest.raises(APIUnavailableError):
                await model.analyze_single_merged(merged, data_dir=images)
        before = await storage.spend.report()
        budget = policy(lifetime_usd="0.000001") if hold == "budget" else policy()
        async with quality(storage, respond, budget=budget) as model:
            reason = "lifetime_limit" if hold == "budget" else "operation_unresolved"
            with pytest.raises(SpendDeferred, match=reason):
                await model.analyze_single_merged(merged, data_dir=images)
        assert len(calls) == 2
        after = await storage.spend.report()
        assert {key: value for key, value in after.items() if key != "as_of"} == {
            key: value for key, value in before.items() if key != "as_of"
        }
        assert len(await checkpoint_rows(storage)) == 1


def image_block(colour):
    stream = BytesIO()
    Image.new("RGB", (32, 32), colour).save(stream, format="JPEG")
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/jpeg",
            "data": base64.b64encode(stream.getvalue()).decode(),
        },
    }


@pytest.mark.parametrize(
    "change", ["bytes", "order", "prompt", "visual_config", "schema", "evaluation_config"]
)
async def test_identity_tracks_effective_visual_inputs_only(tmp_path, monkeypatch, change):
    calls = []

    def respond(request):
        calls.append(request)
        return httpx2.Response(200, json=PHASE1_VISUAL_RESPONSE)

    content = [image_block("white"), image_block("blue"), {"type": "text", "text": "Rent 2000"}]
    async with PropertyStorage(str(tmp_path / "identity.db")) as storage:
        async with quality(storage, respond) as model:
            first = await model._run_visual_analysis(content, "fixture:identity")
            assert await model._run_visual_analysis(content, "fixture:identity") == first
        settings = config()
        if change == "bytes":
            content[0] = image_block("red")
        elif change == "order":
            content[0], content[1] = content[1], content[0]
        elif change == "prompt":
            content[-1]["text"] = "Rent 2100"
        elif change == "visual_config":
            settings = settings.model_copy(
                update={"visual": settings.visual.model_copy(update={"effort": "low"})}
            )
        elif change == "evaluation_config":
            settings = settings.model_copy(
                update={"evaluation": settings.evaluation.model_copy(update={"effort": "low"})}
            )
        else:
            from home_finder.filters import quality as module

            tool = copy.deepcopy(module.VISUAL_ANALYSIS_TOOL)
            tool["description"] += " Revised contract."
            monkeypatch.setattr(module, "VISUAL_ANALYSIS_TOOL", tool)
        async with quality(storage, respond, settings=settings) as model:
            assert await model._run_visual_analysis(content, "fixture:identity") == first
        assert len(calls) == (1 if change == "evaluation_config" else 2)
        assert len(await checkpoint_rows(storage)) == 1


@pytest.mark.parametrize("reconciled", [False, True])
@pytest.mark.parametrize(
    "corrupt",
    ["payload", "receipt", "property", "phase", "missing_usage", "invalid_usage", "failed_usage"],
)
async def test_corrupt_checkpoint_defers_without_respending(tmp_path, corrupt, reconciled):
    calls = []

    def respond(request):
        calls.append(request)
        return httpx2.Response(200, json=PHASE1_VISUAL_RESPONSE)

    async with PropertyStorage(str(tmp_path / "corrupt.db")) as storage:
        async with quality(storage, respond) as model:
            await model._run_visual_analysis(CONTENT, "fixture:corrupt")
            if reconciled:
                attempt = (await checkpoint_rows(storage))[0]["attempt_id"]
                await storage.spend.reconcile(
                    attempt, ConfirmedCost(amount_usd="0.01", evidence_ref="invoice:fixture")
                )
            async with storage._transaction() as conn:
                if corrupt == "payload":
                    await conn.execute("UPDATE visual_checkpoints SET data_json='{}'")
                elif corrupt == "receipt":
                    await conn.execute("UPDATE paid_attempts SET result='truncated'")
                elif corrupt == "property":
                    await conn.execute("UPDATE paid_attempts SET property_id='fixture:other'")
                elif corrupt == "phase":
                    await conn.execute("UPDATE paid_attempts SET phase='evaluation'")
                elif corrupt == "missing_usage":
                    await conn.execute("UPDATE paid_attempts SET settlement_json=NULL")
                elif corrupt == "invalid_usage":
                    await conn.execute("UPDATE paid_attempts SET settlement_json='{}'")
                else:
                    await conn.execute(
                        """UPDATE paid_attempts SET settlement_json=
                           json_set(settlement_json, '$.result', 'truncated')"""
                    )
            with pytest.raises(SpendDeferred, match="visual_checkpoint_invalid"):
                await model._run_visual_analysis(CONTENT, "fixture:corrupt")
        assert len(calls) == 1
        assert (await storage.spend.report())["attempts"] == 1


async def test_confirmation_above_reservation_blocks_checkpoint_reuse(tmp_path):
    calls = []

    def respond(request):
        calls.append(request)
        return httpx2.Response(200, json=PHASE1_VISUAL_RESPONSE)

    async with PropertyStorage(str(tmp_path / "over-reservation.db")) as storage:
        async with quality(storage, respond) as model:
            await model._run_visual_analysis(CONTENT, "fixture:over-reservation")
            attempt = (await checkpoint_rows(storage))[0]["attempt_id"]
            async with storage._read_connection() as conn:
                receipt = await storage.spend._attempt(conn, attempt)
            await storage.spend.reconcile(
                attempt,
                ConfirmedCost(
                    amount_usd=usd_text(receipt["reserved_nanousd"] + 1),
                    evidence_ref="invoice:above-reservation",
                ),
            )
            with pytest.raises(SpendDeferred, match="visual_checkpoint_invalid"):
                await model._run_visual_analysis(CONTENT, "fixture:over-reservation")
        assert len(calls) == 1
        report = await storage.spend.report()
        assert report["attempts"] == report["over_reservation_count"] == 1


@pytest.mark.parametrize("confirmed_amount", ["0", "0.01"])
async def test_confirmed_unknown_attempt_cannot_back_a_visual_checkpoint(
    tmp_path, confirmed_amount
):
    calls = []

    def respond(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx2.Response(200, json=PHASE1_VISUAL_RESPONSE)
        raise httpx2.ReadTimeout("fixture", request=request)

    async with PropertyStorage(str(tmp_path / "confirmed-unknown.db")) as storage:
        async with quality(storage, respond) as model:
            await model._run_visual_analysis(CONTENT, "fixture:unknown")
            with pytest.raises(APIUnavailableError):
                await model._run_visual_analysis(
                    [{"type": "text", "text": "Changed visual request"}], "fixture:unknown"
                )
            attempt = (await storage.spend.report())["unresolved"][0]["attempt_id"]
            await storage.spend.reconcile(
                attempt,
                ConfirmedCost(amount_usd=confirmed_amount, evidence_ref="invoice:unknown"),
            )
            async with storage._read_connection() as conn:
                receipt = await storage.spend._attempt(conn, attempt)
            assert receipt["state"] == "settled"
            assert receipt["cost_basis"] == "operator_confirmed"
            assert receipt["settlement_json"] is None
            # Even a matching, digest-valid checkpoint cannot borrow an unknown
            # attempt's billing confirmation as proof of successful visual output.
            async with storage._transaction() as conn:
                await conn.execute("UPDATE visual_checkpoints SET attempt_id=?", (attempt,))
            with pytest.raises(SpendDeferred, match="visual_checkpoint_invalid"):
                await model._run_visual_analysis(CONTENT, "fixture:unknown")
        assert len(calls) == 2
        report = await storage.spend.report()
        assert report["attempts"] == 2 and report["held_nanousd"] == 0


async def test_failed_checkpoint_write_keeps_charge_unresolved_and_blocks_redispatch(tmp_path):
    calls = []

    def respond(request):
        calls.append(request)
        return httpx2.Response(200, json=PHASE1_VISUAL_RESPONSE)

    async with PropertyStorage(str(tmp_path / "atomic.db")) as storage:
        async with storage._transaction() as conn:
            await conn.execute("""CREATE TRIGGER checkpoint_failure
                BEFORE INSERT ON visual_checkpoints
                BEGIN SELECT RAISE(ABORT, 'fixture checkpoint failure'); END""")
        async with quality(storage, respond) as model:
            with pytest.raises(APIUnavailableError, match="ledger_unavailable"):
                await model._run_visual_analysis(CONTENT, "fixture:atomic")
        assert await checkpoint_rows(storage) == []
        assert (await storage.spend.report())["unresolved"][0]["state"] == "dispatched"
        async with quality(storage, respond) as model:
            with pytest.raises(SpendDeferred, match="operation_unresolved"):
                await model._run_visual_analysis(CONTENT, "fixture:atomic")
        assert len(calls) == 1


async def test_cancellation_after_response_finishes_both_receipt_and_checkpoint(
    tmp_path, monkeypatch
):
    reached = asyncio.Event()
    finish = asyncio.Event()
    calls = []

    def respond(request):
        calls.append(request)
        return httpx2.Response(200, json=PHASE1_VISUAL_RESPONSE)

    async with PropertyStorage(str(tmp_path / "cancel.db")) as storage:
        original = storage.spend.settle_usage

        async def settle(*args, **kwargs):
            reached.set()
            await finish.wait()
            await original(*args, **kwargs)

        monkeypatch.setattr(storage.spend, "settle_usage", settle)
        async with quality(storage, respond) as model:
            task = asyncio.create_task(model._run_visual_analysis(CONTENT, "fixture:cancel"))
            await asyncio.wait_for(reached.wait(), timeout=5)
            task.cancel()
            finish.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert len(await checkpoint_rows(storage)) == 1
            assert (await storage.spend.report())["held_nanousd"] == 0
            await model._run_visual_analysis(CONTENT, "fixture:cancel")
            assert len(calls) == 1


async def test_old_completion_does_not_consume_checkpoint_for_changed_inputs(
    tmp_path, cached_analysis_property
):
    merged, images = cached_analysis_property

    def respond(request):
        result = (
            PHASE1_VISUAL_RESPONSE
            if "tools" in json.loads(request.content)
            else PHASE2_EVALUATION_RESPONSE
        )
        return httpx2.Response(200, json=result)

    async with PropertyStorage(str(tmp_path / "completion.db")) as storage:
        await storage.pipeline.save_pre_analysis_properties([merged], {})
        async with quality(storage, respond) as model:
            _, analysis = await model.analyze_single_merged(merged, data_dir=images)
            await model._run_visual_analysis(CONTENT, merged.unique_id)
            new_key = visual_input_key(
                merged.unique_id, model._visual_request(CONTENT), model._analysis_provenance()
            )
        await storage.pipeline.complete_analysis(merged.unique_id, analysis)
        assert (await checkpoint_rows(storage))[0]["input_sha256"] == new_key
        assert (await storage.spend.report())["attempts"] == 3

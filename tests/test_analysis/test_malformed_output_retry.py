"""A completed but malformed visual response is retried once, as a separately paid attempt.

Refusals and truncation are billed but not retried. Uses Sonnet 5.5 through the
real SDK; the retry is the analyzer's own, not the SDK's.
"""

import copy

import httpx2
import pytest

from home_finder.analysis.contracts import AnalysisOutputError
from home_finder.db import PropertyStorage
from home_finder.models.spend import SpendDeferred
from tests.support.analysis_config import configuration, policy
from tests.support.analysis_responses import (
    SONNET55,
    USAGE,
    failed_message,
    recorded_malformed_visual,
    response,
)
from tests.support.analysis_spend import CONTENT, quality, rows
from tests.support.visual_checkpoints import checkpoint_rows


def billed_visual_failure(scenario):
    output = failed_message("visual", scenario)
    output["model"] = SONNET55
    output["usage"] = copy.deepcopy(USAGE)
    return output


@pytest.mark.parametrize("case", ["c218d61fc0d__r2", "c6a9db682ca__r0"])
@pytest.mark.parametrize("recovered", [False, True])
async def test_recorded_malformed_receipt_retry_is_bounded_and_checkpointed(case, recovered):
    broken = response(thinking=False)
    broken["content"][0]["input"] = recorded_malformed_visual(case)
    outputs = iter([broken, response(thinking=False) if recovered else broken])
    calls = []

    def respond(request):
        calls.append(request)
        return httpx2.Response(200, json=next(outputs))

    async with PropertyStorage(":memory:") as storage:
        async with quality(
            storage, policy(), respond, analysis_config=configuration(reasoning="disabled")
        ) as analyzer:
            if recovered:
                result = await analyzer._run_visual_analysis(CONTENT, "fixture:recorded-failure")
                assert result["kitchen"]["overall_quality"] == "modern"
            else:
                with pytest.raises(AnalysisOutputError):
                    await analyzer._run_visual_analysis(CONTENT, "fixture:recorded-failure")
        assert len(calls) == 2
        assert calls[0].content == calls[1].content
        assert analyzer.token_usage.output_tokens == 2 * USAGE["output_tokens"]
        attempts = await rows(storage)
        assert [attempt["result"] for attempt in attempts] == [
            "malformed_output",
            "success" if recovered else "malformed_output",
        ]
        assert all(attempt["state"] == "settled" for attempt in attempts)
        assert len(await checkpoint_rows(storage)) == int(recovered)


@pytest.mark.parametrize(
    "scenario, result",
    [
        ("missing_tool", "malformed_output"),
        ("wrong_tool", "malformed_output"),
        ("two_tools", "malformed_output"),
        ("missing_visual_field", "malformed_output"),
        ("refusal", "refusal"),
        ("max_tokens", "truncated"),
    ],
)
async def test_failed_visual_outputs_are_billed_and_only_malformed_output_retries(scenario, result):
    output = billed_visual_failure(scenario)
    calls = []

    def respond(request):
        calls.append(request)
        return httpx2.Response(200, json=output)

    async with PropertyStorage(":memory:") as storage:
        async with quality(
            storage,
            policy(),
            respond,
            analysis_config=configuration(reasoning="disabled", sdk_retries=1),
        ) as analyzer:
            with pytest.raises(AnalysisOutputError):
                await analyzer._run_visual_analysis(CONTENT, "fixture:invalid")
        # The SDK never resends a completed response; only the analyzer's own
        # malformed-output retry does, as a separately admitted identical request.
        assert len(calls) == (2 if result == "malformed_output" else 1)
        assert len({call.content for call in calls}) == 1
        attempts = await rows(storage)
        assert len(attempts) == len(calls)
        assert len({attempt["operation_key"] for attempt in attempts}) == 1
        for attempt in attempts:
            assert attempt["state"] == "settled" and attempt["accounted_nanousd"] == 2_335_000
            assert attempt["result"] == result
        assert await checkpoint_rows(storage) == []


async def test_malformed_visual_retry_respects_the_paid_attempt_cap():
    calls = []

    def respond(request):
        calls.append(request)
        return httpx2.Response(200, json=billed_visual_failure("missing_visual_field"))

    async with PropertyStorage(":memory:") as storage:
        async with quality(
            storage,
            policy(max_attempts_per_operation=1),
            respond,
            analysis_config=configuration(reasoning="disabled"),
        ) as analyzer:
            with pytest.raises(SpendDeferred) as caught:
                await analyzer._run_visual_analysis(CONTENT, "fixture:capped")
        assert caught.value.reason == "attempt_limit"
        assert len(calls) == 1 and len(await rows(storage)) == 1

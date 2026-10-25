"""Offline regressions for the exploratory harness's spending records."""

import json
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from mc.ledger import BudgetExceeded, Ledger


def ledger(path):
    return Ledger({"anthropic": 1.0}, {"anthropic": 0.6}, path)


def test_another_process_sees_outstanding_reservation(tmp_path):
    path = tmp_path / "ledger.json"
    subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from pathlib import Path; from mc.ledger import Ledger; "
                "import sys; Ledger({'anthropic': 1.0}, {'anthropic': 0.6}, "
                "Path(sys.argv[1])).check('anthropic')"
            ),
            str(path),
        ],
        check=True,
    )
    with pytest.raises(BudgetExceeded):
        ledger(path).check("anthropic")


@pytest.mark.asyncio
async def test_judge_parse_failure_still_accounts_for_reported_usage(tmp_path):
    from judge import JUDGES, call_judge

    key = next(key for key, spec in JUDGES.items() if spec.provider == "anthropic")
    response = SimpleNamespace(
        usage=SimpleNamespace(
            input_tokens=1000,
            output_tokens=1000,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        ),
        content=[SimpleNamespace(type="text", text="not JSON")],
        stop_reason="end_turn",
    )
    client = SimpleNamespace(messages=SimpleNamespace(create=AsyncMock(return_value=response)))
    budget = ledger(tmp_path / "ledger.json")
    with pytest.raises(json.JSONDecodeError):
        await call_judge(
            key, {"anthropic": client}, budget, [{"type": "text", "text": "fixture"}], "pair"
        )
    assert budget.spent["anthropic"] > 0


@pytest.mark.asyncio
async def test_judge_cancellation_keeps_unresolved_charge(tmp_path):
    import asyncio

    from judge import JUDGES, call_judge

    key = next(key for key, spec in JUDGES.items() if spec.provider == "anthropic")
    client = SimpleNamespace(
        messages=SimpleNamespace(create=AsyncMock(side_effect=asyncio.CancelledError))
    )
    budget = ledger(tmp_path / "ledger.json")
    with pytest.raises(asyncio.CancelledError):
        await call_judge(
            key, {"anthropic": client}, budget, [{"type": "text", "text": "fixture"}], "pair"
        )
    with pytest.raises(BudgetExceeded):
        ledger(tmp_path / "ledger.json").check("anthropic")


def test_ledger_cannot_silently_change_existing_caps(tmp_path):
    path = tmp_path / "ledger.json"
    ledger(path).check("anthropic")
    with pytest.raises(ValueError, match="caps"):
        Ledger({"anthropic": 10}, {"anthropic": 0.6}, path).check("anthropic")


@pytest.mark.parametrize("amount", [-1, float("nan"), float("inf")])
def test_invalid_settlement_does_not_free_reservation(tmp_path, amount):
    budget = ledger(tmp_path / "ledger.json")
    token = budget.check("anthropic")
    with pytest.raises(ValueError):
        budget.add("anthropic", amount, token)
    with pytest.raises(BudgetExceeded):
        ledger(tmp_path / "ledger.json").check("anthropic")


def test_restarted_ledger_can_settle_specific_reservation_once(tmp_path):
    path = tmp_path / "ledger.json"
    token = ledger(path).check("anthropic")
    restarted = ledger(path)
    restarted.add("anthropic", 0.2, token)
    assert ledger(path).spent == {"anthropic": 0.2}
    with pytest.raises(KeyError):
        restarted.add("anthropic", 0.2, token)
    assert ledger(path).spent == {"anthropic": 0.2}
    assert restarted.check("anthropic") != token


def test_cost_above_estimate_is_retained_and_blocks_further_calls(tmp_path):
    path = tmp_path / "ledger.json"
    budget = ledger(path)
    token = budget.check("anthropic")
    budget.add("anthropic", 1.2, token)
    assert ledger(path).spent["anthropic"] == 1.2
    with pytest.raises(BudgetExceeded):
        ledger(path).check("anthropic")


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["cancelled", "connection", "priced_parse_failure"])
async def test_shim_error_keeps_charge_without_repeating_unknown_dispatch(
    tmp_path, monkeypatch, outcome
):
    import asyncio

    import anthropic
    import httpx2
    from mc.shim import Clients, PhaseSpec, RunConfig, ShimClient, _Messages

    async def fail(self, kwargs, spec, phase, record):
        if outcome == "cancelled":
            raise asyncio.CancelledError
        if outcome == "connection":
            raise anthropic.APIConnectionError(
                request=httpx2.Request("POST", "https://example.com")
            )
        if outcome == "priced_parse_failure":
            record.cost_usd = 0.1
            record.extra["usage_priced"] = True
        raise ValueError("fixture post-dispatch failure")

    mocked = AsyncMock(side_effect=fail)

    async def dispatch(self, *args):
        return await mocked(self, *args)

    monkeypatch.setattr(_Messages, "_anthropic", dispatch)
    path = tmp_path / "ledger.json"
    budget = ledger(path)
    phase = PhaseSpec("anthropic", "claude-sonnet-5-5")
    clients = Clients(None, None, budget, {"anthropic": asyncio.Semaphore(1)})
    records = []
    client = ShimClient(RunConfig("fixture", phase, phase), "fixture", clients, records)
    expected = {
        "cancelled": asyncio.CancelledError,
        "connection": anthropic.APIConnectionError,
        "priced_parse_failure": ValueError,
    }[outcome]
    with pytest.raises(expected):
        await client.messages.create(tools=[{}])
    assert mocked.await_count == 1
    assert records[0].attempts == 1
    assert records[0].latency_s is not None
    if outcome == "priced_parse_failure":
        assert ledger(path).spent["anthropic"] == 0.1
        assert ledger(path).check("anthropic")
    else:
        with pytest.raises(BudgetExceeded, match="unresolved"):
            ledger(path).check("anthropic")


@pytest.mark.parametrize("valid", [0, 1, 2])
def test_warm_and_cold_costs_include_failures_on_same_denominator(monkeypatch, valid):
    import analyze

    records = [
        {
            "analysis": {},
            "failure": None if i < valid else {"kind": "malformed_output"},
            "error": None,
        }
        for i in range(2)
    ]
    monkeypatch.setattr(analyze, "recost", lambda row, cold=False: 0.3 if cold else 0.1)
    metrics = analyze.cost_metrics(records)
    assert metrics["cost_per_home"] == pytest.approx(0.1)
    assert metrics["cold_cost_per_home"] == pytest.approx(0.3)
    if valid:
        assert metrics["cost_per_valid_home"] == pytest.approx(0.2 / valid)
        assert metrics["cold_cost_per_valid_home"] == pytest.approx(0.6 / valid)
    else:
        assert metrics["cost_per_valid_home"] is None
        assert metrics["cold_cost_per_valid_home"] is None


@pytest.mark.asyncio
async def test_shim_retries_rate_limit_with_fresh_reservation(tmp_path, monkeypatch):
    import asyncio

    import anthropic
    import httpx2
    from mc.shim import Clients, PhaseSpec, RunConfig, ShimClient, _Messages

    calls = 0

    async def dispatch(self, kwargs, spec, phase, record):
        nonlocal calls
        calls += 1
        if calls == 1:
            response = httpx2.Response(429, request=httpx2.Request("POST", "https://example.com"))
            raise anthropic.RateLimitError("fixture rejection", response=response, body={})
        record.cost_usd = 0.1
        record.extra["usage_priced"] = True
        return SimpleNamespace(stop_reason="tool_use")

    monkeypatch.setattr(_Messages, "_anthropic", dispatch)
    monkeypatch.setattr("mc.shim.asyncio.sleep", AsyncMock())
    path = tmp_path / "ledger.json"
    phase = PhaseSpec("anthropic", "claude-sonnet-5-5")
    clients = Clients(None, None, ledger(path), {"anthropic": asyncio.Semaphore(1)})
    records = []
    client = ShimClient(RunConfig("fixture", phase, phase), "fixture", clients, records)
    result = await client.messages.create(tools=[{}])
    assert result.stop_reason == "tool_use"
    assert calls == 2 and records[0].attempts == 2
    assert ledger(path).spent == {"anthropic": 0.1}
    assert json.loads(path.read_text())["reservations"] == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["budget", "cancelled"])
async def test_sweep_retains_paid_calls_when_later_phase_cannot_finish(
    tmp_path, monkeypatch, outcome
):
    import asyncio

    import run
    from mc.shim import Clients, PhaseSpec, RunConfig, _Messages

    async def dispatch(self, kwargs, spec, phase, record):
        if phase == "evaluation":
            raise asyncio.CancelledError
        record.input_tokens = 1000
        record.output_tokens = 1000
        record.cost_usd = 0.5
        record.request_id = "paid-visual-fixture"
        record.extra["usage_priced"] = True
        return SimpleNamespace(stop_reason="tool_use")

    async def analyze(self, *args, **kwargs):
        await self._client.messages.create(tools=[{}])
        await self._client.messages.create()

    monkeypatch.setattr(_Messages, "_anthropic", dispatch)
    monkeypatch.setattr(run.PropertyQualityFilter, "analyze_single_merged", analyze)
    monkeypatch.setattr(run, "RUNS", tmp_path / "runs")
    budget = Ledger(
        {"anthropic": 1.0},
        {"anthropic": 0.6 if outcome == "budget" else 0.4},
        tmp_path / "ledger.json",
    )
    phase = PhaseSpec("anthropic", "claude-sonnet-5-5")
    config = RunConfig("fixture", phase, phase)
    clients = Clients(None, None, budget, {"anthropic": asyncio.Semaphore(1)})
    case = SimpleNamespace(case_id="fixture", merged=SimpleNamespace(unique_id="fixture"))
    stop = {}
    if outcome == "cancelled":
        with pytest.raises(asyncio.CancelledError):
            await run.run_one("fixture", config, case, 0, clients, stop)
    else:
        assert await run.run_one("fixture", config, case, 0, clients, stop) == "budget"
        assert stop == {"anthropic": True}

    path = run.result_path("fixture", config.id, case.case_id, 0)
    assert path.exists(), "paid and unresolved attempts must survive a partial run"
    payload = json.loads(path.read_text())
    assert payload["cost_usd"] == budget.spent["anthropic"] == 0.5
    assert payload["calls"][0]["request_id"] == "paid-visual-fixture"
    assert len(payload["calls"]) == 2
    assert payload["error"].startswith(
        "BudgetExceeded:" if outcome == "budget" else "CancelledError:"
    )
    assert payload["calls"][1]["error"].startswith(
        "BudgetExceeded:" if outcome == "budget" else "CancelledError:"
    )
    if outcome == "cancelled":
        with pytest.raises(BudgetExceeded, match="unresolved"):
            budget.check("anthropic")
    # Resuming must not silently send the paid visual phase again.
    assert await run.run_one("fixture", config, case, 0, clients, {}) == "skip"

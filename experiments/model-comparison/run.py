"""Run analysis configurations over the frozen case pack.

    uv run python run.py preflight
    uv run python run.py cases --n 30
    uv run python run.py sweep --run core --n 30 --configs sonnet55-low,gpt-x
    uv run python run.py sweep --run repeat --n 10 --configs sonnet55-low --repeats 3

Results: data/runs/<run>/<config>/<case>__r<k>.json (resumable; existing files skip).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import random
import sys
import time
from dataclasses import asdict
from pathlib import Path

import structlog

from home_finder.filters.quality import PropertyQualityFilter
from home_finder.models.analysis import AnalysisConfig, EvaluationPhaseConfig, VisualPhaseConfig
from mc.cases import WORK_DIR, Case, load_cases, select, write_manifest
from mc.configs import CONFIGS
from mc.env import make_clients
from mc.ledger import Ledger
from mc.shim import Clients, RunConfig, ShimClient

ROOT = Path(__file__).resolve().parent
RUNS = ROOT / "data" / "runs"

# Only provenance and the filter's internal token tally read this; the shim
# chooses the real model and reasoning settings per configuration.
APP_CONFIG = AnalysisConfig(
    visual=VisualPhaseConfig(model="claude-sonnet-5-5", reasoning="adaptive", effort="low"),
    evaluation=EvaluationPhaseConfig(model="claude-sonnet-5-5", effort="low"),
)

# The user's budget is $20 per provider; $19 kept a margin until the final
# rating/definition follow-up, which runs only cheap arms.
CAPS = {"anthropic": 20.0, "openai": 19.0}
# Historical estimates only, not worst-case token-cost bounds. Qualify actual
# per-request bounds before another paid trial; these cannot guarantee an invoice cap.
RESERVE = {"anthropic": 0.08, "openai": 0.25}


def quiet_logging() -> None:
    logging.basicConfig(level=logging.WARNING)
    structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.ERROR))


def result_path(run: str, config_id: str, case_id: str, rep: int) -> Path:
    return RUNS / run / config_id / f"{case_id}__r{rep}.json"


async def run_one(
    run: str, config: RunConfig, case: Case, rep: int, clients: Clients, stop: dict[str, bool]
) -> str:
    path = result_path(run, config.id, case.case_id, rep)
    if path.exists():
        return "skip"
    providers = {config.visual.provider, config.evaluation.provider}
    if any(stop.get(p) for p in providers):
        return "stopped"
    records: list = []
    filt = PropertyQualityFilter(api_key="shim", max_images=20, analysis_config=APP_CONFIG)
    filt._client = ShimClient(config, case.case_id, clients, records)  # type: ignore[assignment]
    t0 = time.perf_counter()
    error = None
    analysis = None
    try:
        _, analysis = await filt.analyze_single_merged(case.merged, data_dir=str(WORK_DIR))
    except Exception as exc:  # outages surface here after shim retries
        error = f"{type(exc).__name__}: {str(exc)[:500]}"
    wall = time.perf_counter() - t0
    if any((r.error or "").startswith("BudgetExceeded") for r in records):
        for p in providers:
            stop[p] = True
        return "budget"
    payload = {
        "run": run,
        "config_id": config.id,
        "config": asdict(config),
        "case_id": case.case_id,
        "unique_id": case.merged.unique_id,
        "rep": rep,
        "wall_s": wall,
        "error": error,
        "failure": (
            analysis.analysis_failure.model_dump(mode="json")
            if analysis is not None and analysis.analysis_failure is not None
            else None
        ),
        "analysis": analysis.model_dump(mode="json") if analysis is not None else None,
        "calls": [asdict(r) for r in records],
        "cost_usd": sum(r.cost_usd for r in records),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=1, default=str))
    tmp.replace(path)
    ok = error is None and payload["failure"] is None and analysis is not None
    return "ok" if ok else "fail"


async def sweep(args: argparse.Namespace) -> None:
    quiet_logging()
    all_cases = await load_cases()
    # Arm spec: "id", "id:n" (first n homes) or "id:nxR" (first n homes, R repeats).
    arms: list[tuple[RunConfig, int, int]] = []
    for spec in args.configs.split(","):
        config_id, _, size = spec.partition(":")
        n, _, reps = size.partition("x")
        arms.append((CONFIGS[config_id], int(n or args.n), int(reps or args.repeats)))
    cases = select(all_cases, max(n for _, n, _ in arms))
    a, o = make_clients()
    clients = Clients(
        anthropic=a,  # type: ignore[arg-type]
        openai=o,  # type: ignore[arg-type]
        ledger=Ledger(CAPS, RESERVE),
        semaphores={
            "anthropic": asyncio.Semaphore(args.concurrency),
            "openai": asyncio.Semaphore(args.concurrency),
        },
    )
    rng = random.Random(args.seed)
    jobs: list[tuple[RunConfig, Case, int]] = []
    # Interleave configurations within each case so time-of-day load and
    # provider incidents fall evenly across configurations.
    for rep in range(max(r for _, _, r in arms)):
        for index, case in enumerate(cases):
            order = [c for c, n, r in arms if index < n and rep < r]
            rng.shuffle(order)
            jobs.extend((config, case, rep) for config in order)
    stop: dict[str, bool] = {}
    gate = asyncio.Semaphore(args.concurrency * 2)
    counts: dict[str, int] = {}
    started = time.time()

    async def guarded(job: tuple[RunConfig, Case, int]) -> None:
        async with gate:
            status = await run_one(args.run, *job, clients, stop)
        counts[status] = counts.get(status, 0) + 1
        done = sum(counts.values())
        if done % 10 == 0 or done == len(jobs):
            spent = {k: round(v, 2) for k, v in clients.ledger.spent.items()}
            print(
                f"[{time.time() - started:6.0f}s] {done}/{len(jobs)} {counts} spent={spent}",
                flush=True,
            )

    await asyncio.gather(*(guarded(job) for job in jobs))
    print("final", counts, clients.ledger.spent, flush=True)


async def preflight(_: argparse.Namespace) -> None:
    a, o = make_clients()
    print("anthropic key:", "found" if a else "missing")
    print("openai key:", "found" if o else "missing")
    if a is not None:
        try:
            r = await a.messages.create(  # type: ignore[attr-defined]
                model="claude-haiku-5-5",
                max_tokens=8,
                thinking={"type": "disabled"},
                messages=[{"role": "user", "content": "Reply OK"}],
            )
            print("anthropic ping:", r.stop_reason, r.usage.input_tokens, "in")
        except Exception as exc:
            print("anthropic ping failed:", type(exc).__name__, str(exc)[:200])
    if o is not None:
        try:
            models = [m.id async for m in o.models.list()]  # type: ignore[attr-defined]
            print("openai models visible:", len(models))
            print(" ", sorted(m for m in models if m.startswith(("gpt-5", "gpt-6", "o"))))
        except Exception as exc:
            print("openai list failed:", type(exc).__name__, str(exc)[:200])


async def cases_cmd(args: argparse.Namespace) -> None:
    quiet_logging()
    cases = select(await load_cases(), args.n)
    out = ROOT / "data" / f"cases-{args.n}.json"
    write_manifest(out, cases)
    print(f"{len(cases)} cases -> {out}")
    by = {}
    for c in cases:
        by[(c.source, c.has_floorplan)] = by.get((c.source, c.has_floorplan), 0) + 1
    print(by)


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("preflight")
    c = sub.add_parser("cases")
    c.add_argument("--n", type=int, default=30)
    s = sub.add_parser("sweep")
    s.add_argument("--run", required=True)
    s.add_argument("--configs", required=True)
    s.add_argument("--n", type=int, default=30)
    s.add_argument("--repeats", type=int, default=1)
    s.add_argument("--concurrency", type=int, default=6)
    s.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    fn = {"preflight": preflight, "cases": cases_cmd, "sweep": sweep}[args.cmd]
    asyncio.run(fn(args))


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    main()

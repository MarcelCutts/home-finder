"""Blind pairwise judging of two arms' assessments of the same home, by cross-family judges.

Each judge sees the photos, floorplan and listing text the analyser saw, plus two
anonymised, canonically rendered assessments (A/B). Position is counterbalanced:
for every pair the two judges see opposite orders, and the order assigned to the
first judge alternates by home. The evidence prefix is prompt-cached per home.

    uv run python judge.py --run core --pairs sonnet55-low:sol61-low --n 30
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any

from home_finder.filters.quality import PropertyQualityFilter
from home_finder.utils.image_cache import find_cached_file, is_valid_image_url
from mc.cases import WORK_DIR, Case, load_cases, select
from mc.env import make_clients
from mc.facts import listing_text
from mc.ledger import BudgetExceeded, Ledger
from mc.rates import cost_usd
from mc.shim import PhaseSpec, to_openai_strict
from run import CAPS, RESERVE, RUNS, quiet_logging

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "data" / "judge"

JUDGES: dict[str, PhaseSpec] = {
    "opus55": PhaseSpec("anthropic", "claude-opus-5-5", "low", "adaptive", max_output_tokens=6000),
    "sol56": PhaseSpec("openai", "gpt-5.6-sol", "low", max_output_tokens=6000, image_detail="high"),
}

RUBRIC = """You are auditing two AI-written assessments (A and B) of the same London rental flat.
You have the same evidence the assessments were written from: the listing photos, the floorplan
when there is one, and the listing text. Your job is to decide which assessment a careful renter
should rely on, criterion by criterion.

The renter: works from home full-time, so wants sleep/work separation (2-bed with one as office,
or 1-bed with a large common area); hosts music/social gatherings, so cares about layout for
hosting and sound insulation; wants a gas or induction hob and fast internet; likes characterful
spaces. These are preferences, not dealbreakers.

Criteria:
- factual_accuracy: Which assessment makes fewer claims that the photos, floorplan or text
  contradict or do not support (kitchen/hob, condition, damp/mould, light, floor level, glazing,
  bathroom/bath, outdoor space, room sizes and printed areas, office separation, storage)?
  Check claims against the evidence yourself; do not reward confident tone.
- calibration: Which says "unknown" when the evidence genuinely does not show something, without
  hiding behind "unknown" when the evidence is clear?
- usefulness: Which better helps this renter decide whether to view the flat, given the
  preferences above?
- viewing_notes: Which viewing checks and agent questions are more specific to this property and
  more actionable?

Ignore length, formatting and writing style except where they change what a renter learns.
Identical or equally good answers are a tie. Keep each justification to one short sentence.
In errors_a / errors_b list only concrete factual errors (claim vs evidence, e.g. "says electric
hob; photo 3 shows a gas hob"), at most 6 each; an empty list is fine.
"""

VERDICT = {"type": "string", "enum": ["A", "B", "tie"]}
CRITERIA = ["factual_accuracy", "calibration", "usefulness", "viewing_notes"]
JUDGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        **{
            c: {
                "type": "object",
                "properties": {"winner": VERDICT, "why": {"type": "string"}},
                "required": ["winner", "why"],
                "additionalProperties": False,
            }
            for c in CRITERIA
        },
        "errors_a": {"type": "array", "items": {"type": "string"}},
        "errors_b": {"type": "array", "items": {"type": "string"}},
        "overall": VERDICT,
        "overall_why": {"type": "string"},
    },
    "required": [*CRITERIA, "errors_a", "errors_b", "overall", "overall_why"],
    "additionalProperties": False,
}

# Application-computed or bookkeeping fields, identical across arms or not model output.
_DROP = {"analysis_provenance", "analysis_failure", "listing_text_inputs", "image_review_coverage"}
_VALUE_KEEP = {"quality_adjusted_rating", "quality_adjusted_note"}


def render(analysis: dict[str, Any]) -> str:
    view = {k: v for k, v in analysis.items() if k not in _DROP and v not in (None, [], {}, "")}
    if isinstance(view.get("value"), dict):
        view["value"] = {k: v for k, v in view["value"].items() if k in _VALUE_KEEP}
    return json.dumps(view, sort_keys=True, ensure_ascii=False, indent=1)


async def evidence_blocks(case: Case) -> list[dict[str, Any]]:
    """The images the analyser saw, in its order, as Anthropic content blocks."""
    filt = PropertyQualityFilter(api_key="unused", max_images=20)
    merged = case.merged
    data_dir = str(WORK_DIR)
    blocks: list[dict[str, Any]] = []
    floorplan_url = str(merged.floorplan.url) if merged.floorplan else None
    floorplan_block = None
    if floorplan_url and is_valid_image_url(floorplan_url):
        floorplan_block = await filt._build_image_block(
            floorplan_url,
            cached_path=find_cached_file(data_dir, merged.unique_id, floorplan_url, "floorplan"),
        )
    limit = 20 - (1 if floorplan_block else 0)
    gallery = [str(i.url) for i in merged.images if i.image_type == "gallery"][:limit]
    n = 0
    for url in gallery:
        block = await filt._build_image_block(
            url, cached_path=find_cached_file(data_dir, merged.unique_id, url, "gallery")
        )
        if block:
            n += 1
            blocks += [{"type": "text", "text": f"Photo {n}:"}, dict(block)]
    if floorplan_block:
        blocks += [{"type": "text", "text": "Floorplan:"}, dict(floorplan_block)]
    prop = merged.canonical
    blocks.append(
        {
            "type": "text",
            "text": (
                f"Listing: {prop.bedrooms}-bed, £{prop.price_pcm} pcm, {prop.address} "
                f"{prop.postcode or ''}\n\nListing text:\n{listing_text(merged)}"
            ),
        }
    )
    return blocks


async def call_judge(
    judge_id: str,
    clients: dict[str, Any],
    ledger: Ledger,
    evidence: list[dict[str, Any]],
    pair_text: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    spec = JUDGES[judge_id]
    reservation = ledger.check(spec.provider)
    cost = None
    t0 = time.perf_counter()
    try:
        if spec.provider == "anthropic":
            content = [dict(b) for b in evidence]
            content[-1] = {**content[-1], "cache_control": {"type": "ephemeral"}}
            content.append({"type": "text", "text": pair_text})
            resp = await clients["anthropic"].messages.create(
                model=spec.model,
                max_tokens=spec.max_output_tokens,
                system=[{"type": "text", "text": RUBRIC, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": content}],
                thinking={"type": spec.thinking},
                output_config={
                    "effort": spec.effort,
                    "format": {"type": "json_schema", "schema": JUDGE_SCHEMA},
                },
                timeout=600,
            )
            u = resp.usage
            read = u.cache_read_input_tokens or 0
            write = u.cache_creation_input_tokens or 0
            cost = cost_usd(
                spec,
                uncached_input=u.input_tokens,
                cached_input=read,
                cache_write=write,
                output=u.output_tokens,
            )
            texts = [b.text for b in resp.content if b.type == "text"]
            verdict = json.loads(texts[0])
            meta = {
                "stop": resp.stop_reason,
                "in": u.input_tokens + read + write,
                "cached": read,
                "out": u.output_tokens,
            }
        else:
            parts: list[dict[str, Any]] = []
            for b in evidence:
                if b["type"] == "text":
                    parts.append({"type": "input_text", "text": b["text"]})
                else:
                    src = b["source"]
                    parts.append(
                        {
                            "type": "input_image",
                            "image_url": f"data:{src['media_type']};base64,{src['data']}",
                            "detail": spec.image_detail,
                        }
                    )
            parts[-1]["prompt_cache_breakpoint"] = {"mode": "explicit"}
            parts.append({"type": "input_text", "text": pair_text})
            resp = await clients["openai"].responses.create(
                model=spec.model,
                input=[
                    {"role": "developer", "content": [{"type": "input_text", "text": RUBRIC}]},
                    {"role": "user", "content": parts},
                ],
                reasoning={"effort": spec.effort},
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "pairwise_verdict",
                        "schema": to_openai_strict(JUDGE_SCHEMA),
                        "strict": True,
                    }
                },
                max_output_tokens=spec.max_output_tokens,
                store=False,
                prompt_cache_options={"mode": "explicit"},
                timeout=900,
            )
            u = resp.usage
            read = u.input_tokens_details.cached_tokens or 0
            write = getattr(u.input_tokens_details, "cache_write_tokens", 0) or 0
            cost = cost_usd(
                spec,
                uncached_input=u.input_tokens - read - write,
                cached_input=read,
                cache_write=write,
                output=u.output_tokens,
            )
            verdict = json.loads(resp.output_text)
            meta = {
                "stop": resp.status,
                "in": u.input_tokens,
                "cached": read,
                "out": u.output_tokens,
            }
    except BaseException:
        if cost is not None:
            ledger.add(spec.provider, cost, reservation)
        else:
            ledger.hold(spec.provider, reservation)
        raise
    ledger.add(spec.provider, cost, reservation)
    meta.update(cost_usd=cost, latency_s=time.perf_counter() - t0)
    return verdict, meta


def load_result(run: str, config_id: str, case_id: str) -> dict[str, Any] | None:
    path = RUNS / run / config_id / f"{case_id}__r0.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    ok = data["analysis"] is not None and data["failure"] is None and data["error"] is None
    return data["analysis"] if ok else None


async def main_async(args: argparse.Namespace) -> None:
    quiet_logging()
    cases = select(await load_cases(), args.n)
    a, o = make_clients()
    clients = {"anthropic": a, "openai": o}
    ledger = Ledger(CAPS, RESERVE)
    judge_ids = args.judges.split(",")
    sem = asyncio.Semaphore(args.concurrency)
    done = {"ok": 0, "skip": 0, "missing": 0, "error": 0}

    async def per_case(index: int, case: Case) -> None:
        jobs = []
        for pair in args.pairs.split(","):
            left, right = pair.split(":")
            la, ra = (
                load_result(args.run, left, case.case_id),
                load_result(args.run, right, case.case_id),
            )
            if la is None or ra is None:
                done["missing"] += 1
                continue
            for j, judge_id in enumerate(judge_ids):
                # Opposite orders for the two judges; alternate which judge gets which by home.
                swap = (index + j) % 2 == 1
                first, second = (right, left) if swap else (left, right)
                out = OUT / f"{left}__vs__{right}" / f"{case.case_id}__{judge_id}.json"
                if out.exists():
                    done["skip"] += 1
                    continue
                texts = {left: render(la), right: render(ra)}
                pair_text = (
                    f"Assessment A:\n{texts[first]}\n\nAssessment B:\n{texts[second]}\n\n"
                    "Compare A and B against the evidence and return the verdict."
                )
                jobs.append((judge_id, first, second, out, pair_text))
        if not jobs:
            return
        async with sem:
            evidence = await evidence_blocks(case)

        async def one(job: tuple[str, str, str, Path, str]) -> None:
            judge_id, first, second, out, pair_text = job
            async with sem:
                try:
                    verdict, meta = await call_judge(judge_id, clients, ledger, evidence, pair_text)
                except BudgetExceeded as exc:
                    print("budget", exc, flush=True)
                    done["error"] += 1
                    return
                except Exception as exc:
                    print("judge error", judge_id, case.case_id, type(exc).__name__, str(exc)[:200])
                    done["error"] += 1
                    return
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(
                json.dumps(
                    {
                        "case_id": case.case_id,
                        "judge": judge_id,
                        "A": first,
                        "B": second,
                        "verdict": verdict,
                        "meta": meta,
                        "pair_sha": hashlib.sha256(pair_text.encode()).hexdigest()[:16],
                    },
                    indent=1,
                )
            )
            done["ok"] += 1

        # First call per judge warms that judge's evidence cache; the rest then read it.
        by_judge: dict[str, list] = {}
        for job in jobs:
            by_judge.setdefault(job[0], []).append(job)

        async def judge_chain(items: list) -> None:
            await one(items[0])
            await asyncio.gather(*(one(j) for j in items[1:]))

        await asyncio.gather(*(judge_chain(items) for items in by_judge.values()))
        print(
            f"case {index + 1}/{len(cases)} {done} spent={ {k: round(v, 2) for k, v in ledger.spent.items()} }",
            flush=True,
        )

    await asyncio.gather(*(per_case(i, c) for i, c in enumerate(cases)))
    print("final", done, ledger.spent)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", default="core")
    parser.add_argument("--pairs", required=True, help="left:right,left:right")
    parser.add_argument("--n", type=int, default=30)
    parser.add_argument("--judges", default="opus55,sol56")
    parser.add_argument("--concurrency", type=int, default=6)
    asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    main()

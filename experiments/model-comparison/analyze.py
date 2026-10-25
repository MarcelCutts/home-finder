"""Aggregate runs and judge verdicts into per-arm metrics with cluster-bootstrap CIs.

uv run python analyze.py --run core --out data/summary.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from mc.cases import load_cases
from mc.facts import badge_violations, expected_facts, get_path, listing_text
from mc.rates import cost_usd
from mc.shim import PhaseSpec
from run import RUNS, quiet_logging

ROOT = Path(__file__).resolve().parent
JUDGE_DIR = ROOT / "data" / "judge"

# Top-tier ($10/$50) model from each family; agreement between them is the silver label.
REFERENCES = ("fable51-low", "astra6-low")

CONSENSUS_FIELDS = [
    "kitchen.overall_quality",
    "kitchen.hob_type",
    "kitchen.has_dishwasher",
    "kitchen.has_washing_machine",
    "condition.overall_condition",
    "condition.has_visible_damp",
    "condition.has_worn_fixtures",
    "light_space.natural_light",
    "light_space.floor_level",
    "light_space.ceiling_height",
    "space.hosting_layout",
    "bathroom.overall_condition",
    "bathroom.has_bathtub",
    "bathroom.shower_type",
    "bedroom.office_separation",
    "bedroom.primary_is_double",
    "outdoor_space.availability",
    "flooring_noise.primary_flooring",
    "flooring_noise.has_double_glazing",
    "listing_extraction.property_type",
    "listing_extraction.furnished_status",
]
UNKNOWN = {None, "unknown", ""}


def load_run(run: str) -> dict[str, dict[str, list[dict[str, Any]]]]:
    """config -> case -> [results by rep]"""
    out: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for path in sorted((RUNS / run).glob("*/*.json")):
        data = json.loads(path.read_text())
        out[data["config_id"]][data["case_id"]].append(data)
    return out


def ok(result: dict[str, Any]) -> bool:
    return result["analysis"] is not None and result["failure"] is None and result["error"] is None


def recost(result: dict[str, Any], *, cold: bool = False) -> float:
    """Cost from stored token counts at current rates. cold: cache reads become writes."""
    total = 0.0
    for call in result["calls"]:
        spec = PhaseSpec(**result["config"][call["phase"]])
        cached = call["cached_input_tokens"]
        write = call["cache_write_tokens"]
        uncached = call["input_tokens"] - cached - write
        if cold:
            write, cached = write + cached, 0
        tier = call["extra"].get("service_tier") if spec.provider == "openai" else spec.service_tier
        total += cost_usd(
            spec,
            uncached_input=uncached,
            cached_input=cached,
            cache_write=write,
            output=call["output_tokens"],
            service_tier=tier,
        )
    return total


def cost_metrics(attempts: list[dict[str, Any]]) -> dict[str, float | None]:
    """Use the same valid-result denominator for warm and cold estimates.

    Failed attempts still cost money. Repeated runs are results, not new homes.
    Keep the old per-home keys as compatibility aliases for per-attempt means.
    """
    valid = sum(ok(result) for result in attempts)
    warm = sum(recost(result) for result in attempts)
    cold = sum(recost(result, cold=True) for result in attempts)
    return {
        "cost_per_home": warm / len(attempts) if attempts else None,
        "cost_per_valid_home": warm / valid if valid else None,
        "cold_cost_per_home": cold / len(attempts) if attempts else None,
        "cold_cost_per_valid_home": cold / valid if valid else None,
    }


def pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    k = (len(values) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(values) - 1)
    return values[lo] + (values[hi] - values[lo]) * (k - lo)


def bootstrap(
    per_case: dict[str, float], stat=statistics.mean, b: int = 2000
) -> tuple[float, float]:
    keys = list(per_case)
    if len(keys) < 2:
        return (float("nan"), float("nan"))
    rng = random.Random(0)
    samples = []
    for _ in range(b):
        draw = [per_case[rng.choice(keys)] for _ in keys]
        samples.append(stat(draw))
    samples.sort()
    return samples[int(0.025 * b)], samples[int(0.975 * b)]


def same(field: str, a: Any, b: Any) -> bool:
    if isinstance(a, str) and isinstance(b, str):
        return a.casefold() == b.casefold()
    return a == b


def silver_labels(run_data, cases) -> dict[str, dict[str, Any]]:
    labels: dict[str, dict[str, Any]] = {}
    ref_a, ref_b = (run_data.get(r, {}) for r in REFERENCES)
    for case_id in cases:
        ra = [r for r in ref_a.get(case_id, []) if ok(r)]
        rb = [r for r in ref_b.get(case_id, []) if ok(r)]
        if not ra or not rb:
            continue
        a, b = ra[0]["analysis"], rb[0]["analysis"]
        row: dict[str, Any] = {}
        for field in CONSENSUS_FIELDS:
            va, vb = get_path(a, field), get_path(b, field)
            if va not in UNKNOWN and same(field, va, vb):
                row[field] = va
        ra_rating, rb_rating = a.get("overall_rating"), b.get("overall_rating")
        if ra_rating and rb_rating and abs(ra_rating - rb_rating) <= 1:
            row["overall_rating"] = (ra_rating + rb_rating) / 2
        ta = get_path(a, "space.total_area_sqm")
        tb = get_path(b, "space.total_area_sqm")
        if ta and tb and abs(ta - tb) / max(ta, tb) <= 0.05:
            row["space.total_area_sqm"] = (ta + tb) / 2
        labels[case_id] = row
    return labels


def score_against(analysis: dict[str, Any], labels: dict[str, Any]) -> tuple[int, int, int]:
    """(agree, disagree, abstain) over labelled fields."""
    agree = disagree = abstain = 0
    for field, expected in labels.items():
        got = get_path(analysis, field)
        if field == "overall_rating":
            if got is None:
                abstain += 1
            elif abs(got - expected) <= 1:
                agree += 1
            else:
                disagree += 1
        elif field == "space.total_area_sqm":
            if not got:
                abstain += 1
            elif abs(got - expected) / expected <= 0.05:
                agree += 1
            else:
                disagree += 1
        elif got in UNKNOWN:
            abstain += 1
        elif same(field, got, expected):
            agree += 1
        else:
            disagree += 1
    return agree, disagree, abstain


def loo_majority(run_data, config_id, case_id) -> dict[str, Any]:
    """Majority (>=60% of non-unknown votes, >=4 voters) among all *other* arms."""
    votes: dict[str, Counter] = defaultdict(Counter)
    for other, by_case in run_data.items():
        if other == config_id:
            continue
        rs = [r for r in by_case.get(case_id, []) if ok(r)]
        if not rs:
            continue
        for field in CONSENSUS_FIELDS:
            v = get_path(rs[0]["analysis"], field)
            if v not in UNKNOWN:
                votes[field][str(v).casefold()] += 1
    out = {}
    for field, counter in votes.items():
        total = sum(counter.values())
        value, top = counter.most_common(1)[0]
        if total >= 4 and top / total >= 0.6:
            out[field] = value
    return out


def score_loo(analysis, labels) -> tuple[int, int, int]:
    agree = disagree = abstain = 0
    for field, expected in labels.items():
        got = get_path(analysis, field)
        if got in UNKNOWN:
            abstain += 1
        elif str(got).casefold() == expected:
            agree += 1
        else:
            disagree += 1
    return agree, disagree, abstain


def stability(by_case: dict[str, list[dict[str, Any]]]) -> dict[str, Any] | None:
    flips = total = 0
    rating_sd = []
    for reps in by_case.values():
        good = [r["analysis"] for r in reps if ok(r)]
        if len(good) < 2:
            continue
        for field in CONSENSUS_FIELDS:
            values = [str(get_path(a, field)).casefold() for a in good]
            total += 1
            flips += len(set(values)) > 1
        rating_sd.append(statistics.pstdev([a["overall_rating"] for a in good]))
    if not total:
        return None
    return {
        "field_flip_rate": flips / total,
        "mean_rating_sd": statistics.mean(rating_sd),
        "cases": len(rating_sd),
    }


def judge_summary() -> dict[str, Any]:
    out: dict[str, Any] = {}
    if not JUDGE_DIR.exists():
        return out
    for pair_dir in sorted(JUDGE_DIR.iterdir()):
        left, right = pair_dir.name.split("__vs__")
        tallies: dict[str, dict[str, Counter]] = defaultdict(lambda: defaultdict(Counter))
        errors: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
        per_case_overall: dict[str, list[float]] = defaultdict(list)
        cost = 0.0
        for path in sorted(pair_dir.glob("*.json")):
            d = json.loads(path.read_text())
            judge, verdict = d["judge"], d["verdict"]
            cost += d["meta"]["cost_usd"]
            to_cfg = {"A": d["A"], "B": d["B"]}
            for crit in [
                "factual_accuracy",
                "calibration",
                "usefulness",
                "viewing_notes",
                "overall",
            ]:
                w = verdict[crit] if crit == "overall" else verdict[crit]["winner"]
                outcome = "tie" if w == "tie" else ("left" if to_cfg[w] == left else "right")
                tallies[judge][crit][outcome] += 1
                tallies["both"][crit][outcome] += 1
                if crit == "overall":
                    per_case_overall[d["case_id"]].append(
                        {"left": 1.0, "right": 0.0, "tie": 0.5}[outcome]
                    )
            for side in ("A", "B"):
                errors[judge][to_cfg[side]].append(len(verdict[f"errors_{side.lower()}"]))
        score = {c: statistics.mean(v) for c, v in per_case_overall.items()}
        out[pair_dir.name] = {
            "left": left,
            "right": right,
            "tallies": {j: {c: dict(t) for c, t in crit.items()} for j, crit in tallies.items()},
            "left_overall_score": statistics.mean(score.values()) if score else None,
            "left_overall_ci": bootstrap(score) if score else None,
            "mean_errors": {
                j: {cfg: statistics.mean(v) for cfg, v in by.items()} for j, by in errors.items()
            },
            "cases": len(score),
            "judge_cost_usd": cost,
        }
    return out


async def main_async(args: argparse.Namespace) -> None:
    quiet_logging()
    run_data = load_run(args.run)
    cases = {c.case_id: c for c in await load_cases()}
    facts = {cid: expected_facts(c.merged) for cid, c in cases.items()}
    texts = {cid: listing_text(c.merged) for cid, c in cases.items()}
    silver = silver_labels(run_data, cases)
    arms: dict[str, Any] = {}
    for config_id, by_case in sorted(run_data.items()):
        attempts = [r for reps in by_case.values() for r in reps]
        first = {cid: reps[0] for cid, reps in by_case.items() if reps}
        good = [r for r in attempts if ok(r)]
        # Historical records time the last attempt. Reviewed records include
        # queueing and backoff; neither measures isolated token-generation speed.
        wall = [sum(c["latency_s"] or 0 for c in r["calls"]) for r in good]
        retried = sum(1 for r in attempts for c in r["calls"] if c["attempts"] > 1)
        visual_lat = [c["latency_s"] for r in good for c in r["calls"] if c["phase"] == "visual"]
        eval_lat = [c["latency_s"] for r in good for c in r["calls"] if c["phase"] == "evaluation"]
        tps = [
            c["output_tokens"] / c["latency_s"]
            for r in good
            for c in r["calls"]
            if c["phase"] == "visual" and c["latency_s"]
        ]
        fact = Counter()
        cons = Counter()
        loo = Counter()
        cons_case: dict[str, float] = {}
        violations: list[int] = []
        for cid, r in first.items():
            if not ok(r):
                continue
            a = r["analysis"]
            violations.append(len(badge_violations(a, texts[cid])))
            for field, expected in facts.get(cid, {}).items():
                got = get_path(a, field)
                if got in UNKNOWN or got is False and expected is True:
                    fact["abstain" if got in UNKNOWN else "wrong"] += 1
                elif same(field, got, expected):
                    fact["right"] += 1
                else:
                    fact["wrong"] += 1
            if config_id not in REFERENCES and cid in silver:
                ag, dis, ab = score_against(a, silver[cid])
                cons.update(agree=ag, disagree=dis, abstain=ab)
                if ag + dis:
                    cons_case[cid] = ag / (ag + dis + ab)
            ag, dis, ab = score_loo(a, loo_majority(run_data, config_id, cid))
            loo.update(agree=ag, disagree=dis, abstain=ab)
        failures = Counter(
            (r["failure"] or {}).get("kind") or ("error" if r["error"] else "none")
            for r in attempts
            if not ok(r)
        )
        cfg = attempts[0]["config"]
        arms[config_id] = {
            "provider": "/".join(
                sorted({cfg["visual"]["provider"], cfg["evaluation"]["provider"]})
            ),
            "visual": f"{cfg['visual']['model']} {cfg['visual']['effort'] or cfg['visual']['thinking']}",
            "evaluation": f"{cfg['evaluation']['model']} {cfg['evaluation']['effort'] or cfg['evaluation']['thinking']}",
            "note": cfg.get("note", ""),
            "homes": len(by_case),
            "attempts": len(attempts),
            "valid": len(good),
            "reliability": len(good) / len(attempts),
            "failures": dict(failures),
            **cost_metrics(attempts),
            "retried_calls": retried,
            "wall_p50": pct(wall, 0.5),
            "wall_p90": pct(wall, 0.9),
            "visual_p50": pct(visual_lat, 0.5),
            "eval_p50": pct(eval_lat, 0.5),
            "visual_tok_s_p50": pct(tps, 0.5),
            "visual_output_tokens_p50": pct(
                [c["output_tokens"] for r in good for c in r["calls"] if c["phase"] == "visual"],
                0.5,
            ),
            "text_facts": dict(fact),
            "text_fact_accuracy": fact["right"] / (fact["right"] + fact["wrong"])
            if fact["right"] + fact["wrong"]
            else None,
            "badge_violations_per_home": statistics.mean(violations) if violations else None,
            "homes_with_badge_violation": sum(v > 0 for v in violations),
            "consensus": dict(cons),
            "consensus_agree_rate": cons["agree"] / sum(cons.values())
            if sum(cons.values())
            else None,
            "consensus_ci": bootstrap(cons_case) if cons_case else None,
            "loo": dict(loo),
            "loo_agree_rate": loo["agree"] / sum(loo.values()) if sum(loo.values()) else None,
            "rating_mean": statistics.mean(r["analysis"]["overall_rating"] for r in good)
            if good
            else None,
            "stability": stability(by_case),
        }
    summary = {"run": args.run, "arms": arms, "judge": judge_summary(), "silver_cases": len(silver)}
    Path(args.out).write_text(json.dumps(summary, indent=1, default=str))
    cols = [
        "homes",
        "reliability",
        "cost_per_valid_home",
        "wall_p50",
        "wall_p90",
        "text_fact_accuracy",
        "consensus_agree_rate",
        "loo_agree_rate",
        "rating_mean",
    ]
    print("arm".ljust(26) + "".join(c[:12].rjust(13) for c in cols))
    for config_id, m in sorted(arms.items(), key=lambda kv: kv[1]["cost_per_valid_home"] or 9):
        cells = []
        for c in cols:
            v = m[c]
            cells.append(
                ("-" if v is None else f"{v:.3f}" if isinstance(v, float) else str(v)).rjust(13)
            )
        print(config_id.ljust(26) + "".join(cells))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", default="core")
    parser.add_argument("--out", default=str(ROOT / "data" / "summary.json"))
    asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    main()

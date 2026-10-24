"""Offline audit of the retained bake-off; never imports its runner or calls providers.

Run from the repository root with its dependencies and PYTHONPATH=src:
  python docs/reviews/2026-10-21-model-bakeoff-audit.py EXPERIMENT_ROOT
Only aggregate evidence is printed. The source database is opened read-only.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from home_finder.filters.analysis_contracts import AnalysisOutputError, validate_output
from home_finder.filters.notification_policy import select_discovery
from home_finder.filters.quality import _VisualAnalysisResponse
from home_finder.models.notifications import NotificationPolicy

FIELDS = [
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


def valid(row: dict[str, Any]) -> bool:
    return row["analysis"] is not None and row["failure"] is None and row["error"] is None


def get(data: dict[str, Any], field: str) -> Any:
    value: Any = data
    for part in field.split("."):
        value = value.get(part) if isinstance(value, dict) else None
    return value


def equal(left: Any, right: Any) -> bool:
    if isinstance(left, str) and isinstance(right, str):
        return left.casefold() == right.casefold()
    return left == right


def wilson(wins: int, count: int) -> list[float]:
    z = 1.959963984540054
    rate = wins / count
    centre = (rate + z * z / (2 * count)) / (1 + z * z / count)
    half = z * math.sqrt(rate * (1 - rate) / count + z * z / (4 * count * count))
    half /= 1 + z * z / count
    return [centre - half, centre + half]


def main() -> None:
    root = Path(sys.argv[1]).resolve()
    data = root / "data"
    files = sorted((data / "runs" / "core").glob("*/*.json"))
    digest = hashlib.sha256()
    rows = []
    for path in files:
        body = path.read_bytes()
        digest.update(str(path.relative_to(data)).encode() + b"\0" + body + b"\0")
        rows.append(json.loads(body))
    first = defaultdict(dict)
    arms = defaultdict(list)
    for row in rows:
        arms[row["config_id"]].append(row)
        if row["rep"] == 0:
            first[row["config_id"]][row["case_id"]] = row

    labels = {}
    for case, a in first["fable51-low"].items():
        b = first["astra6-low"].get(case)
        if b is None or not valid(a) or not valid(b):
            continue
        a, b = a["analysis"], b["analysis"]
        label = {}
        for field in FIELDS:
            av, bv = get(a, field), get(b, field)
            if av not in (None, "unknown", "") and equal(av, bv):
                label[field] = av
        if (
            a["overall_rating"]
            and b["overall_rating"]
            and abs(a["overall_rating"] - b["overall_rating"]) <= 1
        ):
            label["overall_rating"] = (a["overall_rating"] + b["overall_rating"]) / 2
        av, bv = get(a, "space.total_area_sqm"), get(b, "space.total_area_sqm")
        if av and bv and abs(av - bv) / max(av, bv) <= 0.05:
            label["space.total_area_sqm"] = (av + bv) / 2
        labels[case] = label

    consensus = {}
    for arm, by_case in first.items():
        tally = Counter()
        for case, row in by_case.items():
            if case not in labels or not valid(row):
                continue
            tally["homes"] += 1
            for field, expected in labels[case].items():
                got = get(row["analysis"], field)
                if got in (None, "unknown", ""):
                    outcome = "abstain"
                elif field == "overall_rating":
                    outcome = "agree" if abs(got - expected) <= 1 else "disagree"
                elif field == "space.total_area_sqm":
                    outcome = "agree" if abs(got - expected) / expected <= 0.05 else "disagree"
                else:
                    outcome = "agree" if equal(got, expected) else "disagree"
                tally[outcome] += 1
        consensus[arm] = dict(tally)

    judges = {}
    judge_digest = hashlib.sha256()
    for pair in sorted((data / "judge").iterdir()):
        grouped = defaultdict(list)
        for path in sorted(pair.glob("*.json")):
            body = path.read_bytes()
            judge_digest.update(str(path.relative_to(data)).encode() + b"\0" + body + b"\0")
            row = json.loads(body)
            grouped[row["judge"]].append(row)
        judges[pair.name] = {}
        for judge, verdicts in grouped.items():
            tallies = {}
            for criterion in (
                "overall",
                "factual_accuracy",
                "calibration",
                "usefulness",
                "viewing_notes",
            ):
                tally = Counter()
                for row in verdicts:
                    win = row["verdict"][criterion]
                    win = win if isinstance(win, str) else win["winner"]
                    tally["tie" if win == "tie" else row[win]] += 1
                tallies[criterion] = dict(tally)
            judges[pair.name][judge] = tallies

    with sqlite3.connect(
        (data / "snapshot" / "properties.db").as_uri() + "?mode=ro", uri=True
    ) as db:
        homes = {
            row[0]: (row[1], row[2])
            for row in db.execute("SELECT unique_id, bedrooms, postcode FROM properties")
        }
    decisions = {}
    for arm in ("sonnet55-low", "sol61-low", "luna6-low", "haiku55-low", "sol6-low"):
        decisions[arm] = {}
        for case, row in first[arm].items():
            bedrooms, postcode = homes[row["unique_id"]]
            decision = select_discovery(
                row["analysis"],
                bedrooms,
                postcode=postcode,
                user_status="new",
                is_off_market=False,
                policy=NotificationPolicy(),
            )
            decisions[arm][case] = decision.disposition == "alert"
    crossings = {}
    for arm in decisions:
        common = [
            case
            for case in first[arm]
            if valid(first[arm][case]) and valid(first["sonnet55-low"][case])
        ]
        crossings[arm] = {
            "jointly_valid_homes": len(common),
            "sonnet_only_alert": sum(
                decisions["sonnet55-low"][c] and not decisions[arm][c] for c in common
            ),
            "challenger_only_alert": sum(
                decisions[arm][c] and not decisions["sonnet55-low"][c] for c in common
            ),
        }

    decode_results = []
    for row in arms["sonnet55-low"]:
        if valid(row):
            continue
        raw = dict(row["calls"][0]["raw_output"])
        decoded = 0
        for field, value in list(raw.items()):
            if isinstance(value, str) and value.lstrip().startswith("{"):
                try:
                    raw[field] = json.loads(value)
                    decoded += 1
                except json.JSONDecodeError:
                    pass
        try:
            validate_output(
                raw, _VisualAnalysisResponse, "visual", SimpleNamespace(stop_reason="tool_use")
            )
            recovered = True
        except AnalysisOutputError:
            recovered = False
        decode_results.append({"decoded_fields": decoded, "recovered": recovered})

    overall = judges["sonnet55-low__vs__sol61-low"]["opus55"]["overall"]
    wins = overall.get("sol61-low", 0)
    vote_count = wins + overall.get("sonnet55-low", 0)
    tail = max(wins, vote_count - wins)
    exact_p = min(
        1.0, 2 * sum(math.comb(vote_count, k) for k in range(tail, vote_count + 1)) / 2**vote_count
    )
    out = {
        "source_core_sha256": digest.hexdigest(),
        "source_judges_sha256": judge_digest.hexdigest(),
        "core_records": len(rows),
        "first_runs": sum(len(x) for x in first.values()),
        "homes": len({r["case_id"] for r in rows}),
        "arms": len(arms),
        "consensus_homes": len(labels),
        "consensus_field_instances": sum(map(len, labels.values())),
        "consensus": consensus,
        "judges": judges,
        "claude_judge_sol61_win_fraction": wins / vote_count,
        "claude_judge_sol61_wilson95": wilson(wins, vote_count),
        "claude_judge_sol61_exact_two_sided_p": exact_p,
        "zero_failures_30_upper95_one_sided_iid": 1 - 0.05 ** (1 / 30),
        "validity": {
            a: {
                "first_valid": sum(valid(r) for r in first[a].values()),
                "first_total": len(first[a]),
                "all_valid": sum(valid(r) for r in rs),
                "all_total": len(rs),
            }
            for a, rs in sorted(arms.items())
        },
        "retry_calls": [
            {"arm": r["config_id"], "phase": c["phase"], "attempts": c["attempts"]}
            for r in rows
            for c in r["calls"]
            if c["attempts"] > 1
        ],
        "decode_replay": decode_results,
        "decision_crossings_vs_sonnet": crossings,
        "alert_counts_assuming_new_active_homes": {
            a: sum(v.values()) for a, v in decisions.items()
        },
        "successful_request_median_seconds": {
            a: statistics.median(
                sum(c["latency_s"] or 0 for c in r["calls"]) for r in rs if valid(r)
            )
            for a, rs in arms.items()
        },
    }
    print(json.dumps(out, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

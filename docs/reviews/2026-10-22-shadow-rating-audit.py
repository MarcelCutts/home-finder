"""Reproduce the retained rating ablation without importing an experiment runner.

PYTHONPATH=src python docs/reviews/2026-10-22-shadow-rating-audit.py EXPERIMENT_ROOT
The experiment and SQLite snapshot are read-only. Output contains no listing prose.
"""

import hashlib
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from home_finder.filters.fit_score import FIT_SCORE_VERSION
from home_finder.filters.shadow_rating import SHADOW_RATING_POLICY_VERSION, compute_shadow_rating
from home_finder.models.notifications import NotificationPolicy
from home_finder.pipeline.rating_shadow_report import compare_rating, read_report


def load(data: Path, run: str) -> tuple[list[dict[str, Any]], str]:
    rows = []
    digest = hashlib.sha256()
    for path in sorted((data / "runs" / run).glob("*/*.json")):
        body = path.read_bytes()
        digest.update(str(path.relative_to(data)).encode() + b"\0" + body + b"\0")
        rows.append(json.loads(body))
    return rows, digest.hexdigest()


def valid(row: dict[str, Any]) -> bool:
    return row["analysis"] is not None and row["failure"] is None and row["error"] is None


def rating_flips(rows: list[dict[str, Any]], *, computed: bool = False) -> dict[str, int]:
    groups: dict[tuple[str, str], list[int]] = defaultdict(list)
    for row in rows:
        if valid(row):
            analysis = row["analysis"]
            rating = (
                compute_shadow_rating(analysis).candidate_rating
                if computed
                else analysis["overall_rating"]
            )
            groups[row["config_id"], row["case_id"]].append(rating)
    eligible = [values for values in groups.values() if len(values) >= 2]
    return {
        "valid_assessments": sum(map(len, groups.values())),
        "eligible_groups": len(eligible),
        "complete_three_repeat_groups": sum(len(values) == 3 for values in eligible),
        "flipping_groups": sum(len(set(values)) > 1 for values in eligible),
    }


def main() -> None:
    data = Path(sys.argv[1]).resolve() / "data"
    core, core_sha = load(data, "core")
    tightened, tightened_sha = load(data, "tightened")
    with sqlite3.connect((data / "snapshot/properties.db").as_uri() + "?mode=ro", uri=True) as db:
        homes = {
            row[0]: (row[1], row[2])
            for row in db.execute("SELECT unique_id,bedrooms,postcode FROM properties")
        }
    policy = NotificationPolicy()
    comparisons = []
    for row in core:
        if not valid(row):
            continue
        bedrooms, postcode = homes[row["unique_id"]]
        comparisons.append(
            {
                "case_id": row["case_id"],
                "arm": row["config_id"],
                "rep": row["rep"],
                **compare_rating(
                    row["analysis"],
                    bedrooms,
                    postcode=postcode,
                    user_status="new",
                    is_off_market=False,
                    policy=policy,
                ),
            }
        )
    matched_keys = {(r["config_id"], r["case_id"]) for r in tightened}
    matched = [r for r in core if (r["config_id"], r["case_id"]) in matched_keys]
    original_crossing_cases = {"cabafe00bcb", "ce8b8d1130c"}
    snapshot = read_report(data / "snapshot/properties.db", policy)
    report = {
        "mode": "retained_core_rating_ablation",
        "context": "All homes held new/active; only overall_rating is varied in isolated copies.",
        "rating_policy_version": SHADOW_RATING_POLICY_VERSION,
        "fit_score_version": FIT_SCORE_VERSION,
        "notification_policy": policy.model_dump(),
        "source_core_sha256": core_sha,
        "source_tightened_sha256": tightened_sha,
        "source_snapshot_context_sha256": hashlib.sha256(
            json.dumps(homes, sort_keys=True).encode()
        ).hexdigest(),
        "valid_assessments": len(comparisons),
        "unique_homes": len({r["case_id"] for r in comparisons}),
        "model_configurations": len({r["arm"] for r in comparisons}),
        "model_ratings": dict(Counter(r["model_rating"] for r in comparisons)),
        "candidate_ratings": dict(Counter(r["shadow"]["candidate_rating"] for r in comparisons)),
        "same_ratings": sum(
            r["model_rating"] == r["shadow"]["candidate_rating"] for r in comparisons
        ),
        "observed_weight_percent": dict(
            Counter(r["shadow"]["observed_weight_percent"] for r in comparisons)
        ),
        "changed_fit_scores": sum(
            r["baseline_selection"]["fit_score"] != r["candidate_selection"]["fit_score"]
            for r in comparisons
        ),
        "threshold_crossings": [r for r in comparisons if r["threshold_crossed"]],
        "original_provider_disagreements": [
            r
            for r in comparisons
            if r["case_id"] in original_crossing_cases
            and r["arm"] in {"sonnet55-low", "sol61-low"}
            and r["rep"] == 0
        ],
        "rating_repeatability": {
            "core_model": rating_flips(matched),
            "core_computed_offline": rating_flips(matched, computed=True),
            "tightened_separate_exploratory_run": rating_flips(tightened),
        },
        # These are the snapshot's historical analyses, not the core reruns.
        "historical_snapshot_separate": {
            key: value for key, value in snapshot.items() if key != "cases"
        }
        | {"crossing_cases": [r for r in snapshot["cases"] if r["threshold_crossed"]]},
    }
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

"""Compare a versioned candidate rating offline, without writes or provider calls.

Run explicitly with --database. This module is not called by the live pipeline.
It holds every other retained observation and owner state fixed. It neither
reinterprets floor/office/hosting definitions nor estimates a pending-send queue.
"""

import argparse
import hashlib
import json
import sqlite3
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from home_finder.filters.fit_score import FIT_SCORE_VERSION, AnalysisInput, normalized_fit_inputs
from home_finder.filters.notification_policy import select_discovery
from home_finder.filters.shadow_rating import SHADOW_RATING_POLICY_VERSION, compute_shadow_rating
from home_finder.models.notifications import NotificationPolicy


def compare_rating(
    analysis: AnalysisInput,
    bedrooms: int,
    *,
    postcode: str | None,
    user_status: str,
    is_off_market: bool,
    policy: NotificationPolicy,
) -> dict[str, Any]:
    """Replay selection on an isolated copy; failed/unrated inputs stay unavailable."""
    baseline = select_discovery(
        analysis,
        bedrooms,
        postcode=postcode,
        user_status=user_status,
        is_off_market=is_off_market,
        policy=policy,
    )
    values = normalized_fit_inputs(analysis, postcode=postcode)
    shadow = compute_shadow_rating(values) if values is not None else None
    candidate = (
        select_discovery(
            {**values, "overall_rating": shadow.candidate_rating},
            bedrooms,
            postcode=postcode,
            user_status=user_status,
            is_off_market=is_off_market,
            policy=policy,
        )
        if values is not None and shadow is not None
        else None
    )
    threshold_crossed = (
        baseline.fit_score is not None
        and candidate is not None
        and candidate.fit_score is not None
        and (baseline.fit_score >= policy.min_fit_score)
        != (candidate.fit_score >= policy.min_fit_score)
    )
    return {
        "model_rating": values["overall_rating"] if values is not None else None,
        "shadow": asdict(shadow) if shadow is not None else None,
        "baseline_selection": baseline.model_dump(),
        "candidate_selection": candidate.model_dump() if candidate is not None else None,
        "threshold_crossed": threshold_crossed,
        "selection_changed": candidate is not None
        and baseline.disposition != candidate.disposition,
    }


def read_report(database: Path, policy: NotificationPolicy) -> dict[str, Any]:
    """Read one consistent snapshot without initializing or migrating the database."""
    path = database.resolve(strict=True)
    conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        cases = []
        source_digest = hashlib.sha256()
        for row in conn.execute("""SELECT p.unique_id,p.bedrooms,p.postcode,p.user_status,
                p.is_off_market,q.analysis_json FROM properties p
                LEFT JOIN quality_analyses q ON q.property_unique_id=p.unique_id
                ORDER BY p.unique_id"""):
            # Hash the exact retained JSON plus scoring/owner context, without
            # emitting addresses, descriptions or model prose in this report.
            source = json.dumps(dict(row), sort_keys=True, ensure_ascii=False).encode()
            source_digest.update(source + b"\0")
            try:
                analysis = json.loads(row["analysis_json"]) if row["analysis_json"] else None
            except (ValueError, TypeError, RecursionError):
                analysis = None
            comparison = compare_rating(
                analysis if isinstance(analysis, dict) else None,
                row["bedrooms"],
                postcode=row["postcode"],
                user_status=row["user_status"],
                is_off_market=bool(row["is_off_market"]),
                policy=policy,
            )
            cases.append(
                {
                    "case_id": "c" + hashlib.sha256(row["unique_id"].encode()).hexdigest()[:10],
                    "source_sha256": hashlib.sha256(source).hexdigest(),
                    **comparison,
                }
            )
        coverage = Counter(case["shadow"]["coverage"] for case in cases if case["shadow"])
        return {
            "report_version": 1,
            "mode": "offline_rating_shadow",
            "changes_stored_ratings": False,
            "creates_deliveries": False,
            "rating_policy_version": SHADOW_RATING_POLICY_VERSION,
            "fit_score_version": FIT_SCORE_VERSION,
            "notification_policy": policy.model_dump(),
            "source_sha256": source_digest.hexdigest(),
            "total_homes": len(cases),
            "compared_homes": sum(coverage.values()),
            "coverage": dict(sorted(coverage.items())),
            "threshold_crossings": sum(case["threshold_crossed"] for case in cases),
            "selection_changes": sum(case["selection_changed"] for case in cases),
            "cases": cases,
        }
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--minimum-fit-score", type=int, default=60)
    args = parser.parse_args()
    try:
        report = read_report(
            args.database, NotificationPolicy(min_fit_score=args.minimum_fit_score)
        )
    except (OSError, sqlite3.Error, ValidationError) as exc:
        parser.exit(1, f"Rating shadow review unavailable: {type(exc).__name__}\n")
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

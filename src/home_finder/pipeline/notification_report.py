"""Replay discovery selection read-only, without credentials, migrations or sends."""

import argparse
import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from home_finder.filters.fit_score import FIT_SCORE_VERSION
from home_finder.filters.notification_policy import select_discovery
from home_finder.models.notifications import NotificationPolicy


def read_report(database: Path, policy: NotificationPolicy) -> dict[str, Any]:
    """Evaluate every retained home as a candidate, irrespective of past delivery.

    This is a counterfactual review, not a pending-send queue. An existing decision
    is never changed. The read transaction keeps all counts on the same snapshot.
    """
    path = database.resolve(strict=True)
    conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        reasons: Counter[str] = Counter()
        statuses: Counter[str] = Counter()
        dispositions: Counter[str] = Counter()
        scored = 0
        for row in conn.execute("""SELECT p.bedrooms,p.postcode,p.user_status,p.is_off_market,
                p.notification_status,q.analysis_json FROM properties p
                LEFT JOIN quality_analyses q ON q.property_unique_id=p.unique_id"""):
            try:
                analysis = json.loads(row["analysis_json"]) if row["analysis_json"] else None
            except json.JSONDecodeError:
                analysis = None
            decision = select_discovery(
                analysis,
                row["bedrooms"],
                postcode=row["postcode"],
                user_status=row["user_status"],
                is_off_market=bool(row["is_off_market"]),
                policy=policy,
            )
            reasons[decision.reason] += 1
            dispositions[decision.disposition] += 1
            statuses[row["notification_status"]] += 1
            scored += decision.fit_score is not None
        return {
            "mode": "shadow_every_retained_home",
            "creates_deliveries": False,
            "policy": policy.model_dump(),
            "fit_score_version": FIT_SCORE_VERSION,
            "total_homes": sum(reasons.values()),
            "scored_homes": scored,
            "dispositions": dict(sorted(dispositions.items())),
            "reasons": dict(sorted(reasons.items())),
            "existing_notification_statuses": dict(sorted(statuses.items())),
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
        parser.exit(1, f"Selection review unavailable: {type(exc).__name__}\n")
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

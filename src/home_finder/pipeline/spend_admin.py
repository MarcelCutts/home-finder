"""Explicit local reconciliation of one paid attempt, with the runner fenced."""

import argparse
import asyncio
import json
import re
from pathlib import Path
from typing import Any

import aiosqlite
from pydantic import ValidationError

from home_finder.db import PropertyStorage
from home_finder.db.spend import AttemptTransitionError
from home_finder.models.spend import ConfirmedCost
from home_finder.pipeline.runner import runner_lock
from home_finder.pipeline.spend_report import read_report


async def administer(
    database: Path, attempt_id: str, *, confirmed: ConfirmedCost | None
) -> dict[str, Any]:
    """A None confirmation releases only an undispatched reservation."""
    if re.fullmatch(r"[0-9a-f]{32}", attempt_id) is None:
        raise ValueError("Invalid attempt ID")
    path = database.resolve(strict=True)
    with runner_lock(str(path)) as acquired:
        if not acquired:
            raise ValueError("Stop the active runner before reconciling spending")
        if not (await read_report(path))["ledger_available"]:
            raise ValueError("This database has no paid-attempt ledger")
        async with PropertyStorage(str(path)) as storage:
            if confirmed is None:
                await storage.spend.release_undispatched(attempt_id)
            else:
                await storage.spend.reconcile(attempt_id, confirmed)
            return await storage.spend.report()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--attempt", required=True)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--release-undispatched", action="store_true")
    action.add_argument("--confirmed-cost-usd")
    parser.add_argument("--evidence-ref", help="Non-secret billing evidence reference")
    args = parser.parse_args()
    if bool(args.evidence_ref) == args.release_undispatched:
        parser.error("Use --evidence-ref only and always with --confirmed-cost-usd")
    try:
        confirmed = (
            None
            if args.release_undispatched
            else ConfirmedCost(amount_usd=args.confirmed_cost_usd, evidence_ref=args.evidence_ref)
        )
        report = asyncio.run(administer(args.database, args.attempt, confirmed=confirmed))
    except ValidationError:
        parser.exit(1, "Invalid exact amount or evidence reference\n")
    except (ValueError, OSError, aiosqlite.Error, AttemptTransitionError) as exc:
        parser.exit(1, f"Spending was not reconciled: {exc}\n")
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

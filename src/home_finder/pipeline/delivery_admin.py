"""Inspect delivery metadata or reconcile one outcome locally, with the runner fenced."""

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any, Literal

import aiosqlite
from pydantic import ValidationError

from home_finder.db import PropertyStorage
from home_finder.db.delivery import delivery_report
from home_finder.pipeline.run_lock import runner_lock


async def read_report(database: Path) -> dict[str, Any]:
    path = database.resolve(strict=True)
    async with aiosqlite.connect(path.as_uri() + "?mode=ro", uri=True) as conn:
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA query_only=ON")
        await conn.execute("BEGIN")
        try:
            return await delivery_report(conn)
        finally:
            await conn.rollback()


async def administer(
    database: Path,
    key: str,
    action: Literal["retry", "suppress", "acknowledge"],
    evidence_ref: str,
    message_ids: list[int] | None = None,
) -> dict[str, Any]:
    path = database.resolve(strict=True)
    with runner_lock(str(path)) as acquired:
        if not acquired:
            raise ValueError("Stop the active runner before reconciling delivery")
        if not (await read_report(path))["available"]:
            raise ValueError("This database has no delivery records")
        async with PropertyStorage(str(path)) as storage:
            await storage.deliveries.resolve(key, action, evidence_ref, message_ids=message_ids)
            return await storage.deliveries.report()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--delivery", help="Exact event_key from the report")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--retry-confirmed-undelivered", action="store_true")
    action.add_argument("--suppress", action="store_true")
    action.add_argument("--message-ids", help="Comma-separated IDs for the uncertain current part")
    parser.add_argument("--evidence-ref", help="Short non-secret operator evidence reference")
    args = parser.parse_args()
    has_action = args.retry_confirmed_undelivered or args.suppress or args.message_ids is not None
    if has_action != bool(args.delivery) or has_action != bool(args.evidence_ref):
        parser.error("Reconciliation needs --delivery, one action and --evidence-ref together")
    try:
        if not has_action:
            report = asyncio.run(read_report(args.database))
        else:
            selected: Literal["retry", "suppress", "acknowledge"] = (
                "retry"
                if args.retry_confirmed_undelivered
                else "suppress"
                if args.suppress
                else "acknowledge"
            )
            ids = [int(i) for i in args.message_ids.split(",")] if args.message_ids else None
            report = asyncio.run(
                administer(args.database, args.delivery, selected, args.evidence_ref, ids)
            )
    except (ValueError, OSError, aiosqlite.Error, ValidationError) as exc:
        parser.exit(1, f"Delivery was not reconciled: {type(exc).__name__}\n")
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

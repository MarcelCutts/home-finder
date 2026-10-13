"""Queue one detail-recovery outcome for up to 20 selected dropped/failed homes."""

import argparse
import asyncio
import json
from pathlib import Path

import aiosqlite

from home_finder.db import PropertyStorage
from home_finder.pipeline.runner import runner_lock


async def request_recovery(
    database: Path, property_ids: list[str], *, reason: str
) -> dict[str, str]:
    """Queue local recovery under the runner fence; perform no network requests."""
    if not 1 <= len(set(property_ids)) <= 20:
        raise ValueError("Select between 1 and 20 property IDs")
    if not reason.strip() or len(reason) > 200:
        raise ValueError("Provide a nonempty reason of at most 200 characters")
    path = database.resolve(strict=True)
    with runner_lock(str(path)) as acquired:
        if not acquired:
            raise ValueError("Stop the active runner before requesting processing recovery")
        async with PropertyStorage(str(path)) as storage:
            return await storage.pipeline.request_enrichment_recovery(
                property_ids, reason=reason.strip()
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--property-id", action="append", required=True)
    parser.add_argument(
        "--reason", required=True, help="Non-secret explanation or evidence reference"
    )
    args = parser.parse_args()
    try:
        result = asyncio.run(request_recovery(args.database, args.property_id, reason=args.reason))
    except (ValueError, OSError, aiosqlite.Error) as exc:
        parser.exit(1, f"Processing recovery was not requested: {exc}\n")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

"""Read spending without loading credentials, migrating or starting the pipeline."""

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite

from home_finder.db.spend import spend_report


async def read_report(path: Path) -> dict[str, Any]:
    resolved = path.resolve(strict=True)
    uri = resolved.as_uri() + "?mode=ro"
    async with aiosqlite.connect(uri, uri=True) as conn:
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA query_only=ON")
        await conn.execute("BEGIN")
        try:
            return await spend_report(conn, datetime.now(UTC))
        finally:
            await conn.rollback()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = asyncio.run(read_report(args.database))
    except (OSError, aiosqlite.Error) as exc:
        parser.exit(1, f"Cannot read spending report: {type(exc).__name__}\n")
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

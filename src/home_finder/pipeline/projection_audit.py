"""Inspect pending listing inputs without migrating, rebuilding or acknowledging them.

Run with ``python -m home_finder.pipeline.projection_audit --database COPY --output NEW_DIR``.
The private report contains retained listing evidence. It is not a membership approval
or a certification that price, availability, assets or assessments are current.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import aiosqlite
from pydantic import JsonValue

from home_finder.db.home_projections import (
    decode_listing_projection,
    listing_projection,
    projection_status,
)
from home_finder.models.home_projection import LISTING_PROJECTION_VERSION
from home_finder.models.observations import canonical_json

SUPPORTED_SCHEMA_VERSION = 15
MAX_AUDIT_BYTES = 16_000_000


async def read_projection_audit(
    database: Path, *, home_ids: tuple[str, ...] = (), limit: int = 10
) -> dict[str, JsonValue]:
    """Read one WAL-aware snapshot, refusing missing/older databases and unbounded output."""
    if not 1 <= limit <= 25:
        raise ValueError("Projection audit requires a limit between 1 and 25")
    if len(home_ids) > limit or len(home_ids) != len(set(home_ids)):
        raise ValueError("Requested home IDs must be unique and fit within the audit limit")
    database = database.resolve(strict=True)
    entries: list[JsonValue] = []
    size = 0
    async with aiosqlite.connect(database.as_uri() + "?mode=ro", uri=True) as conn:
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA query_only = ON")
        await conn.execute("BEGIN")
        async with conn.execute("PRAGMA user_version") as cur:
            row = await cur.fetchone()
        assert row is not None
        version = row[0]
        if version != SUPPORTED_SCHEMA_VERSION:
            raise ValueError(
                f"Projection audit requires an already migrated schema {SUPPORTED_SCHEMA_VERSION}"
            )
        selected = (
            sorted(home_ids)
            if home_ids
            else [
                item.home_id
                for item in await projection_status(conn, limit=limit, pending_only=True)
            ]
        )
        async with conn.execute(
            "SELECT COUNT(*) FROM home_projection_generations g "
            "LEFT JOIN home_listing_projection_heads h USING(home_id) "
            "LEFT JOIN home_listing_projection_snapshots s ON s.snapshot_id = h.snapshot_id "
            "WHERE s.snapshot_id IS NULL OR s.input_generation != g.input_generation "
            "OR s.policy_version != ?",
            (LISTING_PROJECTION_VERSION,),
        ) as cur:
            row = await cur.fetchone()
        assert row is not None
        pending_count = row[0]
        for home_id in selected:
            candidate = await listing_projection(conn, home_id)
            async with conn.execute(
                "SELECT s.* FROM home_listing_projection_heads h "
                "JOIN home_listing_projection_snapshots s ON s.snapshot_id = h.snapshot_id "
                "WHERE h.home_id = ?",
                (home_id,),
            ) as cur:
                row = await cur.fetchone()
            previous = decode_listing_projection(row) if row else None
            before = {item.member.evidence_key for item in previous.listings} if previous else set()
            after = {item.member.evidence_key for item in candidate.listings}
            entry: dict[str, JsonValue] = {
                "home_id": home_id,
                "expected_input_generation": candidate.input_generation,
                "previous_snapshot_id": previous.sha256 if previous else None,
                "candidate_snapshot_id": candidate.sha256,
                "candidate_listing_material_sha256": candidate.listing_material_sha256,
                "material_changed": previous.listing_material_sha256
                != candidate.listing_material_sha256
                if previous
                else None,
                "route_changed": previous.route != candidate.route if previous else None,
                "members_added": cast(list[JsonValue], sorted(after - before)),
                "members_removed": cast(list[JsonValue], sorted(before - after)),
                "previous": previous.model_dump(mode="json") if previous else None,
                "candidate": candidate.model_dump(mode="json"),
            }
            size += len(canonical_json(entry).encode())
            if size > MAX_AUDIT_BYTES:
                raise ValueError(
                    "Projection audit exceeds the 16 MB review bound; reduce the limit"
                )
            entries.append(entry)
        await conn.rollback()
    report: dict[str, JsonValue] = {
        "report_version": 1,
        "mode": "read_only_listing_projection_audit",
        "recorded_at": datetime.now(UTC).isoformat(),
        "database_schema_version": version,
        "listing_projection_version": LISTING_PROJECTION_VERSION,
        "selected_homes": len(entries),
        "pending_homes": pending_count,
        "entries": entries,
    }
    if len(canonical_json(report).encode()) + 1 > MAX_AUDIT_BYTES:
        raise ValueError("Projection audit exceeds the 16 MB review bound; reduce the limit")
    return report


async def write_projection_audit(
    database: Path, output: Path, *, home_ids: tuple[str, ...] = (), limit: int = 10
) -> Path:
    report = await read_projection_audit(database, home_ids=home_ids, limit=limit)
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    path = output / "listing-projection-audit.json"
    with path.open("x", encoding="utf-8") as stream:
        os.fchmod(stream.fileno(), 0o600)
        # Compact canonical JSON also makes the on-disk size bound exact.
        stream.write(canonical_json(report))
        stream.write("\n")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database", type=Path, required=True, help="Existing schema-15 database copy"
    )
    parser.add_argument("--output", type=Path, required=True, help="New private report directory")
    parser.add_argument(
        "--home-id", action="append", default=[], help="Stable home ID; repeat up to the limit"
    )
    parser.add_argument("--limit", type=int, default=10, help="Maximum inspected homes (1-25)")
    args = parser.parse_args()
    path = asyncio.run(
        write_projection_audit(
            args.database, args.output, home_ids=tuple(args.home_id), limit=args.limit
        )
    )
    report = json.loads(path.read_text(encoding="utf-8"))
    print(
        json.dumps(
            {
                "report": str(path),
                "selected_homes": report["selected_homes"],
                "pending_homes": report["pending_homes"],
                "listing_projection_version": report["listing_projection_version"],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()

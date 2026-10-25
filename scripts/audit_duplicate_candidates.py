#!/usr/bin/env python3
"""Rank metadata-similar canonical homes in an offline, read-only SQLite snapshot."""

from __future__ import annotations

import argparse
import heapq
import json
import os
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from itertools import combinations, islice
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from home_finder.matching.scoring import MatchScore, calculate_match_score
from home_finder.models import Property

LIMITATIONS = [
    "Identity is unconfirmed; candidates require independent review before any merge.",
    "Scores are uncalibrated metadata heuristics, not probabilities or identity confidence.",
    "Different flats in the same building can share every scored location signal.",
    "Canonical metadata may be stale or copied; source memberships are not independent evidence.",
    "No images, cached hashes, descriptions, network services or models were consulted.",
    "Only canonical homes are compared; unlinked listings and within-home errors are excluded.",
    "Bounds and metadata gates can omit duplicates; absence is not evidence of distinct identity.",
]


class AuditError(ValueError):
    """An actionable audit error containing no private database values or paths."""


@dataclass(frozen=True)
class Home:
    home_id: str
    canonical: Property
    source_listings: tuple[dict[str, Any], ...]

    @property
    def platforms(self) -> set[str]:
        return {self.canonical.source.value} | {row["source"] for row in self.source_listings}

    def as_record(self) -> dict[str, Any]:
        return {
            "home_id": self.home_id,
            "canonical": self.canonical.model_dump(
                mode="json",
                include={
                    "source",
                    "source_id",
                    "url",
                    "title",
                    "price_pcm",
                    "bedrooms",
                    "address",
                    "postcode",
                    "latitude",
                    "longitude",
                },
            ),
            "source_listings": list(self.source_listings),
        }


@dataclass(frozen=True)
class Candidate:
    left: Home
    right: Home
    score: MatchScore

    def as_record(self) -> dict[str, Any]:
        return {
            "left": self.left.as_record(),
            "right": self.right.as_record(),
            "overlapping_platforms": sorted(self.left.platforms & self.right.platforms),
            "identity_status": "unconfirmed",
            "score_status": "uncalibrated",
            "image_evidence": "not_compared",
            # Deliberately omit MatchScore.confidence / is_match: this is a review queue.
            "metadata_score": {
                "total": self.score.total,
                "signal_count": self.score.signal_count,
                "full_postcode": self.score.full_postcode,
                "coordinates": self.score.coordinates,
                "street_name": self.score.street_name,
                "outcode": self.score.outcode,
                "price": self.score.price,
            },
        }


@contextmanager
def open_snapshot(database: Path) -> Iterator[sqlite3.Connection]:
    """Require a frozen standalone file; never create DB, journal or shared-memory files.

    The caller must keep writers stopped for the entire audit. Immutable mode is
    unsafe for a live database, even when it happens to have no sidecars yet.
    """
    path = database.resolve(strict=True)
    if not path.is_file():
        raise AuditError("Expected a standalone SQLite snapshot")
    if any(Path(f"{path}{suffix}").exists() for suffix in ("-wal", "-shm", "-journal")):
        raise AuditError("Snapshot has SQLite sidecars; use a clean offline backup")
    conn = sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        conn.execute("PRAGMA temp_store=MEMORY")
        yield conn
    finally:
        conn.close()


def _load_homes(conn: sqlite3.Connection, max_homes: int) -> tuple[list[Home], int, bool]:
    # Check the normalized schema even for an empty properties table. Never migrate.
    conn.execute(
        "SELECT unique_id, source, url, merged_id, is_backfilled FROM source_listings LIMIT 0"
    )
    rows = conn.execute(
        """SELECT unique_id, source, source_id, url, title, price_pcm, bedrooms,
                  address, postcode, latitude, longitude
           FROM properties ORDER BY unique_id LIMIT ?""",
        (max_homes + 1,),
    ).fetchall()
    homes = []
    invalid = 0
    for row in rows[:max_homes]:
        values = dict(row)
        home_id = values.pop("unique_id")
        try:
            prop = Property.model_validate(values)
        except ValidationError:
            invalid += 1
            continue
        # Keep every member, including multiple listings from the same platform.
        # Do not trust the legacy sources/source_urls JSON as membership evidence.
        members = conn.execute(
            """SELECT unique_id, source, url, is_backfilled FROM source_listings
               WHERE merged_id = ? ORDER BY unique_id""",
            (home_id,),
        ).fetchall()
        homes.append(Home(home_id, prop, tuple(dict(member) for member in members)))
    return homes, invalid, len(rows) > max_homes


def audit_database(
    database: Path,
    *,
    max_homes: int = 1000,
    max_comparisons: int = 100_000,
    max_pairs: int = 100,
) -> dict[str, Any]:
    """Return a bounded review queue; no matching, regrouping or persistence occurs."""
    for name, value, ceiling in (
        ("max_homes", max_homes, 5000),
        ("max_comparisons", max_comparisons, 1_000_000),
        ("max_pairs", max_pairs, 1000),
    ):
        if not 1 <= value <= ceiling:
            raise AuditError(f"{name} must be between 1 and {ceiling}")
    with open_snapshot(database) as conn:
        homes, invalid, homes_truncated = _load_homes(conn, max_homes)

    compared = 0
    similar = 0

    def candidates() -> Iterator[Candidate]:
        nonlocal compared, similar
        for left, right in islice(combinations(homes, 2), max_comparisons):
            compared += 1
            # Distinct canonical rows are distinct home IDs, irrespective of platform.
            if left.home_id == right.home_id:
                continue
            if left.canonical.price_pcm <= 0 or right.canonical.price_pcm <= 0:
                continue
            score = calculate_match_score(left.canonical, right.canonical, image_hashes=None)
            # Bedrooms are gated by the pure scorer. Require similar positive prices
            # and a specific location signal; outcode + price alone is too broad.
            location_similar = (
                score.full_postcode > 0
                or score.coordinates > 0
                or (score.street_name > 0 and score.outcode > 0)
            )
            if score.price > 0 and location_similar:
                similar += 1
                yield Candidate(left, right, score)

    ranked = heapq.nsmallest(
        max_pairs,
        candidates(),
        key=lambda pair: (-pair.score.total, pair.left.home_id, pair.right.home_id),
    )
    summary = {
        "homes_loaded": len(homes),
        "invalid_homes_skipped": invalid,
        "homes_truncated": homes_truncated,
        "comparisons_examined": compared,
        "comparisons_truncated": len(homes) * (len(homes) - 1) // 2 > compared,
        "metadata_similar_pairs": similar,
        "candidate_pairs_returned": len(ranked),
        "candidates_truncated": similar > len(ranked),
        "same_platform_pairs_returned": sum(
            bool(pair.left.platforms & pair.right.platforms) for pair in ranked
        ),
        "identity_status": "unconfirmed",
        "score_status": "uncalibrated",
    }
    return {
        "version": 1,
        "metadata_basis": "properties (canonical homes)",
        "membership_basis": "source_listings.merged_id",
        "limits": {
            "max_homes": max_homes,
            "max_comparisons": max_comparisons,
            "max_pairs": max_pairs,
        },
        "summary": summary,
        "limitations": LIMITATIONS,
        "candidates": [pair.as_record() for pair in ranked],
    }


def write_private_report(output: Path, report: dict[str, Any]) -> None:
    """Create a private JSON file; existing files and symlinks are never followed."""
    payload = json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(payload)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        type=Path,
        required=True,
        help="Closed standalone snapshot; no writers or SQLite sidecars allowed",
    )
    parser.add_argument(
        "--output", type=Path, required=True, help="New private JSON file (no overwrite)"
    )
    parser.add_argument("--max-homes", type=int, default=1000, help="1..5000, in home ID order")
    parser.add_argument("--max-comparisons", type=int, default=100_000, help="1..1000000")
    parser.add_argument(
        "--max-pairs", type=int, default=100, help="1..1000, ranked within scanned pairs"
    )
    args = parser.parse_args(argv)
    try:
        report = audit_database(
            args.database,
            max_homes=args.max_homes,
            max_comparisons=args.max_comparisons,
            max_pairs=args.max_pairs,
        )
        write_private_report(args.output, report)
    except AuditError as exc:
        parser.exit(1, f"Cannot create duplicate-candidate audit: {exc}.\n")
    except (OSError, sqlite3.Error, ValueError) as exc:
        # Validation/SQL/path exceptions can include private input; print only their class.
        parser.exit(1, f"Cannot create duplicate-candidate audit ({type(exc).__name__}).\n")
    print(json.dumps(report["summary"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

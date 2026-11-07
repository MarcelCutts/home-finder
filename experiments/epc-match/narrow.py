"""Ask whether bedrooms can settle an ambiguous match.

Usage, from this directory, after `run.py`:

    uv run python narrow.py --max-homes 25

A certificate counts habitable rooms: bedrooms, living and dining rooms,
studies. A flat with N bedrooms and one living room has N + 1. For each
ambiguous listing with at most --max-homes candidate homes, this fetches the
newest certificate of each candidate and keeps the homes whose habitable
rooms equal the listing's bedrooms plus one, or whose rooms aren't counted.
One home left is a narrowed match. Every listing tried goes to
data/narrowed/<dataset>.jsonl in run.py's format, with evidence "rooms", for
`report.py` and `handcheck.py --narrowed`. Each also keeps the floor areas of
the homes with the expected rooms, so report.py can ask how close their
median comes when several remain.

The rule is a heuristic; report.py shows how often it holds on the strict
single matches.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from dataclasses import asdict
from typing import Any

from epc_api import CertificateDetail, EpcApi, MissingTokenError, load_token
from paths import CACHE, NARROWED, RESULTS
from report import load_records
from run import in_parallel


def expected_rooms(bedrooms: int) -> int:
    """Habitable rooms of a home with one living room; a studio is one room."""
    return max(bedrooms, 0) + 1


def narrow_by_rooms(
    record: dict[str, Any], detail: Callable[[str], CertificateDetail | None]
) -> dict[str, Any]:
    """The record narrowed to the candidate homes that could have the expected rooms.

    A home whose newest certificate doesn't count rooms can't be ruled out,
    so it stays a candidate: one home left means the only one not ruled out.
    `rooms_known` counts the homes the rule could judge; when it is zero the
    rule wasn't applied and every candidate is left.
    """
    want = expected_rooms(record["listing"]["bedrooms"])
    kept = []
    matched_areas: list[float | None] = []
    known = 0
    for home in record["homes"]:
        newest = detail(home["certificates"][0]["certificate_number"])
        if newest is None or newest.habitable_rooms is None:
            kept.append(home)
            continue
        known += 1
        if newest.habitable_rooms == want:
            kept.append(home)
            matched_areas.append(newest.total_floor_area)
    outcome = "one" if len(kept) == 1 else "ambiguous" if kept else "none"
    narrowed = {
        **record,
        "outcome": outcome,
        "evidence": "rooms",
        "reason": "rooms_not_found" if not kept else None,
        "homes": kept,
        "homes_before": len(record["homes"]),
        "rooms_known": known,
        "matched_floor_areas": matched_areas,
    }
    if outcome == "one":
        certificates = kept[0]["certificates"]
        narrowed["details"] = [
            asdict(d) for c in certificates if (d := detail(c["certificate_number"])) is not None
        ]
    return narrowed


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--max-homes", type=int, default=25)
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args(argv)
    try:
        token = load_token()
    except MissingTokenError as error:
        print(error, file=sys.stderr)
        return 2

    NARROWED.mkdir(parents=True, exist_ok=True)
    with EpcApi(token, CACHE) as api:
        for path in sorted(RESULTS.glob("*.jsonl")):
            chosen = [
                r
                for r in load_records([path])
                if r["outcome"] == "ambiguous" and len(r["homes"]) <= args.max_homes
            ]
            newest = dict.fromkeys(
                h["certificates"][0]["certificate_number"] for r in chosen for h in r["homes"]
            )
            in_parallel(api.certificate, newest, args.workers, f"{path.stem} certificates")
            with (NARROWED / path.name).open("w") as out:
                for record in chosen:
                    out.write(json.dumps(narrow_by_rooms(record, api.certificate)) + "\n")
            print(f"{path.stem}: narrowed {len(chosen)} ambiguous listings")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

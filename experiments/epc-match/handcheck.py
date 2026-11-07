"""Draw the hand-check sample and lay each match out for judgement.

Usage, from this directory, after `run.py`:

    uv run python handcheck.py --size 30

Writes, under the gitignored data/handcheck/:

- sheet.md (sheet-narrowed.md with --narrowed): for each sampled single
  match, what the listing says (from the database copy only) beside what the
  certificate says;
- judgements.csv (judgements-narrowed.csv): one row per sampled listing,
  verdict and reason blank, for a person to fill in. `report.py` reads it
  back. An existing file is never overwritten.

Floor areas are left off the sheet so that judging whether a match is the
right home never leans on the floor-area agreement being measured.

Verdicts: correct (a fact that names the dwelling ties them, such as a flat
number, a lettered house number or the advert's EPC chart score, and nothing
contradicts); likely (consistent on dwelling type, rooms, floor and build age,
nothing specific; a building number alone is not specific, since a converted
house holds several flats); wrong (a fact contradicts); unclear (too little to
say).
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sqlite3
from collections.abc import Sequence
from typing import Any

from paths import HANDCHECK, INPUTS, NARROWED, RESULTS
from report import load_records


def draw_sample(records: Sequence[dict[str, Any]], size: int, seed: int) -> list[dict[str, Any]]:
    """A seeded sample of single matches, spread across datasets and evidence.

    Each (dataset, evidence) stratum gets a share in proportion to its size,
    and at least one, so rare kinds of match are looked at too.
    """
    singles = [r for r in records if r["outcome"] == "one"]
    strata: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for r in sorted(singles, key=lambda r: (r["listing"]["dataset"], r["listing"]["unique_id"])):
        strata.setdefault((r["listing"]["dataset"], r["evidence"]), []).append(r)
    rng = random.Random(seed)
    sample: list[dict[str, Any]] = []
    for key in sorted(strata):
        members = strata[key]
        share = max(1, round(size * len(members) / max(1, len(singles))))
        sample.extend(rng.sample(members, min(share, len(members))))
    return sample


def listing_context(dataset: str, unique_id: str) -> dict[str, Any]:
    """Title, text and analysis notes for a listing, from the database copy."""
    uri = f"file:{INPUTS / f'{dataset}.db'}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        conn.row_factory = sqlite3.Row
        prop = conn.execute(
            "SELECT title, description FROM properties WHERE unique_id = ?",
            (unique_id,),
        ).fetchone()
        analysis = conn.execute(
            "SELECT analysis_json FROM quality_analyses WHERE property_unique_id = ?",
            (unique_id,),
        ).fetchone()
    data = json.loads(analysis["analysis_json"]) if analysis else {}
    light = data.get("light_space") or {}
    images = INPUTS / f"{dataset}_images" / unique_id.replace(":", "_")
    return {
        "title": prop["title"] if prop else None,
        "description": (prop["description"] or "")[:900] if prop else "",
        "floor_level": light.get("floor_level"),
        "light_notes": light.get("notes"),
        "outdoor": (data.get("outdoor_space") or {}).get("notes"),
        "epc_images": sorted(str(p) for p in images.glob("epc_*")) if images.exists() else [],
    }


def sheet_entry(index: int, record: dict[str, Any]) -> str:
    listing = record["listing"]
    context = listing_context(listing["dataset"], listing["unique_id"])
    home = record["homes"][0]
    lines = [
        f"## {index}. {listing['dataset']} {listing['unique_id']} (evidence: {record['evidence']})",
        "",
        "Listing:",
        f"- title: {context['title']}",
        f"- addresses: {' | '.join(listing['addresses'])}",
        f"- postcodes: {', '.join(listing['postcodes']) or listing['outcode']}",
        f"- bedrooms: {listing['bedrooms']}; type: {listing['property_type']}; "
        f"floor: {context['floor_level']}; advert EPC band: {listing['advert_epc_band']}",
        f"- light notes: {context['light_notes']}",
        f"- outdoor notes: {context['outdoor']}",
        f"- EPC chart images: {', '.join(context['epc_images']) or 'none'}",
    ]
    if context["description"]:
        lines.append(f"- description: {context['description']}")
    lines += ["", f"Certificates for the matched home ({len(home['certificates'])}):"]
    details = {d["certificate_number"]: d for d in record.get("details") or []}
    for cert in home["certificates"]:
        d = details.get(cert["certificate_number"], {})
        lines.append(
            f"- {', '.join(cert['address_lines'])}, {cert['postcode']}; "
            f"lodged {cert['registration_date']}; band {cert['band']} "
            f"(SAP {d.get('energy_rating')}); {d.get('dwelling_type')}; "
            f"habitable rooms {d.get('habitable_rooms')}; flat position {d.get('flat_position')}; "
            f"age band {d.get('construction_age_band')}; UPRN source {d.get('uprn_source')}"
        )
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--size", type=int, default=30)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--narrowed", action="store_true", help="sample narrow.py's matches")
    args = parser.parse_args(argv)

    source, suffix = (NARROWED, "-narrowed") if args.narrowed else (RESULTS, "")
    records = load_records(sorted(source.glob("*.jsonl")))
    sample = draw_sample(records, args.size, args.seed)
    HANDCHECK.mkdir(parents=True, exist_ok=True)
    sheet = "\n".join(sheet_entry(i, r) for i, r in enumerate(sample, 1))
    (HANDCHECK / f"sheet{suffix}.md").write_text(sheet)
    judgements = HANDCHECK / f"judgements{suffix}.csv"
    if judgements.exists():
        print(f"{judgements} exists; not overwriting it.")
    else:
        with judgements.open("w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["dataset", "unique_id", "evidence", "verdict", "reason"])
            for r in sample:
                listing = r["listing"]
                writer.writerow([listing["dataset"], listing["unique_id"], r["evidence"], "", ""])
    print(f"Sampled {len(sample)} single matches into {HANDCHECK}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

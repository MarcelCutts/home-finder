"""Aggregate match results into the numbers the README reports.

Usage, from this directory, after `run.py` (and the hand-check, if done):

    uv run python report.py

Prints markdown tables of counts and rates only. It never prints an
address, postcode, listing id or certificate number, so its output can be
committed.
"""

from __future__ import annotations

import csv
import json
import math
import statistics
import sys
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "data" / "results"
NARROWED = HERE / "data" / "narrowed"
JUDGEMENTS = HERE / "data" / "handcheck" / "judgements.csv"
NARROWED_JUDGEMENTS = HERE / "data" / "handcheck" / "judgements-narrowed.csv"

Record = dict[str, Any]

# Floor areas outside this range are not a home's internal area.
PLAUSIBLE_AREA_SQM = (12.0, 600.0)


def load_records(paths: Iterable[Path]) -> list[Record]:
    return [json.loads(line) for path in paths for line in path.read_text().splitlines()]


def pct(part: int, whole: int) -> str:
    return f"{100 * part / whole:.0f}%" if whole else "n/a"


def wilson_interval(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for a proportion; sensible at small n."""
    if n == 0:
        return (0.0, 0.0)
    p = successes / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return (max(0.0, centre - half), min(1.0, centre + half))


# --- Match outcomes -------------------------------------------------------


def outcome_table(records: Sequence[Record], group: str) -> list[list[str]]:
    """Rows of one / ambiguous / none counts, split by a record field."""
    rows = []
    for value in sorted({str(r[group]) for r in records}):
        subset = [r for r in records if str(r[group]) == value]
        counts = Counter(r["outcome"] for r in subset)
        rows.append(
            [
                value,
                str(len(subset)),
                *(
                    f"{counts[o]} ({pct(counts[o], len(subset))})"
                    for o in ("one", "ambiguous", "none")
                ),
            ]
        )
    counts = Counter(r["outcome"] for r in records)
    rows.append(
        [
            "all",
            str(len(records)),
            *(
                f"{counts[o]} ({pct(counts[o], len(records))})"
                for o in ("one", "ambiguous", "none")
            ),
        ]
    )
    return rows


def latest_band(home: Record) -> str | None:
    band: str | None = home["certificates"][0].get("band")
    return band


@dataclass(frozen=True)
class AmbiguitySummary:
    listings: int
    median_homes: float
    quartiles: tuple[float, float]
    one_band: int
    """Listings whose candidate homes all carry the same current band."""
    within_two: int
    """Listings with at most two candidate homes."""


def ambiguity_summary(records: Sequence[Record]) -> AmbiguitySummary | None:
    ambiguous = [r for r in records if r["outcome"] == "ambiguous"]
    if not ambiguous:
        return None
    sizes = [len(r["homes"]) for r in ambiguous]
    q = statistics.quantiles(sizes, n=4) if len(sizes) > 1 else [sizes[0]] * 3
    one_band = sum(len({latest_band(h) for h in r["homes"]} - {None}) == 1 for r in ambiguous)
    return AmbiguitySummary(
        listings=len(ambiguous),
        median_homes=statistics.median(sizes),
        quartiles=(q[0], q[2]),
        one_band=one_band,
        within_two=sum(s <= 2 for s in sizes),
    )


# --- Single matches: certificates and floor area -------------------------


def plausible_area(area: float | None) -> bool:
    return area is not None and PLAUSIBLE_AREA_SQM[0] <= area <= PLAUSIBLE_AREA_SQM[1]


def epc_floor_area(record: Record) -> float | None:
    """Floor area from the newest certificate of a single match that has a plausible one."""
    for detail in record.get("details") or []:
        area = detail.get("total_floor_area")
        if plausible_area(area):
            return float(area)
    return None


@dataclass(frozen=True)
class CertificateSummary:
    matches: int
    with_detail: int
    several_certificates: int
    areas_disagree: int
    """Homes whose certificates' floor areas differ by more than 10%."""
    implausible_area: int
    conflicting: int
    """Homes with two certificates lodged within a month of each other in different bands:
    two dwellings sharing one address, or a corrected assessment."""
    median_age_years: float | None
    """Years from the newest certificate's lodgement to the listing snapshot."""


def certificate_summary(
    records: Sequence[Record], snapshot_year: dict[str, float]
) -> CertificateSummary:
    singles = [r for r in records if r["outcome"] == "one"]
    with_detail = [r for r in singles if r.get("details")]
    disagree = 0
    implausible = 0
    ages = []
    for r in with_detail:
        areas = [
            d["total_floor_area"] for d in r["details"] if plausible_area(d["total_floor_area"])
        ]
        if len(areas) > 1 and max(areas) > 1.1 * min(areas):
            disagree += 1
        if not plausible_area(r["details"][0]["total_floor_area"]):
            implausible += 1
        date = r["details"][0].get("registration_date")
        if date:
            year = int(date[:4]) + (int(date[5:7]) - 1) / 12
            ages.append(snapshot_year[r["listing"]["dataset"]] - year)
    return CertificateSummary(
        matches=len(singles),
        with_detail=len(with_detail),
        several_certificates=sum(len(r["homes"][0]["certificates"]) > 1 for r in singles),
        areas_disagree=disagree,
        implausible_area=implausible,
        conflicting=sum(has_conflicting_certificates(r["homes"][0]) for r in singles),
        median_age_years=statistics.median(ages) if ages else None,
    )


def has_conflicting_certificates(home: Record, within_days: int = 31) -> bool:
    lodged = [
        (date.fromisoformat(c["registration_date"]), c.get("band"))
        for c in home["certificates"]
        if c.get("registration_date")
    ]
    return any(
        abs((a_date - b_date).days) <= within_days and a_band != b_band
        for i, (a_date, a_band) in enumerate(lodged)
        for b_date, b_band in lodged[i + 1 :]
    )


@dataclass(frozen=True)
class AreaAgreement:
    source: str
    n: int
    median_abs_pct: float
    median_signed_pct: float
    """EPC minus listing, as % of the EPC figure; positive means the EPC is larger."""
    within_5: int
    within_10: int
    within_20: int


def area_pairs(
    records: Iterable[Record], correct_only: set[tuple[str, str]] | None = None
) -> list[tuple[str, float, float]]:
    """(listing area source, listing m², EPC m²) for single matches with both figures.

    A listing area outside the plausible range (a square-foot figure stored as
    square metres, say) is left out, as is the matching certificate's.
    """
    pairs = []
    for r in records:
        if r["outcome"] != "one":
            continue
        listing = r["listing"]
        if (
            correct_only is not None
            and (listing["dataset"], listing["unique_id"]) not in correct_only
        ):
            continue
        epc = epc_floor_area(r)
        if epc is None or not plausible_area(listing.get("floor_area_sqm")):
            continue
        source = "floorplan" if listing["floor_area_source"] == "estimated" else "advert"
        pairs.append((source, float(listing["floor_area_sqm"]), epc))
    return pairs


def area_agreement(pairs: Sequence[tuple[str, float, float]], source: str) -> AreaAgreement | None:
    chosen = [(listed, epc) for s, listed, epc in pairs if source in ("all", s)]
    if not chosen:
        return None
    signed = [100 * (epc - listed) / epc for listed, epc in chosen]
    absolute = [abs(d) for d in signed]
    return AreaAgreement(
        source=source,
        n=len(chosen),
        median_abs_pct=statistics.median(absolute),
        median_signed_pct=statistics.median(signed),
        within_5=sum(d <= 5 for d in absolute),
        within_10=sum(d <= 10 for d in absolute),
        within_20=sum(d <= 20 for d in absolute),
    )


def band_agreement(records: Iterable[Record]) -> tuple[int, int, int]:
    """(single matches with an advert band, same band, within one band)."""
    order = "ABCDEFG"
    n = same = near = 0
    for r in records:
        advert = r["listing"].get("advert_epc_band")
        if r["outcome"] != "one" or not advert:
            continue
        epc = latest_band(r["homes"][0])
        if not epc:
            continue
        n += 1
        same += epc == advert
        near += abs(order.index(epc) - order.index(advert)) <= 1
    return n, same, near


def rooms_offsets(
    records: Iterable[Record], only: set[tuple[str, str]] | None = None
) -> Counter[int]:
    """Habitable rooms minus bedrooms, over single matches whose certificate counts rooms.

    `narrow.py` assumes the offset is one; this shows how often it is.
    """
    offsets: Counter[int] = Counter()
    for r in records:
        listing = r["listing"]
        if r["outcome"] != "one" or not r.get("details"):
            continue
        if only is not None and (listing["dataset"], listing["unique_id"]) not in only:
            continue
        rooms = r["details"][0].get("habitable_rooms")
        if rooms is not None:
            offsets[rooms - listing["bedrooms"]] += 1
    return offsets


# --- Hand-check -----------------------------------------------------------

VERDICTS = ("correct", "likely", "wrong", "unclear")


def load_judgements(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open() as f:
        rows = list(csv.DictReader(f))
    for row in rows:
        if row["verdict"] not in VERDICTS:
            raise ValueError(f"unknown verdict {row['verdict']!r}")
    return rows


def judged_correct(judgements: Iterable[dict[str, str]]) -> set[tuple[str, str]]:
    """(dataset, unique_id) of the listings judged correct or likely."""
    return {
        (j["dataset"], j["unique_id"]) for j in judgements if j["verdict"] in ("correct", "likely")
    }


def correct_match_rate(
    judgements: Sequence[dict[str, str]],
) -> tuple[int, int, tuple[float, float]]:
    """(correct or likely, judged, 95% interval). "Unclear" is left out of the base."""
    judged = [j for j in judgements if j["verdict"] != "unclear"]
    good = sum(j["verdict"] in ("correct", "likely") for j in judged)
    return good, len(judged), wilson_interval(good, len(judged))


# --- Output ---------------------------------------------------------------


def table(header: Sequence[str], rows: Iterable[Sequence[str]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    return "\n".join(lines)


def main() -> int:
    paths = sorted(RESULTS.glob("*.jsonl"))
    if not paths:
        print("No results yet: run run.py first.", file=sys.stderr)
        return 2
    records = load_records(paths)
    for r in records:
        r["dataset"] = r["listing"]["dataset"]
        r["full_postcode"] = "full postcode" if r["listing"]["postcodes"] else "district only"

    outcome_header = ["", "listings", "one home", "ambiguous", "none"]
    print("## Outcomes by dataset\n")
    print(table(outcome_header, outcome_table(records, "dataset")))
    print("\n## Outcomes by what the listing's postcode gives\n")
    print(table(outcome_header, outcome_table(records, "full_postcode")))
    print("\n## Outcomes by most specific evidence used\n")
    with_evidence = [r for r in records if r["evidence"]]
    print(table(outcome_header, outcome_table(with_evidence, "evidence")))

    print("\n## Why no match\n")
    reasons = Counter(r["reason"] for r in records if r["outcome"] == "none")
    print(table(["reason", "listings"], [[k, str(v)] for k, v in reasons.most_common()]))
    elsewhere = sum(
        r["outcome"] == "none" and r["candidate_certificates"] == 0 and r["outside_district"] > 0
        for r in records
    )
    print(
        f"\n{elsewhere} district-only listings found certificates on their street only "
        "outside the district the portal gave."
    )

    summary = ambiguity_summary(records)
    if summary:
        print("\n## Ambiguous matches\n")
        print(
            f"- {summary.listings} listings; median {summary.median_homes:g} candidate homes "
            f"(quartiles {summary.quartiles[0]:g} to {summary.quartiles[1]:g})\n"
            f"- {summary.within_two} ({pct(summary.within_two, summary.listings)}) "
            "have only two candidates\n"
            f"- {summary.one_band} ({pct(summary.one_band, summary.listings)}) have candidates "
            "that all share one current band"
        )
        disagree = sum(r["variants_disagree"] for r in records)
        print(f"- {disagree} are ambiguous because two portals' addresses picked different homes")

    certs = certificate_summary(records, snapshot_year={"october": 2026.8, "february": 2026.1})
    print("\n## Single matches: certificates\n")
    print(
        f"- {certs.matches} single matches, {certs.with_detail} with a full certificate fetched\n"
        f"- {certs.several_certificates} homes have more than one certificate; "
        f"{certs.areas_disagree} of those disagree on floor area by more than 10%\n"
        f"- {certs.implausible_area} newest certificates have an implausible floor area\n"
        f"- {certs.conflicting} homes have two certificates a month apart in different bands\n"
        f"- median newest-certificate age: "
        + (f"{certs.median_age_years:.1f} years" if certs.median_age_years is not None else "n/a")
    )

    narrowed = load_records(sorted(NARROWED.glob("*.jsonl")))
    if narrowed:
        counts = Counter(r["outcome"] for r in narrowed)
        sizes = [r["homes_before"] for r in narrowed]
        print("\n## Narrowing ambiguous matches by rooms\n")
        print(
            f"- {len(narrowed)} ambiguous listings with {min(sizes)} to {max(sizes)} candidate "
            f"homes tried: {counts['one']} ({pct(counts['one'], len(narrowed))}) left one home, "
            f"{counts['ambiguous']} still ambiguous, {counts['none']} left none"
        )

    judgements = load_judgements(JUDGEMENTS)
    narrowed_judgements = load_judgements(NARROWED_JUDGEMENTS)
    correct = judged_correct([*judgements, *narrowed_judgements])
    groups = [
        ("strict single matches", records),
        ("narrowed single matches", narrowed),
        ("hand-checked correct, strict and narrowed", [*records, *narrowed]),
    ]

    print("\n## Floor area: EPC against the listing's figure\n")
    dropped = sum(
        r["outcome"] == "one"
        and r["listing"]["floor_area_sqm"] is not None
        and not plausible_area(r["listing"]["floor_area_sqm"])
        for r in [*records, *narrowed]
    )
    print(f"{dropped} single matches dropped for an implausible listing floor area.")
    header = [
        "listing figure",
        "pairs",
        "median abs diff",
        "median EPC minus listing",
        "≤5%",
        "≤10%",
        "≤20%",
    ]
    for label, group in groups:
        only = correct if label.startswith("hand-checked") else None
        pairs = area_pairs(group, correct_only=only)
        rows = []
        for source in ("floorplan", "advert", "all"):
            a = area_agreement(pairs, source)
            if a:
                rows.append(
                    [
                        source,
                        str(a.n),
                        f"{a.median_abs_pct:.1f}%",
                        f"{a.median_signed_pct:+.1f}%",
                        pct(a.within_5, a.n),
                        pct(a.within_10, a.n),
                        pct(a.within_20, a.n),
                    ]
                )
        if rows:
            print(f"\n{label}:\n")
            print(table(header, rows))

    print("\n## Band against the advert's stated band\n")
    for label, group in groups[:2]:
        n, same, near = band_agreement(group)
        print(
            f"- {label}: {n} with an advert band; {same} ({pct(same, n)}) the same, "
            f"{near} ({pct(near, n)}) within one band"
        )

    print("\n## Rooms: habitable rooms minus bedrooms (strict single matches)\n")
    for label, only in (("all", None), ("hand-checked correct", correct)):
        offsets = rooms_offsets(records, only)
        total = sum(offsets.values())
        cells = ", ".join(f"{k:+d}: {v}" for k, v in sorted(offsets.items()))
        print(f"- {label}: {total} with rooms; {cells}; plus one in {pct(offsets[1], total)}")

    print_handcheck("Hand-check of strict matches", judgements)
    print_handcheck("Hand-check of narrowed matches", narrowed_judgements)
    print_handcheck("Hand-check, both together", [*judgements, *narrowed_judgements])
    return 0


def print_handcheck(title: str, judgements: Sequence[dict[str, str]]) -> None:
    if not judgements:
        return
    good, judged, (lo, hi) = correct_match_rate(judgements)
    verdicts = Counter(j["verdict"] for j in judgements)
    print(f"\n## {title}\n")
    print(table(["verdict", "listings"], [[v, str(verdicts[v])] for v in VERDICTS]))
    print(
        f"\nCorrect-match rate: {good}/{judged} = {pct(good, judged)} "
        f"(95% interval {100 * lo:.0f} to {100 * hi:.0f}%)\n"
    )
    reasons = Counter(j["reason"] for j in judgements if j["verdict"] in ("wrong", "unclear"))
    if reasons:
        print(
            table(
                ["wrong or unclear because", "listings"],
                [[k, str(v)] for k, v in reasons.most_common()],
            )
        )


if __name__ == "__main__":
    raise SystemExit(main())

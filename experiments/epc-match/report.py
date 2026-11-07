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
from typing import Any, TypeGuard

from matcher import parse_listing_address
from paths import HANDCHECK, NARROWED, RESULTS

JUDGEMENTS = HANDCHECK / "judgements.csv"
NARROWED_JUDGEMENTS = HANDCHECK / "judgements-narrowed.csv"

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


def coverage_rows(records: Sequence[Record]) -> list[list[str]]:
    """Per dataset: listings with a full postcode, flat number, house number, area, band."""
    rows = []
    for dataset in sorted({r["listing"]["dataset"] for r in records}):
        listings = [r["listing"] for r in records if r["listing"]["dataset"] == dataset]
        parsed = [[parse_listing_address(a) for a in li["addresses"]] for li in listings]
        flat = [any(p.flat for p in ps) for ps in parsed]
        numbered = [
            any(p.numbered for p in ps) and not f for ps, f in zip(parsed, flat, strict=True)
        ]
        rows.append(
            [
                dataset,
                str(len(listings)),
                str(sum(bool(li["postcodes"]) for li in listings)),
                str(sum(flat)),
                str(sum(numbered)),
                str(sum(li["floor_area_sqm"] is not None for li in listings)),
                str(sum(li["advert_epc_band"] is not None for li in listings)),
            ]
        )
    return rows


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


def newest_band(home: Record) -> str | None:
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
    one_band = sum(len({newest_band(h) for h in r["homes"]} - {None}) == 1 for r in ambiguous)
    return AmbiguitySummary(
        listings=len(ambiguous),
        median_homes=statistics.median(sizes),
        quartiles=(q[0], q[2]),
        one_band=one_band,
        within_two=sum(s <= 2 for s in sizes),
    )


# --- Single matches: certificates and floor area -------------------------


def plausible_area(area: float | None) -> TypeGuard[float]:
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
    """Homes with two certificates lodged within 31 days of each other in different bands:
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
        newest_number = r["homes"][0]["certificates"][0]["certificate_number"]
        newest = next((d for d in r["details"] if d["certificate_number"] == newest_number), None)
        if newest is None:
            continue  # the newest certificate couldn't be fetched
        if not plausible_area(newest["total_floor_area"]):
            implausible += 1
        lodged = newest.get("registration_date")
        if lodged:
            year = int(lodged[:4]) + (int(lodged[5:7]) - 1) / 12
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

    A pair is dropped when either area is outside the plausible range, such
    as a square-foot figure stored as square metres.
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
        epc = newest_band(r["homes"][0])
        if not epc:
            continue
        n += 1
        same += epc == advert
        near += abs(order.index(epc) - order.index(advert)) <= 1
    return n, same, near


def candidate_bands(records: Iterable[Record]) -> tuple[int, int, int, int, int]:
    """How a building-level band compares with the advert's, for ambiguous listings.

    Returns (listings with an advert band, those whose candidates share one
    band, of which agree, listings whose most common candidate band agrees,
    and those within one band of it).
    """
    order = "ABCDEFG"
    n = one_band = one_band_agree = modal_same = modal_near = 0
    for r in records:
        advert = r["listing"].get("advert_epc_band")
        if r["outcome"] != "ambiguous" or not advert:
            continue
        bands = Counter(b for h in r["homes"] if (b := newest_band(h)))
        if not bands:
            continue
        n += 1
        modal = bands.most_common(1)[0][0]
        modal_same += modal == advert
        modal_near += abs(order.index(modal) - order.index(advert)) <= 1
        if len(bands) == 1:
            one_band += 1
            one_band_agree += modal == advert
    return n, one_band, one_band_agree, modal_same, modal_near


def candidate_area_pairs(narrowed: Iterable[Record]) -> list[tuple[str, float, float]]:
    """(source, listing m², median m² of the homes with the expected rooms), where rooms
    left several candidates."""
    pairs = []
    for r in narrowed:
        listing_area = r["listing"].get("floor_area_sqm")
        areas = [a for a in r.get("matched_floor_areas") or [] if plausible_area(a)]
        if r["outcome"] != "ambiguous" or not areas or not plausible_area(listing_area):
            continue
        source = "floorplan" if r["listing"]["floor_area_source"] == "estimated" else "advert"
        pairs.append((source, float(listing_area), float(statistics.median(areas))))
    return pairs


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


def load_judgements(path: Path, singles: Iterable[Record]) -> list[dict[str, str]]:
    """Verdicts for listings that are still single matches in `singles`.

    A rerun can change which listings are single matches; a verdict on a
    listing that no longer is one is dropped, and said so, rather than counted.
    """
    if not path.exists():
        return []
    with path.open() as f:
        rows = list(csv.DictReader(f))
    for row in rows:
        if row["verdict"] not in VERDICTS:
            raise ValueError(f"unknown verdict {row['verdict']!r}")
    current = {
        (r["listing"]["dataset"], r["listing"]["unique_id"])
        for r in singles
        if r["outcome"] == "one"
    }
    kept = [row for row in rows if (row["dataset"], row["unique_id"]) in current]
    if len(kept) < len(rows):
        print(
            f"(ignoring {len(rows) - len(kept)} verdicts in {path.name} "
            "on listings no longer single)"
        )
    return kept


def counted_correct(judgements: Iterable[dict[str, str]]) -> set[tuple[str, str]]:
    """(dataset, unique_id) of the listings the correct-match rate counts: correct or likely."""
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


# When each dataset was collected, for the age of the certificates matched:
# the October snapshot on 21 October 2026, the February export over 13-28 February.
SNAPSHOT_YEAR = {"october": 2026.8, "february": 2026.1}


def main() -> int:
    paths = sorted(RESULTS.glob("*.jsonl"))
    if not paths:
        print("No results yet: run run.py first.", file=sys.stderr)
        return 2
    records = load_records(paths)
    narrowed = load_records(sorted(NARROWED.glob("*.jsonl")))
    judgements = load_judgements(JUDGEMENTS, records)
    narrowed_judgements = load_judgements(NARROWED_JUDGEMENTS, narrowed)
    counted = counted_correct([*judgements, *narrowed_judgements])

    print_coverage(records)
    print_outcomes(records)
    print_no_match(records)
    print_ambiguity(records)
    print_certificates(records)
    print_narrowing(narrowed)
    print_floor_area(records, narrowed, counted)
    print_bands(records, narrowed)
    print_rooms(records, counted)
    print_handcheck("Hand-check of strict matches", judgements)
    print_handcheck("Hand-check of narrowed matches", narrowed_judgements)
    print_handcheck("Hand-check, both together", [*judgements, *narrowed_judgements])
    return 0


def print_coverage(records: Sequence[Record]) -> None:
    header = [
        "",
        "listings",
        "full postcode",
        "flat number",
        "house or building number",
        "floor area",
        "advert band",
    ]
    print("## What the listings give\n")
    print(table(header, coverage_rows(records)))


def print_outcomes(records: Sequence[Record]) -> None:
    header = ["", "listings", "one home", "ambiguous", "none"]
    by_postcode = [
        {**r, "postcode": "full postcode" if r["listing"]["postcodes"] else "district only"}
        for r in records
    ]
    by_dataset = [{**r, "dataset": r["listing"]["dataset"]} for r in records]
    print("\n## Outcomes by dataset\n")
    print(table(header, outcome_table(by_dataset, "dataset")))
    print("\n## Outcomes by what the listing's postcode gives\n")
    print(table(header, outcome_table(by_postcode, "postcode")))
    print("\n## Outcomes by most specific evidence used\n")
    print(table(header, outcome_table([r for r in records if r["evidence"]], "evidence")))


def print_no_match(records: Sequence[Record]) -> None:
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


def print_ambiguity(records: Sequence[Record]) -> None:
    summary = ambiguity_summary(records)
    if not summary:
        return
    disagree = sum(r["variants_disagree"] for r in records)
    print("\n## Ambiguous matches\n")
    print(
        f"- {summary.listings} listings; median {summary.median_homes:g} candidate homes "
        f"(quartiles {summary.quartiles[0]:g} to {summary.quartiles[1]:g})\n"
        f"- {summary.within_two} ({pct(summary.within_two, summary.listings)}) "
        "have only two candidates\n"
        f"- {summary.one_band} ({pct(summary.one_band, summary.listings)}) have candidates "
        "that all share one current band\n"
        f"- {disagree} are ambiguous because two portals' addresses picked different homes"
    )


def print_certificates(records: Sequence[Record]) -> None:
    certs = certificate_summary(records, SNAPSHOT_YEAR)
    age = f"{certs.median_age_years:.1f} years" if certs.median_age_years is not None else "n/a"
    print("\n## Single matches: certificates\n")
    print(
        f"- {certs.matches} single matches, {certs.with_detail} with a full certificate fetched\n"
        f"- {certs.several_certificates} homes have more than one certificate; "
        f"{certs.areas_disagree} of those disagree on floor area by more than 10%\n"
        f"- {certs.implausible_area} newest certificates have an implausible floor area\n"
        f"- {certs.conflicting} homes have two certificates within 31 days in different bands\n"
        f"- median age of the newest certificate: {age}"
    )


def print_narrowing(narrowed: Sequence[Record]) -> None:
    if not narrowed:
        return
    tried = [r for r in narrowed if r["rooms_known"]]
    counts = Counter(r["outcome"] for r in tried)
    sizes = [r["homes_before"] for r in narrowed]
    print("\n## Narrowing ambiguous matches by rooms\n")
    print(
        f"- {len(narrowed)} ambiguous listings with {min(sizes)} to {max(sizes)} candidate "
        f"homes; {len(narrowed) - len(tried)} skipped because no candidate's certificate "
        f"counts rooms\n"
        f"- of the {len(tried)} tried, {counts['one']} ({pct(counts['one'], len(tried))}) "
        f"left one home, {counts['ambiguous']} still ambiguous, {counts['none']} left none, "
        f"{counts['unconfirmed']} left one home whose rooms aren't counted"
    )


def print_floor_area(
    records: Sequence[Record], narrowed: Sequence[Record], counted: set[tuple[str, str]]
) -> None:
    groups: list[tuple[str, Sequence[Record], set[tuple[str, str]] | None]] = [
        ("strict single matches", records, None),
        ("narrowed single matches", narrowed, None),
        ("hand-checked correct or likely, strict and narrowed", [*records, *narrowed], counted),
    ]
    header = [
        "listing figure",
        "pairs",
        "median abs diff",
        "median EPC minus listing",
        "≤5%",
        "≤10%",
        "≤20%",
    ]
    dropped = sum(
        r["outcome"] == "one"
        and r["listing"]["floor_area_sqm"] is not None
        and not plausible_area(r["listing"]["floor_area_sqm"])
        for r in [*records, *narrowed]
    )
    print("\n## Floor area: EPC against the listing's figure\n")
    print(f"{dropped} single matches dropped for an implausible listing floor area.")
    for label, group, only in groups:
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

    estimate = area_agreement(candidate_area_pairs(narrowed), "all")
    if estimate:
        print(
            f"\nmedian of the homes left by rooms, where several remain: {estimate.n} listings; "
            f"median abs diff {estimate.median_abs_pct:.1f}%, "
            f"{pct(estimate.within_5, estimate.n)} within 5%, "
            f"{pct(estimate.within_10, estimate.n)} within 10%, "
            f"{pct(estimate.within_20, estimate.n)} within 20%"
        )


def print_bands(records: Sequence[Record], narrowed: Sequence[Record]) -> None:
    print("\n## Band against the advert's stated band\n")
    for label, group in (("strict single matches", records), ("narrowed single matches", narrowed)):
        n, same, near = band_agreement(group)
        print(
            f"- {label}: {n} with an advert band; {same} ({pct(same, n)}) the same, "
            f"{near} ({pct(near, n)}) within one band"
        )
    n, one_band, one_band_agree, modal_same, modal_near = candidate_bands(records)
    print(
        f"- ambiguous listings: {n} with an advert band; the candidates' most common band "
        f"is the same for {modal_same} ({pct(modal_same, n)}), within one band for "
        f"{modal_near} ({pct(modal_near, n)}); {one_band} have candidates all in one band, "
        f"{one_band_agree} of them the advert's"
    )


def print_rooms(records: Sequence[Record], counted: set[tuple[str, str]]) -> None:
    print("\n## Rooms: habitable rooms minus bedrooms (strict single matches)\n")
    for label, only in (("all", None), ("hand-checked correct or likely", counted)):
        offsets = rooms_offsets(records, only)
        total = sum(offsets.values())
        cells = ", ".join(f"{k:+d}: {v}" for k, v in sorted(offsets.items()))
        print(f"- {label}: {total} with rooms; {cells}; plus one in {pct(offsets[1], total)}")


def print_handcheck(title: str, judgements: Sequence[dict[str, str]]) -> None:
    if not judgements:
        return
    good, judged, (lo, hi) = correct_match_rate(judgements)
    verdicts = Counter(j["verdict"] for j in judgements)
    print(f"\n## {title}\n")
    print(table(["verdict", "listings"], [[v, str(verdicts[v])] for v in VERDICTS]))
    print(
        f"\nCorrect-match rate (correct or likely, of those decided): {good}/{judged} = "
        f"{pct(good, judged)} (95% interval {100 * lo:.0f} to {100 * hi:.0f}%). "
        f"Confirmed by a specific fact: {verdicts['correct']} of {len(judgements)}.\n"
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

"""How steady is a home's EPC floor area from one certificate to the next?

Usage, from this directory, after `run.py`:

    uv run python retest.py --homes 300

Floor area can be checked without any listing: a home assessed twice should
get about the same area both times. This draws a seeded sample of homes with
two or more certificates from the searches `run.py` cached (the streets and
postcodes around the listings), fetches the newest certificate and the one
before it, and compares their floor areas and bands, for flats and houses
apart. It prints aggregate numbers only.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import statistics
import sys
from collections.abc import Sequence
from dataclasses import dataclass

from epc_api import (
    CertificateDetail,
    EpcApi,
    MissingTokenError,
    certificate_from_search,
    floor_area_from_parts,
    load_token,
)
from matcher import Certificate, Home, group_homes
from paths import CACHE
from report import pct, plausible_area
from run import in_parallel


def cached_certificates() -> list[Certificate]:
    """Every certificate in the cached search answers, once each."""
    found: dict[str, Certificate] = {}
    for path in sorted((CACHE / "search").glob("*.json")):
        body = json.loads(path.read_text()).get("body") or {}
        for row in body.get("data") or []:
            cert = certificate_from_search(row)
            found[cert.certificate_number] = cert
    return list(found.values())


def floor_sum_check() -> tuple[int, int, int]:
    """(certificates stating a total and per-floor areas, sums within 1% of it, within 5%).

    Backs `epc_api`'s fallback: summing the floors of a certificate that
    leaves the total out.
    """
    n = within_1 = within_5 = 0
    for path in sorted((CACHE / "certificate").glob("*.json")):
        data = (json.loads(path.read_text()).get("body") or {}).get("data")
        if not isinstance(data, dict):
            continue
        stated = data.get("total_floor_area")
        summed = floor_area_from_parts(data.get("sap_building_parts") or [])
        if not isinstance(stated, int | float) or not stated or summed is None:
            continue
        n += 1
        off = abs(summed - stated) / stated
        within_1 += off <= 0.01
        within_5 += off <= 0.05
    return n, within_1, within_5


_UNIT = re.compile(r"\b(?:flat|floor|apartment|unit|maisonette)\b")


def shared_uprns(certificates: Sequence[Certificate]) -> tuple[int, int]:
    """(UPRNs on two or more different addresses, those where two or more name a unit).

    The second count is why homes are grouped by address, not UPRN: a building's
    UPRN on each of its flats would merge them.
    """
    addresses: dict[str, set[str]] = {}
    for cert in certificates:
        if cert.uprn:
            addresses.setdefault(cert.uprn, set()).add(cert.text)
    several = [texts for texts in addresses.values() if len(texts) > 1]
    units = sum(sum(bool(_UNIT.search(t)) for t in texts) >= 2 for texts in several)
    return len(several), units


def homes_with_repeats(certificates: Sequence[Certificate]) -> list[Home]:
    """Homes with at least two certificates, grouped within each postcode."""
    by_postcode: dict[str, list[Certificate]] = {}
    for cert in certificates:
        by_postcode.setdefault(cert.postcode, []).append(cert)
    return [
        home
        for postcode in sorted(by_postcode)
        for home in group_homes(by_postcode[postcode])
        if len(home.certificates) >= 2
    ]


@dataclass(frozen=True)
class Repeat:
    newest: CertificateDetail
    previous: CertificateDetail

    @property
    def area_change_pct(self) -> float | None:
        """Previous area against the newest, as % of the newest; None if either is implausible."""
        new, old = self.newest.total_floor_area, self.previous.total_floor_area
        if not (plausible_area(new) and plausible_area(old)) or new is None or old is None:
            return None
        return 100 * (old - new) / new


def repeat_summary(repeats: Sequence[Repeat]) -> dict[str, str]:
    changes = [c for r in repeats if (c := r.area_change_pct) is not None]
    absolute = [abs(c) for c in changes]
    bands = [r for r in repeats if r.newest.band and r.previous.band]
    n = len(changes)
    return {
        "homes compared": str(len(repeats)),
        "with two plausible areas": str(n),
        "median abs change": f"{statistics.median(absolute):.1f}%" if absolute else "n/a",
        "within 5%": pct(sum(a <= 5 for a in absolute), n),
        "within 10%": pct(sum(a <= 10 for a in absolute), n),
        "within 20%": pct(sum(a <= 20 for a in absolute), n),
        "more than 20% apart": str(sum(a > 20 for a in absolute)),
        "no usable area on either": str(len(repeats) - n),
        "areas summed from floors": str(
            sum(r.newest.floor_area_summed + r.previous.floor_area_summed for r in repeats)
        ),
        "same band": pct(sum(r.newest.band == r.previous.band for r in bands), len(bands)),
    }


def dwelling_kind(detail: CertificateDetail) -> str | None:
    """Flats and maisonettes are "flat"; houses and bungalows "house"."""
    text = (detail.dwelling_type or "").lower()
    if "flat" in text or "maisonette" in text:
        return "flat"
    if "house" in text or "bungalow" in text:
        return "house"
    return None


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--homes", type=int, default=300)
    parser.add_argument("--seed", type=int, default=11)
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args(argv)
    try:
        token = load_token()
    except MissingTokenError as error:
        print(error, file=sys.stderr)
        return 2

    certificates = cached_certificates()
    several, units = shared_uprns(certificates)
    print(
        f"{len(certificates)} certificates in the cached searches; {several} UPRNs cover two "
        f"or more addresses, {units} of them two or more that name a unit"
    )
    n, within_1, within_5 = floor_sum_check()
    print(
        f"{n} fetched certificates state both a total and per-floor areas; the floors sum to "
        f"within 1% of the total for {pct(within_1, n)}, within 5% for {pct(within_5, n)}"
    )
    homes = homes_with_repeats(certificates)
    sample = random.Random(args.seed).sample(homes, min(args.homes, len(homes)))
    pairs = [(h.certificates[0], h.certificates[1]) for h in sample]
    with EpcApi(token, CACHE) as api:
        numbers = [c.certificate_number for pair in pairs for c in pair]
        in_parallel(api.certificate, numbers, args.workers, "certificates")
        repeats = [
            Repeat(newest, previous)
            for new, old in pairs
            if (newest := api.certificate(new.certificate_number)) is not None
            and (previous := api.certificate(old.certificate_number)) is not None
        ]
    print(f"{len(homes)} homes with two or more certificates in the cached searches\n")
    groups = {
        "all": repeats,
        "flats": [r for r in repeats if dwelling_kind(r.newest) == "flat"],
        "houses": [r for r in repeats if dwelling_kind(r.newest) == "house"],
    }
    summaries = {name: repeat_summary(group) for name, group in groups.items()}
    print("| measure | " + " | ".join(summaries) + " |")
    print("|---|" + "---|" * len(summaries))
    for key in summaries["all"]:
        print(f"| {key} | " + " | ".join(s[key] for s in summaries.values()) + " |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

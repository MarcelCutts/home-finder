"""How steady is a home's EPC floor area from one certificate to the next?

Usage, from this directory, after `run.py`:

    uv run python retest.py --homes 300

Floor area can be checked without any listing: a home assessed twice should
get about the same area both times. This draws a seeded sample of homes with
two or more certificates from the searches `run.py` cached (the streets and
postcodes around the listings), fetches every certificate of each, and
compares the newest with the one before. It prints aggregate numbers only.
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
from collections.abc import Sequence
from dataclasses import dataclass

from epc_api import (
    CertificateDetail,
    EpcApi,
    MissingTokenError,
    certificate_from_search,
    load_token,
)
from matcher import Certificate, Home, group_homes
from report import pct, plausible_area
from run import DATA, in_parallel


def cached_certificates() -> list[Certificate]:
    """Every certificate in the cached search answers, once each."""
    found: dict[str, Certificate] = {}
    for path in sorted((DATA / "cache" / "search").glob("*.json")):
        body = json.loads(path.read_text()).get("body") or {}
        for row in body.get("data") or []:
            cert = certificate_from_search(row)
            found[cert.certificate_number] = cert
    return list(found.values())


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
        "implausible area on either": str(len(repeats) - n),
        "same band": pct(sum(r.newest.band == r.previous.band for r in bands), len(bands)),
    }


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

    homes = homes_with_repeats(cached_certificates())
    sample = random.Random(args.seed).sample(homes, min(args.homes, len(homes)))
    pairs = [(h.certificates[0], h.certificates[1]) for h in sample]
    with EpcApi(token, DATA / "cache") as api:
        numbers = [c.certificate_number for pair in pairs for c in pair]
        in_parallel(api.certificate, numbers, args.workers, "certificates")
        repeats = [
            Repeat(newest, previous)
            for new, old in pairs
            if (newest := api.certificate(new.certificate_number)) is not None
            and (previous := api.certificate(old.certificate_number)) is not None
        ]
    print(f"{len(homes)} homes with two or more certificates in the cached searches\n")
    print("| measure | value |\n|---|---|")
    for key, value in repeat_summary(repeats).items():
        print(f"| {key} | {value} |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

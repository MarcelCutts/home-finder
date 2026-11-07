"""Match every listing in the two database copies to EPC certificates.

Usage, from this directory, with EPC_API_TOKEN set:

    uv run python run.py

Writes one JSON line per listing to data/results/<dataset>.jsonl. Those
lines hold certificate and listing addresses, so they stay under data/,
which git ignores. `report.py` turns them into the aggregate numbers.

Certificates come from the register's domestic search. A listing with a full
postcode is matched against every certificate at that postcode. A listing
with only a postcode district is matched against the certificates on each of
its streets (or buildings) in the councils that district covers, kept to
that district.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

import httpx
from epc_api import EpcApi, MissingTokenError, token_from_environment
from listings import Listing, load_listings
from matcher import (
    Certificate,
    MatchResult,
    Outcome,
    match_listing,
    outcode_of,
    parse_listing_address,
)

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
DATASETS = {
    "october": DATA / "inputs" / "october.db",
    "february": DATA / "inputs" / "february.db",
}
POSTCODES_IO = "https://api.postcodes.io"


def councils_for_outcode(outcode: str, cache_dir: Path) -> list[str]:
    """Local authorities a postcode district covers, from postcodes.io (cached)."""
    cache_file = cache_dir / "outcodes" / f"{outcode}.json"
    if not cache_file.exists():
        response = httpx.get(f"{POSTCODES_IO}/outcodes/{outcode}", timeout=30)
        result = response.json().get("result") if response.status_code == 200 else None
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(result))
    result = json.loads(cache_file.read_text())
    return list(result.get("admin_district") or []) if result else []


def candidate_certificates(
    listing: Listing, api: EpcApi, cache_dir: Path
) -> tuple[str, list[Certificate]]:
    """The certificates to match a listing against, and how they were found."""
    if listing.postcodes:
        certs = [c for pc in listing.postcodes for c in api.search_postcode(pc)]
        return "postcode", _unique(certs)
    if not listing.outcode:
        return "none", []

    councils = councils_for_outcode(listing.outcode, cache_dir)
    names = dict.fromkeys(
        name
        for address in listing.addresses
        for parsed in [parse_listing_address(address)]
        for name in (*parsed.streets, *parsed.buildings)
    )
    if not names or not councils:
        return "none", []
    certs = [
        cert
        for name in names
        for cert in api.search({"address": name, "council[]": councils})
        if outcode_of(cert.postcode) == listing.outcode
    ]
    return "street", _unique(certs)


def _unique(certs: Sequence[Certificate]) -> list[Certificate]:
    return list({c.certificate_number: c for c in certs}.values())


def result_record(
    listing: Listing, retrieval: str, candidates: int, result: MatchResult, api: EpcApi
) -> dict[str, Any]:
    """One output line: the listing, its match and, for a single home, its certificates."""
    record: dict[str, Any] = {
        "listing": asdict(listing),
        "retrieval": retrieval,
        "candidate_certificates": candidates,
        "outcome": result.outcome.value,
        "evidence": result.evidence.name.lower() if result.evidence is not None else None,
        "reason": result.reason.value if result.reason else None,
        "variants_disagree": result.variants_disagree,
        "homes": [
            {
                "key": home.key,
                "certificates": [asdict(c) for c in home.certificates],
            }
            for home in result.homes
        ],
    }
    if result.outcome is Outcome.ONE:
        details = [api.certificate(c.certificate_number) for c in result.homes[0].certificates]
        record["details"] = [asdict(d) for d in details if d is not None]
    return record


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dataset", choices=sorted(DATASETS), action="append")
    parser.add_argument("--limit", type=int, help="match only the first N listings")
    args = parser.parse_args(argv)

    try:
        token = token_from_environment()
    except MissingTokenError as error:
        print(error, file=sys.stderr)
        return 2

    cache_dir = DATA / "cache"
    out_dir = DATA / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    with EpcApi(token, cache_dir) as api:
        for dataset in args.dataset or sorted(DATASETS):
            listings = load_listings(DATASETS[dataset], dataset)[: args.limit]
            out_file = out_dir / f"{dataset}.jsonl"
            with out_file.open("w") as out:
                for i, listing in enumerate(listings, 1):
                    retrieval, certs = candidate_certificates(listing, api, cache_dir)
                    result = match_listing(listing.addresses, certs)
                    record = result_record(listing, retrieval, len(certs), result, api)
                    out.write(json.dumps(record) + "\n")
                    if i % 50 == 0:
                        print(f"{dataset}: {i}/{len(listings)}, {api.requests_made} requests")
            print(f"{dataset}: wrote {len(listings)} results, {api.requests_made} requests so far")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

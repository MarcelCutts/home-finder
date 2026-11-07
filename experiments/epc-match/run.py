"""Match every listing in the two database copies to EPC certificates.

Usage, from this directory, with the token in EPC_API_TOKEN or
~/.config/home-finder/epc_token:

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
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, TypeVar

import httpx
from epc_api import EpcApi, MissingTokenError, Params, load_token
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
T = TypeVar("T")


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


@dataclass(frozen=True)
class Candidates:
    """The certificates a listing is matched against, and how they were found."""

    retrieval: str
    """"postcode", "street" (district-only listings) or "none"."""
    certificates: list[Certificate]
    outside_district: int = 0
    """Certificates on the listing's streets that lie outside its district."""


def search_queries(listing: Listing, cache_dir: Path) -> tuple[str, list[Params]]:
    """How to find a listing's candidates ("postcode", "street" or "none"), and the searches."""
    if listing.postcodes:
        return "postcode", [{"postcode": pc} for pc in listing.postcodes]
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
    return "street", [{"address": name, "council[]": councils} for name in names]


def candidate_certificates(listing: Listing, api: EpcApi, cache_dir: Path) -> Candidates:
    retrieval, queries = search_queries(listing, cache_dir)
    found = _unique([c for query in queries for c in api.search(query)])
    if retrieval != "street":
        return Candidates(retrieval, found)
    inside = [c for c in found if outcode_of(c.postcode) == listing.outcode]
    return Candidates(retrieval, inside, outside_district=len(found) - len(inside))


def in_parallel(work: Callable[[T], object], items: Iterable[T], workers: int, label: str) -> None:
    """Run `work` over `items` on a few threads, to fill the cache ahead of matching."""
    todo = list(items)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for i, _ in enumerate(pool.map(work, todo), 1):
            if i % 100 == 0 or i == len(todo):
                print(f"  {label}: {i}/{len(todo)}", flush=True)


def _unique(certs: Sequence[Certificate]) -> list[Certificate]:
    return list({c.certificate_number: c for c in certs}.values())


def result_record(
    listing: Listing, candidates: Candidates, result: MatchResult, api: EpcApi
) -> dict[str, Any]:
    """One output line: the listing, its match and, for a single home, its certificates."""
    record: dict[str, Any] = {
        "listing": asdict(listing),
        "retrieval": candidates.retrieval,
        "candidate_certificates": len(candidates.certificates),
        "outside_district": candidates.outside_district,
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
    parser.add_argument("--workers", type=int, default=4, help="concurrent API requests")
    args = parser.parse_args(argv)

    try:
        token = load_token()
    except MissingTokenError as error:
        print(error, file=sys.stderr)
        return 2

    cache_dir = DATA / "cache"
    out_dir = DATA / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    with EpcApi(token, cache_dir) as api:
        for dataset in args.dataset or sorted(DATASETS):
            listings = load_listings(DATASETS[dataset], dataset)[: args.limit]
            queries = {
                json.dumps(query, sort_keys=True): query
                for listing in listings
                for query in search_queries(listing, cache_dir)[1]
            }
            in_parallel(api.search, queries.values(), args.workers, f"{dataset} searches")

            matches = []
            for listing in listings:
                candidates = candidate_certificates(listing, api, cache_dir)
                matches.append(
                    (listing, candidates, match_listing(listing.addresses, candidates.certificates))
                )
            singles = [
                cert.certificate_number
                for _, _, result in matches
                if result.outcome is Outcome.ONE
                for cert in result.homes[0].certificates
            ]
            in_parallel(api.certificate, singles, args.workers, f"{dataset} certificates")

            out_file = out_dir / f"{dataset}.jsonl"
            with out_file.open("w") as out:
                for listing, candidates, result in matches:
                    out.write(json.dumps(result_record(listing, candidates, result, api)) + "\n")
            print(f"{dataset}: wrote {len(listings)} results, {api.requests_made} requests so far")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

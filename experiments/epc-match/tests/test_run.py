"""Tests for finding a listing's candidate certificates and recording its match."""

import json
from pathlib import Path

import httpx
from epc_api import EpcApi
from listings import Listing
from matcher import match_listing
from run import candidate_certificates, result_record


def listing(postcodes: tuple[str, ...], outcode: str, *addresses: str) -> Listing:
    return Listing(
        dataset="fixture",
        unique_id="zoopla:1",
        addresses=addresses,
        postcodes=postcodes,
        outcode=outcode,
        latitude=None,
        longitude=None,
        bedrooms=2,
        floor_area_sqm=None,
        floor_area_source=None,
        advert_epc_band=None,
        property_type=None,
    )


def row(number: str, line1: str, postcode: str, uprn: int) -> dict[str, object]:
    return {
        "certificateNumber": number,
        "addressLine1": line1,
        "addressLine2": "Quillon Road",
        "postcode": postcode,
        "currentEnergyEfficiencyBand": "C",
        "registrationDate": "2019-03-01",
        "uprn": uprn,
    }


ROWS = [
    row("c12", "12", "Q9 9ZZ", 12),
    row("c14", "14", "Q9 9ZZ", 14),
    row("c40", "40", "Q8 1AA", 40),  # same street name, another district
]


def handler(seen: list[httpx.Request]):
    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/api/certificate":
            return httpx.Response(200, json={"data": {"total_floor_area": 61}})
        params = request.url.params
        if "postcode" in params:
            rows = [r for r in ROWS if r["postcode"] == params["postcode"]]
        else:
            rows = ROWS
        return httpx.Response(200, json={"data": rows})

    return handle


def api(seen: list[httpx.Request], cache: Path) -> EpcApi:
    return EpcApi("t", cache, transport=httpx.MockTransport(handler(seen)), min_interval_s=0)


def test_listing_with_a_full_postcode_is_matched_at_that_postcode(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []
    home = listing(("Q9 9ZZ",), "Q9", "12 Quillon Road, London")
    with api(seen, tmp_path) as client:
        retrieval, certs = candidate_certificates(home, client, tmp_path)
        result = match_listing(home.addresses, certs)
        record = result_record(home, retrieval, len(certs), result, client)

    assert retrieval == "postcode"
    assert {c.certificate_number for c in certs} == {"c12", "c14"}
    assert record["outcome"] == "one"
    assert record["evidence"] == "number"
    assert record["details"][0]["total_floor_area"] == 61.0
    json.dumps(record)  # raises unless the record is one JSON line


def test_district_only_listing_searches_its_street_in_its_councils(tmp_path: Path) -> None:
    (tmp_path / "outcodes").mkdir()
    (tmp_path / "outcodes" / "Q9.json").write_text(json.dumps({"admin_district": ["Brack"]}))
    seen: list[httpx.Request] = []
    home = listing((), "Q9", "2 Bed Flat, Quillon Road, Q9")
    with api(seen, tmp_path) as client:
        retrieval, certs = candidate_certificates(home, client, tmp_path)

    assert retrieval == "street"
    assert {c.certificate_number for c in certs} == {"c12", "c14"}  # Q8 dropped
    params = seen[0].url.params
    assert params["address"] == "quillon road"
    assert params.get_list("council[]") == ["Brack"]


def test_listing_without_a_postcode_or_street_has_no_candidates(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []
    with api(seen, tmp_path) as client:
        assert candidate_certificates(listing((), "", "London"), client, tmp_path) == ("none", [])
    assert seen == []

"""Tests for the cached EPC API client, against a mock transport."""

import json
from pathlib import Path

import httpx
import pytest
from epc_api import EpcApi, MissingTokenError, load_token

TOKEN = "test-token-not-real"


def search_row(number: str, line1: str, uprn: int | None = 1001) -> dict[str, object]:
    return {
        "certificateNumber": number,
        "addressLine1": line1,
        "addressLine2": "Quillon Road",
        "addressLine3": None,
        "postcode": "Q9+9ZZ",
        "currentEnergyEfficiencyBand": "C",
        "registrationDate": "2021-08-11",
        "uprn": uprn,
    }


class Recorder:
    """A mock transport that answers from a handler and records each request."""

    def __init__(self, handler):
        self.requests: list[httpx.Request] = []
        self._handler = handler

    def transport(self) -> httpx.MockTransport:
        def handle(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return self._handler(request)

        return httpx.MockTransport(handle)


def api(recorder: Recorder, cache_dir: Path) -> EpcApi:
    return EpcApi(TOKEN, cache_dir, transport=recorder.transport(), min_interval_s=0)


def test_search_reads_every_page_into_certificates(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        page = request.url.params["current_page"]
        if page == "1":
            return httpx.Response(
                200,
                json={"data": [search_row("c1", "Flat A")], "pagination": {"nextPage": 2}},
            )
        return httpx.Response(
            200,
            json={"data": [search_row("c2", "Flat B", uprn=None)], "pagination": {}},
        )

    recorder = Recorder(handler)
    with api(recorder, tmp_path) as client:
        certs = client.search({"postcode": "Q9 9ZZ"})

    assert [c.certificate_number for c in certs] == ["c1", "c2"]
    assert certs[0].address_lines == ("Flat A", "Quillon Road")
    assert certs[0].postcode == "Q9 9ZZ"
    assert certs[0].uprn == "1001"
    assert certs[1].uprn is None
    assert recorder.requests[0].url.params["postcode"] == "Q9 9ZZ"


def test_answers_are_cached_so_a_rerun_makes_no_requests(tmp_path: Path) -> None:
    recorder = Recorder(lambda _: httpx.Response(404, json={"errors": "none"}))
    with api(recorder, tmp_path) as client:
        assert client.search({"postcode": "Q9 9ZZ"}) == []
        assert client.search({"postcode": "Q9 9ZZ"}) == []
    assert len(recorder.requests) == 1


def test_token_goes_in_the_header_and_never_into_the_cache(tmp_path: Path) -> None:
    recorder = Recorder(lambda _: httpx.Response(200, json={"data": [search_row("c1", "Flat A")]}))
    with api(recorder, tmp_path) as client:
        client.search({"postcode": "Q9 9ZZ"})

    assert recorder.requests[0].headers["Authorization"] == f"Bearer {TOKEN}"
    cached = "".join(p.read_text() for p in tmp_path.rglob("*.json"))
    assert TOKEN not in cached


def test_certificate_detail_reads_floor_area_and_dwelling(tmp_path: Path) -> None:
    body = {
        "data": {
            "total_floor_area": 55,
            "current_energy_efficiency_band": "D",
            "energy_rating_current": 66,
            "dwelling_type": {"value": "Mid-floor flat", "language": "1"},
            "habitable_room_count": 3,
            "sap_flat_details": {"level": 2, "top_storey": "N"},
            "registration_date": "2016-01-12",
            "uprn_source": "Energy Assessor",
            "sap_building_parts": [{"construction_age_band": "D"}],
        }
    }
    recorder = Recorder(lambda _: httpx.Response(200, json=body))
    with api(recorder, tmp_path) as client:
        detail = client.certificate("0000-0000-0000-0000-0001")

    assert detail is not None
    assert detail.total_floor_area == 55.0
    assert detail.band == "D"
    assert detail.energy_rating == 66
    assert detail.dwelling_type == "Mid-floor flat"
    assert detail.habitable_rooms == 3
    assert detail.floor_level == 2
    assert detail.construction_age_band == "D"


def test_missing_certificate_is_none(tmp_path: Path) -> None:
    recorder = Recorder(lambda _: httpx.Response(404, json={}))
    with api(recorder, tmp_path) as client:
        assert client.certificate("0000-0000-0000-0000-0002") is None
    assert json.loads(next(tmp_path.rglob("*.json")).read_text())["body"] is None


def test_token_comes_from_the_file_when_the_variable_is_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("EPC_API_TOKEN", raising=False)
    token_file = tmp_path / "epc_token"
    token_file.write_text("from-file")
    assert load_token(token_file) == "from-file"

    monkeypatch.setenv("EPC_API_TOKEN", "from-variable")
    assert load_token(token_file) == "from-variable"


def test_no_token_anywhere_is_an_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EPC_API_TOKEN", raising=False)
    with pytest.raises(MissingTokenError):
        load_token(tmp_path / "absent")

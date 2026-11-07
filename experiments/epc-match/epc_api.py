"""Read EPC certificates from the register's API, caching every answer on disk.

The API (https://get-energy-performance-data.communities.gov.uk) needs a
GOV.UK One Login bearer token, read from the EPC_API_TOKEN environment
variable or, when that is unset, from ~/.config/home-finder/epc_token. The
token is sent only in the Authorization header; it is never logged or
written to the cache.

Responses hold certificate addresses, whose licence limits them to
energy-performance purposes, so the cache must stay under the gitignored
`data/` directory.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from matcher import Certificate

API_BASE = "https://api.get-energy-performance-data.communities.gov.uk"
TOKEN_VARIABLE = "EPC_API_TOKEN"
TOKEN_FILE = Path("~/.config/home-finder/epc_token")
_PAGE_SIZE = 5000

Params = dict[str, str | list[str]]
"""Query parameters; a list value repeats the key, as `council[]` needs."""


class MissingTokenError(RuntimeError):
    pass


def load_token(token_file: Path = TOKEN_FILE) -> str:
    """The bearer token from the environment, else from `token_file`."""
    token = os.environ.get(TOKEN_VARIABLE, "").strip()
    path = token_file.expanduser()
    if not token and path.is_file():
        token = path.read_text().strip()
    if not token:
        raise MissingTokenError(f"Set {TOKEN_VARIABLE} or write the token to {token_file}.")
    return token


@dataclass(frozen=True)
class CertificateDetail:
    """The fields of a full certificate this experiment reads."""

    certificate_number: str
    total_floor_area: float | None
    """Internal floor area in m². Some 2008-09 certificates (SAP schemas 12 and
    13) leave the total out; for those it is the sum of their per-floor areas,
    and `floor_area_summed` is set."""
    band: str | None
    energy_rating: int | None
    """Current SAP score, 1-100+, behind the band."""
    dwelling_type: str | None
    habitable_rooms: int | None
    flat_position: int | None
    """Where a flat sits in its block: 0 basement, 1 ground, 2 middle, 3 top floor.

    Read from the certificates' own dwelling types ("Basement flat" is 0,
    "Top-floor flat" 3); the register's code list doesn't cover it.
    """
    registration_date: str | None
    uprn_source: str | None
    construction_age_band: str | None
    floor_area_summed: bool = False


def certificate_from_search(row: dict[str, Any]) -> Certificate:
    lines = tuple(
        str(row[key])
        for key in ("addressLine1", "addressLine2", "addressLine3", "addressLine4")
        if row.get(key)
    )
    uprn = row.get("uprn")
    return Certificate(
        certificate_number=str(row["certificateNumber"]),
        address_lines=lines,
        postcode=str(row.get("postcode") or "").replace("+", " ").upper(),
        uprn=str(int(uprn)) if uprn not in (None, "") else None,
        band=row.get("currentEnergyEfficiencyBand"),
        registration_date=row.get("registrationDate"),
    )


def detail_from_certificate(number: str, data: dict[str, Any]) -> CertificateDetail:
    dwelling = data.get("dwelling_type")
    if isinstance(dwelling, dict):
        dwelling = dwelling.get("value")
    flat = data.get("sap_flat_details")
    parts = data.get("sap_building_parts") or []
    total = _number(data.get("total_floor_area"))
    summed = total is None and (total := _floor_area_from_parts(parts)) is not None
    return CertificateDetail(
        certificate_number=number,
        total_floor_area=total,
        floor_area_summed=summed,
        band=data.get("current_energy_efficiency_band"),
        energy_rating=_integer(data.get("energy_rating_current")),
        dwelling_type=dwelling if isinstance(dwelling, str) else None,
        habitable_rooms=_integer(data.get("habitable_room_count")),
        flat_position=_integer(flat.get("level")) if isinstance(flat, dict) else None,
        registration_date=data.get("registration_date"),
        uprn_source=data.get("uprn_source"),
        construction_age_band=(parts[0] or {}).get("construction_age_band") if parts else None,
    )


def _floor_area_from_parts(parts: list[dict[str, Any]]) -> float | None:
    """Sum each building part's floor areas and room in the roof.

    Of the 2,326 certificates fetched here that state both, the sum is within
    1% of the stated total for 93% and within 5% for 99%.
    """
    areas = []
    for part in parts:
        for floor in part.get("sap_floor_dimensions") or []:
            if isinstance(floor, dict):
                areas.append(_number(floor.get("total_floor_area")))
        roof = part.get("sap_room_in_roof")
        if isinstance(roof, dict):
            areas.append(_number(roof.get("floor_area")))
    known = [a for a in areas if a is not None]
    return sum(known) if known else None


def _number(value: Any) -> float | None:
    if isinstance(value, dict):
        value = value.get("value")
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _integer(value: Any) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


class EpcApi:
    """A throttled, cached client for the domestic search and certificate endpoints.

    Safe to share between threads: request starts are spaced at least
    `min_interval_s` apart across all of them.
    """

    def __init__(
        self,
        token: str,
        cache_dir: Path,
        *,
        transport: httpx.BaseTransport | None = None,
        min_interval_s: float = 0.2,
    ) -> None:
        self._cache_dir = cache_dir
        self._min_interval_s = min_interval_s
        self._last_request = 0.0
        self._throttle = threading.Lock()
        self.requests_made = 0
        self._client = httpx.Client(
            base_url=API_BASE,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=60,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> EpcApi:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def search(self, params: Params) -> list[Certificate]:
        """Every domestic certificate matching `params`, across all pages."""
        rows: list[dict[str, Any]] = []
        page = 1
        while True:
            body = self._get_cached(
                "search",
                "/api/domestic/search",
                {**params, "page_size": str(_PAGE_SIZE), "current_page": str(page)},
            )
            if body is None:
                break
            rows.extend(body.get("data") or [])
            pagination = body.get("pagination") or {}
            if not pagination.get("nextPage"):
                break
            page += 1
        return [certificate_from_search(row) for row in rows]

    def certificate(self, number: str) -> CertificateDetail | None:
        body = self._get_cached("certificate", "/api/certificate", {"certificate_number": number})
        if body is None or not isinstance(body.get("data"), dict):
            return None
        return detail_from_certificate(number, body["data"])

    def _get_cached(self, kind: str, path: str, params: Params) -> dict[str, Any] | None:
        key = hashlib.sha256(json.dumps([path, sorted(params.items())]).encode()).hexdigest()
        cache_file = self._cache_dir / kind / f"{key[:32]}.json"
        if cache_file.exists():
            cached: dict[str, Any] = json.loads(cache_file.read_text())
            return cached.get("body")

        response = self._get(path, params)
        body = response.json() if response.status_code == 200 else None
        if response.status_code not in (200, 404):
            response.raise_for_status()
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        partial = cache_file.with_suffix(f".{threading.get_ident()}.tmp")
        partial.write_text(json.dumps({"path": path, "params": params, "body": body}))
        partial.replace(cache_file)
        return body

    def _get(self, path: str, params: Params) -> httpx.Response:
        """GET with spacing between requests, backing off on 429 and timeouts."""
        for attempt in range(1, 6):
            with self._throttle:
                wait = self._min_interval_s - (time.monotonic() - self._last_request)
                if wait > 0:
                    time.sleep(wait)
                self._last_request = time.monotonic()
                self.requests_made += 1
            try:
                response = self._client.get(path, params=params)
            except httpx.TransportError:
                if attempt == 5:
                    raise
                time.sleep(10 * attempt)
                continue
            if response.status_code != 429 or attempt == 5:
                return response
            time.sleep(30 * attempt)
        raise AssertionError("unreachable")

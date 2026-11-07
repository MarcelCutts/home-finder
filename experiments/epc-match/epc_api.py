"""Read EPC certificates from the register's API, caching every answer on disk.

The API (https://get-energy-performance-data.communities.gov.uk) needs a
GOV.UK One Login bearer token, read from the EPC_API_TOKEN environment
variable. The token is sent only in the Authorization header; it is never
logged or written to the cache.

Responses hold certificate addresses, whose licence limits them to
energy-performance purposes, so the cache must stay under the gitignored
`data/` directory.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from matcher import Certificate

API_BASE = "https://api.get-energy-performance-data.communities.gov.uk"
TOKEN_VARIABLE = "EPC_API_TOKEN"
_PAGE_SIZE = 5000

Params = dict[str, str | list[str]]
"""Query parameters; a list value repeats the key, as `council[]` needs."""


class MissingTokenError(RuntimeError):
    pass


def token_from_environment() -> str:
    token = os.environ.get(TOKEN_VARIABLE, "").strip()
    if not token:
        raise MissingTokenError(f"Set {TOKEN_VARIABLE} to an EPC API bearer token.")
    return token


@dataclass(frozen=True)
class CertificateDetail:
    """The fields of a full certificate this experiment reads."""

    certificate_number: str
    total_floor_area: float | None
    band: str | None
    energy_rating: int | None
    """Current SAP score, 1-100+, behind the band."""
    dwelling_type: str | None
    habitable_rooms: int | None
    floor_level: int | None
    registration_date: str | None
    uprn_source: str | None
    construction_age_band: str | None


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
    return CertificateDetail(
        certificate_number=number,
        total_floor_area=_number(data.get("total_floor_area")),
        band=data.get("current_energy_efficiency_band"),
        energy_rating=_integer(data.get("energy_rating_current")),
        dwelling_type=dwelling if isinstance(dwelling, str) else None,
        habitable_rooms=_integer(data.get("habitable_room_count")),
        floor_level=_integer(flat.get("level")) if isinstance(flat, dict) else None,
        registration_date=data.get("registration_date"),
        uprn_source=data.get("uprn_source"),
        construction_age_band=(parts[0] or {}).get("construction_age_band") if parts else None,
    )


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
    """A throttled, cached client for the domestic search and certificate endpoints."""

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

    def search_postcode(self, postcode: str) -> list[Certificate]:
        return self.search({"postcode": postcode})

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
        cache_file.write_text(json.dumps({"path": path, "params": params, "body": body}))
        return body

    def _get(self, path: str, params: Params) -> httpx.Response:
        for attempt in range(5):
            wait = self._min_interval_s - (time.monotonic() - self._last_request)
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.monotonic()
            self.requests_made += 1
            response = self._client.get(path, params=params)
            if response.status_code != 429:
                return response
            time.sleep(30 * (attempt + 1))
        return response

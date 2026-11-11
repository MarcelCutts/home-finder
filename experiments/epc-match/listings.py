"""Read listings from a scratch copy of a home-finder database.

A home listed on several portals has one row in `properties` and one row per
portal in `source_listings`. Each portal can give a different address line
and postcode, so a listing keeps all of them.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from matcher import normalise_postcode, outcode_of


@dataclass(frozen=True)
class Listing:
    dataset: str
    unique_id: str
    addresses: tuple[str, ...]
    """Address lines from every portal, the merged record's first."""
    postcodes: tuple[str, ...]
    """Full postcodes from every portal, normalised; empty if only a district is known."""
    outcode: str | None
    latitude: float | None
    longitude: float | None
    bedrooms: int
    floor_area_sqm: float | None
    floor_area_source: str | None
    """"estimated" for the app's floorplan reading, else the portal that advertised it."""
    advert_epc_band: str | None
    """EPC band the analysis read from the advert, if any."""
    property_type: str | None
    """Analysis label such as "purpose_built" or "period_conversion"."""


def load_listings(db_path: Path, dataset: str) -> list[Listing]:
    """Load every home from the database at `db_path`, opened read-only."""
    uri = f"file:{db_path}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        conn.row_factory = sqlite3.Row
        sources: dict[str, list[sqlite3.Row]] = {}
        for row in conn.execute(
            "SELECT merged_id, address, postcode FROM source_listings "
            "WHERE merged_id IS NOT NULL ORDER BY first_seen"
        ):
            sources.setdefault(row["merged_id"], []).append(row)
        analyses = {
            row["property_unique_id"]: row
            for row in conn.execute(
                "SELECT property_unique_id, epc_rating, analysis_json FROM quality_analyses"
            )
        }
        rows = conn.execute(
            "SELECT unique_id, address, postcode, latitude, longitude, bedrooms, "
            "floor_area_sqm, floor_area_source FROM properties ORDER BY unique_id"
        ).fetchall()

    listings = []
    for row in rows:
        portal_rows = sources.get(row["unique_id"], [])
        addresses = tuple(
            dict.fromkeys(a for a in [row["address"], *(r["address"] for r in portal_rows)] if a)
        )
        raw_postcodes = [row["postcode"], *(r["postcode"] for r in portal_rows)]
        postcodes = tuple(dict.fromkeys(p for p in map(normalise_postcode, raw_postcodes) if p))
        outcode = next((o for o in map(outcode_of, raw_postcodes) if o), None)
        analysis = analyses.get(row["unique_id"])
        listings.append(
            Listing(
                dataset=dataset,
                unique_id=row["unique_id"],
                addresses=addresses,
                postcodes=postcodes,
                outcode=outcode,
                latitude=row["latitude"],
                longitude=row["longitude"],
                bedrooms=row["bedrooms"],
                floor_area_sqm=row["floor_area_sqm"],
                floor_area_source=row["floor_area_source"],
                advert_epc_band=_advert_band(analysis),
                property_type=_property_type(analysis),
            )
        )
    return listings


def _advert_band(analysis: sqlite3.Row | None) -> str | None:
    band = analysis["epc_rating"] if analysis else None
    return band if band in {"A", "B", "C", "D", "E", "F", "G"} else None


def _property_type(analysis: sqlite3.Row | None) -> str | None:
    if analysis is None:
        return None
    try:
        data = json.loads(analysis["analysis_json"])
    except (TypeError, json.JSONDecodeError):
        return None
    value = (data.get("listing_extraction") or {}).get("property_type")
    return value if isinstance(value, str) and value != "unknown" else None

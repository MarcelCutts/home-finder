"""Tests for reading listings out of a home-finder database copy."""

import json
import sqlite3
from pathlib import Path

from listings import load_listings

SCHEMA = """
CREATE TABLE properties (
    unique_id TEXT PRIMARY KEY, address TEXT, postcode TEXT, latitude REAL,
    longitude REAL, bedrooms INTEGER, floor_area_sqm REAL, floor_area_source TEXT
);
CREATE TABLE source_listings (
    unique_id TEXT PRIMARY KEY, merged_id TEXT, address TEXT, postcode TEXT,
    first_seen TEXT
);
CREATE TABLE quality_analyses (
    property_unique_id TEXT PRIMARY KEY, epc_rating TEXT, analysis_json TEXT
);
"""


def make_db(path: Path) -> Path:
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT INTO properties VALUES ('zoopla:1', 'Quillon Road, Q9', 'Q9', 51.5, -0.07, 2, "
        "61.5, 'estimated')"
    )
    conn.executemany(
        "INSERT INTO source_listings VALUES (?, 'zoopla:1', ?, ?, ?)",
        [
            ("zoopla:1", "Quillon Road, Q9", "Q9", "2026-02-01"),
            ("rightmove:9", "12 Quillon Road, London", "q99zz", "2026-02-02"),
        ],
    )
    conn.execute(
        "INSERT INTO quality_analyses VALUES ('zoopla:1', 'C', ?)",
        (json.dumps({"listing_extraction": {"property_type": "period_conversion"}}),),
    )
    conn.commit()
    conn.close()
    return path


def test_listing_gathers_every_portals_address_and_full_postcode(tmp_path: Path) -> None:
    (listing,) = load_listings(make_db(tmp_path / "copy.db"), "fixture")

    assert listing.addresses == ("Quillon Road, Q9", "12 Quillon Road, London")
    assert listing.postcodes == ("Q9 9ZZ",)
    assert listing.outcode == "Q9"
    assert listing.floor_area_sqm == 61.5
    assert listing.floor_area_source == "estimated"
    assert listing.advert_epc_band == "C"
    assert listing.property_type == "period_conversion"


def test_database_is_opened_read_only(tmp_path: Path) -> None:
    db = make_db(tmp_path / "copy.db")
    before = db.read_bytes()
    load_listings(db, "fixture")
    assert db.read_bytes() == before

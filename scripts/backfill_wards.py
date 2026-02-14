"""One-time backfill script to populate ward column for existing properties.

Usage:
    uv run python scripts/backfill_wards.py [--db-path PATH]

Uses postcodes.io reverse geocoding (coordinates → ward) for properties
with lat/lon, and forward lookup for properties with full postcodes.
"""

import argparse
import asyncio

from home_finder.db import PropertyStorage
from home_finder.utils.postcode_lookup import bulk_reverse_lookup_wards, lookup_ward


async def backfill(db_path: str) -> None:
    storage = PropertyStorage(db_path)
    await storage.initialize()

    props = await storage.get_properties_without_ward()
    if not props:
        print("All properties already have wards. Nothing to do.")
        await storage.close()
        return

    print(f"Found {len(props)} properties without ward data.")

    # Split by available data
    coord_props = [p for p in props if p.get("latitude") and p.get("longitude")]
    postcode_props = [
        p
        for p in props
        if not (p.get("latitude") and p.get("longitude"))
        and p.get("postcode")
        and " " in str(p["postcode"])
    ]
    no_data = len(props) - len(coord_props) - len(postcode_props)

    print(f"  {len(coord_props)} with coordinates (reverse geocode)")
    print(f"  {len(postcode_props)} with full postcodes (forward lookup)")
    if no_data:
        print(f"  {no_data} with neither (skipped)")

    ward_map: dict[str, str] = {}

    # Bulk reverse geocode
    if coord_props:
        print("Reverse geocoding coordinates...")
        coords = [(float(p["latitude"]), float(p["longitude"])) for p in coord_props]
        wards = await bulk_reverse_lookup_wards(coords)
        for p, ward in zip(coord_props, wards, strict=True):
            if ward:
                ward_map[str(p["unique_id"])] = ward
        print(f"  Resolved {sum(1 for w in wards if w)}/{len(coord_props)}")

    # Forward lookup for full postcodes
    if postcode_props:
        print("Looking up full postcodes...")
        for p in postcode_props:
            ward = await lookup_ward(str(p["postcode"]))
            if ward:
                ward_map[str(p["unique_id"])] = ward
        matched = sum(1 for p in postcode_props if str(p["unique_id"]) in ward_map)
        print(f"  Resolved {matched}/{len(postcode_props)}")

    # Update database
    if ward_map:
        updated = await storage.update_wards(ward_map)
        print(f"\nUpdated {updated} properties with ward data.")
    else:
        print("\nNo wards resolved.")

    await storage.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill ward data for properties")
    parser.add_argument(
        "--db-path",
        default="data/home_finder.db",
        help="Path to SQLite database (default: data/home_finder.db)",
    )
    args = parser.parse_args()
    asyncio.run(backfill(args.db_path))


if __name__ == "__main__":
    main()

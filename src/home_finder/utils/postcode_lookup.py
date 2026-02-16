"""Postcode ward lookup via postcodes.io API.

Provides forward and reverse geocoding to map postcodes/coordinates to
official ward names, which are then used to identify micro-areas.
"""

import httpx

from home_finder.logging import get_logger

logger = get_logger(__name__)

_BASE_URL = "https://api.postcodes.io"
_TIMEOUT = 10.0


async def lookup_ward(
    postcode: str, *, client: httpx.AsyncClient | None = None
) -> str | None:
    """Forward lookup: full postcode → admin ward name.

    Returns None if the postcode is invalid or the lookup fails.
    """
    if client is not None:
        return await _lookup_ward_with_client(client, postcode)

    async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
        return await _lookup_ward_with_client(c, postcode)


async def _lookup_ward_with_client(
    client: httpx.AsyncClient, postcode: str
) -> str | None:
    try:
        resp = await client.get(f"{_BASE_URL}/postcodes/{postcode}")
        if resp.status_code != 200:
            return None
        data = resp.json()
        result = data.get("result")
        if result is None:
            return None
        ward: str | None = result.get("admin_ward")
        return ward
    except httpx.HTTPError:
        logger.warning("postcode_lookup_failed", postcode=postcode, exc_info=True)
        return None


async def reverse_lookup_ward(
    lat: float, lon: float, *, client: httpx.AsyncClient | None = None
) -> str | None:
    """Reverse geocode: coordinates → admin ward name of nearest postcode.

    Returns None if no postcode is found nearby or the lookup fails.
    """
    if client is not None:
        return await _reverse_lookup_ward_with_client(client, lat, lon)

    async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
        return await _reverse_lookup_ward_with_client(c, lat, lon)


async def _reverse_lookup_ward_with_client(
    client: httpx.AsyncClient, lat: float, lon: float
) -> str | None:
    try:
        resp = await client.get(
            f"{_BASE_URL}/postcodes",
            params={"lat": str(lat), "lon": str(lon), "limit": "1"},
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        result = data.get("result")
        if not result:
            return None
        ward: str | None = result[0].get("admin_ward")
        return ward
    except httpx.HTTPError:
        logger.warning("reverse_lookup_failed", lat=lat, lon=lon, exc_info=True)
        return None


async def bulk_reverse_lookup_wards(
    coords: list[tuple[float, float]],
    *,
    client: httpx.AsyncClient | None = None,
) -> list[str | None]:
    """Bulk reverse geocode: list of (lat, lon) → list of ward names.

    postcodes.io supports up to 100 geolocations per bulk request.
    Returns a list parallel to the input coords.
    """
    if not coords:
        return []

    if client is not None:
        return await _bulk_reverse_with_client(client, coords)

    async with httpx.AsyncClient(timeout=_TIMEOUT * 3) as c:
        return await _bulk_reverse_with_client(c, coords)


async def _bulk_reverse_with_client(
    client: httpx.AsyncClient, coords: list[tuple[float, float]]
) -> list[str | None]:
    results: list[str | None] = [None] * len(coords)

    # Process in batches of 100 (API limit)
    for batch_start in range(0, len(coords), 100):
        batch = coords[batch_start : batch_start + 100]
        geolocations = [{"latitude": lat, "longitude": lon} for lat, lon in batch]

        try:
            resp = await client.post(
                f"{_BASE_URL}/postcodes",
                json={"geolocations": geolocations},
            )
            if resp.status_code != 200:
                logger.warning(
                    "bulk_reverse_lookup_failed",
                    status=resp.status_code,
                    batch_start=batch_start,
                )
                continue

            data = resp.json()
            for i, item in enumerate(data.get("result", [])):
                if item and item.get("result"):
                    # First result is the nearest postcode
                    nearest = item["result"][0]
                    results[batch_start + i] = nearest.get("admin_ward")
        except httpx.HTTPError:
            logger.warning(
                "bulk_reverse_lookup_failed",
                batch_start=batch_start,
                exc_info=True,
            )

    return results

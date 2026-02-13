"""Shared parsing helpers for property scrapers."""

import re


def extract_price(text: str) -> int | None:
    """Extract monthly price from text.

    Handles both pcm (per calendar month) and pw (per week) formats.
    When both are present (e.g. Rightmove shows "£2,400 pcm £554 pw"),
    the PCM value is used directly.

    Args:
        text: Price text (e.g., "£2,300 pcm", "£500 pw", "£2,400 pcm £554 pw").

    Returns:
        Monthly price in GBP, or None if not parseable.
    """
    if not text:
        return None

    text_lower = text.lower()

    # Prefer explicit PCM price (avoids double-conversion when both pcm and pw present)
    pcm_match = re.search(r"£([\d,]+)\s*pcm", text_lower)
    if pcm_match:
        return int(pcm_match.group(1).replace(",", ""))

    match = re.search(r"£([\d,]+)", text)
    if not match:
        return None

    price = int(match.group(1).replace(",", ""))

    # Convert weekly to monthly (only when no PCM value was found)
    if "pw" in text_lower:
        price = int(price * 52 / 12)

    return price


def extract_bedrooms(text: str) -> int | None:
    """Extract bedroom count from text.

    Args:
        text: Title or description text (e.g., "2 bed flat", "Studio to rent").

    Returns:
        Number of bedrooms (0 for studio), or None if not found.
    """
    if not text:
        return None

    text_lower = text.lower()

    # Handle studio
    if "studio" in text_lower:
        return 0

    # Match "1 bed", "2 bedroom", "3 bedrooms", etc.
    match = re.search(r"(\d+)\s*bed(?:room)?s?", text_lower)
    return int(match.group(1)) if match else None


def extract_postcode(address: str) -> str | None:
    """Extract UK postcode from address.

    Args:
        address: Full address string (e.g., "Mare Street, London E8 3RH").

    Returns:
        Postcode (e.g., "E8 3RH" or "E8"), or None if not found.
    """
    if not address:
        return None

    match = re.search(
        r"\b([A-Z]{1,2}\d{1,2}[A-Z]?)\s*(\d[A-Z]{2})?\b",
        address.upper(),
    )
    if match:
        outward = match.group(1)
        inward = match.group(2)
        if inward:
            return f"{outward} {inward}"
        return outward
    return None

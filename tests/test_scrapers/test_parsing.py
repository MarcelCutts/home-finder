"""Price, bedroom and postcode parsing shared by every portal's search scraper."""

import pytest

from home_finder.scrapers.parsing import extract_bedrooms, extract_postcode, extract_price


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("£2,300 pcm", 2300),
        ("£1,950 pcm", 1950),
        ("£2,100 pcm", 2100),
        ("£500 pw", 2166),  # Weekly to monthly: 500 * 52 / 12
        ("£2,400 pcm£554 pw", 2400),  # Both present: use the monthly figure
        ("£2,400 pcm\n£554 pw", 2400),
        ("£2,400 pcm (£554 pw)", 2400),
    ],
)
def test_extract_price(text: str, expected: int) -> None:
    assert extract_price(text) == expected


@pytest.mark.parametrize("text", ["Contact agent", "", "POA"])
def test_extract_price_without_a_price(text: str) -> None:
    assert extract_price(text) is None


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("1 bedroom flat to rent", 1),
        ("1 bed flat to rent", 1),
        ("2 bedroom apartment to rent", 2),
        ("2 bed apartment to rent", 2),
        ("Studio to rent", 0),
        ("3 bed house", 3),
        ("3 bedroom house", 3),
    ],
)
def test_extract_bedrooms(title: str, expected: int) -> None:
    assert extract_bedrooms(title) == expected


@pytest.mark.parametrize("title", ["Flat to rent", ""])
def test_extract_bedrooms_without_a_count(title: str) -> None:
    assert extract_bedrooms(title) is None


@pytest.mark.parametrize(
    ("address", "expected"),
    [
        ("Wayland Avenue, London E8", "E8"),
        ("Some Street, N1", "N1"),
        ("Mare Street, Hackney, London E8 3RH", "E8 3RH"),
        ("Wayland Avenue, Hackney, London E8 3RH", "E8 3RH"),
        ("Islington N1 2AA", "N1 2AA"),
    ],
)
def test_extract_postcode(address: str, expected: str) -> None:
    assert extract_postcode(address) == expected


@pytest.mark.parametrize("address", ["Some Address, London", ""])
def test_extract_postcode_without_a_postcode(address: str) -> None:
    assert extract_postcode(address) is None

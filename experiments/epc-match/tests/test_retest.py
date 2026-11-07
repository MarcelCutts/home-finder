"""Tests for comparing a home's floor area across its certificates."""

from epc_api import CertificateDetail
from matcher import Certificate
from retest import Repeat, homes_with_repeats, repeat_summary


def detail(area: float | None, band: str = "C") -> CertificateDetail:
    return CertificateDetail(
        certificate_number="n",
        total_floor_area=area,
        band=band,
        energy_rating=70,
        dwelling_type=None,
        habitable_rooms=None,
        flat_position=None,
        registration_date=None,
        uprn_source=None,
        construction_age_band=None,
    )


def cert(number: str, line: str, uprn: str, postcode: str = "Q9 9ZZ") -> Certificate:
    return Certificate(number, (line,), postcode, uprn=uprn)


def test_only_homes_assessed_twice_are_kept() -> None:
    certs = [
        cert("a1", "1 Quillon Road", "1"),
        cert("a2", "1 Quillon Road", "1"),
        cert("b1", "2 Quillon Road", "2"),
    ]
    (home,) = homes_with_repeats(certs)
    assert {c.certificate_number for c in home.certificates} == {"a1", "a2"}


def test_area_change_is_relative_to_the_newest() -> None:
    assert Repeat(detail(50), detail(55)).area_change_pct == 10.0
    assert Repeat(detail(50), detail(0)).area_change_pct is None


def test_summary_counts_steady_and_moved_areas() -> None:
    repeats = [
        Repeat(detail(50), detail(51)),
        Repeat(detail(50), detail(54)),
        Repeat(detail(50), detail(70, band="D")),
        Repeat(detail(50), detail(None)),
    ]
    summary = repeat_summary(repeats)
    assert summary["with two plausible areas"] == "3"
    assert summary["within 5%"] == "33%"
    assert summary["within 10%"] == "67%"
    assert summary["more than 20% apart"] == "1"
    assert summary["implausible area on either"] == "1"
    assert summary["same band"] == "75%"

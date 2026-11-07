"""Tests for narrowing ambiguous matches by habitable rooms."""

from epc_api import CertificateDetail
from narrow import expected_rooms, narrow_by_rooms


def detail(number: str, rooms: int | None) -> CertificateDetail:
    return CertificateDetail(
        certificate_number=number,
        total_floor_area=50.0,
        band="C",
        energy_rating=70,
        dwelling_type="Mid-floor flat",
        habitable_rooms=rooms,
        flat_position=1,
        registration_date="2020-01-01",
        uprn_source="Energy Assessor",
        construction_age_band="K",
    )


def ambiguous(bedrooms: int, *numbers: str) -> dict:
    return {
        "outcome": "ambiguous",
        "evidence": "street",
        "reason": None,
        "listing": {"bedrooms": bedrooms},
        "homes": [{"key": n, "certificates": [{"certificate_number": n}]} for n in numbers],
    }


def lookup(rooms: dict[str, int | None]):
    return lambda number: detail(number, rooms[number]) if number in rooms else None


def test_a_studio_has_one_room_and_a_two_bed_three() -> None:
    assert expected_rooms(0) == 1
    assert expected_rooms(2) == 3


def test_the_only_home_with_the_expected_rooms_is_a_narrowed_match() -> None:
    narrowed = narrow_by_rooms(ambiguous(2, "a", "b", "c"), lookup({"a": 2, "b": 3, "c": 4}))
    assert narrowed["outcome"] == "one"
    assert narrowed["evidence"] == "rooms"
    assert [h["key"] for h in narrowed["homes"]] == ["b"]
    assert narrowed["homes_before"] == 3
    assert narrowed["details"][0]["habitable_rooms"] == 3


def test_several_homes_with_the_expected_rooms_stay_ambiguous() -> None:
    narrowed = narrow_by_rooms(ambiguous(1, "a", "b", "c"), lookup({"a": 2, "b": 2, "c": 3}))
    assert narrowed["outcome"] == "ambiguous"
    assert len(narrowed["homes"]) == 2


def test_no_home_with_the_expected_rooms_is_no_match() -> None:
    narrowed = narrow_by_rooms(ambiguous(3, "a", "b"), lookup({"a": 2, "b": 3}))
    assert narrowed["outcome"] == "none"
    assert narrowed["reason"] == "rooms_not_found"


def test_a_home_whose_rooms_are_not_counted_stays_a_candidate() -> None:
    narrowed = narrow_by_rooms(ambiguous(2, "a", "b", "c"), lookup({"a": 3, "b": 2, "c": None}))
    assert narrowed["outcome"] == "ambiguous"
    assert [h["key"] for h in narrowed["homes"]] == ["a", "c"]
    assert narrowed["rooms_known"] == 2
    assert narrowed["matched_floor_areas"] == [50.0]  # only the home with the right rooms


def test_rule_leaves_every_candidate_when_no_certificate_counts_rooms() -> None:
    narrowed = narrow_by_rooms(ambiguous(2, "a", "b"), lookup({"a": None}))
    assert narrowed["outcome"] == "ambiguous"
    assert len(narrowed["homes"]) == 2
    assert narrowed["rooms_known"] == 0

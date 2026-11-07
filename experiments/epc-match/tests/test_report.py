"""Tests for the aggregate numbers and the hand-check sample."""

import pytest
from handcheck import draw_sample
from report import (
    ambiguity_summary,
    area_agreement,
    area_pairs,
    band_agreement,
    correct_match_rate,
    epc_floor_area,
    wilson_interval,
)


def home(*bands: str) -> dict:
    return {"key": "k", "certificates": [{"band": b} for b in bands]}


def record(
    outcome: str = "one",
    *,
    uid: str = "x:1",
    dataset: str = "fixture",
    evidence: str = "number",
    homes: list | None = None,
    listing_area: float | None = None,
    area_source: str | None = None,
    advert_band: str | None = None,
    epc_areas: tuple[float | None, ...] = (),
) -> dict:
    return {
        "outcome": outcome,
        "evidence": evidence,
        "listing": {
            "dataset": dataset,
            "unique_id": uid,
            "floor_area_sqm": listing_area,
            "floor_area_source": area_source,
            "advert_epc_band": advert_band,
        },
        "homes": homes if homes is not None else [home("C")],
        "details": [{"total_floor_area": a} for a in epc_areas],
    }


class TestFloorArea:
    def test_newest_plausible_certificate_area_is_used(self) -> None:
        assert epc_floor_area(record(epc_areas=(0, 9999, 61.0, 58.0))) == 61.0
        assert epc_floor_area(record(epc_areas=(0,))) is None

    def test_pairs_split_floorplan_readings_from_advert_figures(self) -> None:
        records = [
            record(uid="a", listing_area=50, area_source="estimated", epc_areas=(55,)),
            record(uid="b", listing_area=70, area_source="rightmove", epc_areas=(70,)),
            record(uid="c", listing_area=None, epc_areas=(70,)),
            record("ambiguous", uid="d", listing_area=70, epc_areas=(70,)),
        ]
        assert area_pairs(records) == [("floorplan", 50.0, 55.0), ("advert", 70.0, 70.0)]

    def test_pairs_can_be_kept_to_hand_checked_listings(self) -> None:
        records = [
            record(uid="a", listing_area=50, area_source="estimated", epc_areas=(55,)),
            record(uid="b", listing_area=70, area_source="estimated", epc_areas=(70,)),
        ]
        pairs = area_pairs(records, correct_only={("fixture", "b")})
        assert pairs == [("floorplan", 70.0, 70.0)]

    def test_agreement_is_relative_to_the_epc_figure(self) -> None:
        pairs = [("floorplan", 45.0, 50.0), ("floorplan", 50.0, 50.0), ("advert", 80.0, 50.0)]
        floorplan = area_agreement(pairs, "floorplan")
        assert floorplan is not None
        assert floorplan.n == 2
        assert floorplan.median_abs_pct == pytest.approx(5.0)
        assert floorplan.median_signed_pct == pytest.approx(5.0)  # EPC larger
        assert (floorplan.within_5, floorplan.within_10) == (1, 2)  # 10% and 0% off
        everything = area_agreement(pairs, "all")
        assert everything is not None
        assert everything.within_20 == 2  # 80 against 50 is 60% off

    def test_no_pairs_gives_no_agreement(self) -> None:
        assert area_agreement([], "all") is None


class TestBandsAndAmbiguity:
    def test_band_agreement_counts_exact_and_neighbouring_bands(self) -> None:
        records = [
            record(advert_band="C", homes=[home("C")]),
            record(advert_band="B", homes=[home("C")]),
            record(advert_band="E", homes=[home("C")]),
            record(advert_band=None, homes=[home("C")]),
        ]
        assert band_agreement(records) == (3, 1, 2)

    def test_candidates_sharing_one_band_are_counted(self) -> None:
        records = [
            record("ambiguous", homes=[home("B"), home("B", "D")]),
            record("ambiguous", homes=[home("B"), home("C"), home("C")]),
            record("one"),
        ]
        summary = ambiguity_summary(records)
        assert summary is not None
        assert summary.listings == 2
        assert summary.one_band == 1  # the newest certificate's band counts
        assert summary.within_two == 1
        assert summary.median_homes == 2.5


class TestHandCheck:
    def test_unclear_verdicts_are_left_out_of_the_rate(self) -> None:
        judgements = [
            {"verdict": "correct"},
            {"verdict": "likely"},
            {"verdict": "wrong"},
            {"verdict": "unclear"},
        ]
        good, judged, (lo, hi) = correct_match_rate(judgements)
        assert (good, judged) == (2, 3)
        assert lo < 2 / 3 < hi

    def test_wilson_interval_stays_inside_zero_and_one(self) -> None:
        lo, hi = wilson_interval(30, 30)
        assert 0.85 < lo < 1.0
        assert hi == pytest.approx(1.0)
        assert wilson_interval(0, 0) == (0.0, 0.0)

    def test_sample_is_seeded_and_covers_every_stratum(self) -> None:
        records = [record(uid=f"a{i}", evidence="number") for i in range(40)]
        records += [record(uid="b0", evidence="flat")]
        records += [record("ambiguous", uid="c0")]
        first = draw_sample(records, size=10, seed=1)
        assert first == draw_sample(records, size=10, seed=1)
        assert {r["evidence"] for r in first} == {"number", "flat"}
        assert all(r["outcome"] == "one" for r in first)
        assert len(first) == 11  # ten in proportion, plus the lone flat match

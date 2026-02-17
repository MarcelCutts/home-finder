"""Tests for the evaluation harness."""

from __future__ import annotations

from evaluate import (
    AgreementMetrics,
    SqmMetrics,
    _is_within_one_step,
    decide,
    evaluate,
    HOSTING_LAYOUT_ORDER,
    OFFICE_SEP_ORDER,
)


class TestSqmMetrics:
    def test_empty(self) -> None:
        m = SqmMetrics()
        assert m.n == 0
        assert m.mae == 0.0
        assert m.median_ae == 0.0
        assert m.within(5) == 0.0

    def test_basic(self) -> None:
        m = SqmMetrics(
            errors=[2.0, -3.0, 4.0],
            abs_errors=[2.0, 3.0, 4.0],
        )
        assert m.n == 3
        assert m.mae == 3.0
        assert m.median_ae == 3.0
        assert m.mean_error == 1.0  # slight overestimate bias
        assert m.within(3) == pytest.approx(66.666, abs=0.1)
        assert m.within(5) == 100.0

    def test_within_threshold(self) -> None:
        m = SqmMetrics(abs_errors=[1, 2, 5, 8, 12])
        assert m.within(3) == 40.0  # 2 of 5
        assert m.within(5) == 60.0  # 3 of 5
        assert m.within(10) == 80.0  # 4 of 5


class TestAgreementMetrics:
    def test_empty(self) -> None:
        m = AgreementMetrics()
        assert m.exact_rate == 0.0

    def test_perfect(self) -> None:
        m = AgreementMetrics(exact_matches=10, within_one_step=10, total=10)
        assert m.exact_rate == 100.0

    def test_partial(self) -> None:
        m = AgreementMetrics(exact_matches=3, within_one_step=4, total=5)
        assert m.exact_rate == 60.0
        assert m.within_one_rate == 80.0


class TestWithinOneStep:
    def test_same_value(self) -> None:
        assert _is_within_one_step("good", "good", HOSTING_LAYOUT_ORDER)

    def test_adjacent(self) -> None:
        assert _is_within_one_step("good", "excellent", HOSTING_LAYOUT_ORDER)
        assert _is_within_one_step("good", "awkward", HOSTING_LAYOUT_ORDER)

    def test_not_adjacent(self) -> None:
        assert not _is_within_one_step("excellent", "poor", HOSTING_LAYOUT_ORDER)
        assert not _is_within_one_step("excellent", "awkward", HOSTING_LAYOUT_ORDER)

    def test_unknown_value(self) -> None:
        assert not _is_within_one_step("unknown", "good", HOSTING_LAYOUT_ORDER)

    def test_office_separation(self) -> None:
        assert _is_within_one_step("dedicated_room", "separate_area", OFFICE_SEP_ORDER)
        assert not _is_within_one_step("dedicated_room", "none", OFFICE_SEP_ORDER)


class TestDecide:
    def test_full_go(self) -> None:
        sqm = SqmMetrics(abs_errors=[2.0, 3.0, 4.0, 1.0, 5.0])  # MAE = 3.0
        spacious = AgreementMetrics(exact_matches=9, total=10)  # 90%
        verdict, _ = decide(sqm, spacious)
        assert verdict == "FULL_GO"

    def test_qualitative_moderate_error(self) -> None:
        sqm = SqmMetrics(abs_errors=[7.0, 8.0, 6.0, 9.0, 5.0])  # MAE = 7.0
        spacious = AgreementMetrics(exact_matches=8, total=10)  # 80%
        verdict, _ = decide(sqm, spacious)
        assert verdict == "QUALITATIVE_ONLY"

    def test_qualitative_large_error(self) -> None:
        sqm = SqmMetrics(abs_errors=[12.0, 15.0, 11.0, 13.0, 14.0])  # MAE = 13.0
        spacious = AgreementMetrics(exact_matches=8, total=10)  # 80%
        verdict, _ = decide(sqm, spacious)
        assert verdict == "QUALITATIVE_ONLY"

    def test_dont_pursue(self) -> None:
        sqm = SqmMetrics(abs_errors=[5.0, 5.0])  # MAE = 5.0
        spacious = AgreementMetrics(exact_matches=7, total=10)  # 70%
        verdict, _ = decide(sqm, spacious)
        assert verdict == "DONT_PURSUE"


class TestEvaluate:
    def test_perfect_results(self, perfect_inference: list[dict]) -> None:
        sqm, spacious, hosting, office, confidence = evaluate(perfect_inference)
        assert sqm.mae == 0.0
        assert spacious.exact_rate == 100.0
        assert hosting.exact_rate == 100.0
        assert office.exact_rate == 100.0

    def test_good_results(self, good_inference: list[dict]) -> None:
        sqm, spacious, hosting, office, confidence = evaluate(good_inference)
        assert sqm.mae <= 5.0
        assert spacious.exact_rate == 100.0
        verdict, _ = decide(sqm, spacious)
        assert verdict == "FULL_GO"

    def test_mediocre_results(self, mediocre_inference: list[dict]) -> None:
        sqm, spacious, hosting, office, confidence = evaluate(mediocre_inference)
        assert 5 < sqm.mae <= 10
        assert spacious.exact_rate == 100.0
        verdict, _ = decide(sqm, spacious)
        assert verdict == "QUALITATIVE_ONLY"

    def test_poor_results(self, poor_inference: list[dict]) -> None:
        sqm, spacious, hosting, office, confidence = evaluate(poor_inference)
        assert sqm.mae > 10
        assert spacious.exact_rate == 0.0
        verdict, _ = decide(sqm, spacious)
        assert verdict == "DONT_PURSUE"

    def test_confidence_shift(self, good_inference: list[dict]) -> None:
        """Photo-only inference should use lower confidence than floorplan GT."""
        _, _, _, _, confidence = evaluate(good_inference)
        # Ground truth has mostly "high" confidence (from floorplan)
        assert confidence.gt_counts["high"] >= 3
        # Inference should have more "medium" (photo-only self-calibration)
        assert confidence.inf_counts["medium"] >= 3

    def test_handles_none_values(self) -> None:
        """Gracefully handle None values in either ground truth or inference."""
        results = [
            {
                "unique_id": "test:1",
                "inference": {
                    "living_room_sqm": None,
                    "is_spacious_enough": None,
                    "hosting_layout": "unknown",
                    "confidence": "low",
                    "office_separation": "unknown",
                },
                "ground_truth": {
                    "living_room_sqm": 20.0,
                    "is_spacious_enough": True,
                    "hosting_layout": "good",
                    "confidence": "high",
                    "office_separation": "dedicated_room",
                },
            }
        ]
        sqm, spacious, hosting, office, _ = evaluate(results)
        assert sqm.skipped == 1
        assert sqm.n == 0
        assert spacious.skipped == 1
        assert hosting.skipped == 1
        assert office.skipped == 1


# Need pytest import at module level for approx
import pytest  # noqa: E402

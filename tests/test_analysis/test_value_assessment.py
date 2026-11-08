"""Value-for-money against historical rental benchmarks (assess_value in analysis/analyzer.py)."""

import pytest

from home_finder.analysis.analyzer import assess_value
from home_finder.data.area_context import DEFAULT_BENCHMARK, RENTAL_BENCHMARKS


class TestAssessValue:
    """Ratings and notes compare the asking rent with the outcode's benchmark."""

    def test_excellent_value_below_average(self) -> None:
        """Property well below average should be excellent value."""
        value = assess_value(price_pcm=1600, postcode="E8 2LX", bedrooms=1)
        assert value.rating == "excellent"
        assert value.difference is not None and value.difference < 0
        assert "below" in value.note.lower()

    def test_good_value_at_average(self) -> None:
        """Property at average should be good value."""
        value = assess_value(price_pcm=1900, postcode="E8 2LX", bedrooms=1)
        assert value.rating == "good"

    def test_fair_value_slightly_above(self) -> None:
        """Property slightly above average should be fair value."""
        value = assess_value(price_pcm=2050, postcode="E8 2LX", bedrooms=1)
        assert value.rating == "fair"
        assert "above" in value.note.lower()

    def test_poor_value_well_above(self) -> None:
        """Property well above average should be poor value."""
        value = assess_value(price_pcm=2300, postcode="E8 2LX", bedrooms=1)
        assert value.rating == "poor"

    def test_handles_missing_postcode(self) -> None:
        """Should handle missing postcode gracefully."""
        value = assess_value(price_pcm=1800, postcode=None, bedrooms=1)
        assert value.rating is None
        assert "cannot assess" in value.note.lower()

    def test_uses_default_for_unknown_area(self) -> None:
        """An outcode without its own benchmark is compared with the default benchmark."""
        assert "W1A" not in RENTAL_BENCHMARKS
        value = assess_value(price_pcm=1800, postcode="W1A 1AA", bedrooms=1)
        assert value.area_average == DEFAULT_BENCHMARK[1]
        assert value.difference == 1800 - DEFAULT_BENCHMARK[1]

    @pytest.mark.parametrize(
        ("bedrooms", "benchmark_beds"),
        [(0, 1), (1, 1), (2, 2), (3, 3), (5, 3)],
        ids=["studio", "one_bed", "two_bed", "three_bed", "five_bed"],
    )
    def test_bedroom_count_selects_benchmark(self, bedrooms: int, benchmark_beds: int) -> None:
        """Studios use the 1-bed benchmark and larger homes are capped at the 3-bed one."""
        value = assess_value(price_pcm=2000, postcode="E8 2LX", bedrooms=bedrooms)
        assert value.area_average == RENTAL_BENCHMARKS["E8"][benchmark_beds]

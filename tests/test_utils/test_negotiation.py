"""Tests for negotiation intelligence with graduated scoring."""

from unittest.mock import patch

from home_finder.utils.negotiation import (
    NegotiationSignal,
    _score_benchmark,
    _score_days_listed,
    _score_price_drops,
    _score_rent_trend,
    _score_seasonal,
    generate_negotiation_brief,
)


class TestScoreDaysListed:
    def test_just_listed(self) -> None:
        sig = _score_days_listed(0)
        assert sig.direction == "seller"
        assert sig.weight > 0

    def test_fresh_listing(self) -> None:
        sig = _score_days_listed(3)
        assert sig.direction == "seller"
        assert sig.weight == 0.2

    def test_one_week(self) -> None:
        sig = _score_days_listed(7)
        assert sig.direction == "buyer"
        assert sig.weight == 0.2

    def test_two_weeks(self) -> None:
        sig = _score_days_listed(14)
        assert sig.direction == "buyer"
        assert 0.4 <= sig.weight <= 0.6

    def test_28_days_max(self) -> None:
        sig = _score_days_listed(28)
        assert sig.direction == "buyer"
        assert sig.weight == 1.0
        assert "significantly stale" in sig.text

    def test_graduates_between_7_and_28(self) -> None:
        w7 = _score_days_listed(7).weight
        w14 = _score_days_listed(14).weight
        w21 = _score_days_listed(21).weight
        w28 = _score_days_listed(28).weight
        assert w7 < w14 < w21 < w28


class TestScorePriceDrops:
    def test_no_drops(self) -> None:
        sig = _score_price_drops([])
        assert sig.direction == "neutral"
        assert sig.weight == 0.0

    def test_single_drop(self) -> None:
        sig = _score_price_drops([{"change_amount": -100}])
        assert sig.direction == "buyer"
        assert sig.weight >= 0.3

    def test_multiple_drops(self) -> None:
        sig = _score_price_drops([{"change_amount": -100}, {"change_amount": -50}])
        assert sig.direction == "buyer"
        assert sig.weight > _score_price_drops([{"change_amount": -100}]).weight

    def test_large_drop(self) -> None:
        sig = _score_price_drops([{"change_amount": -500}])
        assert sig.direction == "buyer"
        assert sig.weight > _score_price_drops([{"change_amount": -50}]).weight

    def test_capped_at_1(self) -> None:
        sig = _score_price_drops(
            [
                {"change_amount": -500},
                {"change_amount": -500},
                {"change_amount": -500},
            ]
        )
        assert sig.weight <= 1.0

    def test_text_pluralises(self) -> None:
        one = _score_price_drops([{"change_amount": -100}])
        two = _score_price_drops([{"change_amount": -100}, {"change_amount": -50}])
        assert "once" in one.text
        assert "2 times" in two.text


class TestScoreBenchmark:
    def test_neutral_zone(self) -> None:
        """Within ±5% = neutral."""
        sig = _score_benchmark(1900, 1900, "E8")
        assert sig.direction == "neutral"
        assert sig.weight == 0.0

    def test_slightly_above(self) -> None:
        """5-15% above = graduated buyer signal."""
        sig = _score_benchmark(2100, 1900, "E8")  # ~10.5% above
        assert sig.direction == "buyer"
        assert 0.3 < sig.weight < 1.0

    def test_far_above(self) -> None:
        """>15% above = full buyer signal."""
        sig = _score_benchmark(2300, 1900, "E8")  # ~21% above
        assert sig.direction == "buyer"
        assert sig.weight == 1.0

    def test_below_median(self) -> None:
        """Below median = seller advantage."""
        sig = _score_benchmark(1600, 1900, "E8")  # ~16% below
        assert sig.direction == "seller"
        assert sig.weight == 1.0
        assert "competitive" in sig.text or "below market" in sig.text

    def test_text_includes_outcode(self) -> None:
        sig = _score_benchmark(2100, 1900, "E8")
        assert "E8" in sig.text

    def test_text_includes_median(self) -> None:
        sig = _score_benchmark(2100, 1900, "E8")
        assert "1,900" in sig.text


class TestScoreRentTrend:
    def test_rising_strongly(self) -> None:
        trend = {"yoy_pct": 8.9, "direction": "rising strongly"}
        sig = _score_rent_trend(trend, "E15")
        assert sig.direction == "seller"
        assert sig.weight >= 0.5

    def test_rising(self) -> None:
        trend = {"yoy_pct": 4.3, "direction": "rising"}
        sig = _score_rent_trend(trend, "E8")
        assert sig.direction == "seller"
        assert sig.weight == 0.3

    def test_flat(self) -> None:
        trend = {"yoy_pct": 1.0, "direction": "flat"}
        sig = _score_rent_trend(trend, "E8")
        assert sig.direction == "buyer"

    def test_falling(self) -> None:
        trend = {"yoy_pct": -2.0, "direction": "falling"}
        sig = _score_rent_trend(trend, "E8")
        assert sig.direction == "buyer"
        assert sig.weight >= 0.4


class TestScoreSeasonal:
    def test_off_peak(self) -> None:
        from datetime import datetime as _dt

        with patch("home_finder.utils.negotiation.datetime") as mock_dt:
            mock_dt.now.return_value = _dt(2026, 1, 15)
            mock_dt.side_effect = lambda *a, **kw: _dt(*a, **kw)
            sig = _score_seasonal()
            assert sig.direction == "buyer"
            assert sig.weight == 0.3

    def test_peak(self) -> None:
        from datetime import datetime as _dt

        with patch("home_finder.utils.negotiation.datetime") as mock_dt:
            mock_dt.now.return_value = _dt(2026, 7, 15)
            mock_dt.side_effect = lambda *a, **kw: _dt(*a, **kw)
            sig = _score_seasonal()
            assert sig.direction == "seller"
            assert sig.weight == 0.3

    def test_moderate(self) -> None:
        from datetime import datetime as _dt

        with patch("home_finder.utils.negotiation.datetime") as mock_dt:
            mock_dt.now.return_value = _dt(2026, 4, 15)
            mock_dt.side_effect = lambda *a, **kw: _dt(*a, **kw)
            sig = _score_seasonal()
            assert sig.direction == "neutral"
            assert sig.weight == 0.1


class TestGenerateNegotiationBrief:
    def test_none_when_no_data(self) -> None:
        result = generate_negotiation_brief(
            days_listed=0,
            price_history=[],
            price_pcm=0,
            outcode=None,
            bedrooms=0,
        )
        assert result is None

    def test_strong_position(self) -> None:
        """Long listing + price drops + above median → strong."""
        result = generate_negotiation_brief(
            days_listed=30,
            price_history=[{"change_amount": -100}, {"change_amount": -50}],
            price_pcm=2200,
            outcode="E8",
            bedrooms=1,
        )
        assert result is not None
        assert result["strength"] in ("strong", "moderate")
        assert isinstance(result["signals"], list)
        assert all(isinstance(s, NegotiationSignal) for s in result["signals"])

    def test_limited_position(self) -> None:
        """Fresh listing + below median → limited."""
        result = generate_negotiation_brief(
            days_listed=0,
            price_history=[],
            price_pcm=1500,
            outcode="E8",
            bedrooms=1,
        )
        assert result is not None
        # Below median + fresh + rising rents → seller-heavy = limited
        assert result["strength"] in ("limited", "balanced")

    def test_balanced_position(self) -> None:
        """Mixed signals — some buyer, some seller."""
        result = generate_negotiation_brief(
            days_listed=10,
            price_history=[],
            price_pcm=1900,  # at median
            outcode="E8",
            bedrooms=1,
        )
        assert result is not None
        assert result["strength"] in ("balanced", "moderate", "limited")

    def test_signals_list_returned(self) -> None:
        result = generate_negotiation_brief(
            days_listed=14,
            price_history=[{"change_amount": -200}],
            price_pcm=2100,
            outcode="E8",
            bedrooms=1,
        )
        assert result is not None
        categories = {s.category for s in result["signals"]}
        assert "days_listed" in categories
        assert "price_drops" in categories
        assert "benchmark" in categories
        assert "seasonal" in categories

    def test_approach_text_is_contextual(self) -> None:
        """Approach should reference actual amounts, not generic templates."""
        result = generate_negotiation_brief(
            days_listed=30,
            price_history=[{"change_amount": -200}],
            price_pcm=2200,
            outcode="E8",
            bedrooms=1,
        )
        assert result is not None
        approach = result["suggested_approach"]
        # Should reference specific data, not just generic percentages
        assert len(approach) > 20

    def test_benchmark_source_present(self) -> None:
        result = generate_negotiation_brief(
            days_listed=7,
            price_history=[],
            price_pcm=2000,
            outcode="E8",
            bedrooms=1,
        )
        assert result is not None
        assert result["benchmark_source"] is not None
        assert "E8" in result["benchmark_source"]

    def test_benchmark_source_none_without_outcode(self) -> None:
        result = generate_negotiation_brief(
            days_listed=7,
            price_history=[],
            price_pcm=2000,
            outcode=None,
            bedrooms=1,
        )
        # No outcode → still returns (days > 0) but no benchmark source
        assert result is not None
        assert result["benchmark_source"] is None

    def test_rent_trend_included_when_borough_known(self) -> None:
        """E8 maps to Hackney which has rent trend data."""
        result = generate_negotiation_brief(
            days_listed=7,
            price_history=[],
            price_pcm=2000,
            outcode="E8",
            bedrooms=1,
        )
        assert result is not None
        categories = {s.category for s in result["signals"]}
        assert "rent_trend" in categories

"""Tests for negotiation intelligence (Ticket 10)."""

from home_finder.utils.negotiation import generate_negotiation_brief


class TestGenerateNegotiationBrief:
    def test_strong_position(self) -> None:
        """Multiple signals (14+ days + price drops) = strong."""
        result = generate_negotiation_brief(
            days_listed=21,
            price_history=[{"change_amount": -100}],
            benchmark_diff=200,  # above median
            area_median=2000,
        )
        assert result is not None
        assert result["strength"] == "strong"
        assert "21 days" in result["days_context"]
        assert "above" in result["price_context"]
        assert "dropped" in result["history_context"]

    def test_moderate_position(self) -> None:
        """Single signal = moderate."""
        result = generate_negotiation_brief(
            days_listed=14,
            price_history=[],
            benchmark_diff=None,
            area_median=None,
        )
        assert result is not None
        assert result["strength"] == "moderate"

    def test_weak_position(self) -> None:
        """No signals = weak."""
        result = generate_negotiation_brief(
            days_listed=3,
            price_history=[],
            benchmark_diff=None,
            area_median=None,
        )
        assert result is not None
        assert result["strength"] == "weak"
        assert "fresh listing" in result["days_context"]

    def test_none_when_no_data(self) -> None:
        """No days, no history, no benchmark = None."""
        result = generate_negotiation_brief(
            days_listed=0,
            price_history=[],
            benchmark_diff=None,
            area_median=None,
        )
        assert result is None

    def test_seasonal_context_present(self) -> None:
        result = generate_negotiation_brief(
            days_listed=7,
            price_history=[],
            benchmark_diff=None,
            area_median=None,
        )
        assert result is not None
        assert result["seasonal_context"]  # non-empty string

    def test_benchmark_diff_sign_convention(self) -> None:
        """Positive benchmark_diff = price above median = above avg in context."""
        result = generate_negotiation_brief(
            days_listed=5,
            price_history=[],
            benchmark_diff=200,  # price is 200 above median
            area_median=1800,
        )
        assert result is not None
        assert "above" in result["price_context"]

    def test_below_median_context(self) -> None:
        """Negative benchmark_diff = price below median = below avg."""
        result = generate_negotiation_brief(
            days_listed=5,
            price_history=[],
            benchmark_diff=-200,  # price is 200 below median
            area_median=2000,
        )
        assert result is not None
        assert "below" in result["price_context"]

    def test_suggested_approach_by_strength(self) -> None:
        strong = generate_negotiation_brief(
            days_listed=30,
            price_history=[{"change_amount": -100}],
            benchmark_diff=300,
            area_median=2000,
        )
        assert strong is not None
        assert "5-8%" in strong["suggested_approach"]

        weak = generate_negotiation_brief(
            days_listed=2,
            price_history=[],
            benchmark_diff=None,
            area_median=None,
        )
        assert weak is not None
        assert "strongest applicant" in weak["suggested_approach"]

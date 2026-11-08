"""TokenUsage: the analyzer's in-memory token and cost estimate.

The durable paid-attempt ledger, not this estimate, is authoritative for spend.
"""

from types import SimpleNamespace

import pytest

from home_finder.analysis.analyzer import TokenUsage


class TestTokenUsage:
    def test_defaults_to_zero(self) -> None:
        usage = TokenUsage()
        assert usage.input_tokens == 0
        assert usage.output_tokens == 0
        assert usage.cache_read_tokens == 0
        assert usage.cache_creation_tokens == 0
        assert usage.estimated_cost_usd == 0.0

    @pytest.mark.parametrize(
        ("counters", "cost_usd"),
        [
            # 500k * $3/MTok input
            ({"input_tokens": 500_000}, 1.5),
            # (4000*3 + 1000*15 + 3000*0.30) / 1M, a typical property
            ({"input_tokens": 4000, "output_tokens": 1000, "cache_read_tokens": 3000}, 0.0279),
            # $3 input + $15 output + $0.30 cache read + $3.75 five-minute cache write
            (
                {
                    "input_tokens": 1_000_000,
                    "output_tokens": 1_000_000,
                    "cache_read_tokens": 1_000_000,
                    "cache_creation_tokens": 1_000_000,
                },
                22.05,
            ),
        ],
    )
    def test_prepopulated_counters_are_priced_at_sonnet_46_rates(self, counters, cost_usd) -> None:
        assert TokenUsage(**counters).estimated_cost_usd == pytest.approx(cost_usd, abs=1e-9)

    def test_add_from_response_basic(self) -> None:
        usage = TokenUsage()

        class FakeUsage:
            input_tokens = 100
            output_tokens = 50
            cache_read_input_tokens = 200
            cache_creation_input_tokens = 30

        usage.add_from_response(FakeUsage())
        assert usage.input_tokens == 100
        assert usage.output_tokens == 50
        assert usage.cache_read_tokens == 200
        assert usage.cache_creation_tokens == 30

    def test_add_from_response_accumulates(self) -> None:
        usage = TokenUsage()

        class FakeUsage:
            input_tokens = 100
            output_tokens = 50
            cache_read_input_tokens = 0
            cache_creation_input_tokens = 0

        usage.add_from_response(FakeUsage())
        usage.add_from_response(FakeUsage())

        assert usage.input_tokens == 200
        assert usage.output_tokens == 100

    def test_cache_write_durations_keep_their_distinct_prices(self) -> None:
        usage = TokenUsage()
        usage.add_from_response(
            SimpleNamespace(
                cache_creation_input_tokens=1_000_000,
                cache_creation=SimpleNamespace(ephemeral_1h_input_tokens=250_000),
            )
        )
        assert usage.cache_creation_tokens == 1_000_000
        assert usage.cache_creation_1h_tokens == 250_000
        assert usage.estimated_cost_usd == pytest.approx(0.75 * 3.75 + 0.25 * 6)

    def test_add_from_response_handles_missing_attrs(self) -> None:
        usage = TokenUsage(input_tokens=10)

        class MinimalUsage:
            input_tokens = 5

        usage.add_from_response(MinimalUsage())
        assert usage.input_tokens == 15
        assert usage.output_tokens == 0
        assert usage.cache_read_tokens == 0
        assert usage.cache_creation_tokens == 0

    def test_add_from_response_handles_none_values(self) -> None:
        usage = TokenUsage()

        class NoneUsage:
            input_tokens = None
            output_tokens = 50
            cache_read_input_tokens = None
            cache_creation_input_tokens = None

        usage.add_from_response(NoneUsage())
        assert usage.input_tokens == 0
        assert usage.output_tokens == 50
        assert usage.cache_read_tokens == 0
        assert usage.cache_creation_tokens == 0

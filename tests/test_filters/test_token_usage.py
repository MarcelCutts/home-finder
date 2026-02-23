"""Tests for TokenUsage dataclass in quality.py."""

import pytest

from home_finder.filters.quality import TokenUsage


class TestTokenUsage:
    def test_defaults_to_zero(self) -> None:
        usage = TokenUsage()
        assert usage.input_tokens == 0
        assert usage.output_tokens == 0
        assert usage.cache_read_tokens == 0
        assert usage.cache_creation_tokens == 0

    def test_estimated_cost_zero_when_empty(self) -> None:
        usage = TokenUsage()
        assert usage.estimated_cost_usd == 0.0

    def test_estimated_cost_calculation(self) -> None:
        usage = TokenUsage(
            input_tokens=1_000_000,
            output_tokens=1_000_000,
            cache_read_tokens=1_000_000,
            cache_creation_tokens=1_000_000,
        )
        # $3 input + $15 output + $0.30 cache read + $3.75 cache write = $22.05
        assert usage.estimated_cost_usd == pytest.approx(22.05)

    def test_estimated_cost_input_only(self) -> None:
        usage = TokenUsage(input_tokens=500_000)
        # 500k * $3/MTok = $1.50
        assert usage.estimated_cost_usd == pytest.approx(1.5)

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

    def test_cost_matches_sonnet_pricing(self) -> None:
        """Verify against documented Sonnet pricing."""
        # Typical per-property usage: ~4k input, ~1k output, ~3k cache read
        usage = TokenUsage(
            input_tokens=4000,
            output_tokens=1000,
            cache_read_tokens=3000,
        )
        # (4000*3 + 1000*15 + 3000*0.30) / 1M = (12000+15000+900)/1M = 0.0279
        assert usage.estimated_cost_usd == pytest.approx(0.0279, abs=1e-6)

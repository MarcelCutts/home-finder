"""Published list prices, USD per million tokens, checked 2026-10-21.

Anthropic: platform.claude.com/docs/en/about-claude/pricing (5-minute cache writes).
OpenAI: developers.openai.com/api/docs/pricing (standard tier, prompts <=272K,
30-minute cache writes). Estimates only; provider invoices are authoritative.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Rate:
    input: float
    output: float
    cached_input: float
    cache_write: float | None = None  # None: writes billed as ordinary input
    # Haiku 5.5 charges 5x for the whole request once the prompt exceeds 100K.
    long_prompt_threshold: int | None = None
    long_prompt_multiplier: float = 1.0


ANTHROPIC: dict[str, Rate] = {
    "claude-fable-5-1": Rate(10.0, 50.0, 0.25, 12.50),
    "claude-opus-5-5": Rate(4.0, 20.0, 0.20, 5.0),
    "claude-sonnet-5-5": Rate(2.0, 10.0, 0.10, 2.50),
    "claude-haiku-5-5": Rate(0.10, 0.50, 0.01, 0.125, 100_000, 5.0),
    "claude-sonnet-4-6": Rate(3.0, 15.0, 0.30, 3.75),
}

OPENAI: dict[str, Rate] = {
    "gpt-6-astra": Rate(10.0, 50.0, 1.00, 12.50),
    "gpt-6.1-sol": Rate(2.0, 10.0, 0.10, 2.50),
    "gpt-6-sol": Rate(2.0, 10.0, 0.20, 2.50),
    "gpt-6-luna": Rate(0.10, 0.50, 0.01, 0.125),
    "gpt-5.6-sol": Rate(4.0, 20.0, 0.40, 5.0),  # promotional through >= 2026-11-21
    "gpt-5.6-terra": Rate(2.0, 12.0, 0.20, 2.50),
    "gpt-5.6-luna": Rate(0.20, 1.20, 0.02, 0.25),
}

# Multipliers on the standard rate for processing tiers.
TIER_MULTIPLIER: dict[tuple[str, str], float] = {
    ("openai", "flex"): 0.5,
    ("openai", "fast"): 2.0,
    ("openai", "priority"): 2.0,
    ("openai", "ultrafast"): 6.0,
    ("anthropic", "fast"): 2.0,  # Opus 5.5 fast mode: $8/$40
}


def rate_for(spec: Any) -> Rate:
    table = ANTHROPIC if spec.provider == "anthropic" else OPENAI
    if spec.model not in table:
        raise KeyError(f"unpriced model {spec.provider}:{spec.model}")
    return table[spec.model]


def cost_usd(
    spec: Any,
    *,
    uncached_input: int,
    cached_input: int = 0,
    cache_write: int = 0,
    output: int = 0,
    service_tier: str | None = None,
) -> float:
    rate = rate_for(spec)
    write_rate = rate.cache_write if rate.cache_write is not None else rate.input
    total = (
        uncached_input * rate.input
        + cached_input * rate.cached_input
        + cache_write * write_rate
        + output * rate.output
    ) / 1_000_000
    prompt = uncached_input + cached_input + cache_write
    if rate.long_prompt_threshold and prompt > rate.long_prompt_threshold:
        total *= rate.long_prompt_multiplier
    if service_tier:
        total *= TIER_MULTIPLIER.get((spec.provider, service_tier), 1.0)
    return total

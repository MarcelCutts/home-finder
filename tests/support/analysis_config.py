"""Explicit model configuration and spend policy for offline analysis tests."""

from home_finder.models.analysis import AnalysisConfig
from home_finder.models.spend import (
    SpendPolicy,
)
from tests.support.analysis_responses import SONNET55


def configuration(visual=SONNET55, evaluation=SONNET55, **overrides):
    phase = {"sdk_retries": 0, "input_token_limit": 140_000, "max_output_tokens": 8192}
    return AnalysisConfig.model_validate(
        {
            "visual": {**phase, "model": visual, **overrides},
            "evaluation": {**phase, "model": evaluation, **overrides},
        }
    )


def policy(**overrides):
    return SpendPolicy.model_validate(
        {"per_call_usd": "1", "daily_usd": "10", "monthly_usd": "10", **overrides}
    )

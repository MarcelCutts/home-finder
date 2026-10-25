"""Explicit paid-attempt, policy, and settlement fixtures for ledger tests."""

import hashlib

from home_finder.analysis.analyzer import PropertyQualityAnalyzer
from home_finder.models.spend import (
    AttemptSpec,
    BillableUsage,
    SpendPolicy,
    UsageSettlement,
)


def policy(**overrides):
    return SpendPolicy(
        **{
            "per_call_usd": "7",
            "daily_usd": "20",
            "monthly_usd": "100",
            "max_in_flight": 5,
            **overrides,
        }
    )


def spec(key="fixture", phase="visual"):
    digest = hashlib.sha256(key.encode()).hexdigest()
    return AttemptSpec(
        operation_key=digest,
        request_sha256=digest,
        property_id="fixture:1",
        phase=phase,
        provenance=PropertyQualityAnalyzer("fixture")._analysis_provenance(),
    )


def settlement(**overrides):
    return UsageSettlement(
        usage=BillableUsage(
            input_tokens=100,
            output_tokens=200,
            cache_write_5m_tokens=10,
            cache_write_1h_tokens=20,
            cache_read_tokens=300,
        ),
        result="refusal",
        request_id="fixture-request",
        stop_reason="refusal",
        **overrides,
    )

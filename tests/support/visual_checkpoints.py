"""Construct offline checkpoint clients and inspect persisted visual observations."""

import httpx2

from home_finder.analysis.analyzer import PropertyQualityAnalyzer
from home_finder.models.analysis import AnalysisConfig
from home_finder.models.spend import SpendPolicy

CONTENT = [{"type": "text", "text": "Visual checkpoint fixture"}]


def config():
    phase = {"sdk_retries": 0, "input_token_limit": 140000, "max_output_tokens": 8192}
    return AnalysisConfig.model_validate({"visual": phase, "evaluation": phase})


def policy(**changes):
    return SpendPolicy.model_validate(
        {"per_call_usd": "1", "daily_usd": "10", "monthly_usd": "10", **changes}
    )


def unavailable():
    return httpx2.Response(
        503, json={"type": "error", "error": {"type": "api_error", "message": "fixture"}}
    )


def quality(storage, respond, *, settings=None, budget=None):
    return PropertyQualityAnalyzer(
        "fixture",
        analysis_config=settings or config(),
        spend_repository=storage.spend,
        spend_policy=budget or policy(),
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(respond)),
    )


async def checkpoint_rows(storage):
    async with storage._read_connection() as conn:
        cursor = await conn.execute("SELECT * FROM visual_checkpoints")
        return [dict(row) for row in await cursor.fetchall()]

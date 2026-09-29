"""Batch experiments must not silently inherit production scope or paid retries."""

import json
import os
import subprocess
import sys
from decimal import Decimal

import pytest

from home_finder.pipeline.smoke import _bounded_spend_policy, run_batch_smoke


@pytest.mark.parametrize("budget", ["10.000000001", "NaN", "-1"])
def test_batch_budget_rejects_invalid_caps(budget):
    with pytest.raises(ValueError):
        _bounded_spend_policy(budget, max_properties=50, maximum_usd="10")


@pytest.mark.parametrize(
    "size,sources,areas",
    [
        (51, {"rightmove"}, ["E8"]),
        (0, {"rightmove"}, ["E8"]),
        (49, {"rightmove", "onthemarket"}, ["E8"]),
        (50, set(), ["E8"]),
        (50, {"invalid"}, ["E8"]),
        (50, {"rightmove"}, []),
        (50, {"rightmove"}, ["London"]),
    ],
)
async def test_invalid_scope_creates_no_output(tmp_path, size, sources, areas):
    output = tmp_path / "invalid"
    with pytest.raises(ValueError):
        await run_batch_smoke(
            output, sources=sources, areas=areas, max_properties=size, budget_usd="0"
        )
    assert not output.exists()


@pytest.mark.parametrize("selected", [50, 51])
def test_fresh_batch_limits_and_selection_audit(tmp_path, selected):
    # A separate process avoids structlog's cached loggers crossing experiment
    # boundaries. No portal/model call is made; the real isolated DB is used.
    script = """
import asyncio,functools,sys
from pathlib import Path
from home_finder.config import Settings
from home_finder.logging import get_logger
from home_finder.pipeline import smoke
smoke.Settings = functools.partial(Settings, _env_file=None)
async def pipeline(settings, **kwargs):
    assert kwargs['dry_run'] and kwargs['max_per_scraper'] == 25
    assert kwargs['only_scrapers'] == {'rightmove', 'onthemarket'}
    assert settings.max_analysis_per_run == 50
    assert settings.quality_concurrency == 1
    assert not settings.telegram_bot_token.get_secret_value()
    assert not settings.traveltime_api_key.get_secret_value()
    assert settings.analysis_spend_policy.max_total_attempts == 100
    assert settings.analysis_spend_policy.max_attempts_per_operation == 1
    assert settings.analysis_spend_policy.lifetime_usd == 10
    assert settings.analysis_config.visual.sdk_retries == 0
    assert settings.analysis_config.evaluation.sdk_retries == 0
    assert not (await kwargs['storage'].get_all_known_source_ids())
    get_logger('fixture').info('scraping_summary', total_found=int(sys.argv[2]), by_source={})
smoke.run_pipeline = pipeline
asyncio.run(smoke.run_batch_smoke(
    Path(sys.argv[1]), sources={'rightmove', 'onthemarket'}, areas=['E8', 'E9'],
    max_properties=50, budget_usd='10',
))
"""
    output = tmp_path / "batch"
    result = subprocess.run(
        [sys.executable, "-c", script, str(output), str(selected)],
        env={**os.environ, "HOME_FINDER_ANTHROPIC_API_KEY": "fixture-secret"},
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads((output / "report.json").read_text())
    assert report["status"] == ("incomplete" if selected == 50 else "failed")
    assert report["checks"]["selected_input_limit"] == (selected <= 50)
    assert report["target_reached"] == (selected == 50)
    assert Decimal(report["spend_policy"]["lifetime_usd"]) == 10
    assert report["spending"]["attempts"] == 0
    assert "fixture-secret" not in (output / "events.jsonl").read_text()
    assert "fixture-secret" not in (output / "report.json").read_text()

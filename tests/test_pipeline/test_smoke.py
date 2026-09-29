"""The smoke CLI records incomplete/failed runs without sending notifications."""

import json
import os
import subprocess
import sys
from decimal import Decimal

import pytest

from home_finder.pipeline.smoke import EvidenceLog, audit_report, small_spend_policy


def test_smoke_budget_is_zero_by_default_and_cannot_exceed_one_dollar():
    assert small_spend_policy("0").lifetime_usd == 0
    assert small_spend_policy("1").max_total_attempts == 2
    for invalid in ("1.01", "NaN", "-1"):
        with pytest.raises(ValueError):
            small_spend_policy(invalid)


def test_evidence_redacts_credentials_and_payloads_before_writing(tmp_path):
    log = EvidenceLog(tmp_path / "events.jsonl", secrets=("fixture-secret",))
    log(
        None,
        "info",
        {
            "event": "fixture",
            "api_key": "fixture-secret",
            "nested": {"prompt": "private", "error": "failed fixture-secret"},
        },
    )
    log.close()
    saved = (tmp_path / "events.jsonl").read_text()
    assert "fixture-secret" not in saved and "private" not in saved
    assert "failed [redacted]" in saved
    assert (tmp_path / "events.jsonl").stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("mode", ["empty", "failure", "cancelled", "storage_failure"])
def test_real_database_cli_retains_stop_reason_and_no_provider_work(tmp_path, mode):
    # The actual public pipeline is exercised in the integration suite. Here
    # replace only that entry point in a fresh process to test CLI evidence and
    # process-scoped logging without inherited fixture logging configuration.
    script = """
import asyncio,functools,json,sys
from pathlib import Path
from home_finder.config import Settings
from home_finder.logging import get_logger
from home_finder.pipeline import smoke
smoke.Settings = functools.partial(Settings, _env_file=None)
async def pipeline(settings, **kwargs):
    assert kwargs['dry_run'] and kwargs['max_per_scraper'] == 1
    assert kwargs['only_scrapers'] == {'rightmove'}
    assert settings.max_analysis_per_run == settings.quality_concurrency == 1
    assert not settings.telegram_bot_token.get_secret_value()
    assert not settings.traveltime_api_key.get_secret_value()
    assert settings.analysis_config.visual.sdk_retries == 0
    get_logger('fixture').info('pipeline_discovery_finished', scraped=0, collection_status='failed')
    if sys.argv[2] == 'failure': raise RuntimeError('fixture-secret')
    if sys.argv[2] == 'cancelled': raise asyncio.CancelledError()
smoke.run_pipeline = pipeline
if sys.argv[2] == 'storage_failure':
    def unavailable(*args, **kwargs): raise RuntimeError('fixture-secret')
    smoke.PropertyStorage = unavailable
try:
    asyncio.run(smoke.run_smoke(Path(sys.argv[1]), source='rightmove', area='E8', budget_usd='0'))
except (RuntimeError,asyncio.CancelledError): pass
"""
    destination = tmp_path / "smoke"
    result = subprocess.run(
        [sys.executable, "-c", script, str(destination), mode],
        env={**os.environ, "HOME_FINDER_ANTHROPIC_API_KEY": "fixture-secret"},
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads((destination / "report.json").read_text())
    assert report["status"] == ("incomplete" if mode == "empty" else "failed")
    assert "fixture-secret" not in (destination / "events.jsonl").read_text()
    assert report["scope"]["max_model_images_including_floorplan"] == 4
    assert len(report["runtime"]["application_sources_sha256"]) == 64
    assert destination.stat().st_mode & 0o777 == 0o700
    if mode == "storage_failure":
        assert report["error_type"] == "RuntimeError"
        assert any(event["event"] == "smoke_evidence_failed" for event in report["diagnostics"])
        return
    assert report["spending"]["attempts"] == report["spending"]["held_nanousd"] == 0
    assert report["foreign_key_errors"] == 0 and report["integrity"] == ["ok"]
    assert report["counts"]["properties"] == 0
    assert any(event["event"] == "pipeline_discovery_finished" for event in report["diagnostics"])
    if mode != "empty":
        assert report["error_type"] == ("RuntimeError" if mode == "failure" else "CancelledError")
    assert all(report["checks"].values())


@pytest.mark.parametrize(
    "section,field,value",
    [
        ("counts", "properties", 2),
        ("counts", "paid_attempts", 3),
        ("spending", "lifetime_accounted_nanousd", 1_000_000_001),
        ("spending", "held_nanousd", 1),
        ("spending", "over_reservation_count", 1),
        ("spending", "unresolved", [{"state": "unknown"}]),
        (None, "integrity", ["corrupt"]),
        (None, "properties", [{"notification_status": "sent"}]),
    ],
)
def test_report_rejects_budget_scope_and_integrity_violations(section, field, value):
    # A completed assessment alone is not enough to certify the smoke's scope.
    report = {
        "assessments": [{"overall_rating": 4}],
        "counts": {"properties": 1, "quality_analyses": 1, "paid_attempts": 2},
        "spending": {
            "attempts": 2,
            "lifetime_accounted_nanousd": 1_000_000_000,
            "held_nanousd": 0,
            "over_reservation_count": 0,
            "in_flight": 0,
            "unresolved": [],
        },
        "integrity": ["ok"],
        "foreign_key_errors": 0,
        "properties": [{"notification_status": "pending"}],
    }
    assert all(audit_report(report, budget_usd=Decimal("1")).values())
    (report if section is None else report[section])[field] = value
    assert not all(audit_report(report, budget_usd=Decimal("1")).values())

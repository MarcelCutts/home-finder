"""One-property pipeline smoke with private evidence and an explicit model budget.

Run once per new output directory. This invokes the real pipeline in Telegram
dry-run mode. A zero budget is the default; positive model spend is opt-in.
"""

import argparse
import asyncio
import hashlib
import json
import logging
import os
import platform
import re
import sys
import time
from collections.abc import MutableMapping
from datetime import UTC, datetime
from decimal import Decimal
from importlib.metadata import version
from pathlib import Path
from typing import Any

import structlog
from pydantic import SecretStr

from home_finder.config import Settings
from home_finder.db import PropertyStorage
from home_finder.logging import configure_logging, get_logger
from home_finder.main import run_pipeline
from home_finder.models.analysis import AnalysisConfig
from home_finder.models.spend import SpendPolicy, nanousd

logger = get_logger(__name__)
_SECRET_FIELDS = {
    "authorization",
    "x-api-key",
    "api_key",
    "headers",
    "prompt",
    "body",
    "content",
    "response",
}


def small_analysis_config() -> AnalysisConfig:
    phase = {
        "model": "claude-sonnet-5-5",
        "reasoning": "between_tools",
        "effort": "low",
        "sdk_retries": 0,
        "input_token_limit": 200_000,
        "max_output_tokens": 8192,
        "request_timeout_seconds": 120,
    }
    return AnalysisConfig.model_validate({"visual": phase, "evaluation": phase})


def small_spend_policy(budget_usd: str) -> SpendPolicy:
    policy = SpendPolicy(
        per_call_usd=Decimal(budget_usd),
        daily_usd=Decimal(budget_usd),
        monthly_usd=Decimal(budget_usd),
        lifetime_usd=Decimal(budget_usd),
        max_total_attempts=2,
        max_in_flight=1,
        max_attempts_per_operation=1,
    )
    if not Decimal("0") <= policy.daily_usd <= Decimal("1"):
        raise ValueError("This one-property smoke permits a total budget of at most US$1")
    return policy


class EvidenceLog:
    """Record structured events without prompts, credentials or unbounded values."""

    def __init__(self, path: Path, *, secrets: tuple[str, ...]) -> None:
        self.path = path
        self.secrets = tuple(secret for secret in secrets if secret)
        self.events: list[dict[str, Any]] = []
        self._file = path.open("x", encoding="utf-8")
        path.chmod(0o600)

    def clean(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {
                str(key): "[redacted]" if str(key).lower() in _SECRET_FIELDS else self.clean(item)
                for key, item in value.items()
            }
        if isinstance(value, (tuple, list)):
            return [self.clean(item) for item in value[:100]]
        if isinstance(value, str):
            for secret in self.secrets:
                value = value.replace(secret, "[redacted]")
            return value[:2000]
        if value is None or isinstance(value, (int, float, bool)):
            return value
        return self.clean(str(value))

    def __call__(
        self, _logger: Any, _method: str, event: MutableMapping[str, Any]
    ) -> dict[str, Any]:
        cleaned: dict[str, Any] = self.clean(dict(event))
        self._file.write(json.dumps(cleaned, sort_keys=True, allow_nan=False) + "\n")
        self._file.flush()
        if len(self.events) < 20_000:
            self.events.append(cleaned)
        return cleaned

    def close(self) -> None:
        self._file.flush()
        os.fsync(self._file.fileno())
        self._file.close()


def _write_report(path: Path, report: dict[str, Any]) -> None:
    with path.open("x", encoding="utf-8") as handle:
        path.chmod(0o600)
        json.dump(report, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def audit_report(report: dict[str, Any], *, budget_usd: Decimal) -> dict[str, bool]:
    """Check operating invariants independently of whether an assessment exists."""
    counts, spend = report["counts"], report["spending"]
    return {
        "at_most_one_property": counts["properties"] <= 1,
        "at_most_one_assessment": counts["quality_analyses"] <= 1,
        "at_most_two_model_attempts": counts["paid_attempts"] == spend["attempts"] <= 2,
        "within_total_model_budget": spend["lifetime_accounted_nanousd"] + spend["held_nanousd"]
        <= nanousd(budget_usd),
        "no_exceeded_reservations": spend["over_reservation_count"] == 0,
        "no_unresolved_model_attempts": spend["in_flight"] == 0 and not spend["unresolved"],
        "database_integrity": report["integrity"] == ["ok"] and report["foreign_key_errors"] == 0,
        "no_recorded_notifications": all(
            prop["notification_status"] != "sent" for prop in report["properties"]
        ),
    }


def _application_sources_sha256() -> str:
    root = Path(__file__).resolve().parents[1]
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*.py")):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


async def run_smoke(output: Path, *, source: str, area: str, budget_usd: str) -> dict[str, Any]:
    """Invoke once per CLI process, with a fresh database and process-scoped logging."""
    if source not in {"rightmove", "openrent", "zoopla", "onthemarket"}:
        raise ValueError("Unknown source")
    if re.fullmatch(r"[A-Za-z]{1,2}[0-9][0-9A-Za-z]?", area) is None:
        raise ValueError("Smoke scope must be exactly one postcode outcode")
    policy = small_spend_policy(budget_usd)
    inherited = Settings()
    if policy.daily_usd and not inherited.anthropic_api_key.get_secret_value():
        raise ValueError("A positive model budget requires an Anthropic API key")
    output = output.expanduser().resolve()
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        database_path=str(output / "properties.db"),
        search_areas=area.lower(),
        min_price=inherited.min_price,
        max_price=inherited.max_price,
        min_bedrooms=inherited.min_bedrooms,
        max_bedrooms=inherited.max_bedrooms,
        furnish_types=inherited.furnish_types,
        min_bathrooms=inherited.min_bathrooms,
        include_let_agreed=inherited.include_let_agreed,
        telegram_bot_token=SecretStr(""),
        telegram_chat_id=0,
        traveltime_app_id="",
        traveltime_api_key=SecretStr(""),
        proxy_url="",
        anthropic_api_key=inherited.anthropic_api_key,
        enable_quality_filter=True,
        analysis_config=small_analysis_config(),
        analysis_spend_policy=policy,
        max_analysis_per_run=1,
        quality_concurrency=1,
        quality_filter_max_images=4,
        require_floorplan=False,
        property_analysis_timeout=300,
    )
    configure_logging(json_output=True, level=logging.DEBUG)
    evidence = EvidenceLog(
        output / "events.jsonl", secrets=(inherited.anthropic_api_key.get_secret_value(),)
    )
    processors = list(structlog.get_config()["processors"])
    structlog.configure(processors=[*processors[:-1], evidence, processors[-1]])
    smoke_id = output.name
    structlog.contextvars.bind_contextvars(smoke_id=smoke_id, dry_run=True)
    started = time.monotonic()
    report: dict[str, Any] = {
        "schema_version": 2,
        "smoke_id": smoke_id,
        "started_at": datetime.now(UTC).isoformat(),
        "scope": {
            "source": source,
            "area": area.upper(),
            "max_properties": 1,
            "max_gallery_images": 4,
            "max_model_images_including_floorplan": 4,
            "floorplan_required": False,
            "telegram": False,
            "commute_api": False,
            "search_criteria": {
                "min_price": settings.min_price,
                "max_price": settings.max_price,
                "min_bedrooms": settings.min_bedrooms,
                "max_bedrooms": settings.max_bedrooms,
                "furnish_types": settings.furnish_types,
                "min_bathrooms": settings.min_bathrooms,
                "include_let_agreed": settings.include_let_agreed,
            },
        },
        "spend_policy": policy.model_dump(mode="json"),
        "analysis_config": settings.analysis_config.model_dump(mode="json")
        if settings.analysis_config
        else None,
        "runtime": {
            "python": platform.python_version(),
            "anthropic": version("anthropic"),
            "home_finder": version("home-finder"),
            "application_sources_sha256": _application_sources_sha256(),
        },
        "status": "started",
    }
    _write_report(output / "plan.json", report)
    logger.info("smoke_started", scope=report["scope"], spend_policy=report["spend_policy"])
    interrupted: BaseException | None = None
    try:
        async with PropertyStorage(settings.database_path) as storage:
            try:
                async with asyncio.timeout(600):
                    await run_pipeline(
                        settings,
                        max_per_scraper=1,
                        only_scrapers={source},
                        dry_run=True,
                        storage=storage,
                    )
            except BaseException as exc:
                interrupted = exc
                report["error_type"] = type(exc).__name__
                logger.error("smoke_interrupted", error_type=type(exc).__name__)
            finally:
                report["spending"] = await storage.spend.report()
                async with storage._read_connection() as conn:
                    counts = {}
                    for table in (
                        "source_listings",
                        "source_observations",
                        "properties",
                        "property_images",
                        "quality_analyses",
                        "paid_attempts",
                    ):
                        async with conn.execute(f"SELECT COUNT(*) FROM {table}") as cursor:
                            row = await cursor.fetchone()
                            assert row is not None
                            counts[table] = row[0]
                    async with conn.execute(
                        "SELECT unique_id, enrichment_status, notification_status, "
                        "analysis_attempts "
                        "FROM properties ORDER BY unique_id"
                    ) as cursor:
                        report["properties"] = [dict(row) for row in await cursor.fetchall()]
                    async with conn.execute(
                        "SELECT property_unique_id, overall_rating FROM quality_analyses"
                    ) as cursor:
                        report["assessments"] = [dict(row) for row in await cursor.fetchall()]
                    async with conn.execute("PRAGMA integrity_check") as cursor:
                        report["integrity"] = [row[0] for row in await cursor.fetchall()]
                    async with conn.execute("PRAGMA foreign_key_check") as cursor:
                        report["foreign_key_errors"] = sum(1 for _ in await cursor.fetchall())
                report["counts"] = counts
        complete = (
            len(report.get("assessments", [])) == 1
            and report["assessments"][0]["overall_rating"] is not None
        )
        report["checks"] = audit_report(report, budget_usd=policy.daily_usd)
        report["status"] = (
            "failed"
            if interrupted or not all(report["checks"].values())
            else "analyzed"
            if complete
            else "incomplete"
        )
        if not all(report["checks"].values()):
            logger.error("smoke_invariants_failed", checks=report["checks"])
    except BaseException as exc:
        report["status"] = "failed"
        report["error_type"] = type(exc).__name__
        logger.error("smoke_evidence_failed", error_type=type(exc).__name__)
        raise
    finally:
        report["completed_at"] = datetime.now(UTC).isoformat()
        report["duration_seconds"] = round(time.monotonic() - started, 3)
        report["diagnostics"] = [
            event
            for event in evidence.events
            if event.get("event")
            in {
                "pipeline_started",
                "pipeline_discovery_finished",
                "analysis_queue_selected",
                "analyzing_property",
                "visual_analysis_complete",
                "property_analysis_duration",
                "scraper_summary",
                "scraper_metrics",
                "criteria_filter_summary",
                "location_filter_summary",
                "enrichment_summary",
                "cross_run_property_fates",
                "smoke_interrupted",
                "analysis_dispatch_deferred",
                "analysis_output_failed",
                "analysis_attempt_reserved",
                "analysis_attempt_dispatched",
                "analysis_attempt_settled",
                "analysis_attempt_unknown",
                "pipeline_timing_summary",
                "dry_run_complete",
            }
            or event.get("level") in {"warning", "error", "critical"}
        ]
        logger.info(
            "smoke_finished",
            status=report["status"],
            duration_seconds=report["duration_seconds"],
            counts=report.get("counts"),
            spending=report.get("spending"),
            checks=report.get("checks"),
        )
        evidence.close()
        report["events_sha256"] = hashlib.sha256(evidence.path.read_bytes()).hexdigest()
        _write_report(output / "report.json", evidence.clean(report))
        structlog.configure(processors=processors)
        structlog.contextvars.unbind_contextvars("smoke_id", "dry_run")
    if interrupted is not None:
        raise interrupted
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--source", choices=["rightmove", "openrent", "zoopla", "onthemarket"], default="rightmove"
    )
    parser.add_argument("--area", default="E8")
    parser.add_argument(
        "--budget-usd", default="0", help="Explicit total model budget, at most US$1; default 0"
    )
    args = parser.parse_args()
    os.umask(0o077)
    report = asyncio.run(
        run_smoke(args.output, source=args.source, area=args.area, budget_usd=args.budget_usd)
    )
    print(json.dumps({"status": report["status"], "report": str(args.output / "report.json")}))
    sys.exit(0 if report["status"] == "analyzed" else 1)


if __name__ == "__main__":
    main()

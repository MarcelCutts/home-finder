"""Replay retained bytes through application parsers without acquiring more data."""

import hashlib
import json
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from importlib.resources import files
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from bs4 import BeautifulSoup

from home_finder.models import Property, PropertySource
from home_finder.models.observations import (
    MAX_BATCH_BYTES,
    ObservationFields,
    ObservationParser,
    RawCaptureReference,
    SourceObservation,
    canonical_json,
)
from home_finder.pipeline.capture import _parser_fingerprint, _stop_reason, _write_private
from home_finder.pipeline.capture_evidence import VerifiedCapture, verified_capture
from home_finder.scrapers.detail_fetcher import DetailFetcher, DetailPageData
from home_finder.scrapers.observations import (
    detail_observation,
    request_context,
    search_observations,
)
from home_finder.scrapers.onthemarket import OnTheMarketScraper
from home_finder.scrapers.openrent import OpenRentScraper
from home_finder.scrapers.rightmove import RightmoveScraper
from home_finder.scrapers.zoopla import ZooplaScraper


class _ReplayDetailFetcher(DetailFetcher):
    """Use production extraction paths, replacing their complete fetch boundary."""

    def __init__(self, response: httpx.Response, observed_at: datetime | None = None) -> None:
        super().__init__()
        self._record_observations = False  # Re-parsing is not a new provider receipt.
        self.response = response
        self.captured_at = observed_at

    def _observed_detail(
        self,
        prop: Property,
        data: DetailPageData,
        observed_at: datetime,
        *,
        metadata: ObservationFields | None = None,
    ) -> DetailPageData:
        if self.captured_at is not None:
            data.source_observation = detail_observation(
                prop,
                data,
                observed_at=self.captured_at,
                metadata=metadata,
                request=request_context(str(self.response.request.url)),
            )
        return data

    async def _httpx_get_with_retry(self, url: str) -> httpx.Response:
        if url != str(self.response.request.url):
            raise ValueError("Replay parser requested a different URL")
        return self.response

    async def _curl_get_with_retry(
        self,
        url: str,
        *,
        min_interval: float = 3.0,
        throttle_name: str | None = None,
        timeout: int = 30,
    ) -> httpx.Response:
        return await self._httpx_get_with_retry(url)

    async def _get_client(self) -> httpx.AsyncClient:
        raise RuntimeError("Replay cannot create an HTTP client")

    async def _get_curl_session(self) -> Any:
        raise RuntimeError("Replay cannot create a curl session")

    async def download_image_bytes(self, url: str) -> bytes | None:
        raise RuntimeError("Replay cannot download media")


def _parse_search(source: PropertySource, html: str, url: str) -> list[Property] | None:
    soup = BeautifulSoup(html, "html.parser")
    match source:
        case PropertySource.RIGHTMOVE:
            return RightmoveScraper()._parse_search_results(soup, url)
        case PropertySource.OPENRENT:
            return OpenRentScraper()._parse_search_results(soup, url)
        case PropertySource.ONTHEMARKET:
            return OnTheMarketScraper()._parse_next_data(html)
        case PropertySource.ZOOPLA:
            scraper = ZooplaScraper()
            rsc = scraper._parse_rsc_properties(html)
            if rsc:
                return rsc
            cards = scraper._parse_search_results(soup, url)
            return cards if cards or rsc is None else rsc


async def replay(capture_dir: Path, output_root: Path) -> Path:
    """Verify response hashes, then write a separate versioned parser-result file.

    Parser success is not a correctness label or source qualification. Empty
    parser output cannot establish that a query has no matching homes.
    """
    with verified_capture(capture_dir) as capture:
        return await _replay_verified(capture, output_root)


def _bind_capture(
    observation: SourceObservation,
    reference: RawCaptureReference,
    index: int,
    implementation_sha256: str,
) -> SourceObservation:
    """Stable event identity per capture, parser implementation and record position."""
    assert observation.parser.code_sha256 is not None
    digest = hashlib.sha256((observation.parser.code_sha256 + ":" + implementation_sha256).encode())
    parser = ObservationParser(
        name=observation.parser.name + ".capture_replay",
        version="1",
        code_sha256=digest.hexdigest(),
    )
    event_id = hashlib.sha256(
        canonical_json([reference.model_dump(mode="json"), parser.model_dump(), index]).encode()
    ).hexdigest()
    payload = observation.model_dump(mode="json", exclude_none=True)
    payload.update(
        producer_id=f"capture-{reference.capture_mode}-replay-v1",
        event_id=event_id,
        parser=parser.model_dump(mode="json"),
        raw_capture=reference.model_dump(mode="json"),
    )
    return SourceObservation.model_validate(payload)


async def _replay_verified(capture: VerifiedCapture, output_root: Path) -> Path:
    manifest = capture.manifest
    plan = manifest.plan
    results: list[dict[str, Any]] = []
    normalized_bytes = 0
    now = datetime.now(UTC)
    parser_files = _parser_fingerprint()
    root = files("home_finder")
    for name in (
        "pipeline/capture.py",
        "pipeline/capture_replay.py",
        "pipeline/capture_evidence.py",
        "utils/listing_identity.py",
    ):
        parser_files[name] = hashlib.sha256(root.joinpath(name).read_bytes()).hexdigest()
    implementation_sha256 = hashlib.sha256(canonical_json(parser_files).encode()).hexdigest()
    for index, record in enumerate(manifest.attempts):
        request = plan.requests[index]
        result: dict[str, Any] = {
            "index": index,
            "observed_at": record.observed_at.isoformat() if record.observed_at else None,
            "observations": [],
            "observation_state": "not_extracted",
        }
        results.append(result)
        if record.body_file is None:
            result["outcome"] = "incomplete_capture"
            continue
        body = capture.body(index, now=now)
        result["body_sha256"] = record.body_sha256
        if record.state != "received":
            result["outcome"] = "incomplete_capture"
            continue
        assert record.status is not None
        stop = _stop_reason(record.status, record.response_headers, body)
        if stop:
            result.update(outcome="unusable_response", reason=stop)
            continue
        if record.observed_at is not None and record.observed_at > now + timedelta(minutes=5):
            raise ValueError("Capture observation time is implausibly in the future")
        response = httpx.Response(
            record.status,
            content=body,
            headers=record.response_headers,
            request=httpx.Request("GET", str(request.url)),
        )
        observations: list[SourceObservation] = []
        try:
            if request.kind == "search":
                properties = _parse_search(plan.source, response.text, str(request.url))
                if properties and len(properties) > 100:
                    raise ValueError("Capture replay exceeds 100 listings per response")
                result["outcome"] = "extracted" if properties else "empty_or_unrecognised"
                # Constructor defaults are not historical observations. Preserve the
                # capture's explicit observed_at separately, including unknown dates.
                result["properties"] = [
                    prop.model_dump(mode="json", exclude={"first_seen"})
                    for prop in properties or []
                ]
                if record.observed_at is not None:
                    observed, errors = search_observations(
                        properties or [],
                        observed_at=record.observed_at,
                        request=request_context(str(request.url)),
                    )
                    observations = [p.source_observation for p in observed if p.source_observation]
                    result["normalization_errors"] = errors
            elif request.kind == "detail":
                assert request.listing is not None
                fetcher = _ReplayDetailFetcher(response, record.observed_at)
                data = await fetcher.fetch_detail_page(request.listing)
                if data and data.source_observation:
                    observations = [data.source_observation]
                detail = asdict(data) if data else None
                if detail is not None:
                    detail.pop("source_observation", None)
                    if data is not None and data.floor_area_readings:
                        detail["floor_area_readings"] = [
                            item.model_dump(mode="json") for item in data.floor_area_readings
                        ]
                result["detail"] = detail
                result["outcome"] = (
                    "extracted" if detail and any(detail.values()) else "empty_or_unrecognised"
                )
            else:
                result["outcome"] = "reference_only"
        except Exception as exc:
            result.update(outcome="parser_error", error_type=type(exc).__name__)
            continue
        if observations:
            reference = capture.reference(index, body)
            for position, observation in enumerate(observations):
                event = _bind_capture(observation, reference, position, implementation_sha256)
                normalized_bytes += len(event.payload_json().encode())
                if normalized_bytes > MAX_BATCH_BYTES:
                    raise ValueError("Replay exceeds the 4 MB normalized observation bound")
                result["observations"].append(event.model_dump(mode="json", exclude_none=True))
            result["observation_state"] = f"exported_{manifest.mode}_evidence"
        elif record.observed_at is None and request.kind != "reference":
            result["observation_state"] = "unavailable_capture_time"
    report = {
        "schema_version": 2,
        "capture_manifest_sha256": capture.sha256,
        "capture_mode": manifest.mode,
        "capture_state": manifest.state,
        "raw_retention": (
            plan.raw_retention.model_dump(mode="json") if plan.raw_retention else None
        ),
        "parser_files": parser_files,
        "replayed_at": now.isoformat(),
        "human_labels": None,
        "live_requests_reserved": 0,
        "model_calls": 0,
        "commute_calls": 0,
        "notifications": 0,
        "results": results,
    }
    encoded = (json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()
    if len(encoded) > 16_000_000:
        raise ValueError("Replay output exceeds 16 MB")
    output_root = output_root.resolve()
    output_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    output_dir = output_root / ("replay-" + uuid4().hex)
    output_dir.mkdir(mode=0o700)
    _write_private(output_dir / "replay.json", encoded)
    return output_dir

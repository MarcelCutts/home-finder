"""Replay retained bytes through application parsers without acquiring more data."""

import hashlib
import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from bs4 import BeautifulSoup

from home_finder.models import Property, PropertySource
from home_finder.pipeline.capture import (
    CapturePlan,
    _parser_fingerprint,
    _stop_reason,
    _write_json,
)
from home_finder.scrapers.detail_fetcher import DetailFetcher
from home_finder.scrapers.onthemarket import OnTheMarketScraper
from home_finder.scrapers.openrent import OpenRentScraper
from home_finder.scrapers.rightmove import RightmoveScraper
from home_finder.scrapers.zoopla import ZooplaScraper


class _ReplayDetailFetcher(DetailFetcher):
    """Use production extraction paths, replacing their complete fetch boundary."""

    def __init__(self, response: httpx.Response) -> None:
        super().__init__()
        self.response = response

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
    capture_dir = capture_dir.resolve(strict=True)
    with (capture_dir / "manifest.json").open("rb") as stream:
        raw_manifest = stream.read(256_001)
    if len(raw_manifest) > 256_000:
        raise ValueError("Capture manifest exceeds 256,000 bytes")
    manifest = json.loads(raw_manifest)
    if manifest.get("schema_version") != 1 or manifest.get("mode") not in {"live", "fixture"}:
        raise ValueError("Replay requires a version 1 source-response capture")
    plan = CapturePlan.model_validate(manifest["plan"])
    attempts = manifest["attempts"]
    if len(attempts) > len(plan.requests):
        raise ValueError("Capture has more attempts than its plan")
    results: list[dict[str, Any]] = []
    for index, record in enumerate(attempts):
        request = plan.requests[index]
        if (
            record["index"] != index
            or record["url"] != str(request.url)
            or record["kind"] != request.kind
        ):
            raise ValueError("Capture attempt does not match its plan")
        result: dict[str, Any] = {"index": index, "observed_at": record.get("observed_at")}
        results.append(result)
        if "body_file" not in record:
            result["outcome"] = "incomplete_capture"
            continue
        if record["body_file"] != f"{index:02d}.body":
            raise ValueError("Unexpected response filename")
        path = (capture_dir / record["body_file"]).resolve(strict=True)
        if path.parent != capture_dir:
            raise ValueError("Response escapes its capture directory")
        with path.open("rb") as stream:
            body = stream.read(plan.max_response_bytes + 1)
        if (
            len(body) > plan.max_response_bytes
            or len(body) != record["body_bytes"]
            or hashlib.sha256(body).hexdigest() != record["body_sha256"]
        ):
            raise ValueError("Response bytes do not match the capture hash/size")
        result["body_sha256"] = record["body_sha256"]
        if record["state"] != "received":
            result["outcome"] = "incomplete_capture"
            continue
        headers = record["response_headers"]
        stop = _stop_reason(record["status"], headers, body)
        if stop:
            result.update(outcome="unusable_response", reason=stop)
            continue
        response = httpx.Response(
            record["status"],
            content=body,
            headers=headers,
            request=httpx.Request("GET", str(request.url)),
        )
        try:
            if request.kind == "search":
                properties = _parse_search(plan.source, response.text, str(request.url))
                result["outcome"] = "extracted" if properties else "empty_or_unrecognised"
                # Constructor defaults are not historical observations. Preserve the
                # capture's explicit observed_at separately, including unknown dates.
                result["properties"] = [
                    prop.model_dump(mode="json", exclude={"first_seen"})
                    for prop in properties or []
                ]
            elif request.kind == "detail":
                assert request.listing is not None
                fetcher = _ReplayDetailFetcher(response)
                data = await fetcher.fetch_detail_page(request.listing)
                result["detail"] = asdict(data) if data else None
                result["outcome"] = (
                    "extracted" if data and any(asdict(data).values()) else "empty_or_unrecognised"
                )
            else:
                result["outcome"] = "reference_only"
        except Exception as exc:
            result.update(outcome="parser_error", error_type=type(exc).__name__)
    output_root = output_root.resolve()
    output_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    output_dir = output_root / ("replay-" + uuid4().hex)
    output_dir.mkdir(mode=0o700)
    _write_json(
        output_dir / "replay.json",
        {
            "schema_version": 1,
            "capture_manifest_sha256": hashlib.sha256(raw_manifest).hexdigest(),
            "capture_mode": manifest["mode"],
            "capture_state": manifest["state"],
            "parser_files": _parser_fingerprint(),
            "replayed_at": datetime.now(UTC).isoformat(),
            "human_labels": None,
            "live_requests_reserved": 0,
            "model_calls": 0,
            "commute_calls": 0,
            "notifications": 0,
            "results": results,
        },
    )
    return output_dir

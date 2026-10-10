"""Isolated, bounded response capture. Never runs the application pipeline.

Use ``python -m home_finder.pipeline.capture --help``. Offline input is the
default; live requests require a dated source qualification in the plan.
"""

import argparse
import asyncio
import hashlib
import json
import os
import platform
import time
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any, Literal, Self
from urllib.parse import parse_qsl
from uuid import uuid4

import httpx
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, HttpUrl, model_validator

from home_finder.models import Property, PropertySource
from home_finder.pipeline.runner import runner_lock
from home_finder.utils.http_evidence import is_challenge

_HOSTS = {
    PropertySource.RIGHTMOVE: {"www.rightmove.co.uk"},
    PropertySource.ZOOPLA: {"www.zoopla.co.uk"},
    PropertySource.OPENRENT: {"www.openrent.com", "www.openrent.co.uk"},
    PropertySource.ONTHEMARKET: {"www.onthemarket.com"},
}
_RESPONSE_HEADERS = {"content-type", "retry-after", "cf-mitigated"}
_SENSITIVE_QUERY_KEYS = {
    "token",
    "key",
    "api_key",
    "password",
    "auth",
    "authorization",
    "signature",
}
_REQUEST_HEADERS = {
    "User-Agent": "HomeFinder-EvidenceCapture/1.0",
    "Accept": "text/html,application/json",
    "Accept-Encoding": "identity",
}


class CaptureQualification(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    source: PropertySource
    reference: str = Field(min_length=1, max_length=1000)
    acquisition: Literal[True]
    retention: Literal[True]
    expires_at: AwareDatetime


class CaptureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    url: HttpUrl
    kind: Literal["search", "detail", "reference"]
    scope: str = Field(min_length=1, max_length=500)
    listing: Property | None = None
    fixture: str | None = Field(default=None, max_length=200)
    fixture_status: int = Field(default=200, ge=100, le=599)
    fixture_observed_at: AwareDatetime | None = None
    fixture_headers: dict[str, str] = Field(default_factory=dict, max_length=20)


class CapturePlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    source: PropertySource
    requests: list[CaptureRequest] = Field(min_length=1, max_length=12)
    max_requests: int = Field(ge=1, le=12)
    max_response_bytes: int = Field(default=8_000_000, ge=1, le=8_000_000)
    request_timeout_seconds: float = Field(default=30, gt=0, le=60)
    delay_seconds: float = Field(default=3, ge=0, le=60)
    qualification: CaptureQualification | None = None

    @model_validator(mode="after")
    def validate_requests(self) -> Self:
        if len(self.requests) > self.max_requests:
            raise ValueError("Plan exceeds its request budget")
        for request in self.requests:
            url = request.url
            if (
                url.scheme != "https"
                or url.host not in _HOSTS[self.source]
                or url.port != 443
                or url.username
                or url.password
                or url.fragment
            ):
                raise ValueError("Capture requires an HTTPS URL on the selected portal")
            if any(k.lower() in _SENSITIVE_QUERY_KEYS for k, _ in parse_qsl(url.query or "")):
                raise ValueError("Credential-bearing URLs are not supported")
            if request.kind == "detail":
                if request.listing is None:
                    raise ValueError("Detail replay requires the retained search listing as input")
                if request.listing.source != self.source or request.listing.url != url:
                    raise ValueError("Detail URL/source must match its retained listing input")
            if request.fixture:
                path = Path(request.fixture)
                if path.is_absolute() or ".." in path.parts:
                    raise ValueError("Fixture paths must be relative to the fixture directory")
            if any(k.lower() not in _RESPONSE_HEADERS for k in request.fixture_headers):
                raise ValueError("Fixture headers must use the retained response-header allowlist")
        return self


def _write_private(path: Path, data: bytes) -> None:
    """Atomically publish one private artifact and settle its local write."""
    temp = path.with_name(path.name + ".tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        temp.replace(path)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temp.unlink(missing_ok=True)


def _write_json(path: Path, value: Any) -> None:
    _write_private(path, (json.dumps(value, indent=2, sort_keys=True) + "\n").encode())


def _parser_fingerprint() -> dict[str, str]:
    """Hash the installed parser/model source, including local uncommitted changes."""
    root = Path(__file__).parents[1]
    files = sorted((root / "scrapers").glob("*.py")) + sorted((root / "models").glob("*.py"))
    files.append(root / "utils" / "http_evidence.py")
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}


def _stop_reason(status: int, headers: dict[str, str], body: bytes) -> str | None:
    if is_challenge(
        body[:100_000].decode("utf-8", errors="replace"), headers, scan_characters=100_000
    ):
        return "challenge"
    if status != 200:
        return "redirect" if 300 <= status < 400 else "http_error"
    return None


async def capture(
    plan: CapturePlan,
    output_root: Path,
    *,
    fixture_root: Path | None = None,
    live: bool = False,
    transport: httpx.AsyncBaseTransport | None = None,
) -> Path:
    """Capture into a new private namespace; never open a property database.

    Every live request is reserved durably before sending. No retries, redirect
    following, warmup calls, image downloads or automatic detail discovery.
    ``transport`` exists for network-blocked tests, not fallback transport trials.
    """
    if live:
        qualification = plan.qualification
        if (
            qualification is None
            or qualification.source != plan.source
            or qualification.expires_at <= datetime.now(UTC)
        ):
            raise ValueError(
                "Live capture requires current source acquisition/retention qualification"
            )
        if any(r.fixture is not None for r in plan.requests) or fixture_root is not None:
            raise ValueError("Live and fixture input cannot be mixed")
        if plan.delay_seconds < 1:
            raise ValueError("Live capture requires at least one second between requests")
    elif fixture_root is None or any(r.fixture is None for r in plan.requests):
        raise ValueError("Offline capture requires a fixture directory and every fixture filename")

    root = output_root.resolve()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    with runner_lock(str(root / "capture-authority")) as acquired:
        if not acquired:
            raise RuntimeError("A capture is already running in this output namespace")
        run_dir = root / (datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ-") + uuid4().hex[:12])
        run_dir.mkdir(mode=0o700, exist_ok=False)
        manifest: dict[str, Any] = {
            "schema_version": 1,
            "mode": "live" if live else "fixture",
            "transport": "httpx_direct_no_session" if live else "supplied_bytes",
            "started_at": datetime.now(UTC).isoformat(),
            "state": "running",
            "plan": plan.model_dump(mode="json"),
            "request_headers": _REQUEST_HEADERS,
            "python_version": platform.python_version(),
            "httpx_version": version("httpx"),
            "parser_environment": {
                name: version(name)
                for name in ("beautifulsoup4", "pydantic", "crawlee", "curl_cffi")
            },
            "parser_files": _parser_fingerprint(),
            "attempts": [],
            "live_requests_reserved": 0,
            "model_calls": 0,
            "commute_calls": 0,
            "notifications": 0,
        }
        manifest_path = run_dir / "manifest.json"
        _write_json(manifest_path, manifest)
        try:
            # Offline mode never constructs an HTTP client.
            for index, request in enumerate(plan.requests):
                if index and live:
                    await asyncio.sleep(plan.delay_seconds)
                if index >= plan.max_requests:
                    raise RuntimeError("Capture budget exhausted")
                record: dict[str, Any] = {
                    "index": index,
                    "url": str(request.url),
                    "kind": request.kind,
                    "scope": request.scope,
                    "started_at": datetime.now(UTC).isoformat(),
                    "state": "started",
                }
                manifest["attempts"].append(record)
                if live:
                    manifest["live_requests_reserved"] += 1
                _write_json(manifest_path, manifest)
                body = bytearray()
                start = time.monotonic()
                try:
                    if live:
                        # Each request starts with no cookie jar or inherited proxy/auth.
                        async with asyncio.timeout(plan.request_timeout_seconds):
                            async with (
                                httpx.AsyncClient(
                                    timeout=plan.request_timeout_seconds,
                                    follow_redirects=False,
                                    trust_env=False,
                                    transport=transport,
                                    headers=_REQUEST_HEADERS,
                                ) as client,
                                client.stream("GET", str(request.url)) as response,
                            ):
                                record["request_headers"] = dict(response.request.headers)
                                record["status"] = response.status_code
                                record["response_headers"] = {
                                    k: v
                                    for k, v in response.headers.items()
                                    if k in _RESPONSE_HEADERS
                                }
                                async for chunk in response.aiter_bytes():
                                    remaining = plan.max_response_bytes - len(body)
                                    body.extend(chunk[:remaining])
                                    if len(chunk) > remaining:
                                        record["state"] = "truncated"
                                        break
                        record["observed_at"] = datetime.now(UTC).isoformat()
                    else:
                        assert fixture_root is not None and request.fixture is not None
                        fixture = (fixture_root / request.fixture).resolve()
                        if not fixture.is_relative_to(fixture_root.resolve()):
                            raise ValueError("Fixture escapes its input directory")
                        with fixture.open("rb") as stream:
                            raw = stream.read(plan.max_response_bytes + 1)
                        body.extend(raw[: plan.max_response_bytes])
                        if len(raw) > plan.max_response_bytes:
                            record["state"] = "truncated"
                        record["status"] = request.fixture_status
                        record["response_headers"] = {
                            k.lower(): v for k, v in request.fixture_headers.items()
                        }
                        record["observed_at"] = (
                            request.fixture_observed_at.isoformat()
                            if request.fixture_observed_at
                            else None
                        )
                    if record["state"] != "truncated":
                        record["state"] = "received"
                        record["stop_reason"] = _stop_reason(
                            record["status"], record["response_headers"], bytes(body)
                        )
                except asyncio.CancelledError:
                    record["state"] = "cancelled"
                    raise
                except Exception as exc:
                    record.update(state="error", error_type=type(exc).__name__)
                finally:
                    record["seconds"] = round(time.monotonic() - start, 6)
                    record["body_bytes"] = len(body)
                    record["body_sha256"] = hashlib.sha256(body).hexdigest()
                    record["body_file"] = f"{index:02d}.body"
                    record["body_encoding"] = "http_decoded" if live else "supplied"
                    _write_private(run_dir / record["body_file"], bytes(body))
                    _write_json(manifest_path, manifest)
                if record["state"] != "received" or record.get("stop_reason"):
                    manifest["state"] = "stopped"
                    break
            else:
                manifest["state"] = "completed"
        except asyncio.CancelledError:
            manifest["state"] = "cancelled"
            raise
        except BaseException:
            manifest["state"] = "failed"
            raise
        finally:
            manifest["finished_at"] = datetime.now(UTC).isoformat()
            _write_json(manifest_path, manifest)
        return run_dir


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--plan", type=Path)
    inputs.add_argument("--snapshot", type=Path)
    inputs.add_argument("--replay", type=Path, help="Replay an existing response capture directory")
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--fixture-root", type=Path)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--max-cases", type=int, default=24)
    args = parser.parse_args()
    if args.snapshot or args.replay:
        if args.live or args.fixture_root:
            parser.error("Snapshot export and replay are offline operations")
        if args.snapshot:
            from home_finder.pipeline.snapshot_export import export_snapshot

            result = export_snapshot(args.snapshot, args.output_root, max_cases=args.max_cases)
        else:
            from home_finder.pipeline.capture_replay import replay

            result = asyncio.run(replay(args.replay, args.output_root))
        print(result)
        return
    with args.plan.open("rb") as stream:
        raw = stream.read(128_001)
    if len(raw) > 128_000:
        parser.error("Plan exceeds 128,000 bytes")
    plan = CapturePlan.model_validate_json(raw)
    result = asyncio.run(
        capture(plan, args.output_root, fixture_root=args.fixture_root, live=args.live)
    )
    print(result)
    if json.loads((result / "manifest.json").read_text())["state"] != "completed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()

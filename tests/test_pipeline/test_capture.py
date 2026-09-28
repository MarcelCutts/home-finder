"""Acquisition boundaries, immutable historical export and production parser replay."""

import asyncio
import hashlib
import json
import sqlite3
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import httpx
import pytest
from pydantic import ValidationError

from home_finder.pipeline.capture import CapturePlan, capture, main
from home_finder.pipeline.capture_replay import replay
from home_finder.pipeline.runner import runner_lock
from home_finder.pipeline.snapshot_export import export_snapshot

URL = "https://www.rightmove.co.uk/property-to-rent/find.html"


def make_plan(*, fixture: str | None = "input.html", **changes: Any) -> CapturePlan:
    data: dict[str, Any] = {
        "source": "rightmove",
        "max_requests": 2,
        "delay_seconds": 1,
        "requests": [{"url": URL, "kind": "search", "scope": "test contract", "fixture": fixture}],
    }
    data.update(changes)
    return CapturePlan.model_validate(data)


def qualified_plan(**changes: Any) -> CapturePlan:
    changes.setdefault(
        "raw_retention",
        {
            "policy_reference": "temporary mocked capture only",
            "retain_until": datetime.now(UTC) + timedelta(days=1),
        },
    )
    return make_plan(
        fixture=None,
        qualification={
            "source": "rightmove",
            "reference": "synthetic permission record for mocked transport only",
            "acquisition": True,
            "retention": True,
            "expires_at": datetime.now(UTC) + timedelta(days=1),
        },
        **changes,
    )


def load_manifest(path: Path) -> dict[str, Any]:
    return json.loads((path / "manifest.json").read_text())


@pytest.fixture
def inputs(tmp_path: Path) -> Path:
    path = tmp_path / "inputs"
    path.mkdir()
    (path / "input.html").write_bytes(b"<html>retained bytes</html>")
    return path


@pytest.mark.parametrize(
    "url",
    [
        "http://www.rightmove.co.uk/",
        "https://example.org/",
        "https://www.rightmove.co.uk.example.org/",
        "https://www.rightmove.co.uk:444/",
        "https://owner:password@www.rightmove.co.uk/",
        URL + "#secret",
        URL + "?TOKEN=secret",
    ],
)
def test_plan_rejects_unsupported_destinations_and_credentials(url: str) -> None:
    with pytest.raises(ValidationError):
        make_plan(requests=[{"url": url, "kind": "reference", "scope": "test"}])


@pytest.mark.parametrize(
    "changes",
    [
        {"max_requests": 0},
        {"max_requests": 13},
        {"max_response_bytes": 8_000_001},
        {"request_timeout_seconds": 61},
        {"fixture": "../outside.html"},
        {"fixture": "/absolute.html"},
    ],
)
def test_plan_rejects_unbounded_or_escaping_input(changes: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        make_plan(**changes)


def test_detail_requires_actual_listing_input(sample_property: Any) -> None:
    request = {"url": str(sample_property.url), "kind": "detail", "scope": "test"}
    with pytest.raises(ValidationError, match="retained search listing"):
        make_plan(source=sample_property.source, requests=[request])
    request["listing"] = sample_property.model_dump(mode="json")
    make_plan(source=sample_property.source, requests=[request])
    request["url"] = str(sample_property.url) + "99999"
    with pytest.raises(ValidationError, match="must match"):
        make_plan(source=sample_property.source, requests=[request])


def test_request_count_cannot_exceed_explicit_budget() -> None:
    request = make_plan().requests[0].model_dump(mode="json")
    with pytest.raises(ValidationError, match="budget"):
        make_plan(max_requests=1, requests=[request, request])


async def test_live_does_not_reuse_response_cookies(tmp_path: Path) -> None:
    requests = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert "cookie" not in request.headers
        return httpx.Response(200, content=b"ok", headers={"set-cookie": "session=private"})

    plan = qualified_plan()
    plan = plan.model_copy(update={"requests": plan.requests * 2})
    result = await capture(
        plan, tmp_path / "out", live=True, transport=httpx.MockTransport(respond)
    )
    data = load_manifest(result)
    assert data["state"] == "completed" and data["live_requests_reserved"] == 2
    assert len(requests) == 2
    assert all(item["response_headers"] == {} for item in data["attempts"])


async def test_live_requires_unexpired_matching_qualification(tmp_path: Path) -> None:
    for plan in (
        make_plan(fixture=None),
        qualified_plan().model_copy(
            update={
                "qualification": qualified_plan().qualification.model_copy(
                    update={"expires_at": datetime.now(UTC) - timedelta(seconds=1)}
                )
            }
        ),
        qualified_plan().model_copy(
            update={
                "qualification": qualified_plan().qualification.model_copy(
                    update={"source": "zoopla"}
                )
            }
        ),
    ):
        with pytest.raises(ValueError, match="qualification"):
            await capture(plan, tmp_path / "output", live=True)
    assert not (tmp_path / "output").exists()


async def test_offline_is_private_repeatable_and_has_no_http_client(
    tmp_path: Path, inputs: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = Mock(side_effect=AssertionError("offline client construction"))
    monkeypatch.setattr(httpx, "AsyncClient", client)
    output = tmp_path / "output"
    first = await capture(make_plan(), output, fixture_root=inputs)
    second = await capture(make_plan(), output, fixture_root=inputs)
    assert first != second
    assert (first / "00.body").read_bytes() == (inputs / "input.html").read_bytes()
    data = load_manifest(first)
    assert data["state"] == "completed"
    assert data["live_requests_reserved"] == data["model_calls"] == data["notifications"] == 0
    assert data["commute_calls"] == 0
    assert data["attempts"][0]["observed_at"] is None
    assert (
        data["attempts"][0]["body_sha256"]
        == hashlib.sha256((inputs / "input.html").read_bytes()).hexdigest()
    )
    assert first.stat().st_mode & 0o777 == 0o700
    assert (first / "manifest.json").stat().st_mode & 0o777 == 0o600
    assert (first / "00.body").stat().st_mode & 0o777 == 0o600
    client.assert_not_called()


async def test_namespace_excludes_a_second_collector(tmp_path: Path, inputs: Path) -> None:
    output = tmp_path / "output"
    with runner_lock(str(output / "capture-authority")) as held:
        assert held
        with pytest.raises(RuntimeError, match="already running"):
            await capture(make_plan(), output, fixture_root=inputs)
    assert list(output.glob("*/manifest.json")) == []


async def test_fixture_symlink_escape_is_a_failed_attempt(tmp_path: Path, inputs: Path) -> None:
    (tmp_path / "outside").write_text("private")
    (inputs / "link").symlink_to(tmp_path / "outside")
    result = await capture(make_plan(fixture="link"), tmp_path / "out", fixture_root=inputs)
    data = load_manifest(result)
    assert data["state"] == "stopped"
    assert data["attempts"][0]["error_type"] == "ValueError"
    assert (result / "00.body").read_bytes() == b""


@pytest.mark.parametrize(
    "status,body,reason",
    [
        (301, b"redirect", "redirect"),
        (429, b"limited", "http_error"),
        (403, b"denied", "http_error"),
        (200, b"Verify you are human", "challenge"),
    ],
)
async def test_live_reserves_before_send_and_stops_without_retry(
    tmp_path: Path, status: int, body: bytes, reason: str
) -> None:
    output = tmp_path / "out"
    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        data = load_manifest(next(output.glob("*/")))
        assert data["live_requests_reserved"] == len(calls)
        assert data["attempts"][-1]["state"] == "started"
        assert "cookie" not in request.headers and "authorization" not in request.headers
        return httpx.Response(
            status,
            content=body,
            headers={
                "location": URL + "?page=2",
                "set-cookie": "session=private",
                "retry-after": "3600",
            },
        )

    plan = qualified_plan()
    plan = plan.model_copy(update={"requests": plan.requests * 2})
    result = await capture(plan, output, live=True, transport=httpx.MockTransport(respond))
    data = load_manifest(result)
    assert len(calls) == len(data["attempts"]) == data["live_requests_reserved"] == 1
    assert data["state"] == "stopped"
    assert data["attempts"][0]["stop_reason"] == reason
    assert data["attempts"][0]["response_headers"] == {"retry-after": "3600"}
    assert data["attempts"][0]["request_headers"]["host"] == "www.rightmove.co.uk"
    assert (result / "00.body").read_bytes() == body


@pytest.mark.parametrize("live", [False, True])
async def test_retained_body_is_bounded_and_not_parsed_when_truncated(
    tmp_path: Path, inputs: Path, live: bool
) -> None:
    plan = qualified_plan(max_response_bytes=5) if live else make_plan(max_response_bytes=5)
    result = await capture(
        plan,
        tmp_path / "out",
        live=live,
        fixture_root=None if live else inputs,
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"123456789")),
    )
    data = load_manifest(result)
    assert data["state"] == "stopped"
    assert data["attempts"][0]["state"] == "truncated"
    assert data["attempts"][0]["body_bytes"] == 5
    replay_dir = await replay(result, tmp_path / "replays")
    assert json.loads((replay_dir / "replay.json").read_text())["results"][0]["outcome"] == (
        "incomplete_capture"
    )


class InterruptedStream(httpx.AsyncByteStream):
    def __init__(self, started: asyncio.Event) -> None:
        self.started = started
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield b"partial bytes"
        self.started.set()
        await asyncio.Event().wait()

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.parametrize("cancel", [False, True])
async def test_interruption_keeps_prefix_and_final_journal(tmp_path: Path, cancel: bool) -> None:
    started = asyncio.Event()
    stream = InterruptedStream(started)
    plan = qualified_plan(request_timeout_seconds=0.05)
    output = tmp_path / "out"
    task = asyncio.create_task(
        capture(
            plan,
            output,
            live=True,
            transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=stream)),
        )
    )
    await started.wait()
    if cancel:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        await task
    result = next(output.glob("*/"))
    data = load_manifest(result)
    assert data["state"] == ("cancelled" if cancel else "stopped")
    assert data["live_requests_reserved"] == 1
    assert data["attempts"][0]["state"] == ("cancelled" if cancel else "error")
    assert (result / "00.body").read_bytes() == b"partial bytes"
    assert stream.closed


@pytest.mark.parametrize(
    "source,host,expected",
    [
        ("rightmove", "www.rightmove.co.uk", 3),
        ("zoopla", "www.zoopla.co.uk", 3),
        ("openrent", "www.openrent.com", 3),
        ("onthemarket", "www.onthemarket.com", 0),
    ],
)
async def test_replay_uses_existing_search_parser_contracts(
    tmp_path: Path,
    fixtures_path: Path,
    source: str,
    host: str,
    expected: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = Mock(side_effect=AssertionError("replay client construction"))
    monkeypatch.setattr(httpx, "AsyncClient", client)
    plan = make_plan(
        source=source,
        requests=[
            {
                "url": f"https://{host}/search/",
                "kind": "search",
                "scope": "legacy test fixture",
                "fixture": f"{source}_search.html",
            }
        ],
    )
    captured = await capture(plan, tmp_path / "out", fixture_root=fixtures_path)
    replayed = await replay(captured, tmp_path / "replays")
    data = json.loads((replayed / "replay.json").read_text())
    result = data["results"][0]
    assert len(result["properties"]) == expected
    assert result["outcome"] == ("extracted" if expected else "empty_or_unrecognised")
    assert all("first_seen" not in prop for prop in result["properties"])
    assert result["observed_at"] is None
    assert data["human_labels"] is None and data["live_requests_reserved"] == 0
    client.assert_not_called()


@pytest.mark.parametrize(
    "source,host",
    [
        ("rightmove", "www.rightmove.co.uk"),
        ("zoopla", "www.zoopla.co.uk"),
        ("openrent", "www.openrent.com"),
        ("onthemarket", "www.onthemarket.com"),
    ],
)
async def test_replay_details_runs_actual_extraction_without_media_requests(
    tmp_path: Path,
    fixtures_path: Path,
    sample_property: Any,
    source: str,
    host: str,
) -> None:
    listing = sample_property.model_dump(mode="json")
    listing.update(source=source, url=f"https://{host}/properties/12345")
    plan = make_plan(
        source=source,
        requests=[
            {
                "url": listing["url"],
                "kind": "detail",
                "scope": "legacy parser contract",
                "listing": listing,
                "fixture": f"{source}_detail_with_floorplan.html",
            }
        ],
    )
    captured = await capture(plan, tmp_path / "out", fixture_root=fixtures_path)
    replayed = await replay(captured, tmp_path / "replays")
    result = json.loads((replayed / "replay.json").read_text())["results"][0]
    assert result["outcome"] == "extracted"
    assert result["detail"]["floorplan_url"]


async def test_replay_rejects_modified_bytes(tmp_path: Path, inputs: Path) -> None:
    captured = await capture(make_plan(), tmp_path / "out", fixture_root=inputs)
    (captured / "00.body").write_text("modified")
    with pytest.raises(ValueError, match="hash/size"):
        await replay(captured, tmp_path / "replays")
    assert not (tmp_path / "replays").exists()


@pytest.fixture
def snapshot(tmp_path: Path) -> Path:
    path = tmp_path / "frozen.db"
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE properties(unique_id TEXT, first_seen TEXT, description TEXT, user_notes TEXT);
        INSERT INTO properties VALUES('a', '2025-01-01', 'listing text', 'private notes');
        INSERT INTO properties VALUES('b', '2025-02-01', 'another home', 'private notes');
        CREATE TABLE source_listings(unique_id TEXT, merged_id TEXT, description TEXT);
        INSERT INTO source_listings VALUES('source-a', 'a', 'source description');
        CREATE TABLE quality_analyses(property_unique_id TEXT, analysis_json TEXT);
        INSERT INTO quality_analyses VALUES('a', '{"epc_rating": "D"}');
        CREATE TABLE property_images(id INTEGER, property_unique_id TEXT, url TEXT);
        INSERT INTO property_images VALUES(1, 'a', 'https://images.example.org/photo.jpg');
        CREATE TABLE personal_contacts(email TEXT);
        INSERT INTO personal_contacts VALUES('private@example.org');
        PRAGMA user_version=7;
    """)
    connection.close()
    path.chmod(0o400)
    return path


def test_snapshot_export_preserves_copy_and_separates_evidence(
    tmp_path: Path, snapshot: Path
) -> None:
    before = snapshot.read_bytes()
    result = export_snapshot(snapshot, tmp_path / "out", max_cases=1)
    data = load_manifest(result)
    assert data["state"] == "completed" and data["mode"] == "historical_projection"
    assert data["table_counts"]["properties"] == 2
    assert len(data["cases"]) == 1 and data["cases"][0]["unique_id"] == "a"
    assert data["source_response_evidence"] is data["image_bytes_retained"] is False
    raw = (result / data["cases"][0]["file"]).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == data["cases"][0]["sha256"]
    assert b"private notes" not in raw and b"private@example.org" not in raw
    case = json.loads(raw)
    assert case["human_labels"] is None
    assert case["unverified_model_output"][0]["analysis_json"] == '{"epc_rating": "D"}'
    assert case["source_listing_projections"][0]["description"] == "source description"
    assert case["image_references_only"][0]["url"].endswith("photo.jpg")
    assert snapshot.read_bytes() == before
    assert snapshot.stat().st_mode & 0o777 == 0o400
    assert (result / "000.json").stat().st_mode & 0o777 == 0o600


def test_snapshot_export_refuses_wal_and_bad_limits(tmp_path: Path, snapshot: Path) -> None:
    with pytest.raises(ValueError, match=r"1\.\.100"):
        export_snapshot(snapshot, tmp_path / "out", max_cases=101)
    snapshot.with_name(snapshot.name + "-wal").write_bytes(b"pending writes")
    with pytest.raises(ValueError, match="WAL"):
        export_snapshot(snapshot, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_cli_returns_failure_for_truncated_capture(
    tmp_path: Path, inputs: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(make_plan(max_response_bytes=1).model_dump_json())
    monkeypatch.setattr(
        "sys.argv",
        [
            "capture",
            "--plan",
            str(plan_path),
            "--output-root",
            str(tmp_path / "out"),
            "--fixture-root",
            str(inputs),
        ],
    )
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 1

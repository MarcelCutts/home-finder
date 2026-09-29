"""Raw bytes, normalized observations and expiry remain independently verifiable."""

import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import httpx
import pytest

from home_finder.db import PropertyStorage
from home_finder.models import PropertySource
from home_finder.models.observations import RawCaptureReference, SourceObservation
from home_finder.pipeline import capture_replay
from home_finder.pipeline.capture import CapturePlan, capture
from home_finder.pipeline.capture_evidence import (
    RawCaptureUnavailable,
    expire_capture,
    inspect_capture,
    main,
    resolve_reference,
    verified_capture,
)
from home_finder.pipeline.runner import runner_lock

AT = datetime(2026, 9, 1, 12, tzinfo=UTC)


async def captured(tmp_path, fixtures_path, *, dated=True, managed=True, count=1):
    request = {
        "url": "https://www.rightmove.co.uk/property-to-rent/find.html",
        "kind": "search",
        "scope": "synthetic development fixture",
        "fixture": "rightmove_next_search.html",
        "fixture_observed_at": AT if dated else None,
    }
    plan = CapturePlan.model_validate(
        {
            "source": "rightmove",
            "requests": [request] * count,
            "max_requests": count,
            "raw_retention": {
                "policy_reference": "temporary fixture test, not source qualification",
                "retain_until": datetime.now(UTC) + timedelta(days=1),
            }
            if managed
            else None,
        }
    )
    return await capture(plan, tmp_path / "captures", fixture_root=fixtures_path)


def metadata(path):
    return json.loads((path / "manifest.json").read_bytes())


def digest(path):
    return hashlib.sha256((path / "manifest.json").read_bytes()).hexdigest()


def past_deadline(path):
    return datetime.fromisoformat(metadata(path)["plan"]["raw_retention"]["retain_until"])


def events(path):
    report = json.loads((path / "replay.json").read_bytes())
    return [
        SourceObservation.model_validate(x) for r in report["results"] for x in r["observations"]
    ]


async def test_replay_exports_stable_exact_evidence_and_survives_ledger_roundtrip(
    tmp_path, fixtures_path, monkeypatch
):
    client = Mock(side_effect=AssertionError("offline replay cannot construct a client"))
    monkeypatch.setattr(httpx, "AsyncClient", client)
    original = await captured(tmp_path, fixtures_path)
    original_hash = digest(original)
    first = await capture_replay.replay(original, tmp_path / "replays")
    second = await capture_replay.replay(original, tmp_path / "replays")
    a, b = events(first), events(second)
    assert len(a) == 2 and [x.sha256 for x in a] == [x.sha256 for x in b]
    assert all(x.observed_at == AT and x.raw_capture.capture_mode == "fixture" for x in a)
    assert all(x.producer_id == "capture-fixture-replay-v1" for x in a)
    assert a[0].fields.first_visible_date.value.meaning == "first_visible"
    assert a[0].fields.observed_floorplan_count.value == 0
    assert a[0].fields.agent.value.branch_id
    assert a[0].parser.name == "rightmove.search.capture_replay"
    assert a[0].request.query is None and a[0].request.page_index is None
    request_url = metadata(original)["plan"]["requests"][0]["url"]
    assert a[0].request.requested_url_sha256 == hashlib.sha256(request_url.encode()).hexdigest()
    async with PropertyStorage(str(tmp_path / "isolated.db")) as db:
        receipt = await db.observations.ingest(a)
        assert receipt.inserted == 2
        repeated = await db.observations.ingest(b)
        assert repeated.inserted == 0 and repeated.replayed == 2
        retained = await db.observations.get(a[0].sha256)
        assert retained.payload_json() == a[0].payload_json()
    assert (
        resolve_reference(original, a[0].raw_capture, now=datetime.now(UTC))
        == (fixtures_path / "rightmove_next_search.html").read_bytes()
    )
    assert digest(original) == original_hash
    assert (first / "replay.json").stat().st_mode & 0o777 == 0o600
    assert first.stat().st_mode & 0o777 == 0o700
    client.assert_not_called()


@pytest.mark.parametrize("source", list(PropertySource))
async def test_detail_replay_retains_source_metadata_with_capture_time(
    tmp_path, fixtures_path, make_property, source
):
    urls = {
        "rightmove": "https://www.rightmove.co.uk/properties/100001",
        "zoopla": "https://www.zoopla.co.uk/to-rent/details/100001/",
        "openrent": "https://www.openrent.co.uk/property-to-rent/london/flat/100001",
        "onthemarket": "https://www.onthemarket.com/details/100001/",
    }
    prop = make_property(source=source, source_id="100001", url=urls[source.value])
    plan = CapturePlan.model_validate(
        {
            "source": source,
            "max_requests": 1,
            "requests": [
                {
                    "url": str(prop.url),
                    "kind": "detail",
                    "scope": "legacy test contract",
                    "listing": prop.model_dump(mode="json"),
                    "fixture_observed_at": AT,
                    "fixture": "rightmove_indexed_detail.html"
                    if source == PropertySource.RIGHTMOVE
                    else f"{source.value}_detail_with_floorplan.html",
                }
            ],
        }
    )
    raw = await capture(plan, tmp_path / "out", fixture_root=fixtures_path)
    result = await capture_replay.replay(raw, tmp_path / "replays")
    event = events(result)[0]
    assert event.origin == "detail" and event.observed_at == AT
    assert event.source_unique_id == prop.unique_id
    assert event.fields.gallery_urls and event.fields.price_pcm is None
    assert event.raw_capture.response_sha256 == metadata(raw)["attempts"][0]["body_sha256"]
    if source == PropertySource.RIGHTMOVE:
        assert event.fields.observed_floorplan_count.value == 0


async def test_unknown_capture_time_cannot_become_a_fresh_observation(tmp_path, fixtures_path):
    raw = await captured(tmp_path, fixtures_path, dated=False)
    output = await capture_replay.replay(raw, tmp_path / "replays")
    result = json.loads((output / "replay.json").read_bytes())["results"][0]
    assert result["properties"] and not result["observations"]
    assert result["observation_state"] == "unavailable_capture_time"


async def test_parser_revision_has_distinct_stable_identity(tmp_path, fixtures_path, monkeypatch):
    raw = await captured(tmp_path, fixtures_path)
    before = events(await capture_replay.replay(raw, tmp_path / "before"))
    factory = capture_replay.search_observations

    def changed(*args, **kwargs):
        props, errors = factory(*args, **kwargs)
        revised = []
        for prop in props:
            original = prop.source_observation
            revised.append(
                prop.model_copy(
                    update={
                        "source_observation": original.model_copy(
                            update={
                                "parser": original.parser.model_copy(
                                    update={"code_sha256": "b" * 64}
                                )
                            }
                        )
                    }
                )
            )
        return revised, errors

    monkeypatch.setattr(capture_replay, "search_observations", changed)
    after = events(await capture_replay.replay(raw, tmp_path / "after"))
    assert before[0].fields == after[0].fields
    assert before[0].observed_at == after[0].observed_at
    assert before[0].event_id != after[0].event_id


@pytest.mark.parametrize("mutation", ["hash", "size", "index", "manifest", "mode"])
async def test_reference_must_match_manifest_and_bytes(tmp_path, fixtures_path, mutation):
    raw = await captured(tmp_path, fixtures_path)
    with verified_capture(raw) as verified:
        reference = verified.reference(0, verified.body(0, now=datetime.now(UTC)))
    changes = {
        "hash": {"response_sha256": "a" * 64},
        "size": {"response_bytes": 1},
        "index": {"record_index": 11},
        "manifest": {"capture_manifest_sha256": "b" * 64},
        "mode": {"capture_mode": "live"},
    }
    bad = RawCaptureReference.model_validate(reference.model_dump() | changes[mutation])
    with pytest.raises(ValueError):
        resolve_reference(raw, bad, now=datetime.now(UTC))


@pytest.mark.parametrize("kind", ["body", "manifest", "root", "ancestor", "fifo", "huge"])
async def test_special_files_and_symlink_paths_are_never_read(tmp_path, fixtures_path, kind):
    raw = await captured(tmp_path, fixtures_path)
    if kind in {"body", "manifest"}:
        name = "00.body" if kind == "body" else "manifest.json"
        target = tmp_path / "external"
        (raw / name).rename(target)
        (raw / name).symlink_to(target)
    elif kind == "root":
        link = tmp_path / "linked"
        link.symlink_to(raw, target_is_directory=True)
        raw = link
    elif kind == "ancestor":
        link = tmp_path / "linked"
        link.symlink_to(raw.parent, target_is_directory=True)
        raw = link / raw.name
    elif kind == "fifo":
        (raw / "00.body").unlink()
        os.mkfifo(raw / "00.body")
    else:
        (raw / "manifest.json").write_bytes(b" " * 256_001)
    with pytest.raises((ValueError, OSError)):
        await capture_replay.replay(raw, tmp_path / "replays")
    assert not (tmp_path / "replays").exists()


@pytest.mark.parametrize("mutation", ["duplicate", "nonfinite", "running", "traversal", "naive"])
async def test_invalid_manifest_fails_before_parser(tmp_path, fixtures_path, mutation):
    raw = await captured(tmp_path, fixtures_path)
    manifest = metadata(raw)
    if mutation == "duplicate":
        data = '{"mode":"live",' + (raw / "manifest.json").read_text()[1:]
    else:
        if mutation == "nonfinite":
            manifest["ignored"] = float("nan")
        elif mutation == "running":
            manifest["state"] = "running"
        elif mutation == "traversal":
            manifest["attempts"][0]["body_file"] = "../00.body"
        else:
            manifest["attempts"][0]["observed_at"] = "2026-09-01T12:00:00"
        data = json.dumps(manifest)
    (raw / "manifest.json").write_text(data)
    with pytest.raises(ValueError):
        await capture_replay.replay(raw, tmp_path / "replays")
    assert not (tmp_path / "replays").exists()


async def test_expiry_keeps_reference_and_normalized_evidence_but_never_promises_missing_bytes(
    tmp_path, fixtures_path
):
    raw = await captured(tmp_path, fixtures_path)
    replayed = await capture_replay.replay(raw, tmp_path / "replays")
    event = events(replayed)[0]
    checksum = digest(raw)
    before = {p.name: p.read_bytes() for p in raw.iterdir()}
    report = inspect_capture(raw, now=datetime.now(UTC))
    assert report["records"] == [{"index": 0, "raw_state": "available"}]
    assert before == {p.name: p.read_bytes() for p in raw.iterdir()}
    deadline = past_deadline(raw)
    assert inspect_capture(raw, now=deadline)["records"][0]["raw_state"] == "expired_retained"
    with pytest.raises(RawCaptureUnavailable, match="expired"):
        resolve_reference(raw, event.raw_capture, now=deadline)
    (raw / "unrelated.txt").write_text("keep")
    result = expire_capture(raw, expected_sha256=checksum, now=deadline)
    assert result["state"] == "completed" and result["removal_requested"] == [0]
    assert not (raw / "00.body").exists()
    assert (raw / "unrelated.txt").read_text() == "keep" and digest(raw) == checksum
    assert events(replayed)[0].payload_json() == event.payload_json()
    repeated = expire_capture(raw, expected_sha256=checksum, now=deadline)
    assert repeated["already_absent"] == [0] and repeated["removal_requested"] == []
    with pytest.raises(RawCaptureUnavailable, match="missing"):
        resolve_reference(raw, event.raw_capture, now=deadline - timedelta(seconds=1))


@pytest.mark.parametrize("reason", ["unmanaged", "too_early", "wrong_hash", "locked", "corrupt"])
async def test_expiry_preconditions_fail_without_deleting_any_body(tmp_path, fixtures_path, reason):
    raw = await captured(tmp_path, fixtures_path, managed=reason != "unmanaged", count=2)
    checksum = digest(raw)
    now = datetime.now(UTC) + timedelta(days=2)
    if reason == "too_early":
        now = datetime.now(UTC)
    if reason == "wrong_hash":
        checksum = "f" * 64
    if reason == "corrupt":
        (raw / "01.body").write_bytes(b"changed")
    if reason == "locked":
        with runner_lock(str(raw.parent / "capture-authority")) as acquired:
            assert acquired
            with pytest.raises(ValueError, match="in use"):
                expire_capture(raw, expected_sha256=checksum, now=now)
    else:
        with pytest.raises(ValueError):
            expire_capture(raw, expected_sha256=checksum, now=now)
    assert (raw / "00.body").exists() and (raw / "01.body").exists()
    assert not (raw / "raw-expiry.json").exists()


async def test_expiry_resumes_after_a_real_process_exit_between_body_removals(
    tmp_path, fixtures_path
):
    raw = await captured(tmp_path, fixtures_path, count=2)
    checksum = digest(raw)
    program = tmp_path / "exit_during_expiry.py"
    program.write_text(
        "from datetime import datetime\n"
        "from pathlib import Path\n"
        "import os, sys\n"
        "from home_finder.pipeline.capture_evidence import expire_capture\n"
        "original = os.unlink\n"
        "def interrupted(name, **kwargs):\n"
        "    original(name, **kwargs)\n"
        "    if name == '00.body': os._exit(23)\n"
        "os.unlink = interrupted\n"
        "expire_capture(Path(sys.argv[1]), expected_sha256=sys.argv[2], "
        "now=datetime.fromisoformat(sys.argv[3]))\n"
    )
    result = subprocess.run(
        [sys.executable, str(program), str(raw), checksum, past_deadline(raw).isoformat()],
        capture_output=True,
        timeout=20,
    )
    assert result.returncode == 23, result.stderr
    assert not (raw / "00.body").exists() and (raw / "01.body").exists()
    assert json.loads((raw / "raw-expiry.json").read_bytes())["state"] == "removing"
    resumed = expire_capture(raw, expected_sha256=checksum, now=past_deadline(raw))
    assert resumed["already_absent"] == [0] and resumed["removal_requested"] == [1]
    assert resumed["state"] == "completed" and digest(raw) == checksum


@pytest.mark.parametrize("days", [-1, 91])
async def test_new_capture_rejects_elapsed_or_unbounded_retention(tmp_path, fixtures_path, days):
    original = await captured(tmp_path, fixtures_path)
    plan = metadata(original)["plan"]
    plan["raw_retention"]["retain_until"] = datetime.now(UTC) + timedelta(days=days)
    with pytest.raises(ValueError, match="within 90 days"):
        await capture(
            CapturePlan.model_validate(plan), tmp_path / "new", fixture_root=fixtures_path
        )
    assert not (tmp_path / "new").exists()


async def test_live_capture_cannot_omit_retention_deadline(tmp_path, fixtures_path):
    raw = await captured(tmp_path, fixtures_path, managed=False)
    plan = metadata(raw)["plan"]
    plan["requests"][0]["fixture"] = None
    plan["qualification"] = {
        "source": "rightmove",
        "acquisition": True,
        "retention": True,
        "reference": "mocked test only",
        "expires_at": datetime.now(UTC) + timedelta(days=1),
    }
    with pytest.raises(ValueError, match="explicit raw-body retention"):
        await capture(CapturePlan.model_validate(plan), tmp_path / "live", live=True)
    assert not (tmp_path / "live").exists()


async def test_cli_inspects_by_default_and_requires_both_expiry_arguments(
    tmp_path, fixtures_path, monkeypatch, capsys
):
    raw = await captured(tmp_path, fixtures_path)
    monkeypatch.setattr(sys, "argv", ["capture_evidence", str(raw)])
    main()
    assert json.loads(capsys.readouterr().out)["retention"] == "managed"
    monkeypatch.setattr(sys, "argv", ["capture_evidence", str(raw), "--expire"])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2 and (raw / "00.body").exists()


async def test_expired_capture_cannot_be_replayed_even_when_body_remains(
    tmp_path, fixtures_path, monkeypatch
):
    raw = await captured(tmp_path, fixtures_path)

    class AfterExpiry(datetime):
        @classmethod
        def now(cls, tz=None):
            return past_deadline(raw)

    monkeypatch.setattr(capture_replay, "datetime", AfterExpiry)
    with pytest.raises(RawCaptureUnavailable, match="expired"):
        await capture_replay.replay(raw, tmp_path / "replays")
    assert (raw / "00.body").exists() and not (tmp_path / "replays").exists()


async def test_normalized_export_bound_fails_before_publishing_a_partial_artifact(
    tmp_path, fixtures_path, make_property, monkeypatch
):
    raw = await captured(tmp_path, fixtures_path)
    prop = make_property(
        source=PropertySource.RIGHTMOVE,
        source_id="100001",
        url="https://www.rightmove.co.uk/properties/100001",
        description="x" * 99_999,
    )
    monkeypatch.setattr(capture_replay, "_parse_search", lambda *args: [prop] * 45)
    with pytest.raises(ValueError, match="4 MB"):
        await capture_replay.replay(raw, tmp_path / "replays")
    assert not (tmp_path / "replays").exists()


async def test_capture_record_limit_is_explicit_not_silently_truncated(
    tmp_path, fixtures_path, sample_property, monkeypatch
):
    raw = await captured(tmp_path, fixtures_path)
    monkeypatch.setattr(capture_replay, "_parse_search", lambda *args: [sample_property] * 101)
    output = await capture_replay.replay(raw, tmp_path / "replays")
    result = json.loads((output / "replay.json").read_bytes())["results"][0]
    assert result["outcome"] == "parser_error" and result["observations"] == []
    assert "properties" not in result


async def test_missing_file_is_never_an_available_reference(tmp_path, fixtures_path):
    raw = await captured(tmp_path, fixtures_path)
    (raw / "00.body").unlink()
    assert inspect_capture(raw, now=datetime.now(UTC))["records"] == [
        {"index": 0, "raw_state": "missing"}
    ]
    with pytest.raises(RawCaptureUnavailable, match="missing"):
        await capture_replay.replay(raw, tmp_path / "replays")

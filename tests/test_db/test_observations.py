"""Source evidence survives retries, arrival reordering, failure and migration."""

import asyncio
import itertools
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import aiosqlite
import pytest
from pydantic import ValidationError

from home_finder.db import PropertyStorage
from home_finder.db.migrations import MIGRATIONS
from home_finder.db.observations import ObservationConflict
from home_finder.models import ListingTextEvidence, UserStatus
from home_finder.models.observations import (
    ObservationFields,
    SourceObservation,
    observation_field_rows,
    project_field_rows,
)

AT = datetime(2026, 9, 1, 12, tzinfo=UTC)


def observation(event="first", *, minute=0, origin="search", source="rightmove", **fields):
    return SourceObservation.model_validate(
        {
            "producer_id": "fixture-collector",
            "event_id": event,
            "source": source,
            "source_id": "123",
            "listing_url": f"https://www.{source}.co.uk/properties/123",
            "origin": origin,
            "observed_at": AT + timedelta(minutes=minute),
            "parser": {"name": "fixture.search", "version": "1", "code_sha256": "a" * 64},
            "correction_reason": "Explicit source correction" if origin == "correction" else None,
            "fields": fields,
        }
    )


def value(item):
    return {"value": item}


@pytest.fixture
async def storage(tmp_path):
    async with PropertyStorage(str(tmp_path / "observations.db")) as db:
        yield db


@pytest.mark.parametrize(
    "fields",
    [
        {"price_pcm": value(True)},
        {"price_pcm": value("2000")},
        {"bedrooms": value(-1)},
        {"coordinates": value({"latitude": True, "longitude": -0.1})},
        {"coordinates": value({"latitude": 51.5})},
        {"coordinates": value({"latitude": float("nan"), "longitude": -0.1})},
        {"description": value("")},
        {"features": value([])},
        {"features": value([" "])},
        {"features": value(["x"] * 101)},
        {"agent": value({"agent_name": "Guessed agency"})},
        {"observed_floorplan_count": value(-1)},
        {"description": {"state": "cleared"}},
        {"council_tax_budget_estimate": value("C")},
        {"portal_date": value({"literal": "Today", "meaning": "invented"})},
    ],
)
def test_source_contract_rejects_invalid_or_non_source_fields(fields):
    with pytest.raises(ValidationError):
        observation(**fields)


def test_metadata_keeps_source_dates_and_implicit_missing_distinct():
    item = observation(
        description=None,
        observed_floorplan_count=value(0),
        portal_date=value(
            {
                "literal": "Reduced 29 August 2026",
                "meaning": "reduced",
                "calendar_date": "2026-08-29",
            }
        ),
        agent=value({"branch_id": "456", "branch_name": "Source branch"}),
    )
    payload = json.loads(item.payload_json())
    assert "description" not in payload["fields"]
    assert payload["fields"]["portal_date"]["value"]["meaning"] == "reduced"
    assert item.observed_at.date().isoformat() == "2026-09-01"
    assert item.fields.observed_floorplan_count.value == 0
    assert SourceObservation.model_validate_json(item.payload_json()).sha256 == item.sha256


@pytest.mark.parametrize(
    "url",
    [
        "https://user:secret@example.test/123",
        "https://example.test/123?token=secret",
        "https://example.test/123#secret",
    ],
)
def test_credentials_or_noncanonical_urls_cannot_enter_metadata(url):
    data = observation().model_dump(mode="json")
    data["listing_url"] = url
    with pytest.raises(ValidationError, match="credentials"):
        SourceObservation.model_validate(data)


@pytest.mark.parametrize("change", ["naive", "parser", "headers", "body_limit", "clear_reason"])
def test_provenance_and_utf8_limits_are_explicit(change):
    data = observation().model_dump(mode="json")
    if change == "naive":
        data["observed_at"] = "2026-09-01T12:00:00"
    elif change == "parser":
        data["parser"]["code_sha256"] = None
    elif change == "headers":
        data["request_headers"] = {"Authorization": "secret"}
    elif change == "body_limit":
        data["fields"]["description"] = value("漢" * 90_000)
    else:
        data["origin"] = "correction"
        data["fields"]["description"] = {"state": "cleared"}
    with pytest.raises(ValidationError):
        SourceObservation.model_validate(data)


async def test_idempotent_redelivery_does_not_replace_receipt_or_field_evidence(storage):
    item = observation(description=value("Text 漢字 <b>literal</b>"), price_pcm=value(1800))
    first = await storage.observations.ingest([item])
    saved = await storage.observations.get(first.observation_ids[0])
    assert saved == item
    async with storage._read_connection() as conn:
        cursor = await conn.execute("SELECT received_at FROM source_observations")
        before = (await cursor.fetchone())[0]
    replay = await storage.observations.ingest([item, item])
    assert replay.inserted == 0 and replay.replayed == 2
    assert len(await storage.observations.history(item.source_unique_id)) == 1
    async with storage._read_connection() as conn:
        cursor = await conn.execute("SELECT received_at FROM source_observations")
        assert (await cursor.fetchone())[0] == before
        cursor = await conn.execute("SELECT COUNT(*) FROM source_observation_fields")
        assert (await cursor.fetchone())[0] == 2


async def test_identity_conflict_rolls_back_every_new_event_in_batch(storage):
    item = observation(description=value("Original"))
    await storage.observations.ingest([item])
    fresh = observation("new", price_pcm=value(2000))
    conflict = observation(description=value("Different bytes for same event"))
    with pytest.raises(ObservationConflict):
        await storage.observations.ingest([fresh, conflict])
    assert await storage.observations.get(fresh.sha256) is None
    assert len(await storage.observations.history(item.source_unique_id)) == 1


async def test_arrival_order_replays_to_identical_fields_without_sparse_erasure(storage):
    events = [
        observation("search", minute=2, description=value("Summary"), price_pcm=value(1900)),
        observation(
            "detail",
            minute=1,
            origin="detail",
            description=value("Rich text"),
            features=value(["Balcony"]),
            price_pcm=value(2000),
        ),
        observation("sparse", minute=3, price_pcm=value(1800)),
        observation("clear", minute=4, origin="correction", features={"state": "cleared"}),
    ]
    expected = project_field_rows(
        events[0].source_unique_id, [row for e in events for row in observation_field_rows(e)]
    )
    assert expected.fields["description"].value == "Rich text"
    assert expected.fields["price_pcm"].value == 1800
    assert expected.fields["features"].state == "cleared"
    for sequence in itertools.permutations(events):
        replay = project_field_rows(
            events[0].source_unique_id, [row for e in sequence for row in observation_field_rows(e)]
        )
        assert replay.sha256 == expected.sha256
    await storage.observations.ingest(list(reversed(events)))
    actual = await storage.observations.project(events[0].source_unique_id)
    assert actual == expected


async def test_equal_rank_conflicts_are_explicit_and_do_not_cross_sources(storage):
    left = observation(
        "left", origin="detail", description=value("Pets allowed"), price_pcm=value(2000)
    )
    right = observation(
        "right", origin="detail", description=value("No pets"), price_pcm=value(2000)
    )
    other = observation(
        "other", origin="detail", source="zoopla", description=value("Separate portal")
    )
    await storage.observations.ingest([right, other, left])
    current = await storage.observations.project(left.source_unique_id)
    conflict = current.fields["description"]
    assert (
        conflict.state == "conflicting"
        and conflict.value is None
        and conflict.alternative_count == 2
    )
    assert set(conflict.observation_ids) == {left.sha256, right.sha256}
    assert current.fields["price_pcm"].value == 2000
    assert (await storage.observations.project(other.source_unique_id)).fields[
        "description"
    ].value == "Separate portal"
    correction = observation(
        "resolved", minute=1, origin="correction", description=value("Pets subject to approval")
    )
    await storage.observations.ingest([correction])
    assert (await storage.observations.project(left.source_unique_id)).fields[
        "description"
    ].value == "Pets subject to approval"
    assert len(await storage.observations.history(left.source_unique_id)) == 3


async def test_two_connections_accept_exactly_one_copy(storage):
    async with PropertyStorage(storage.db_path) as second:
        item = observation(price_pcm=value(1850))
        results = await asyncio.gather(
            storage.observations.ingest([item]), second.observations.ingest([item])
        )
        assert sum(r.inserted for r in results) == sum(r.replayed for r in results) == 1


async def test_failure_between_ledger_and_fields_rolls_back(storage, monkeypatch):
    conn = await storage._get_connection()
    original = conn.executemany

    async def fail(sql, parameters):
        if "INSERT INTO source_observation_fields" in sql:
            raise RuntimeError("Injected field persistence failure")
        return await original(sql, parameters)

    monkeypatch.setattr(conn, "executemany", fail)
    item = observation(description=value("Cannot become partial evidence"))
    with pytest.raises(RuntimeError, match="Injected"):
        await storage.observations.ingest([item])
    assert await storage.observations.get(item.sha256) is None
    assert not (await storage.observations.project(item.source_unique_id)).fields


async def test_cancellation_before_fields_leaves_no_partial_observation(storage, monkeypatch):
    conn = await storage._get_connection()
    original = conn.executemany
    entered = asyncio.Event()

    async def pause(sql, parameters):
        if "INSERT INTO source_observation_fields" in sql:
            entered.set()
            await asyncio.Event().wait()
        return await original(sql, parameters)

    monkeypatch.setattr(conn, "executemany", pause)
    item = observation(description=value("Interrupted before fields"))
    task = asyncio.create_task(storage.observations.ingest([item]))
    await asyncio.wait_for(entered.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await storage.observations.get(item.sha256) is None


@pytest.mark.parametrize("table", ["source_observations", "source_observation_fields"])
async def test_committed_evidence_is_immutable_and_independent_of_properties(storage, table):
    item = observation(price_pcm=value(1900))
    await storage.observations.ingest([item])
    for statement in (f"DELETE FROM {table}", f"UPDATE {table} SET source_unique_id = 'changed'"):
        with pytest.raises(aiosqlite.IntegrityError, match="immutable"):
            async with storage._transaction() as conn:
                await conn.execute(statement)
    assert (await storage.observations.get(item.sha256)).sha256 == item.sha256
    async with storage._read_connection() as conn:
        cursor = await conn.execute("SELECT COUNT(*) FROM properties")
        assert (await cursor.fetchone())[0] == 0


async def test_history_paginates_without_losing_evidence(storage):
    items = [observation(f"page-{i}", price_pcm=value(i)) for i in range(5)]
    await storage.observations.ingest(items)
    seen = []
    after = ""
    while page := await storage.observations.history(
        items[0].source_unique_id, after=after, limit=2
    ):
        seen.extend(item.sha256 for item in page)
        after = page[-1].sha256
    assert sorted(seen) == sorted(item.sha256 for item in items)
    assert await storage.observations.get("f" * 64) is None
    assert await storage.observations.baseline("absent") is None
    with pytest.raises(ValueError, match=r"1\.\.100"):
        await storage.observations.history(items[0].source_unique_id, limit=101)


async def test_batch_limits_future_clock_and_model_copy_bypass_fail(storage):
    with pytest.raises(ValueError, match=r"1\.\.100"):
        await storage.observations.ingest([])
    with pytest.raises(ValueError, match=r"1\.\.100"):
        await storage.observations.ingest([observation()] * 101)
    storage.observations._clock = lambda: AT
    with pytest.raises(ValueError, match="five minutes"):
        await storage.observations.ingest([observation(minute=6)])
    invalid = observation().model_copy(
        update={"fields": ObservationFields.model_construct(price_pcm={"value": True})}
    )
    with pytest.raises(ValidationError):
        await storage.observations.ingest([invalid])
    assert not await storage.observations.history("rightmove:123")


async def test_large_batches_history_pages_and_projection_ties_have_byte_limits(storage):
    items = [observation(f"large-{i}", description=value("x" * 70_000)) for i in range(60)]
    with pytest.raises(ValueError, match="4 MB"):
        await storage.observations.ingest(items)
    assert not await storage.observations.history("rightmove:123")
    await storage.observations.ingest(items[:30])
    await storage.observations.ingest(items[30:])
    first = await storage.observations.history("rightmove:123")
    assert 0 < len(first) < 60
    second = await storage.observations.history("rightmove:123", after=first[-1].sha256)
    assert len(first) + len(second) == 60
    with pytest.raises(ValueError, match="tied inputs"):
        await storage.observations.project("rightmove:123")


async def test_late_and_sparse_updates_never_revive_an_explicit_clear(storage):
    events = [
        observation("clear", minute=5, origin="correction", description={"state": "cleared"}),
        observation("late", minute=4, origin="detail", description=value("Old detail")),
        observation("search", minute=10, description=value("New summary")),
        observation("missing", minute=11),
    ]
    await storage.observations.ingest(events)
    result = await storage.observations.project("rightmove:123")
    assert result.fields["description"].state == "cleared"
    assert result.fields["description"].value is None
    assert result.fields["description"].observation_ids == (events[0].sha256,)


async def test_historical_migration_preserves_exact_rows_and_never_invents_freshness(
    tmp_path, sample_property
):
    path = str(tmp_path / "historical.db")
    # Simulate the version-9 writer as well as its schema: identity recording
    # was introduced in version 11 and must not run while preparing this fixture.
    with (
        patch("home_finder.db.migrations.MIGRATIONS", MIGRATIONS[:9]),
        patch("home_finder.db.storage.record_stored_identities", new_callable=AsyncMock),
    ):
        async with PropertyStorage(path) as old:
            await old.save_property(sample_property)
            await old.update_source_text(
                sample_property.unique_id,
                ListingTextEvidence(description="Retained historical text 漢字"),
            )
            await old.update_user_status(sample_property.unique_id, UserStatus.INTERESTED)
            async with old._transaction() as conn:
                await conn.execute("UPDATE source_listings SET is_backfilled = 1")
            async with old._read_connection() as conn:
                source = dict(
                    await (await conn.execute("SELECT * FROM source_listings")).fetchone()
                )
                property_before = dict(
                    await (await conn.execute("SELECT * FROM properties")).fetchone()
                )
    async with PropertyStorage(path) as updated:
        baseline = await updated.observations.baseline(sample_property.unique_id)
        assert baseline["snapshot"] == source and baseline["source_observed_at"] is None
        assert baseline["classification"] == "historical_projection"
        assert baseline["is_backfilled"] == 1 and baseline["description_state"] == "retained"
        assert not await updated.observations.history(sample_property.unique_id)
        assert not (await updated.observations.project(sample_property.unique_id)).fields
        async with updated._read_connection() as conn:
            assert (
                dict(await (await conn.execute("SELECT * FROM properties")).fetchone())
                == property_before
            )
            assert (
                dict(await (await conn.execute("SELECT * FROM source_listings")).fetchone())
                == source
            )
        with pytest.raises(aiosqlite.IntegrityError, match="immutable"):
            async with updated._transaction() as conn:
                await conn.execute("DELETE FROM source_observation_baselines")


async def test_acknowledged_batch_survives_real_process_exit(tmp_path):
    path = tmp_path / "crash.db"
    item = observation(description=value("Committed before process exit"))
    payload = tmp_path / "observation.json"
    payload.write_text(item.payload_json())
    program = """
import asyncio, os, sys
from pathlib import Path
from home_finder.db import PropertyStorage
from home_finder.models.observations import SourceObservation
async def run():
    db = PropertyStorage(sys.argv[1])
    await db.initialize()
    item = SourceObservation.model_validate_json(Path(sys.argv[2]).read_bytes())
    await db.observations.ingest([item])
    os._exit(23)
asyncio.run(run())
"""
    result = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-c", program, str(path), str(payload)],
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 23, result.stderr
    async with PropertyStorage(str(path)) as restored:
        assert await restored.observations.get(item.sha256) == item
        assert (await restored.observations.project(item.source_unique_id)).fields[
            "description"
        ].value == "Committed before process exit"

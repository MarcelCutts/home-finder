"""Selection transactions, replay and delivery stop decisions using real SQLite."""

import json
import sqlite3
from collections import Counter
from contextlib import closing
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import aiosqlite
import pytest
from pydantic import ValidationError

from home_finder.config import Settings
from home_finder.db import PropertyStorage
from home_finder.db.migrations import migrate_012_notification_deliveries, run_migrations
from home_finder.filters.fit_score import FIT_SCORE_VERSION, compute_fit_score
from home_finder.models import UserStatus
from home_finder.models.analysis import AnalysisFailure
from home_finder.models.delivery import DeliveryPart, DeliveryPlan
from home_finder.models.notifications import NotificationDecision, NotificationPolicy
from home_finder.notifiers.delivery import deliver
from home_finder.pipeline.notification_report import read_report
from tests.support.delivery import notifier, receipt, status
from tests.support.web import web_client


@pytest.fixture
async def storage(tmp_path):
    async with PropertyStorage(str(tmp_path / "selection.db")) as db:
        yield db


async def select(storage, merged, analysis, policy=None):
    await storage.processing.save_pre_analysis_properties([merged], {})
    await storage.processing.complete_analysis(
        merged.unique_id, analysis, notification_policy=policy
    )
    return await storage.deliveries.get(f"discovery:{merged.unique_id}")


@pytest.mark.parametrize("offset,eligible", [(0, True), (1, False)])
async def test_inclusive_score_boundary_is_recorded_with_version(
    storage, make_merged_property, make_quality_analysis, offset, eligible
):
    merged = make_merged_property()
    analysis = make_quality_analysis()
    score = compute_fit_score(
        analysis, merged.canonical.bedrooms, postcode=merged.canonical.postcode
    )
    policy = NotificationPolicy(min_fit_score=score + offset)
    row = await select(storage, merged, analysis, policy)
    decision = NotificationDecision.model_validate_json(row["selection_json"])
    assert decision.fit_score == score
    assert decision.fit_score_version == FIT_SCORE_VERSION
    assert decision.min_fit_score == policy.min_fit_score
    assert decision.policy_version == "owner_notifications_v1"
    assert decision.reason == ("eligible_fit" if eligible else "below_min_fit")
    assert row["state"] == ("queued" if eligible else "suppressed")
    assert (row["snapshot_json"] is not None) == eligible
    assert await status(storage, merged.unique_id) == ("pending" if eligible else "dashboard_only")


@pytest.mark.parametrize("assessment", ["none", "unrated", "failed", "invalid"])
async def test_no_cutoff_can_admit_unassessed_or_failed_evidence(
    storage, make_merged_property, make_quality_analysis, assessment
):
    analysis = make_quality_analysis()
    if assessment == "none":
        analysis = None
    elif assessment == "unrated":
        analysis = analysis.model_copy(update={"overall_rating": None})
    elif assessment == "failed":
        analysis = analysis.model_copy(
            update={
                "analysis_failure": AnalysisFailure(phase="evaluation", kind="malformed_output")
            }
        )
    merged = make_merged_property()
    if assessment == "invalid":
        await storage.processing.save_pre_analysis_properties([merged], {})
        await storage.save_quality_analysis(merged.unique_id, analysis)
        async with storage._transaction() as conn:
            await conn.execute("UPDATE quality_analyses SET analysis_json='not json'")
        analysis = None
    row = await select(storage, merged, analysis, NotificationPolicy(min_fit_score=0))
    assert row["state"] == "suppressed"
    assert row["reason"] == "assessment_unavailable"
    assert json.loads(row["selection_json"])["fit_score"] is None
    assert await storage.deliveries.pending() == []


@pytest.mark.parametrize(
    "stopped,reason",
    [("archived", "user_archived"), ("rejected", "user_rejected"), ("off_market", "off_market")],
)
async def test_current_owner_and_availability_state_override_discovery(
    storage, make_merged_property, make_quality_analysis, stopped, reason
):
    merged = make_merged_property()
    await storage.processing.save_pre_analysis_properties([merged], {})
    if stopped == "off_market":
        await storage.mark_off_market(merged.unique_id)
    else:
        await storage.update_user_status(merged.unique_id, UserStatus(stopped))
    row = await select(
        storage, merged, make_quality_analysis(), NotificationPolicy(min_fit_score=0)
    )
    assert row["state"] == "suppressed" and row["reason"] == reason


async def test_selection_failure_rolls_back_assessment_status_and_intent(
    storage, make_merged_property, make_quality_analysis
):
    merged = make_merged_property()
    await storage.processing.save_pre_analysis_properties([merged], {})
    async with storage._transaction() as conn:
        await conn.execute("""CREATE TRIGGER fail_selection AFTER INSERT ON notification_deliveries
            BEGIN SELECT RAISE(ABORT,'selection crash'); END""")
    with pytest.raises(aiosqlite.IntegrityError, match="selection crash"):
        await storage.processing.complete_analysis(merged.unique_id, make_quality_analysis())
    assert await status(storage, merged.unique_id) == "pending_analysis"
    assert await storage.get_quality_analysis(merged.unique_id) is None
    assert await storage.deliveries.get(f"discovery:{merged.unique_id}") is None
    async with storage._read_connection() as conn:
        row = await (await conn.execute("SELECT analysis_attempts FROM properties")).fetchone()
    assert row[0] == 0


async def test_reenriched_assessment_and_selection_also_share_one_transaction(
    storage, make_merged_property, make_quality_analysis
):
    merged = make_merged_property()
    await storage.processing.save_unenriched_property(merged)
    old = make_quality_analysis(summary="Earlier evidence")
    await storage.save_quality_analysis(merged.unique_id, old)
    async with storage._read_connection() as conn:
        before = dict(await (await conn.execute("SELECT * FROM quality_analyses")).fetchone())
    async with storage._transaction() as conn:
        await conn.execute("""CREATE TRIGGER fail_selection AFTER INSERT ON notification_deliveries
            BEGIN SELECT RAISE(ABORT,'selection crash'); END""")
    with pytest.raises(aiosqlite.IntegrityError, match="selection crash"):
        await storage.processing.complete_analysis(
            merged.unique_id, make_quality_analysis(summary="New evidence")
        )
    assert await status(storage, merged.unique_id) == "pending_enrichment"
    async with storage._read_connection() as conn:
        after = dict(await (await conn.execute("SELECT * FROM quality_analyses")).fetchone())
    assert after == before
    assert await storage.deliveries.get(f"discovery:{merged.unique_id}") is None


async def test_archiving_during_first_send_preserves_receipt_and_stops_second_part(
    storage, make_merged_property, make_quality_analysis
):
    merged = make_merged_property()
    row = await select(
        storage, merged, make_quality_analysis(), NotificationPolicy(min_fit_score=0)
    )
    key = row["event_key"]
    plan = DeliveryPlan(
        parts=(
            DeliveryPart(method="message", payload={"text": "first"}),
            DeliveryPart(method="message", payload={"text": "second"}),
        )
    )
    await storage.deliveries.prepare(key, "123456:123", plan)
    sender, bot = notifier(storage)

    async def archive(**kwargs):
        await storage.update_user_status(merged.unique_id, UserStatus.ARCHIVED)
        return receipt(11)

    bot.send_message.side_effect = archive
    assert not await deliver(sender, storage.deliveries, key)
    bot.send_message.assert_awaited_once()
    saved = await storage.deliveries.get(key)
    assert saved["state"] == "suppressed" and saved["reason"] == "user_archived"
    assert json.loads(saved["receipts_json"]) == [[11]] and saved["next_part"] == 1
    assert storage.deliveries.plan(saved) == plan


async def test_prior_discovery_enables_price_update_below_current_cutoff(
    storage, make_merged_property, make_quality_analysis
):
    merged = make_merged_property(price_pcm=1850)
    await storage.save_merged_property(merged)
    await storage.save_quality_analysis(merged.unique_id, make_quality_analysis())
    await storage.mark_notified(merged.unique_id)
    await storage.detect_and_record_price_change(merged.unique_id, 1750)
    keys = await storage.deliveries.pending()
    assert len(keys) == 1
    row = await storage.deliveries.get(keys[0])
    assert json.loads(row["selection_json"])["reason"] == "prior_discovery"
    sender, bot = notifier(storage)
    assert await deliver(sender, storage.deliveries, keys[0])
    bot.send_message.assert_awaited_once()


async def test_dashboard_only_is_terminal_across_reanalysis_policy_change_and_restart(
    tmp_path, make_merged_property, make_quality_analysis
):
    path = str(tmp_path / "restart.db")
    merged = make_merged_property()
    async with PropertyStorage(path) as storage:
        row = await select(storage, merged, make_quality_analysis())
        assert row["reason"] == "below_min_fit"
        assert json.loads(row["selection_json"])["min_fit_score"] == 60
        await storage.processing.complete_analysis(
            merged.unique_id,
            make_quality_analysis(rating=5),
            notification_policy=NotificationPolicy(min_fit_score=0),
        )
        assert await storage.deliveries.get(row["event_key"]) == row
        client = web_client(storage)
        for suffix in ("", "/card"):
            response = client.get(f"/property/{merged.unique_id}{suffix}")
            assert response.status_code == 200
            assert "Dashboard only" in response.text
            assert "below alert minimum 60 at selection" in response.text
    async with PropertyStorage(path) as storage:
        await storage.deliveries.recover()
        assert await storage.deliveries.pending() == []
        assert await storage.deliveries.get(row["event_key"]) == row
        assert await status(storage, merged.unique_id) == "dashboard_only"


@pytest.mark.parametrize(
    "user_status,off_market,expected",
    [
        ("new", False, "no_prior_interest"),
        ("interested", False, "tracked_home"),
        ("viewed", False, "tracked_home"),
        ("offered", False, "tracked_home"),
        ("archived", False, "user_archived"),
        ("rejected", False, "user_rejected"),
        ("interested", True, "off_market"),
    ],
)
async def test_price_updates_for_explicitly_tracked_dashboard_only_homes(
    storage, make_merged_property, make_quality_analysis, user_status, off_market, expected
):
    merged = make_merged_property(price_pcm=1850)
    discovery = await select(storage, merged, make_quality_analysis())
    assert discovery["state"] == "suppressed"
    await storage.update_user_status(merged.unique_id, UserStatus(user_status))
    if off_market:
        await storage.mark_off_market(merged.unique_id)
    await storage.detect_and_record_price_change(merged.unique_id, 1800)
    assert await storage.detect_and_record_price_change(merged.unique_id, 1800) is None
    async with storage._read_connection() as conn:
        rows = await (
            await conn.execute("SELECT * FROM notification_deliveries WHERE kind='price_drop'")
        ).fetchall()
    assert len(rows) == 1
    row = dict(rows[0])
    assert json.loads(row["selection_json"])["reason"] == expected
    assert json.loads(row["selection_json"])["min_fit_score"] is None
    sender, bot = notifier(storage)
    eligible = expected == "tracked_home"
    assert await deliver(sender, storage.deliveries, row["event_key"]) == eligible
    assert bot.send_message.await_count == int(eligible)
    assert await storage.deliveries.get(discovery["event_key"]) == discovery
    assert await status(storage, merged.unique_id) == "dashboard_only"


@pytest.mark.parametrize("state", ["queued", "ready", "waiting", "blocked", "sending", "uncertain"])
async def test_archiving_stops_only_known_unsent_parts_preserving_plan_and_receipts(
    storage, make_merged_property, make_quality_analysis, state
):
    merged = make_merged_property()
    row = await select(
        storage, merged, make_quality_analysis(), NotificationPolicy(min_fit_score=0)
    )
    key = row["event_key"]
    plan = DeliveryPlan(
        parts=(
            DeliveryPart(method="message", payload={"text": "first"}),
            DeliveryPart(method="message", payload={"text": "second"}),
        )
    )
    await storage.deliveries.prepare(key, "123456:123", plan)
    if state != "queued":
        assert await storage.deliveries.claim(key, "123456:123", 0)
        await storage.deliveries.acknowledge(key, 0, [11])
    async with storage._transaction() as conn:
        await conn.execute(
            "UPDATE notification_deliveries SET state=? WHERE event_key=?", (state, key)
        )
    before = await storage.deliveries.get(key)
    await storage.update_user_status(merged.unique_id, UserStatus.ARCHIVED)
    sender, bot = notifier(storage)
    assert not await deliver(sender, storage.deliveries, key)
    assert not bot.mock_calls
    after = await storage.deliveries.get(key)
    for field in ("plan_json", "plan_sha256", "receipts_json", "next_part", "selection_json"):
        assert after[field] == before[field]
    if state in {"sending", "uncertain"}:
        assert after == before
    else:
        assert after["state"] == "suppressed" and after["reason"] == "user_archived"


async def test_claim_rechecks_archive_after_planning(
    storage, make_merged_property, make_quality_analysis
):
    merged = make_merged_property()
    row = await select(
        storage, merged, make_quality_analysis(), NotificationPolicy(min_fit_score=0)
    )
    plan = DeliveryPlan(parts=(DeliveryPart(method="message", payload={"text": "fixture"}),))
    await storage.deliveries.prepare(row["event_key"], "123456:123", plan)
    await storage.update_user_status(merged.unique_id, UserStatus.ARCHIVED)
    assert not await storage.deliveries.claim(row["event_key"], "123456:123", 0)
    assert (await storage.deliveries.get(row["event_key"]))["reason"] == "user_archived"


@pytest.mark.parametrize("stopped", ["archived", "rejected", "off_market"])
async def test_stopping_rate_limited_alert_preserves_recipient_delay_through_restart(
    tmp_path, make_merged_property, make_quality_analysis, stopped
):
    path = str(tmp_path / "recipient-delay.db")
    now = datetime(2026, 10, 13, tzinfo=UTC)
    homes = [make_merged_property(source_id=f"delay-{index}") for index in range(3)]
    first_plan = DeliveryPlan(
        parts=(
            DeliveryPart(method="message", payload={"text": "first"}),
            DeliveryPart(method="message", payload={"text": "second"}),
        )
    )
    other_plan = DeliveryPlan(parts=(DeliveryPart(method="message", payload={"text": "other"}),))
    async with PropertyStorage(path) as storage:
        storage.deliveries._clock = lambda: now
        keys = []
        for index, home in enumerate(homes):
            row = await select(
                storage, home, make_quality_analysis(), NotificationPolicy(min_fit_score=0)
            )
            keys.append(row["event_key"])
            await storage.deliveries.prepare(
                row["event_key"],
                "123456:456" if index == 2 else "123456:123",
                first_plan if index == 0 else other_plan,
            )
        assert await storage.deliveries.claim(keys[0], "123456:123", 0)
        await storage.deliveries.acknowledge(keys[0], 0, [11])
        assert await storage.deliveries.claim(keys[0], "123456:123", 1)
        await storage.deliveries.fail(keys[0], 1, "waiting", "rate_limited", retry_after=60)
        before = await storage.deliveries.get(keys[0])
        assert not await storage.deliveries.claim(keys[1], "123456:123", 0)

        if stopped == "off_market":
            await storage.mark_off_market(homes[0].unique_id)
        else:
            await storage.update_user_status(homes[0].unique_id, UserStatus(stopped))
        sender, bot = notifier(storage)
        assert not await deliver(sender, storage.deliveries, keys[0])
        assert not bot.mock_calls
        after = await storage.deliveries.get(keys[0])
        assert after["state"] == "suppressed"
        for field in ("plan_json", "plan_sha256", "receipts_json", "next_part", "selection_json"):
            assert after[field] == before[field]
        assert not await storage.deliveries.claim(keys[1], "123456:123", 0)
        assert after["retry_at"] == before["retry_at"]
        # A server delay for one recipient must not stop another recipient.
        assert await storage.deliveries.claim(keys[2], "123456:456", 0)
        await storage.deliveries.acknowledge(keys[2], 0, [21])

    async with PropertyStorage(path) as storage:
        storage.deliveries._clock = lambda: now + timedelta(seconds=59)
        await storage.deliveries.recover()
        sender, bot = notifier(storage)
        assert not await deliver(sender, storage.deliveries, keys[1])
        assert not bot.mock_calls
        storage.deliveries._clock = lambda: now + timedelta(seconds=60)
        assert await deliver(sender, storage.deliveries, keys[1])
        bot.send_message.assert_awaited_once()
        assert await storage.deliveries.get(keys[0]) == after


async def test_shadow_report_matches_admission_without_migrating_or_creating_intents(
    tmp_path, make_merged_property, make_quality_analysis
):
    path = tmp_path / "shadow.db"
    policy = NotificationPolicy()
    async with PropertyStorage(str(path)) as storage:
        decisions = Counter()
        for index, analysis in enumerate(
            [None, make_quality_analysis(), make_quality_analysis(rating=5)]
        ):
            row = await select(
                storage, make_merged_property(source_id=f"shadow-{index}"), analysis, policy
            )
            decisions[json.loads(row["selection_json"])["reason"]] += 1
    # A shadow command must work on older schemas and must never call initialize.
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("DROP TABLE notification_deliveries")
        conn.execute("PRAGMA user_version=11")
        conn.commit()
    before = path.read_bytes()
    report = read_report(path, policy)
    assert report["reasons"] == dict(decisions)
    assert report["total_homes"] == 3 and report["scored_homes"] == 2
    assert report["creates_deliveries"] is False
    assert path.read_bytes() == before


async def test_v15_preserves_old_deliveries_and_v12_does_not_call_runtime_classifier(
    storage, make_merged_property, make_quality_analysis
):
    merged = make_merged_property()
    row = await select(
        storage, merged, make_quality_analysis(), NotificationPolicy(min_fit_score=0)
    )
    async with storage._transaction() as conn:
        await conn.execute("ALTER TABLE notification_deliveries DROP COLUMN selection_json")
        await conn.execute("PRAGMA user_version=14")
    async with aiosqlite.connect(storage.db_path) as conn:
        await run_migrations(conn)
        await run_migrations(conn)
    restored = await storage.deliveries.get(row["event_key"])
    assert restored.pop("selection_json") is None
    row.pop("selection_json")
    assert restored == row
    with patch(
        "home_finder.db.delivery.record_legacy_deliveries",
        new=AsyncMock(side_effect=AssertionError),
    ):
        async with storage._transaction() as conn:
            await migrate_012_notification_deliveries(conn)
    assert await storage.deliveries.pending() == [row["event_key"]]


def test_notification_policy_environment_is_strict_and_versioned(monkeypatch):
    monkeypatch.setenv("HOME_FINDER_NOTIFICATION_POLICY", '{"min_fit_score":65}')
    assert Settings(_env_file=None).notification_policy == NotificationPolicy(min_fit_score=65)
    for value in (-1, 101, True, 59.5, "60"):
        with pytest.raises(ValidationError):
            NotificationPolicy(min_fit_score=value)

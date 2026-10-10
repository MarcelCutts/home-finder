"""Incomplete regrouping requires explicit rehearsal; ordinary ownership still works."""

import json

import pytest
from structlog.testing import capture_logs

from home_finder.db.membership_ops import MembershipConflict
from tests.test_db.test_membership import REVIEW, homes, snapshot

__all__ = ["homes"]  # Reuse the synthetic homes fixture, not an application opt-in.


@pytest.mark.parametrize("operation", ["merge", "split", "reversal", "representative_change"])
async def test_new_reviewed_decisions_are_disabled_without_changing_any_application_rows(
    homes, operation
):
    db, props, targets = homes
    a, b = targets[0].home_id, targets[1].home_id
    merge = await db.memberships.prepare_merge(a, (b,), **REVIEW)
    decision = merge
    if operation != "merge":
        await db.memberships.apply(merge, allow_incomplete_projections=True)
        if operation == "split":
            decision = await db.memberships.prepare_split(a, {props[1].unique_id: b}, **REVIEW)
        elif operation == "reversal":
            decision = await db.memberships.prepare_reversal(merge.decision_id, **REVIEW)
        else:
            decision = await db.memberships.prepare_merge(b, (a,), **REVIEW)
    # Completing the listing-input component cannot certify other consumers.
    await db.projections.rebuild_pending()
    assert not await db.projections.status(pending_only=True)
    original = await snapshot(db)
    with (
        capture_logs() as logs,
        pytest.raises(MembershipConflict, match=r"^reviewed_regrouping_disabled$"),
    ):
        await db.memberships.apply(decision)
    assert await snapshot(db) == original
    refusal = next(event for event in logs if event["event"] == "membership_decision_refused")
    assert refusal["reason"] == "reviewed_regrouping_disabled"
    assert refusal["decision_id"] == decision.decision_id
    assert decision.reason not in json.dumps(logs)
    assert (await db.memberships.audit()).valid


async def test_default_allows_inspection_and_exact_committed_retry_after_split_and_restart(homes):
    db, props, targets = homes
    a, b = targets[0].home_id, targets[1].home_id
    merge = await db.memberships.prepare_merge(a, (b,), **REVIEW)
    await db.memberships.apply(merge, allow_incomplete_projections=True)
    split = await db.memberships.prepare_split(a, {props[1].unique_id: b}, **REVIEW)
    await db.memberships.apply(split, allow_incomplete_projections=True)
    await db.close()
    await db.initialize()
    original = await snapshot(db)
    assert (await db.memberships.inspect((a, b))).scope_home_ids == tuple(sorted((a, b)))
    assert (await db.memberships.audit()).valid
    assert (await db.memberships.apply(merge)).replayed
    assert (await db.memberships.apply(split)).replayed
    assert await snapshot(db) == original
    assert await db.homes.resolve(props[0].unique_id) == targets[0]
    assert await db.homes.resolve(props[1].unique_id) == targets[1]
    with pytest.raises(MembershipConflict, match="different_payload"):
        await db.memberships.apply(merge.model_copy(update={"reason": "Different review"}))
    fresh = await db.memberships.prepare_merge(a, (b,), **REVIEW)
    with pytest.raises(MembershipConflict, match="reviewed_regrouping_disabled"):
        await db.memberships.apply(fresh)
    assert await snapshot(db) == original


@pytest.mark.parametrize("override", [False, "true", 1])
async def test_truthy_or_false_override_is_not_an_explicit_rehearsal(homes, override):
    db, _, targets = homes
    decision = await db.memberships.prepare_merge(
        targets[0].home_id, (targets[1].home_id,), **REVIEW
    )
    original = await snapshot(db)
    with pytest.raises(MembershipConflict, match="reviewed_regrouping_disabled"):
        await db.memberships.apply(decision, allow_incomplete_projections=override)
    assert await snapshot(db) == original

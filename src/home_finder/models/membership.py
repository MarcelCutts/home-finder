"""Reviewable ownership commands. A listing membership is not a user-state merge."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from home_finder.models.home_group import HomeRoute
from home_finder.models.home_identity import is_home_id


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MemberAssignment(_FrozenModel):
    source_unique_id: str = Field(min_length=1)
    home_id: str | None


class PersonalState(_FrozenModel):
    """Original home provenance; message contents and event notes stay in their tables."""

    home_id: str
    property_id: str | None
    user_status: str | None
    last_status_event_id: int
    viewing_message_sha256: str | None


class MembershipSnapshot(_FrozenModel):
    revision: int = Field(ge=0)
    scope_home_ids: tuple[str, ...]
    members: tuple[MemberAssignment, ...]
    source_evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    personal_states: tuple[PersonalState, ...]
    routes: tuple[HomeRoute, ...] = ()

    @property
    def has_status_conflict(self) -> bool:
        return len({state.user_status for state in self.personal_states if state.property_id}) > 1

    @property
    def has_message_conflict(self) -> bool:
        return len({s.viewing_message_sha256 for s in self.personal_states} - {None}) > 1


class MembershipDecision(_FrozenModel):
    """The exact reviewed map is the idempotency payload, including its revision."""

    version: Literal[1, 2] = 1
    decision_id: str = Field(pattern=r"^md_[0-9a-f]{32}$")
    operation: Literal["merge", "split", "reversal"]
    actor: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=2000)
    evidence: tuple[str, ...] = Field(min_length=1, max_length=50)
    before: MembershipSnapshot
    after: tuple[MemberAssignment, ...]
    after_routes: tuple[HomeRoute, ...] = ()
    reverses: str | None = None

    @model_validator(mode="after")
    def validate_map(self) -> MembershipDecision:
        if not self.actor.strip() or not self.reason.strip():
            raise ValueError("Actor and reason must contain text")
        if any(not ref.strip() or len(ref) > 2000 for ref in self.evidence):
            raise ValueError("Evidence references must be nonempty and bounded")
        if (self.operation == "reversal") != (self.reverses is not None):
            raise ValueError("Only a reversal names an earlier decision")
        scope = self.before.scope_home_ids
        if not scope or tuple(sorted(set(scope))) != scope or not all(map(is_home_id, scope)):
            raise ValueError("Scope must contain sorted unique stable home IDs")
        before_ids = tuple(m.source_unique_id for m in self.before.members)
        after_ids = tuple(m.source_unique_id for m in self.after)
        if (
            (not before_ids and self.version == 1)
            or before_ids != tuple(sorted(set(before_ids)))
            or after_ids != before_ids
        ):
            raise ValueError("Before and after must cover the same sorted unique source IDs")
        if any(
            m.home_id not in scope
            and not (self.version == 2 and self.operation == "reversal" and m.home_id is None)
            for m in (*self.before.members, *self.after)
        ):
            raise ValueError("Reviewed changes require existing homes in the review scope")
        if self.version == 2:
            if (
                tuple(r.home_id for r in self.before.routes) != scope
                or tuple(r.home_id for r in self.after_routes) != scope
            ):
                raise ValueError("Version 2 requires complete sorted before/after routes")
            if any(
                r.target_home_id is not None and r.target_home_id not in scope
                for r in (*self.before.routes, *self.after_routes)
            ):
                raise ValueError("Routing destination must be in scope")
        elif self.before.routes or self.after_routes:
            raise ValueError("Version 1 cannot contain routing")
        if self.before.members == self.after and self.before.routes == self.after_routes:
            raise ValueError("A decision must change membership")
        return self


class MembershipReceipt(_FrozenModel):
    decision_id: str
    revision: int
    changed: int
    routes_changed: int = 0
    replayed: bool = False


class MembershipAudit(_FrozenModel):
    revision: int
    decisions: int
    retained_source_memberships: int
    errors: dict[str, int]
    valid: bool

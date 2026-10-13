"""Versioned single-owner notification selection, separate from transport state."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class NotificationPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal["owner_notifications_v1"] = "owner_notifications_v1"
    min_fit_score: int = Field(default=60, strict=True, ge=0, le=100)


SelectionReason = Literal[
    "eligible_fit",
    "below_min_fit",
    "assessment_unavailable",
    "user_archived",
    "user_rejected",
    "off_market",
    "tracked_home",
    "prior_discovery",
    "no_prior_interest",
]


class NotificationDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_version: Literal["owner_notifications_v1"] = "owner_notifications_v1"
    kind: Literal["discovery", "price_drop"]
    disposition: Literal["alert", "dashboard_only"]
    reason: SelectionReason
    fit_score: int | None = None
    fit_score_version: int | None = None
    min_fit_score: int | None = None


TRACKED_USER_STATUSES = frozenset(
    {"interested", "enquired", "viewing_booked", "viewed", "applied", "offered"}
)

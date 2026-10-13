"""Pure decisions shared by transactional admission and read-only shadow review."""

from home_finder.filters.fit_score import FIT_SCORE_VERSION, AnalysisInput, compute_fit_score
from home_finder.models.notifications import (
    TRACKED_USER_STATUSES,
    NotificationDecision,
    NotificationPolicy,
    SelectionReason,
)


def stopped_reason(user_status: str, is_off_market: bool) -> SelectionReason | None:
    if user_status == "archived":
        return "user_archived"
    if user_status == "rejected":
        return "user_rejected"
    return "off_market" if is_off_market else None


def select_discovery(
    analysis: AnalysisInput,
    bedrooms: int,
    *,
    postcode: str | None,
    user_status: str,
    is_off_market: bool,
    policy: NotificationPolicy,
) -> NotificationDecision:
    score = compute_fit_score(analysis, bedrooms, postcode=postcode)
    reason = stopped_reason(user_status, is_off_market)
    if reason is None:
        if score is None:
            reason = "assessment_unavailable"
        elif score < policy.min_fit_score:
            reason = "below_min_fit"
        else:
            reason = "eligible_fit"
    return NotificationDecision(
        kind="discovery",
        disposition="alert" if reason == "eligible_fit" else "dashboard_only",
        reason=reason,
        fit_score=score,
        fit_score_version=FIT_SCORE_VERSION,
        min_fit_score=policy.min_fit_score,
        policy_version=policy.version,
    )


def select_price_drop(
    *, user_status: str, is_off_market: bool, has_discovery_interest: bool
) -> NotificationDecision:
    """Updates for announced/explicitly tracked homes do not reapply discovery fit."""
    reason = stopped_reason(user_status, is_off_market)
    if reason is None:
        if user_status in TRACKED_USER_STATUSES:
            reason = "tracked_home"
        elif has_discovery_interest:
            reason = "prior_discovery"
        else:
            reason = "no_prior_interest"
    return NotificationDecision(
        kind="price_drop",
        disposition="alert" if reason in {"tracked_home", "prior_discovery"} else "dashboard_only",
        reason=reason,
    )

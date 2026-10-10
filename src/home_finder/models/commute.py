"""Measured commute outcomes; unknown results never imply a zero-minute pass."""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from home_finder.models.core import TransportMode


class CommuteState(StrEnum):
    WITHIN_LIMIT = "within_limit"
    OVER_LIMIT = "over_limit"
    UNREACHABLE = "unreachable_within_search"
    MISSING_COORDINATES = "missing_coordinates"
    DESTINATION_UNRESOLVED = "destination_unresolved"
    PROVIDER_ERROR = "provider_error"
    INVALID_RESPONSE = "invalid_response"
    NOT_CONFIGURED = "not_configured"


class CommuteResult(BaseModel):
    """One mode's result for a property, compared to the exact limit in seconds."""

    model_config = ConfigDict(frozen=True)

    property_id: str
    destination_postcode: str
    transport_mode: TransportMode
    limit_seconds: int = Field(gt=0, strict=True)
    state: CommuteState
    travel_time_seconds: int | None = Field(default=None, ge=0, strict=True)

    @model_validator(mode="after")
    def coherent_outcome(self) -> Self:
        measured = self.state in (CommuteState.WITHIN_LIMIT, CommuteState.OVER_LIMIT)
        if measured != (self.travel_time_seconds is not None):
            raise ValueError("Only measured commute outcomes have a travel duration")
        if self.travel_time_seconds is not None:
            expected = (
                CommuteState.WITHIN_LIMIT
                if self.travel_time_seconds <= self.limit_seconds
                else CommuteState.OVER_LIMIT
            )
            if self.state != expected:
                raise ValueError("Commute state must use the exact duration and limit in seconds")
        return self

    @property
    def within_limit(self) -> bool | None:
        if self.state == CommuteState.WITHIN_LIMIT:
            return True
        if self.state == CommuteState.OVER_LIMIT:
            return False
        return None

    @property
    def travel_time_minutes(self) -> int | None:
        seconds = self.travel_time_seconds
        return (seconds + 59) // 60 if seconds is not None else None


def best_measured_commutes(results: list[CommuteResult]) -> dict[str, CommuteResult]:
    """Choose by seconds across modes, including known journeys over the limit."""
    chosen: dict[str, CommuteResult] = {}
    for result in results:
        seconds = result.travel_time_seconds
        if seconds is None:
            continue
        previous = chosen.get(result.property_id)
        if previous is None:
            chosen[result.property_id] = result
        else:
            assert previous.travel_time_seconds is not None
            if seconds < previous.travel_time_seconds:
                chosen[result.property_id] = result
    return chosen


def commute_minutes_projection(
    results: list[CommuteResult],
) -> dict[str, tuple[int, TransportMode]]:
    """Bridge to the existing minute projection; unknowns are never written as 0."""
    projection = {}
    for uid, result in best_measured_commutes(results).items():
        minutes = result.travel_time_minutes
        assert minutes is not None
        projection[uid] = (minutes, result.transport_mode)
    return projection

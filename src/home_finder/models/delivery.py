"""Bounded Telegram delivery snapshots; no provider secrets or retry history."""

from typing import Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field

from home_finder.models import MergedProperty, PropertyQualityAnalysis, TransportMode

UnfinishedDeliveryState: TypeAlias = Literal[
    "queued", "ready", "sending", "waiting", "uncertain", "blocked", "review"
]


class DiscoverySnapshot(BaseModel):
    merged: MergedProperty
    commute_minutes: int | None = None
    transport_mode: TransportMode | None = None
    quality_analysis: PropertyQualityAnalysis | None = None


class DeliveryPart(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    method: Literal["message", "photo", "album", "venue"]
    payload: dict[str, Any]

    @property
    def receipt_count(self) -> int:
        return len(self.payload["photos"]) if self.method == "album" else 1


class DeliveryPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: Literal[1] = 1
    parts: tuple[DeliveryPart, ...] = Field(min_length=1, max_length=3)

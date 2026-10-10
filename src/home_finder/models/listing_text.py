"""Current source text, with explicit missing/correction and freshness rules.

This is a small current-value contract. Legacy text has no invented
observation date; only actual search/detail receipts carry freshness.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

TextOrigin = Literal["legacy", "search", "detail", "correction"]


class ListingTextInputs(BaseModel):
    """Application-selected listing text sent to both model phases.

    Hashes refer to the full normalized source evidence. These bounded inputs
    survive later source refreshes without requiring a full revision journal.
    """

    model_config = ConfigDict(frozen=True)
    policy_version: int = 1
    description: str | None = None
    features: tuple[str, ...] = ()
    source_hashes: dict[str, str] = Field(default_factory=dict)
    source_urls: dict[str, str] = Field(default_factory=dict)


class ListingTextEvidence(BaseModel):
    """Description and features can be observed independently.

    None means unavailable. Empty values clear a field only through an explicit
    dated correction. Search/legacy input cannot replace retained detail text.
    """

    model_config = ConfigDict(frozen=True)

    description: str | None = None
    description_observed_at: datetime | None = None
    description_origin: TextOrigin = "legacy"
    features: tuple[str, ...] | None = None
    features_observed_at: datetime | None = None
    features_origin: TextOrigin = "legacy"

    @field_validator("features")
    @classmethod
    def nonempty_features(cls, values: tuple[str, ...] | None) -> tuple[str, ...] | None:
        return tuple(value for value in values if value.strip()) if values is not None else None

    @field_validator("description_observed_at", "features_observed_at")
    @classmethod
    def aware_timestamp(cls, value: datetime | None) -> datetime | None:
        if value is not None:
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("Text observation timestamps must include a timezone")
            return value.astimezone(UTC)
        return value

    @classmethod
    def observed(
        cls,
        *,
        observed_at: datetime,
        description: str | None = None,
        features: list[str] | tuple[str, ...] | None = None,
        origin: Literal["search", "detail", "correction"] = "detail",
    ) -> ListingTextEvidence:
        return cls(
            description=description,
            description_observed_at=observed_at if description is not None else None,
            description_origin=origin,
            features=tuple(features) if features is not None else None,
            features_observed_at=observed_at if features is not None else None,
            features_origin=origin,
        )


def _accept(
    incoming: str | tuple[str, ...] | None,
    observed_at: datetime | None,
    origin: TextOrigin,
    previous: str | tuple[str, ...] | None,
    previous_at: datetime | None,
    previous_origin: TextOrigin,
) -> bool:
    if incoming is None:
        return False
    empty = not incoming or (isinstance(incoming, str) and not incoming.strip())
    if empty and (origin != "correction" or observed_at is None):
        return False
    # Authority comes before receipt time: search summaries never overwrite
    # detail text, and a delayed detail receipt can enrich a newer summary.
    authority = {"legacy": 0, "search": 1, "detail": 2, "correction": 2}
    if previous is not None and authority[origin] != authority[previous_origin]:
        return authority[origin] > authority[previous_origin] and observed_at is not None
    if previous_at is not None and (observed_at is None or observed_at <= previous_at):
        return False
    # Undated input can fill a gap, but cannot claim to be a newer correction.
    return not (observed_at is None and previous)


def merge_listing_text(
    previous: ListingTextEvidence, incoming: ListingTextEvidence
) -> ListingTextEvidence:
    description = _accept(
        incoming.description,
        incoming.description_observed_at,
        incoming.description_origin,
        previous.description,
        previous.description_observed_at,
        previous.description_origin,
    )
    features = _accept(
        incoming.features,
        incoming.features_observed_at,
        incoming.features_origin,
        previous.features,
        previous.features_observed_at,
        previous.features_origin,
    )
    desc = incoming if description else previous
    feats = incoming if features else previous
    return ListingTextEvidence(
        description=desc.description,
        description_observed_at=desc.description_observed_at,
        description_origin=desc.description_origin,
        features=feats.features,
        features_observed_at=feats.features_observed_at,
        features_origin=feats.features_origin,
    )

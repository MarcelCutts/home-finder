"""Current source text, with explicit missing/correction and freshness rules.

This current-value contract can reference immutable source observations.
Legacy text has no invented observation date or evidence identifier.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from home_finder.models.observations import SourceObservation

TextOrigin = Literal["legacy", "search", "detail", "correction"]


class ListingTextInputs(BaseModel):
    """Application-selected listing text sent to both model phases.

    Hashes refer to the full normalized source evidence. These bounded inputs
    survive later source refreshes; identifiers link newly captured evidence
    to its immutable source observation when available.
    """

    model_config = ConfigDict(frozen=True)
    policy_version: int = 1
    description: str | None = None
    features: tuple[str, ...] = ()
    source_hashes: dict[str, str] = Field(default_factory=dict)
    source_urls: dict[str, str] = Field(default_factory=dict)
    source_observation_ids: dict[str, tuple[str, ...]] = Field(default_factory=dict)


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
    description_observation_ids: tuple[Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")], ...] = ()
    features_observation_ids: tuple[Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")], ...] = ()

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
    def from_observation(cls, observation: SourceObservation) -> ListingTextEvidence:
        """Reference only the exact fields in a retained source observation."""
        description = observation.fields.description
        features = observation.fields.features
        return cls(
            description=("" if description.state == "cleared" else description.value)
            if description
            else None,
            description_observed_at=observation.observed_at if description else None,
            description_origin=observation.origin,
            features=(() if features.state == "cleared" else features.value) if features else None,
            features_observed_at=observation.observed_at if features else None,
            features_origin=observation.origin,
            description_observation_ids=(observation.sha256,) if description else (),
            features_observation_ids=(observation.sha256,) if features else (),
        )

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
    if previous_at is not None and (observed_at is None or observed_at <= previous_at):
        return False
    if previous_origin in {"detail", "correction"} and origin in {"legacy", "search"}:
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
        description_observation_ids=desc.description_observation_ids,
        features_observation_ids=feats.features_observation_ids,
    )

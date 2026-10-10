"""Current source text, with explicit missing/correction and freshness rules.

This current-value contract can reference immutable source observations.
Legacy text has no invented observation date or evidence identifier.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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
    source_labels: dict[str, str] = Field(default_factory=dict)
    omitted_description_keys: tuple[str, ...] = ()
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
    description_observation_ids: tuple[Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")], ...] = (
        Field(default=(), max_length=512)
    )
    features_observation_ids: tuple[Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")], ...] = Field(
        default=(), max_length=512
    )
    description_conflicting: bool = False
    features_conflicting: bool = False

    @model_validator(mode="after")
    def explicit_conflicts(self) -> ListingTextEvidence:
        for name in ("description", "features"):
            if getattr(self, f"{name}_conflicting") and (
                getattr(self, name)
                or getattr(self, f"{name}_observed_at") is None
                or len(set(getattr(self, f"{name}_observation_ids"))) < 2
            ):
                raise ValueError("A text conflict needs dated evidence IDs and no chosen value")
        return self

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


@dataclass(frozen=True)
class _TextField:
    value: str | tuple[str, ...] | None
    observed_at: datetime | None
    origin: TextOrigin
    ids: tuple[str, ...]
    conflicting: bool

    @property
    def present(self) -> bool:
        if self.conflicting:
            return True
        if self.value is None:
            return False
        if self.value and (not isinstance(self.value, str) or self.value.strip()):
            return True
        return self.origin == "correction" and self.observed_at is not None

    @property
    def authority(self) -> int:
        return 2 if self.origin in {"detail", "correction"} else 1 if self.origin == "search" else 0


def _merge_field(
    previous: _TextField, incoming: _TextField, *, empty: str | tuple[str, ...]
) -> _TextField:
    if not incoming.present:
        return previous
    if not previous.present:
        return incoming
    # Undated historical text remains a separate category. It can fill a gap,
    # but cannot claim a new capture time or revise an existing dated value.
    if incoming.observed_at is None:
        return previous
    if previous.observed_at is None:
        return incoming if incoming.authority >= previous.authority else previous

    previous_rank = (previous.authority, previous.observed_at)
    incoming_rank = (incoming.authority, incoming.observed_at)
    if incoming_rank != previous_rank:
        return incoming if incoming_rank > previous_rank else previous

    conflict = previous.conflicting or incoming.conflicting or previous.value != incoming.value
    if incoming.conflicting and not previous.ids:
        return incoming
    if conflict and not (previous.ids and incoming.ids):
        return previous  # Do not invent immutable provenance for legacy disagreement.
    identifiers = tuple(sorted(set(previous.ids) | set(incoming.ids)))
    if len(identifiers) > 512:
        raise ValueError("Too many tied text observations; adjudication is required")
    # At equal authority this makes mixed detail/correction provenance stable;
    # the exact original origins remain in the referenced immutable observations.
    origin: TextOrigin = (
        "correction"
        if "correction" in {previous.origin, incoming.origin}
        else min(previous.origin, incoming.origin)
    )
    return _TextField(
        empty if conflict else previous.value, previous.observed_at, origin, identifiers, conflict
    )


def _field(evidence: ListingTextEvidence, name: Literal["description", "features"]) -> _TextField:
    if name == "description":
        return _TextField(
            evidence.description,
            evidence.description_observed_at,
            evidence.description_origin,
            evidence.description_observation_ids,
            evidence.description_conflicting,
        )
    return _TextField(
        evidence.features,
        evidence.features_observed_at,
        evidence.features_origin,
        evidence.features_observation_ids,
        evidence.features_conflicting,
    )


def merge_listing_text(
    previous: ListingTextEvidence, incoming: ListingTextEvidence
) -> ListingTextEvidence:
    desc = _merge_field(_field(previous, "description"), _field(incoming, "description"), empty="")
    feats = _merge_field(_field(previous, "features"), _field(incoming, "features"), empty=())
    return ListingTextEvidence(
        description=cast(str | None, desc.value),
        description_observed_at=desc.observed_at,
        description_origin=desc.origin,
        features=cast(tuple[str, ...] | None, feats.value),
        features_observed_at=feats.observed_at,
        features_origin=feats.origin,
        description_observation_ids=desc.ids,
        features_observation_ids=feats.ids,
        description_conflicting=desc.conflicting,
        features_conflicting=feats.conflicting,
    )

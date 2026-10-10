"""Versioned listing inputs for a home; derived prices/assets are separate consumers."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from home_finder.models.home_group import HomeRoute
from home_finder.models.identity import SourceIdentityResolution
from home_finder.models.listing_member import ListingMember
from home_finder.models.observations import Digest, SourceProjection, canonical_json, sha256_text

LISTING_PROJECTION_VERSION: Literal[1] = 1
MAX_HOME_PROJECTION_BYTES = 4_000_000


class ProjectedListingInput(BaseModel):
    """Keep retained columns, exact observations and identity claims distinguishable."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    member: ListingMember
    identity: SourceIdentityResolution
    observed: SourceProjection
    field_material_hashes: dict[str, Digest]
    retained_columns: dict[str, JsonValue]

    @model_validator(mode="after")
    def matching_representation(self) -> ProjectedListingInput:
        if (
            self.member.source_unique_id != self.observed.source_unique_id
            or self.identity.representation.unique_id != self.member.source_unique_id
        ):
            raise ValueError("Projected evidence belongs to a different representation")
        if set(self.observed.fields) != set(self.field_material_hashes):
            raise ValueError("Every projected field needs its material-content reference")
        return self

    def material_content(self) -> dict[str, JsonValue]:
        """Exclude receipt times/references while retaining conflicting alternatives."""
        member = self.member
        return {
            "source_unique_id": member.source_unique_id,
            "source": member.source.value,
            "source_id": member.source_id,
            "url": str(member.url),
            "price_pcm": member.price_pcm,
            "is_backfilled": member.is_backfilled,
            "is_off_market": member.is_off_market,
            "text": {
                "description": member.text.description,
                "features": list(member.text.features)
                if member.text.features is not None
                else None,
                "description_conflicting": member.text.description_conflicting,
                "features_conflicting": member.text.features_conflicting,
            },
            "identity": self.identity.model_dump(mode="json"),
            "field_material_hashes": dict(self.field_material_hashes),
            "retained_columns": {
                key: value
                for key, value in self.retained_columns.items()
                if key not in {"first_seen", "last_seen", "last_checked_at", "off_market_since"}
            },
        }


class HomeListingProjection(BaseModel):
    """An immutable input snapshot, not a claim that downstream projections are current."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    policy_version: Literal[1] = LISTING_PROJECTION_VERSION
    home_id: str
    property_id: str | None
    route: HomeRoute
    input_generation: int = Field(ge=1)
    membership_revision: int = Field(ge=0)
    listings: tuple[ProjectedListingInput, ...] = Field(max_length=500)

    @model_validator(mode="after")
    def consistent_home(self) -> HomeListingProjection:
        if self.route.home_id != self.home_id:
            raise ValueError("Projection routing belongs to another home")
        keys = [item.member.evidence_key for item in self.listings]
        if keys != sorted(set(keys)):
            raise ValueError("Projection members must be unique and ordered by stored identity")
        if self.listings and (
            self.route.state != "active" or self.route.target_home_id != self.home_id
        ):
            raise ValueError("Only an active group root may project owned listings")
        return self

    @property
    def payload_json(self) -> str:
        return canonical_json(self.model_dump(mode="json"))

    @property
    def sha256(self) -> str:
        return sha256_text(self.payload_json)

    @property
    def listing_material_sha256(self) -> str:
        # This is intentionally scoped to listing inputs. It is not the complete
        # paid-assessment key (asset bytes, preprocessing and model policy follow).
        return sha256_text(
            canonical_json(
                {
                    "policy_version": self.policy_version,
                    "home_id": self.home_id,
                    "property_id": self.property_id,
                    "route": self.route.model_dump(mode="json"),
                    "listings": [item.material_content() for item in self.listings],
                }
            )
        )


class ProjectionStatus(BaseModel):
    model_config = ConfigDict(frozen=True)

    home_id: str
    input_generation: int
    built_generation: int | None
    snapshot_id: str | None
    pending: bool
    cause: str


class ProjectionBuild(BaseModel):
    model_config = ConfigDict(frozen=True)

    home_id: str
    snapshot_id: str
    input_generation: int
    listing_material_sha256: str
    membership_invalidations_acknowledged: int
    replayed: bool

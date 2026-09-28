"""Versioned source evidence; user preferences and area estimates are separate."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import UTC, date, datetime
from typing import Annotated, Any, Generic, Literal, TypeVar

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    StringConstraints,
    field_validator,
    model_validator,
)

from home_finder.models.sources import PropertySource

MAX_OBSERVATION_BYTES = 250_000
MAX_BATCH_BYTES = 4_000_000
_T = TypeVar("_T")
Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
ShortText = Annotated[str, StringConstraints(min_length=1, max_length=1000, pattern=r"\S")]


def canonical_json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _public_url(value: HttpUrl) -> HttpUrl:
    # The wire contract accepts canonical public URLs only. Preserve response
    # bodies separately; never smuggle credentials/signed URLs through metadata.
    if value.username or value.password or value.query or value.fragment:
        raise ValueError("Evidence URLs must have no credentials, query or fragment")
    return value


PublicURL = Annotated[HttpUrl, AfterValidator(_public_url)]


class _EvidenceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FieldChange(_EvidenceModel, Generic[_T]):
    """Null/omitted fields mean unavailable; clearing is a separate operation."""

    state: Literal["observed", "cleared"] = "observed"
    value: _T | None = None

    @model_validator(mode="after")
    def explicit_value_or_clear(self) -> FieldChange[_T]:
        if self.state == "cleared":
            if self.value is not None:
                raise ValueError("Clearing a field cannot supply a value")
        elif (
            self.value is None
            or (isinstance(self.value, str) and not self.value.strip())
            or (isinstance(self.value, tuple) and not self.value)
        ):
            raise ValueError("An observed field needs a nonempty value, otherwise omit or clear it")
        return self


class SourceCoordinates(_EvidenceModel):
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False, strict=True)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False, strict=True)


class PortalDate(_EvidenceModel):
    """Keep literal source wording, date precision and meaning distinct."""

    literal: str = Field(min_length=1, max_length=300)
    meaning: Literal["listed", "first_visible", "reduced", "relisted", "available", "unspecified"]
    calendar_date: date | None = None
    instant: AwareDatetime | None = None

    @field_validator("instant")
    @classmethod
    def utc_instant(cls, value: datetime | None) -> datetime | None:
        return value.astimezone(UTC) if value is not None else None

    @model_validator(mode="after")
    def single_precision(self) -> PortalDate:
        if self.calendar_date is not None and self.instant is not None:
            raise ValueError("Source date precision must be a date or an instant, not both")
        return self


class AgentReference(_EvidenceModel):
    agent_id: str | None = Field(default=None, min_length=1, max_length=200)
    agent_name: str | None = Field(default=None, min_length=1, max_length=300)
    branch_id: str | None = Field(default=None, min_length=1, max_length=200)
    branch_name: str | None = Field(default=None, min_length=1, max_length=300)

    @model_validator(mode="after")
    def source_identifier_present(self) -> AgentReference:
        if not self.agent_id and not self.branch_id:
            raise ValueError("Agent/branch metadata requires an explicit source identifier")
        return self


class ObservationFields(_EvidenceModel):
    title: FieldChange[ShortText] | None = None
    price_pcm: FieldChange[Annotated[int, Field(ge=0, le=1_000_000, strict=True)]] | None = None
    bedrooms: FieldChange[Annotated[int, Field(ge=0, le=100, strict=True)]] | None = None
    address: (
        FieldChange[Annotated[str, StringConstraints(min_length=1, max_length=2000)]] | None
    ) = None
    postcode: FieldChange[Annotated[str, StringConstraints(min_length=1, max_length=16)]] | None = (
        None
    )
    coordinates: FieldChange[SourceCoordinates] | None = None
    description: FieldChange[Annotated[str, StringConstraints(max_length=100_000)]] | None = None
    features: FieldChange[Annotated[tuple[ShortText, ...], Field(max_length=100)]] | None = None
    gallery_urls: FieldChange[Annotated[tuple[PublicURL, ...], Field(max_length=50)]] | None = None
    floorplan_urls: FieldChange[Annotated[tuple[PublicURL, ...], Field(max_length=20)]] | None = (
        None
    )
    observed_floorplan_count: (
        FieldChange[Annotated[int, Field(ge=0, le=100, strict=True)]] | None
    ) = None
    portal_date: FieldChange[PortalDate] | None = None
    first_visible_date: FieldChange[PortalDate] | None = None
    availability_date: FieldChange[PortalDate] | None = None
    agent: FieldChange[AgentReference] | None = None


class RawCaptureReference(_EvidenceModel):
    """An external private capture reference, not a claim of locally verified bytes."""

    capture_manifest_sha256: Digest
    record_index: int = Field(ge=0, le=11, strict=True)
    response_sha256: Digest
    response_bytes: int = Field(gt=0, le=8_000_000, strict=True)


class ObservationParser(_EvidenceModel):
    name: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_.:-]+$")
    version: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_.:-]+$")
    code_sha256: Digest | None = None


class SourceObservation(_EvidenceModel):
    schema_version: Literal[1] = 1
    producer_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}$")
    event_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}$")
    source: PropertySource
    source_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}$")
    listing_url: PublicURL
    origin: Literal["search", "detail", "correction"]
    observed_at: AwareDatetime
    parser: ObservationParser
    raw_capture: RawCaptureReference | None = None
    normalization_omissions: (
        dict[Literal["gallery_urls", "floorplan_urls"], Literal["unsupported_url"]] | None
    ) = None
    correction_reason: str | None = Field(default=None, min_length=1, max_length=1000)
    fields: ObservationFields = Field(default_factory=ObservationFields)

    @field_validator("observed_at")
    @classmethod
    def utc_observation(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def correction_and_limits(self) -> SourceObservation:
        if self.origin == "correction":
            if not self.correction_reason or not self.correction_reason.strip():
                raise ValueError("Explicit corrections require a reason")
        elif self.correction_reason is not None or self.parser.code_sha256 is None:
            raise ValueError(
                "Parsed observations require a parser checksum and no correction reason"
            )
        for name in ObservationFields.model_fields:
            change = getattr(self.fields, name)
            if change is not None and name in (self.normalization_omissions or {}):
                raise ValueError("An omitted field cannot also supply observed evidence")
            if change is not None and change.state == "cleared" and self.origin != "correction":
                raise ValueError("Only an explicit correction can clear a field")
        if len(self.payload_json().encode()) > MAX_OBSERVATION_BYTES:
            raise ValueError("Observation exceeds the 250 KB normalized-input bound")
        return self

    @property
    def source_unique_id(self) -> str:
        return f"{self.source.value}:{self.source_id}"

    def payload_json(self) -> str:
        # Ingestion revalidates this JSON. Avoid serializer warnings that could
        # disclose an invalid in-process value before that validation fails.
        return canonical_json(self.model_dump(mode="json", exclude_none=True, warnings=False))

    @property
    def sha256(self) -> str:
        return sha256_text(self.payload_json())


def field_authority(name: str, origin: str) -> int:
    """Version 1: detail/corrections outrank search text, independent of arrival."""
    return 2 if name in {"description", "features"} and origin in {"detail", "correction"} else 1


class ProjectedField(_EvidenceModel):
    state: Literal["observed", "cleared", "conflicting"]
    value: Any = None
    observed_at: AwareDatetime
    authority: int
    observation_ids: tuple[Digest, ...]
    alternative_count: int


class SourceProjection(_EvidenceModel):
    policy_version: Literal[1] = 1
    source_unique_id: str
    fields: dict[str, ProjectedField]

    @property
    def sha256(self) -> str:
        return sha256_text(canonical_json(self.model_dump(mode="json")))


def project_field_rows(source_unique_id: str, rows: list[dict[str, Any]]) -> SourceProjection:
    """Deterministic replay; equally authoritative disagreement stays explicit."""
    by_field: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_field[row["field_name"]].append(row)
    result = {}
    for name, candidates in sorted(by_field.items()):
        rank = max((row["authority"], row["observed_at"]) for row in candidates)
        winners = [row for row in candidates if (row["authority"], row["observed_at"]) == rank]
        alternatives = {(row["field_state"], row["value_json"]) for row in winners}
        state, value_json = min(alternatives)
        result[name] = ProjectedField(
            state="conflicting" if len(alternatives) > 1 else state,
            value=None if len(alternatives) > 1 else json.loads(value_json),
            observed_at=rank[1],
            authority=rank[0],
            observation_ids=tuple(sorted({row["observation_id"] for row in winners})),
            alternative_count=len(alternatives),
        )
    return SourceProjection(source_unique_id=source_unique_id, fields=result)


def observation_field_rows(observation: SourceObservation) -> list[dict[str, Any]]:
    rows = []
    digest = observation.sha256
    for name, change in observation.fields.model_dump(mode="json", exclude_none=True).items():
        rows.append(
            {
                "observation_id": digest,
                "source_unique_id": observation.source_unique_id,
                "field_name": name,
                "authority": field_authority(name, observation.origin),
                "observed_at": observation.observed_at.isoformat(timespec="microseconds"),
                "field_state": change["state"],
                "value_json": canonical_json(change.get("value")),
            }
        )
    return rows

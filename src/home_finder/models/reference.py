"""Human reference labels, distinct from model output and owner preferences."""

from decimal import Decimal
from typing import Any, Final, Generic, Literal, TypeVar

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    field_validator,
    model_validator,
)

RUBRIC_VERSION: Final = "home-finder-reference-v1"
_Value = TypeVar("_Value")


class EvidenceCitation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    material_id: str = Field(pattern=r"^(image|text):[0-9a-f]{64}$")
    location: str = Field(min_length=1, max_length=300)
    quote: str | None = Field(default=None, min_length=1, max_length=1000)


class ReferenceFact(BaseModel, Generic[_Value]):
    model_config = ConfigDict(extra="forbid", frozen=True)

    state: Literal["unreviewed", "supported", "unknown", "conflicting"] = "unreviewed"
    value: _Value | None = None
    evidence: list[EvidenceCitation] = Field(default_factory=list, max_length=8)
    reason: str | None = Field(default=None, min_length=1, max_length=1000)

    @model_validator(mode="after")
    def explicit_evidence_state(self) -> "ReferenceFact[_Value]":
        if self.state == "unreviewed":
            if self.value is not None or self.evidence or self.reason is not None:
                raise ValueError("Unreviewed labels cannot contain a reference answer")
        elif self.state == "supported":
            if self.value is None or not self.evidence:
                raise ValueError("Supported labels require a value and evidence")
        else:
            if self.value is not None or not self.reason:
                raise ValueError("Unknown/conflicting labels require a reason and no value")
            if (
                self.state == "conflicting"
                and len({(item.material_id, item.location, item.quote) for item in self.evidence})
                < 2
            ):
                raise ValueError("Conflicting labels require both evidence references")
        return self


class PrintedArea(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    amount: Decimal = Field(gt=0, le=100_000, allow_inf_nan=False, max_digits=10, decimal_places=4)
    unit: Literal["sq_m", "sq_ft"]
    definition: Literal["gross_internal", "net_internal", "gross_external", "unspecified_total"]

    @field_validator("amount", mode="before")
    @classmethod
    def exact_transcription(cls, value: Any) -> Any:
        if isinstance(value, bool | float):
            raise ValueError("Use an exact decimal string for the printed area")
        return value

    @property
    def square_metres(self) -> Decimal:
        """Exact international-foot conversion; retain the printed area definition."""
        return self.amount if self.unit == "sq_m" else self.amount * Decimal("0.09290304")


class ReferenceFacts(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    printed_total_area: ReferenceFact[PrintedArea] = Field(default_factory=ReferenceFact)
    labelled_bedrooms_on_plan: ReferenceFact[StrictInt] = Field(default_factory=ReferenceFact)
    advertised_epc: ReferenceFact[Literal["A", "B", "C", "D", "E", "F", "G"]] = Field(
        default_factory=ReferenceFact
    )
    advertised_council_tax: ReferenceFact[Literal["A", "B", "C", "D", "E", "F", "G", "H"]] = Field(
        default_factory=ReferenceFact
    )
    advertised_pets_allowed: ReferenceFact[StrictBool] = Field(default_factory=ReferenceFact)
    advertised_bills_included: ReferenceFact[StrictBool] = Field(default_factory=ReferenceFact)
    bathtub_visible: ReferenceFact[StrictBool] = Field(default_factory=ReferenceFact)

    @model_validator(mode="after")
    def validate_transcribed_facts(self) -> "ReferenceFacts":
        count = self.labelled_bedrooms_on_plan.value
        if count is not None and not 0 <= count <= 20:
            raise ValueError("Plan bedroom count is outside the supported range")
        for name in (
            "printed_total_area",
            "advertised_epc",
            "advertised_council_tax",
            "advertised_pets_allowed",
            "advertised_bills_included",
        ):
            fact = getattr(self, name)
            if fact.state == "supported" and not any(item.quote for item in fact.evidence):
                raise ValueError("Advertised/printed facts require an exact evidence transcription")
        return self


class OwnerJudgment(BaseModel):
    """A personal preference, never a universally correct factual label."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    state: Literal["unreviewed", "reviewed", "insufficient_evidence"] = "unreviewed"
    profile_reference: str | None = Field(default=None, min_length=1, max_length=200)
    workspace_rating: StrictInt | None = Field(default=None, ge=1, le=5)
    hosting_rating: StrictInt | None = Field(default=None, ge=1, le=5)
    retain_for_review: StrictBool | None = None
    reason: str | None = Field(default=None, min_length=1, max_length=1000)

    @model_validator(mode="after")
    def explicit_personal_context(self) -> "OwnerJudgment":
        answers = (self.workspace_rating, self.hosting_rating, self.retain_for_review)
        if self.state == "unreviewed":
            if any(v is not None for v in (*answers, self.reason, self.profile_reference)):
                raise ValueError("Unreviewed preference cannot contain an answer")
        elif not self.reason or not self.profile_reference:
            raise ValueError("Owner judgment requires its profile and rationale")
        elif self.state == "reviewed" and any(v is None for v in answers):
            raise ValueError("Reviewed owner judgment requires all three answers")
        elif self.state == "insufficient_evidence" and any(v is not None for v in answers):
            raise ValueError("Insufficient evidence cannot become a suitability score")
        return self


class CaseAnnotation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1] = 1
    rubric_version: Literal["home-finder-reference-v1"] = RUBRIC_VERSION
    case_id: str = Field(pattern=r"^case-[0-9a-f]{16}$")
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    revision: int = Field(default=1, ge=1, strict=True)
    supersedes_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    reviewer_id: str | None = Field(default=None, min_length=1, max_length=100)
    reviewed_at: AwareDatetime | None = None
    independent_of_candidate: StrictBool = False
    adjudicator_id: str | None = Field(default=None, min_length=1, max_length=100)
    facts: ReferenceFacts = Field(default_factory=ReferenceFacts)
    owner_judgment: OwnerJudgment = Field(default_factory=OwnerJudgment)

    @model_validator(mode="after")
    def reviewed_identity_and_revision(self) -> "CaseAnnotation":
        reviewed = self.owner_judgment.state != "unreviewed" or any(
            getattr(self.facts, name).state != "unreviewed" for name in ReferenceFacts.model_fields
        )
        if reviewed and (
            not self.reviewer_id or self.reviewed_at is None or not self.independent_of_candidate
        ):
            raise ValueError("Reviewed labels require an independent reviewer's ID and date")
        if (self.revision == 1) != (self.supersedes_sha256 is None):
            raise ValueError("A correction requires the preceding annotation checksum")
        if self.adjudicator_id is not None and self.adjudicator_id == self.reviewer_id:
            raise ValueError("A second check requires a different reviewer")
        return self

    def validate_material(self, material_ids: set[str], *, require_reviewed: bool = False) -> None:
        """Check references/completeness, not a human's identity or factual correctness."""
        for name in ReferenceFacts.model_fields:
            fact = getattr(self.facts, name)
            if require_reviewed and fact.state == "unreviewed":
                raise ValueError("Unreviewed factual labels remain")
            if any(item.material_id not in material_ids for item in fact.evidence):
                raise ValueError("Label references material outside this exact case")


class ReferenceImage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    file: str = Field(pattern=r"^media/[0-9a-f]{64}\.(jpg|png|webp)$")
    roles: list[Literal["gallery", "floorplan"]] = Field(min_length=1, max_length=2)
    width: int = Field(gt=0, strict=True)
    height: int = Field(gt=0, strict=True)
    size: int = Field(gt=0, le=20_000_000, strict=True)

    @model_validator(mode="after")
    def bounded_matching_file(self) -> "ReferenceImage":
        if self.file.split("/")[1].split(".")[0] != self.sha256:
            raise ValueError("Image filename must match its content checksum")
        if self.width * self.height > 40_000_000 or len(set(self.roles)) != len(self.roles):
            raise ValueError("Image dimensions or roles exceed the pack contract")
        return self


class ReferenceText(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    file: str = Field(pattern=r"^text/[0-9a-f]{64}\.txt$")
    kind: Literal["stored_source_description"] = "stored_source_description"
    source: str = Field(min_length=1, max_length=40)
    is_backfilled: bool
    size: int = Field(gt=0, le=1_000_000, strict=True)

    @model_validator(mode="after")
    def matching_file(self) -> "ReferenceText":
        if self.file != f"text/{self.sha256}.txt":
            raise ValueError("Text filename must match its content checksum")
        return self


class ReferenceCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1] = 1
    case_id: str = Field(pattern=r"^case-[0-9a-f]{16}$")
    group_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    connected_canonical_count: int = Field(ge=1, strict=True)
    corpus_kind: Literal["historical_projection_with_retained_assets"] = (
        "historical_projection_with_retained_assets"
    )
    split: Literal["development"] = "development"
    images: list[ReferenceImage] = Field(min_length=1, max_length=12)
    texts: list[ReferenceText] = Field(default_factory=list, max_length=20)
    omitted_image_candidates: int = Field(ge=0, strict=True)
    omitted_descriptions: int = Field(ge=0, strict=True)

    @property
    def material_ids(self) -> set[str]:
        return {f"image:{item.sha256}" for item in self.images} | {
            f"text:{item.sha256}" for item in self.texts
        }


class ReferenceCaseEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str = Field(pattern=r"^case-[0-9a-f]{16}$")
    file: str = Field(pattern=r"^cases/case-[0-9a-f]{16}\.json$")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def matching_file(self) -> "ReferenceCaseEntry":
        if self.file != f"cases/{self.case_id}.json":
            raise ValueError("Case filename must match its identity")
        return self


class ReferencePackManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1]
    rubric_version: Literal["home-finder-reference-v1"]
    created_at: AwareDatetime
    corpus_kind: Literal["historical_projection_with_retained_assets"]
    split: Literal["development"]
    promotion_ready: Literal[False]
    human_labels_assigned: Literal[0]
    model_calls: Literal[0]
    network_requests: Literal[0]
    backup_database_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    selection_seed: str = Field(min_length=1, max_length=100)
    selection_policy: Literal["greedy_stratum_coverage_one_canonical_per_connected_group_v1"]
    requested_cases: int = Field(ge=1, le=60, strict=True)
    canonical_rows: int = Field(ge=1, le=10_000, strict=True)
    candidate_groups_with_media: int = Field(ge=1, le=10_000, strict=True)
    selected_strata: dict[str, int] = Field(max_length=400)
    selected_material_bytes: int = Field(gt=0, le=600_000_000, strict=True)
    undecodable_or_unsupported_media: int = Field(ge=0, strict=True)
    cases: list[ReferenceCaseEntry] = Field(min_length=1, max_length=60)
    input_files: dict[str, str] = Field(min_length=4, max_length=2500)

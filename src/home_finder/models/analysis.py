"""Versioned analysis settings and application-owned assessment provenance."""

import hashlib
import json
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PhaseConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    # Expand supported models/providers together with their price accounting and
    # evaluated request capabilities, not through an unpriced arbitrary string.
    provider: Literal["anthropic"] = "anthropic"
    model: Literal["claude-sonnet-4-6"] = "claude-sonnet-4-6"
    reasoning: Literal["adaptive", "disabled"] = "disabled"
    input_token_limit: int | None = Field(
        default=None, ge=1, le=1_000_000, strict=True, exclude_if=lambda value: value is None
    )
    effort: Literal["low", "medium", "high"] | None = None
    max_output_tokens: int = Field(default=4096, ge=1, le=21_000, strict=True)
    request_timeout_seconds: float = Field(
        default=60, gt=0, le=600, allow_inf_nan=False, strict=True
    )
    connect_timeout_seconds: float = Field(default=5, gt=0, le=60, allow_inf_nan=False, strict=True)
    sdk_retries: int = Field(default=1, ge=0, le=1, strict=True)

    @model_validator(mode="after")
    def validate_timeouts(self) -> Self:
        if self.connect_timeout_seconds > self.request_timeout_seconds:
            raise ValueError("Connect timeout must not exceed the request timeout")
        return self


class VisualPhaseConfig(PhaseConfig):
    reasoning: Literal["adaptive", "disabled"] = "adaptive"
    max_output_tokens: int = Field(default=21_000, ge=1, le=21_000, strict=True)
    request_timeout_seconds: float = Field(
        default=240, gt=0, le=600, allow_inf_nan=False, strict=True
    )
    output_contract: Literal["visual_tool_v1"] = "visual_tool_v1"


class EvaluationPhaseConfig(PhaseConfig):
    output_contract: Literal["evaluation_schema_v1"] = "evaluation_schema_v1"


class AnalysisConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1] = 1
    visual: VisualPhaseConfig = Field(default_factory=VisualPhaseConfig)
    evaluation: EvaluationPhaseConfig = Field(default_factory=EvaluationPhaseConfig)


class AnalysisProvenance(BaseModel):
    """Configuration identity, not a complete retained-input or attempt ledger."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # v1 precedes required live-output validation; v2 records that contract;
    # v3 additionally requires per-attempt spend admission and billing scope.
    # Keep the v1 default when reading an older record without a version field.
    version: Literal[1, 2, 3] = 1
    configuration: AnalysisConfig
    sdk_version: str
    max_images: int = Field(ge=1, le=20)
    prompt_version: Literal["two_phase_2026_09_28_v1"] = "two_phase_2026_09_28_v1"
    visual_system_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    evaluation_system_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    visual_output_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    evaluation_output_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    input_budget_policy: Literal["bounded_listing_payload_2026_09_29_v1"] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    visual_input_sha256: str | None = Field(
        default=None, pattern=r"^[a-f0-9]{64}$", exclude_if=lambda value: value is None
    )

    @property
    def fingerprint(self) -> str:
        """Stable identity for PR13/14 to include in their durable work keys."""
        canonical = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()


AnalysisFailureKind = Literal[
    "refusal",
    "truncated",
    "unsupported_schema",
    "request_rejected",
    "unexpected_stop_reason",
    "malformed_output",
    "oversized_output",
    "internal_error",
]


class AnalysisFailure(BaseModel):
    """A failed required phase, with no provider text or listing content."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    phase: Literal["visual", "evaluation", "assembly"]
    kind: AnalysisFailureKind
    stop_reason: str | None = Field(default=None, max_length=80)
    request_id: str | None = Field(default=None, max_length=128)

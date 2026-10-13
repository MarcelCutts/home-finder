"""Exact, versioned spending contracts for the supported analysis provider."""

from decimal import Decimal
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from home_finder.models.analysis import AnalysisModel, AnalysisProvenance, PhaseConfig

NANOUSD_PER_USD = 1_000_000_000
SHA256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Units = Annotated[int, Field(strict=True, ge=0, le=10_000_000)]
USD = Annotated[Decimal, Field(ge=0, le=1_000_000, max_digits=16, decimal_places=9)]


class SpendDeferred(Exception):
    """No new paid dispatch is allowed; retain the work for review or retry."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class VisualCheckpointWrite(BaseModel):
    """One validated visual result to commit with its paid-attempt settlement."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    input_sha256: SHA256
    data: dict[str, Any]


def nanousd(amount: Decimal) -> int:
    """Never round a budget or provider charge into a different monetary value."""
    scaled = amount * NANOUSD_PER_USD
    if not amount.is_finite() or scaled != scaled.to_integral_value() or amount < 0:
        raise ValueError("Amount must be nonnegative with at most nine decimal places")
    return int(scaled)


def usd_text(amount: int) -> str:
    return format(Decimal(amount) / NANOUSD_PER_USD, ".9f")


class SpendPolicy(BaseModel):
    """Explicit caps for the single supported provider; no implicit spend allowance."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1] = 1
    provider: Literal["anthropic"] = "anthropic"
    per_call_usd: USD
    daily_usd: USD
    monthly_usd: USD
    max_in_flight: int = Field(default=5, strict=True, ge=1, le=50)
    max_attempts_per_operation: int = Field(default=2, strict=True, ge=1, le=3)

    lifetime_usd: USD | None = None
    max_total_attempts: int | None = Field(default=None, strict=True, ge=1, le=10000)

    @field_validator("per_call_usd", "daily_usd", "monthly_usd", "lifetime_usd", mode="before")
    @classmethod
    def _exact_amounts(cls, value: Any) -> Any:
        if isinstance(value, (bool, float)):
            raise ValueError("Use an exact decimal string for USD caps")
        return value

    @model_validator(mode="after")
    def _ordered_caps(self) -> "SpendPolicy":
        if not self.per_call_usd <= self.daily_usd <= self.monthly_usd:
            raise ValueError("Caps must satisfy per_call <= daily <= monthly")
        return self


class BillableUsage(BaseModel):
    """Non-overlapping provider units; output includes any billed thinking."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    input_tokens: Units
    output_tokens: Units
    cache_write_5m_tokens: Units = 0
    cache_write_1h_tokens: Units = 0
    cache_read_tokens: Units = 0


class RateCard(BaseModel):
    """USD nanodollars per token, immutable once referenced by an attempt."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[
        "anthropic-sonnet-4-6-global-standard-2026-09-28-v1",
        "anthropic-sonnet-5-5-global-standard-2026-10-10-v1",
        "anthropic-sonnet-4-6-global-standard-2026-10-13-v2",
        "anthropic-sonnet-5-5-global-standard-2026-10-13-v2",
    ] = "anthropic-sonnet-4-6-global-standard-2026-09-28-v1"
    provider: Literal["anthropic"] = "anthropic"
    model: AnalysisModel = "claude-sonnet-4-6"
    currency: Literal["USD"] = "USD"
    input_nanousd: Literal[3000, 2000] = 3000
    output_nanousd: Literal[15000, 10000] = 15000
    cache_write_5m_nanousd: Literal[3750, 2500] = 3750
    cache_write_1h_nanousd: Literal[6000, 4000] = 6000
    cache_read_nanousd: Literal[300, 100] = 300
    input_context_bound: Literal[1000000] = 1000000

    @model_validator(mode="after")
    def validate_card_identity(self) -> Self:
        """A frozen card must be one complete known tariff, never a hybrid."""
        identities = {
            "anthropic-sonnet-4-6-global-standard-2026-09-28-v1": (
                "claude-sonnet-4-6",
                3000,
                15000,
                3750,
                6000,
                300,
            ),
            "anthropic-sonnet-5-5-global-standard-2026-10-10-v1": (
                "claude-sonnet-5-5",
                2000,
                10000,
                2500,
                4000,
                100,
            ),
        }
        # v2 changes reservation policy, not token tariffs. Keep v1 decoding and
        # reservation calculations frozen for existing ledger evidence.
        identities["anthropic-sonnet-4-6-global-standard-2026-10-13-v2"] = identities[
            "anthropic-sonnet-4-6-global-standard-2026-09-28-v1"
        ]
        identities["anthropic-sonnet-5-5-global-standard-2026-10-13-v2"] = identities[
            "anthropic-sonnet-5-5-global-standard-2026-10-10-v1"
        ]
        if (
            self.model,
            self.input_nanousd,
            self.output_nanousd,
            self.cache_write_5m_nanousd,
            self.cache_write_1h_nanousd,
            self.cache_read_nanousd,
        ) != identities[self.version]:
            raise ValueError("Rate card does not match its version and model")
        return self

    @property
    def reservation_cache_ttl(self) -> Literal["5m", "1h"]:
        """Maximum cache-write price covered by this version's reservation.

        New native requests enforce the same TTL before admission. Historical
        v1 cards retain the more conservative one-hour bound.
        """
        return "5m" if self.version.endswith("-v2") else "1h"

    def cost(self, usage: BillableUsage) -> int:
        return (
            usage.input_tokens * self.input_nanousd
            + usage.output_tokens * self.output_nanousd
            + usage.cache_write_5m_tokens * self.cache_write_5m_nanousd
            + usage.cache_write_1h_tokens * self.cache_write_1h_nanousd
            + usage.cache_read_tokens * self.cache_read_nanousd
        )

    def reservation(self, phase: PhaseConfig) -> int:
        """Cover admitted input at the highest allowed rate, plus maximum output.

        The default reserves the full context. Smaller explicit limits require
        the local supported-payload admission policy; they are not a provider
        input or dollar cap. Cache hits never reduce the reservation. Regional,
        priority, batch and server-tool charges are outside this rate card.
        """
        if (phase.provider, phase.model) != (self.provider, self.model):
            raise ValueError("Unpriced provider/model")
        # An explicit input limit is checked against the supported payload at
        # the native HTTP boundary. Older configurations keep the full context.
        input_bound = phase.input_token_limit or self.input_context_bound
        input_rate = (
            self.cache_write_5m_nanousd
            if self.reservation_cache_ttl == "5m"
            else self.cache_write_1h_nanousd
        )
        return input_bound * input_rate + phase.max_output_tokens * self.output_nanousd


SONNET_STANDARD_RATES = RateCard()
# https://platform.claude.com/docs/en/models/sonnet-5-5/overview (2026-10-10)
SONNET_55_STANDARD_RATES = RateCard(
    version="anthropic-sonnet-5-5-global-standard-2026-10-10-v1",
    model="claude-sonnet-5-5",
    input_nanousd=2000,
    output_nanousd=10000,
    cache_write_5m_nanousd=2500,
    cache_write_1h_nanousd=4000,
    cache_read_nanousd=100,
)

# https://platform.claude.com/docs/en/build-with-claude/prompt-caching (2026-10-13)
# These versions require the coupled five-minute-only native request policy.
SONNET_46_FIVE_MINUTE_RATES = RateCard.model_validate(
    {
        **SONNET_STANDARD_RATES.model_dump(),
        "version": "anthropic-sonnet-4-6-global-standard-2026-10-13-v2",
    }
)
SONNET_55_FIVE_MINUTE_RATES = RateCard.model_validate(
    {
        **SONNET_55_STANDARD_RATES.model_dump(),
        "version": "anthropic-sonnet-5-5-global-standard-2026-10-13-v2",
    }
)


def rate_card_for(model: str) -> RateCard:
    for card in (SONNET_46_FIVE_MINUTE_RATES, SONNET_55_FIVE_MINUTE_RATES):
        if model == card.model:
            return card
    raise ValueError("Unpriced model")


class AttemptSpec(BaseModel):
    """Audit metadata, never a prompt, image, credential or complete response."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    operation_key: SHA256
    request_sha256: SHA256
    property_id: str = Field(min_length=1, max_length=512)
    phase: Literal["visual", "evaluation"]
    provenance: AnalysisProvenance

    @property
    def phase_config(self) -> PhaseConfig:
        config = self.provenance.configuration
        return config.visual if self.phase == "visual" else config.evaluation


class UsageSettlement(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    usage: BillableUsage
    result: Literal[
        "success",
        "refusal",
        "truncated",
        "malformed_output",
        "oversized_output",
        "unexpected_stop_reason",
        "unsupported_schema",
        "request_rejected",
        "authentication",
        "rate_limit",
        "quota",
        "transient",
        "internal_error",
    ]
    request_id: str | None = Field(default=None, max_length=128)
    stop_reason: str | None = Field(default=None, max_length=80)


class ConfirmedCost(BaseModel):
    """Operator reconciliation requires a durable, non-secret evidence reference."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    amount_usd: USD
    evidence_ref: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9._:/#-]+$")

    @field_validator("amount_usd", mode="before")
    @classmethod
    def _exact_amount(cls, value: Any) -> Any:
        return SpendPolicy._exact_amounts(value)


class ProviderRejection(BaseModel):
    """A complete provider error response, without invented token counts."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    http_status: int = Field(strict=True, ge=400, le=599)
    error_type: Literal[
        "invalid_request_error",
        "authentication_error",
        "billing_error",
        "permission_error",
        "not_found_error",
        "conflict_error",
        "request_too_large",
        "rate_limit_error",
        "api_error",
        "timeout_error",
        "overloaded_error",
    ]
    result: Literal[
        "request_rejected",
        "unsupported_schema",
        "authentication",
        "rate_limit",
        "quota",
        "transient",
    ]
    request_id: str | None = Field(default=None, max_length=128)

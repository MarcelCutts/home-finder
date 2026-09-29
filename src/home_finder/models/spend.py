"""Exact, versioned spending contracts for the supported analysis provider."""

from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from home_finder.models.analysis import AnalysisProvenance, PhaseConfig

NANOUSD_PER_USD = 1_000_000_000
SHA256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Units = Annotated[int, Field(strict=True, ge=0, le=10_000_000)]
USD = Annotated[Decimal, Field(ge=0, le=1_000_000, max_digits=16, decimal_places=9)]


class SpendDeferred(Exception):
    """No new paid dispatch is allowed; retain the work for review or retry."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


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
        "anthropic-sonnet-5-5-global-standard-2026-09-29-v1",
    ] = "anthropic-sonnet-4-6-global-standard-2026-09-28-v1"
    provider: Literal["anthropic"] = "anthropic"
    model: Literal["claude-sonnet-4-6", "claude-sonnet-5-5"] = "claude-sonnet-4-6"
    currency: Literal["USD"] = "USD"
    input_nanousd: Literal[3000, 2000] = 3000
    output_nanousd: Literal[15000, 10000] = 15000
    cache_write_5m_nanousd: Literal[3750, 2500] = 3750
    cache_write_1h_nanousd: Literal[6000, 4000] = 6000
    cache_read_nanousd: Literal[300, 200] = 300
    input_context_bound: Literal[1000000] = 1000000

    @model_validator(mode="after")
    def _coherent_rates(self) -> "RateCard":
        actual = (
            self.version,
            self.input_nanousd,
            self.output_nanousd,
            self.cache_write_5m_nanousd,
            self.cache_write_1h_nanousd,
            self.cache_read_nanousd,
        )
        expected = {
            "claude-sonnet-4-6": (
                "anthropic-sonnet-4-6-global-standard-2026-09-28-v1",
                3000,
                15000,
                3750,
                6000,
                300,
            ),
            "claude-sonnet-5-5": (
                "anthropic-sonnet-5-5-global-standard-2026-09-29-v1",
                2000,
                10000,
                2500,
                4000,
                200,
            ),
        }
        if actual != expected[self.model]:
            raise ValueError("Rate card does not match its model and version")
        return self

    def cost(self, usage: BillableUsage) -> int:
        return (
            usage.input_tokens * self.input_nanousd
            + usage.output_tokens * self.output_nanousd
            + usage.cache_write_5m_tokens * self.cache_write_5m_nanousd
            + usage.cache_write_1h_tokens * self.cache_write_1h_nanousd
            + usage.cache_read_tokens * self.cache_read_nanousd
        )

    def reservation(self, phase: PhaseConfig) -> int:
        """Cover admitted input at the highest input rate, plus maximum output.

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
        return input_bound * self.cache_write_1h_nanousd + (
            phase.max_output_tokens * self.output_nanousd
        )


SONNET_STANDARD_RATES = RateCard()
SONNET_5_5_STANDARD_RATES = RateCard(
    version="anthropic-sonnet-5-5-global-standard-2026-09-29-v1",
    model="claude-sonnet-5-5",
    input_nanousd=2000,
    output_nanousd=10000,
    cache_write_5m_nanousd=2500,
    cache_write_1h_nanousd=4000,
    cache_read_nanousd=200,
)


def rate_card_for(model: str) -> RateCard:
    for card in (SONNET_STANDARD_RATES, SONNET_5_5_STANDARD_RATES):
        if card.model == model:
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
        "permission_error",
        "not_found_error",
        "request_too_large",
        "rate_limit_error",
        "api_error",
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

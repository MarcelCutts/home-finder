"""Admission and accounting around every physical native SDK request attempt."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from home_finder.filters.analysis_contracts import AnalysisOutputError, Phase
from home_finder.filters.request_budget import input_reservation_tokens
from home_finder.logging import get_logger
from home_finder.models.analysis import AnalysisProvenance, PhaseConfig
from home_finder.models.spend import (
    AttemptSpec,
    BillableUsage,
    ProviderRejection,
    SpendDeferred,
    SpendPolicy,
    UsageSettlement,
    VisualCheckpointWrite,
    rate_card_for,
    usd_text,
)
from home_finder.utils.async_tasks import finish_before_cancelling

logger = get_logger(__name__)

if TYPE_CHECKING:
    from anthropic import APIRequest, AsyncAPIResponse, AsyncCallNext

    from home_finder.db.spend import SpendRepository


@dataclass(frozen=True)
class _CallContext:
    property_id: str
    phase: Phase
    provenance: AnalysisProvenance
    validate: Callable[[Any], dict[str, Any]]
    visual_input_sha256: str | None = None


def _digest(value: Any) -> str:
    body = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(body.encode()).hexdigest()


def visual_input_key(property_id: str, body: dict[str, Any], provenance: AnalysisProvenance) -> str:
    """Identity of actual ordered image bytes, rendered prompt and visual request contract.

    Evaluation configuration deliberately does not invalidate reusable observations.
    Billing defaults match the physical request pinned by the middleware below.
    """
    return _digest(
        [
            1,
            property_id,
            {**body, "service_tier": "standard_only", "inference_geo": "global"},
            provenance.configuration.visual.output_contract,
            provenance.visual_output_sha256,
            provenance.sdk_version,
        ]
    )


def _default_cache_ttl(value: Any) -> bool:
    if isinstance(value, dict):
        control = value.get("cache_control")
        if control is not None and (
            not isinstance(control, dict)
            or control.get("type") != "ephemeral"
            or control.get("ttl", "5m") != "5m"
        ):
            return False
        return all(_default_cache_ttl(item) for item in value.values())
    if isinstance(value, list):
        return all(_default_cache_ttl(item) for item in value)
    return True


def reported_usage(
    body: dict[str, Any], *, expected_model: str = "claude-sonnet-4-6"
) -> BillableUsage:
    """Price only the known model, billing modes and complete non-overlapping units."""
    rate_card_for(expected_model)
    usage = body.get("usage")
    if not isinstance(usage, dict) or body.get("model") != expected_model:
        raise ValueError("Missing usage or unpriced response model")
    if set(usage) - {
        "input_tokens",
        "output_tokens",
        "cache_creation_input_tokens",
        "cache_creation",
        "cache_read_input_tokens",
        "service_tier",
        "inference_geo",
        "server_tool_use",
        # This is a decomposition of output_tokens, not an additional charge.
        "output_tokens_details",
    }:
        raise ValueError("Unsupported provider usage fields")
    if usage.get("service_tier") not in {None, "standard"} or usage.get("inference_geo") not in {
        None,
        "global",
    }:
        raise ValueError("Unpriced response billing mode")
    tools = usage.get("server_tool_use")
    if tools is not None and (
        not isinstance(tools, dict)
        or any(type(value) is not int or value != 0 for value in tools.values())
    ):
        raise ValueError("Unpriced server tool usage")
    total_written = usage.get("cache_creation_input_tokens")
    total_written = 0 if total_written is None else total_written
    breakdown = usage.get("cache_creation")
    write_5m: Any
    write_1h: Any
    if breakdown is None:
        # These requests use the default five-minute cache TTL only.
        write_5m, write_1h = total_written, 0
    elif isinstance(breakdown, dict):
        if set(breakdown) != {"ephemeral_5m_input_tokens", "ephemeral_1h_input_tokens"}:
            raise ValueError("Unsupported cache breakdown")
        write_5m = breakdown.get("ephemeral_5m_input_tokens")
        write_1h = breakdown.get("ephemeral_1h_input_tokens")
    else:
        raise ValueError("Invalid cache breakdown")
    result = BillableUsage.model_validate(
        {
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "cache_write_5m_tokens": write_5m,
            "cache_write_1h_tokens": write_1h,
            "cache_read_tokens": usage.get("cache_read_input_tokens")
            if usage.get("cache_read_input_tokens") is not None
            else 0,
        }
    )
    if type(total_written) is not int or (
        result.cache_write_5m_tokens + result.cache_write_1h_tokens != total_written
    ):
        raise ValueError("Cache breakdown does not match total")
    return result


def _supported_phase_options(body: dict[str, Any], config: PhaseConfig, phase: Phase) -> bool:
    """Do not let native SDK overrides bypass the configured request contract."""
    thinking = {"type": config.thinking_type} if config.thinking_type is not None else None
    if body.get("thinking") != thinking:
        return False
    output = body.get("output_config", {})
    if (
        not isinstance(output, dict)
        or set(output) - {"effort", "format"}
        or output.get("effort") != config.effort
    ):
        return False
    if phase == "evaluation":
        return "tools" not in body and "tool_choice" not in body
    choice = (
        {"type": "auto"}
        if config.thinking_type is not None
        else {"type": "tool", "name": "property_visual_analysis"}
    )
    return body.get("tool_choice") == choice


def provider_rejection(status: int, body: Any, request_id: str | None) -> ProviderRejection:
    if not isinstance(body, dict) or body.get("type") != "error":
        raise ValueError("No complete provider error envelope")
    error = body.get("error")
    if not isinstance(error, dict) or not isinstance(error.get("message"), str):
        raise ValueError("No complete provider error envelope")
    message = error["message"].lower()
    result = "request_rejected"
    if status == 401:
        result = "authentication"
    elif status == 402 or any(word in message for word in ("credit balance", "billing", "quota")):
        result = "quota"
    elif status == 429:
        result = "rate_limit"
    elif status in {408, 409} or status >= 500:
        result = "transient"
    elif "schema" in message and any(
        word in message for word in ("invalid", "unsupported", "complex", "limit")
    ):
        result = "unsupported_schema"
    return ProviderRejection.model_validate(
        {
            "http_status": status,
            "error_type": error.get("type"),
            "result": result,
            "request_id": request_id,
        }
    )


async def _retain_uncertainty(
    repo: SpendRepository, attempt_id: str, reason: str, request_id: str | None = None
) -> None:
    try:
        await finish_before_cancelling(repo.mark_unknown(attempt_id, reason, request_id=request_id))
        logger.warning(
            "analysis_attempt_unknown",
            attempt_id=attempt_id,
            reason=reason,
            request_id=request_id,
            reservation_held=True,
        )
    except Exception:
        # The committed dispatched reservation still holds if this write fails.
        raise SpendDeferred("ledger_unavailable") from None


class AnalysisSpendMeter:
    """The supported SDK middleware runs this once per physical HTTP attempt."""

    def __init__(self, repository: SpendRepository | None, policy: SpendPolicy | None) -> None:
        self._repository = repository
        self._policy = policy
        self._context: ContextVar[_CallContext | None] = ContextVar("analysis_spend", default=None)

    @contextmanager
    def bind(
        self,
        property_id: str,
        phase: Phase,
        provenance: AnalysisProvenance,
        validate: Callable[[Any], dict[str, Any]],
        *,
        visual_input_sha256: str | None = None,
    ) -> Iterator[None]:
        token = self._context.set(
            _CallContext(property_id, phase, provenance, validate, visual_input_sha256)
        )
        try:
            yield
        finally:
            self._context.reset(token)

    async def get_visual_checkpoint(
        self, property_id: str, input_sha256: str
    ) -> dict[str, Any] | None:
        if self._repository is None:
            return None
        try:
            return await self._repository.get_visual_checkpoint(property_id, input_sha256)
        except SpendDeferred:
            raise
        except Exception:
            raise SpendDeferred("ledger_unavailable") from None

    async def __call__(
        self, request: APIRequest, call_next: AsyncCallNext
    ) -> AsyncAPIResponse[Any]:
        from anthropic import APIConnectionError, APITimeoutError, omit

        scope = self._context.get()
        repo, policy = self._repository, self._policy
        if scope is None or repo is None or policy is None:
            logger.warning(
                "analysis_dispatch_deferred",
                reason="spending_not_configured",
                hint="Configure HOME_FINDER_ANALYSIS_SPEND_POLICY before enabling paid analysis",
            )
            raise SpendDeferred("spending_not_configured")
        body = request.json
        phase_config = getattr(scope.provenance.configuration, scope.phase)
        allowed = {
            "model",
            "max_tokens",
            "system",
            "messages",
            "tools",
            "tool_choice",
            "thinking",
            "output_config",
            "service_tier",
            "inference_geo",
        }
        if (
            request.method.lower() != "post"
            or request.url != "/v1/messages"
            or request.stream
            or any(key.lower() == "anthropic-beta" for key in request.headers)
            or not isinstance(body, dict)
            or set(body) - allowed
            or body.get("model") != phase_config.model
            or body.get("max_tokens") != phase_config.max_output_tokens
            or type(body.get("max_tokens")) is not int
            or body.get("service_tier", "standard_only") != "standard_only"
            or body.get("inference_geo", "global") != "global"
            or not isinstance(body.get("tools", []), list)
            or not _default_cache_ttl(body)
            or not _supported_phase_options(body, phase_config, scope.phase)
            or any(
                not isinstance(tool, dict)
                or tool.get("type", "custom") != "custom"
                or tool.get("name") != "property_visual_analysis"
                for tool in body.get("tools", [])
            )
        ):
            raise SpendDeferred("unpriced_request")
        # Pin billing scope independently of account-level priority defaults.
        body = {**body, "service_tier": "standard_only", "inference_geo": "global"}
        if scope.visual_input_sha256 is not None and (
            scope.phase != "visual"
            or visual_input_key(scope.property_id, body, scope.provenance)
            != scope.visual_input_sha256
        ):
            raise SpendDeferred("checkpoint_request_mismatch")
        input_bound = None
        if phase_config.input_token_limit is not None:
            try:
                input_bound = input_reservation_tokens(body)
                if input_bound > phase_config.input_token_limit:
                    raise SpendDeferred("input_limit")
            except SpendDeferred as exc:
                logger.warning(
                    "analysis_dispatch_deferred",
                    property_id=scope.property_id,
                    phase=scope.phase,
                    reason=exc.reason,
                    input_bound=input_bound,
                    input_limit=phase_config.input_token_limit,
                )
                raise
        # SDK default headers (including ANTHROPIC_CUSTOM_HEADERS) are merged
        # below middleware, so explicitly suppress inherited beta opt-ins too.
        request = request.copy(body=body, headers={**request.headers, "anthropic-beta": omit})
        request_digest = _digest(body)
        spec = AttemptSpec(
            operation_key=_digest(
                [1, scope.property_id, scope.phase, scope.provenance.fingerprint, request_digest]
            ),
            request_sha256=request_digest,
            property_id=scope.property_id,
            phase=scope.phase,
            provenance=scope.provenance,
        )
        log = logger.bind(
            property_id=scope.property_id,
            phase=scope.phase,
            model=phase_config.model,
            operation_key=spec.operation_key,
            request_sha256=request_digest,
            configuration_sha256=scope.provenance.fingerprint,
        )
        reserved = rate_card_for(phase_config.model).reservation(phase_config)
        try:
            attempt_id = await repo.admit(spec, policy)
            log = log.bind(attempt_id=attempt_id)
            log.info(
                "analysis_attempt_reserved",
                reserved_usd=usd_text(reserved),
                input_bound=input_bound,
                input_limit=phase_config.input_token_limit,
                max_output_tokens=phase_config.max_output_tokens,
            )
            await repo.mark_dispatched(attempt_id)
        except SpendDeferred as exc:
            log.warning("analysis_dispatch_deferred", reason=exc.reason)
            raise
        except Exception:
            # No provider work may proceed if durable admission fails.
            raise SpendDeferred("ledger_unavailable") from None

        request_id: str | None = None
        started = time.monotonic()
        log.info("analysis_attempt_dispatched")
        try:
            response = await call_next(request)
            request_id = response.headers.get("request-id")
            request_id = request_id[:128] if request_id else None
            payload = await response.json()
        except asyncio.CancelledError:
            # Preserve cancellation even when the uncertainty marker fails.
            # The earlier durable dispatch still prevents another send.
            with suppress(SpendDeferred):
                await _retain_uncertainty(repo, attempt_id, "cancelled", request_id)
            raise
        except APIConnectionError as exc:
            await _retain_uncertainty(
                repo, attempt_id, "timeout" if isinstance(exc, APITimeoutError) else "connection"
            )
            # The SDK follows __cause__ to decide retryability. Do not chain
            # the transport failure, or it would repeat an uncertain paid call.
            raise SpendDeferred("uncertain_provider_outcome") from None
        except Exception:
            await _retain_uncertainty(repo, attempt_id, "invalid_usage", request_id)
            raise SpendDeferred("uncertain_provider_outcome") from None

        try:
            cost = 0
            usage_fields: dict[str, Any] = {}
            output_diagnostics: dict[str, Any] = {}
            outcome: str
            if response.status_code >= 400:
                rejection = provider_rejection(response.status_code, payload, request_id)
                outcome = rejection.result
                if rejection.result not in {"rate_limit", "transient"}:
                    response.http_response.headers["x-should-retry"] = "false"
                action = repo.settle_rejection(attempt_id, rejection)
            else:
                if response.status_code != 200 or not isinstance(payload, dict):
                    raise ValueError("Unsupported response")
                usage = reported_usage(payload, expected_model=phase_config.model)
                cost = rate_card_for(phase_config.model).cost(usage)
                usage_fields = usage.model_dump()
                outcome = "success"
                visual_data = None
                try:
                    validated = scope.validate(await response.parse())
                    if scope.visual_input_sha256 is not None:
                        visual_data = VisualCheckpointWrite(
                            input_sha256=scope.visual_input_sha256, data=validated
                        )
                except AnalysisOutputError as exc:
                    outcome = exc.failure.kind
                    output_diagnostics = exc.diagnostics
                except Exception:
                    outcome = "malformed_output"
                stop = payload.get("stop_reason")
                settlement = UsageSettlement.model_validate(
                    {
                        "usage": usage,
                        "result": outcome,
                        "request_id": request_id,
                        "stop_reason": stop[:80] if isinstance(stop, str) else None,
                    }
                )
                action = repo.settle_usage(attempt_id, settlement, checkpoint=visual_data)
        except (TypeError, ValueError):
            await _retain_uncertainty(repo, attempt_id, "invalid_usage", request_id)
            raise SpendDeferred("unpriced_provider_response") from None
        # Finish accounting even when cancellation arrives after the response.
        # Keep this outside the network try block: settled usage must not be
        # overwritten by an uncertainty marker during cancellation cleanup.
        try:
            await finish_before_cancelling(action)
        except Exception:
            raise SpendDeferred("ledger_unavailable") from None
        log.info(
            "analysis_attempt_settled",
            request_id=request_id,
            http_status=response.status_code,
            outcome=outcome,
            accounted_usd=usd_text(cost),
            duration_ms=round((time.monotonic() - started) * 1000, 1),
            output_diagnostics=output_diagnostics,
            **usage_fields,
        )
        if scope.visual_input_sha256 is not None and outcome == "success" and cost <= reserved:
            log.info("visual_checkpoint_saved", input_sha256=scope.visual_input_sha256)
        if cost > reserved:
            log.error("analysis_reservation_exceeded", accounted_usd=usd_text(cost))
            raise SpendDeferred("reservation_exceeded")
        return response

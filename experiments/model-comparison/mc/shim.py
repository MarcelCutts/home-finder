"""Stand-ins for AsyncAnthropic so PropertyQualityFilter runs unchanged against either provider.

The filter builds its exact production request (system prompt, labelled image
blocks, tool schema / JSON schema) and calls ``client.messages.create``. The
shim swaps in the configured model and reasoning settings, sends the request to
Anthropic or a translated equivalent to the OpenAI Responses API, records
timing/usage/cost, and returns an Anthropic-shaped message so the filter's own
validation, reconciliation and merge logic run exactly as in production.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

import anthropic
import openai
from anthropic.types import Message, TextBlock, ToolUseBlock, Usage

from mc.ledger import BudgetExceeded, Ledger
from mc.rates import cost_usd

VISUAL_TOOL_NAME = "property_visual_analysis"
# A connection failure or server error can leave the paid dispatch uncertain.
# Only a reported rate-limit rejection is automatically retried by this harness.
RETRYABLE_OPENAI = (openai.RateLimitError,)
RETRYABLE_ANTHROPIC = (anthropic.RateLimitError,)


@dataclass(frozen=True)
class PhaseSpec:
    provider: str  # "anthropic" | "openai"
    model: str
    effort: str | None = None  # Anthropic output_config.effort / OpenAI reasoning.effort
    thinking: str | None = None  # Anthropic only: adaptive | between_tools | disabled | None
    max_output_tokens: int = 16_000
    image_detail: str | None = None  # OpenAI only: low | high | auto | original
    verbosity: str | None = None  # OpenAI only
    service_tier: str | None = None  # OpenAI: flex | fast | ...; Anthropic: fast (speed)


@dataclass(frozen=True)
class RunConfig:
    id: str
    visual: PhaseSpec
    evaluation: PhaseSpec
    note: str = ""


@dataclass
class CallRecord:
    case_id: str
    config_id: str
    phase: str
    provider: str
    model: str
    started_at: float
    latency_s: float | None = None
    attempts: int = 0
    stop_reason: str | None = None
    request_id: str | None = None
    input_tokens: int = 0  # total prompt tokens, cached included
    cached_input_tokens: int = 0
    cache_write_tokens: int = 0
    output_tokens: int = 0  # visible + reasoning
    reasoning_tokens: int | None = None
    cost_usd: float = 0.0
    error: str | None = None
    raw_output: Any = None
    extra: dict[str, Any] = field(default_factory=dict)


class ShimClient:
    """The subset of AsyncAnthropic that PropertyQualityFilter touches."""

    def __init__(
        self,
        config: RunConfig,
        case_id: str,
        clients: Clients,
        records: list[CallRecord],
    ) -> None:
        self.messages = _Messages(self, config, case_id, clients, records)

    def with_options(self, **_: Any) -> ShimClient:
        return self

    async def close(self) -> None:  # real clients are owned by the harness
        return None


@dataclass
class Clients:
    anthropic: anthropic.AsyncAnthropic | None
    openai: openai.AsyncOpenAI | None
    ledger: Ledger
    semaphores: dict[str, asyncio.Semaphore]
    max_attempts: int = 4


def _phase_of(kwargs: dict[str, Any]) -> str:
    return "visual" if kwargs.get("tools") else "evaluation"


class _Messages:
    def __init__(
        self,
        owner: ShimClient,
        config: RunConfig,
        case_id: str,
        clients: Clients,
        records: list[CallRecord],
    ) -> None:
        self._config = config
        self._case_id = case_id
        self._clients = clients
        self._records = records

    async def create(self, **kwargs: Any) -> Message:
        kwargs.pop("timeout", None)
        phase = _phase_of(kwargs)
        spec: PhaseSpec = getattr(self._config, phase)
        record = CallRecord(
            case_id=self._case_id,
            config_id=self._config.id,
            phase=phase,
            provider=spec.provider,
            model=spec.model,
            started_at=time.time(),
        )
        self._records.append(record)
        record.extra["timing_scope"] = "queue_backoff_and_request"
        sem = self._clients.semaphores[spec.provider]
        retryable = RETRYABLE_ANTHROPIC if spec.provider == "anthropic" else RETRYABLE_OPENAI
        last_error: Exception | None = None
        started = time.perf_counter()
        async with sem:
            for attempt in range(1, self._clients.max_attempts + 1):
                try:
                    reservation = self._clients.ledger.check(spec.provider)
                except BudgetExceeded as exc:
                    record.error = f"BudgetExceeded: {exc}"
                    raise
                record.attempts = attempt
                try:
                    if spec.provider == "anthropic":
                        message = await self._anthropic(kwargs, spec, phase, record)
                    else:
                        message = await self._openai(kwargs, spec, phase, record)
                    record.error = None
                    self._clients.ledger.add(spec.provider, record.cost_usd, reservation)
                    return message
                except retryable as exc:
                    self._clients.ledger.release(spec.provider, reservation)
                    last_error = exc
                    record.error = f"{type(exc).__name__}: {str(exc)[:300]}"
                    if attempt < self._clients.max_attempts:
                        await asyncio.sleep(min(60, 4 * 2 ** (attempt - 1)))
                except BaseException as exc:
                    record.error = f"{type(exc).__name__}: {str(exc)[:500]}"
                    if record.extra.get("usage_priced"):
                        self._clients.ledger.add(spec.provider, record.cost_usd, reservation)
                    else:
                        self._clients.ledger.hold(spec.provider, reservation)
                    raise
                finally:
                    record.latency_s = time.perf_counter() - started
        # Surface as an outage so the filter's own outage path handles it.
        raise anthropic.APIConnectionError(
            message=f"exhausted retries: {last_error}",
            request=_dummy_request(),
        )

    # ── Anthropic ──────────────────────────────────────────────────────────
    async def _anthropic(
        self, kwargs: dict[str, Any], spec: PhaseSpec, phase: str, record: CallRecord
    ) -> Message:
        client = self._clients.anthropic
        assert client is not None, "Anthropic key not configured"
        req = dict(kwargs)
        req["model"] = spec.model
        req["max_tokens"] = spec.max_output_tokens
        if spec.thinking in (None, "none"):
            req.pop("thinking", None)
        else:
            req["thinking"] = {"type": spec.thinking}
        if phase == "visual":
            # Opus/Sonnet/Fable 5.x reject forced tool choice outright; only a
            # thinking-disabled request (Haiku 5.5, 4.x) can force the tool.
            req["tool_choice"] = (
                {"type": "tool", "name": VISUAL_TOOL_NAME}
                if spec.thinking == "disabled"
                else {"type": "auto"}
            )
        output_config = dict(req.get("output_config") or {})
        output_config.pop("effort", None)
        if spec.effort:
            output_config["effort"] = spec.effort
        if output_config:
            req["output_config"] = output_config
        else:
            req.pop("output_config", None)
        if spec.service_tier == "fast":
            req["speed"] = "fast"
        response = await client.messages.create(**req, timeout=600)
        record.extra["speed"] = getattr(response.usage, "speed", None)
        usage = response.usage
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
        record.request_id = getattr(response, "_request_id", None)
        record.stop_reason = response.stop_reason
        record.input_tokens = usage.input_tokens + cache_read + cache_write
        record.cached_input_tokens = cache_read
        record.cache_write_tokens = cache_write
        record.output_tokens = usage.output_tokens
        record.cost_usd = cost_usd(
            spec,
            uncached_input=usage.input_tokens,
            cached_input=cache_read,
            cache_write=cache_write,
            output=usage.output_tokens,
            service_tier=spec.service_tier,
        )
        record.extra["usage_priced"] = True
        details = getattr(usage, "output_tokens_details", None)
        record.reasoning_tokens = getattr(details, "thinking_tokens", None)
        thinking_chars = sum(
            len(getattr(b, "thinking", "") or "") for b in response.content if b.type == "thinking"
        )
        record.extra["thinking_chars"] = thinking_chars
        record.raw_output = _anthropic_payload(response)
        return response

    # ── OpenAI ─────────────────────────────────────────────────────────────
    async def _openai(
        self, kwargs: dict[str, Any], spec: PhaseSpec, phase: str, record: CallRecord
    ) -> Message:
        client = self._clients.openai
        assert client is not None, "OpenAI key not configured"
        system = "\n\n".join(block["text"] for block in kwargs["system"])
        user_content = kwargs["messages"][0]["content"]
        if isinstance(user_content, str):
            parts: list[dict[str, Any]] = [{"type": "input_text", "text": user_content}]
        else:
            parts = []
            for block in user_content:
                if block["type"] == "text":
                    parts.append({"type": "input_text", "text": block["text"]})
                elif block["type"] == "image":
                    src = block["source"]
                    parts.append(
                        {
                            "type": "input_image",
                            "image_url": f"data:{src['media_type']};base64,{src['data']}",
                            "detail": spec.image_detail or "auto",
                        }
                    )
                else:
                    raise ValueError(f"unsupported block {block['type']}")
        if phase == "visual":
            tool = kwargs["tools"][0]
            schema = to_openai_strict(tool["input_schema"])
            name = VISUAL_TOOL_NAME
        else:
            schema = to_openai_strict(kwargs["output_config"]["format"]["schema"])
            name = "property_evaluation"
        text_config: dict[str, Any] = {
            "format": {"type": "json_schema", "name": name, "schema": schema, "strict": True}
        }
        if spec.verbosity:
            text_config["verbosity"] = spec.verbosity
        # Mirror the app's Anthropic cache_control on the system prompt: an explicit
        # breakpoint after the developer prefix, so per-home images are billed as
        # plain input rather than written to cache (GPT-5.6+ implicit mode writes
        # the whole prompt at 1.25x).
        req: dict[str, Any] = {
            "model": spec.model,
            "input": [
                {
                    "role": "developer",
                    "content": [
                        {
                            "type": "input_text",
                            "text": system,
                            "prompt_cache_breakpoint": {"mode": "explicit"},
                        }
                    ],
                },
                {"role": "user", "content": parts},
            ],
            "text": text_config,
            "max_output_tokens": spec.max_output_tokens,
            "store": False,
            "prompt_cache_options": {"mode": "explicit"},
        }
        if spec.effort:
            req["reasoning"] = {"effort": spec.effort}
        if spec.service_tier:
            req["service_tier"] = spec.service_tier
        response = await client.responses.create(**req, timeout=900)
        usage = response.usage
        cached = getattr(usage.input_tokens_details, "cached_tokens", 0) or 0
        cache_write = getattr(usage.input_tokens_details, "cache_write_tokens", 0) or 0
        reasoning = getattr(usage.output_tokens_details, "reasoning_tokens", 0) or 0
        record.request_id = getattr(response, "_request_id", None) or response.id
        record.input_tokens = usage.input_tokens
        record.cached_input_tokens = cached
        record.cache_write_tokens = cache_write
        record.output_tokens = usage.output_tokens
        record.reasoning_tokens = reasoning
        record.extra["service_tier"] = getattr(response, "service_tier", None)
        record.cost_usd = cost_usd(
            spec,
            # input_tokens includes both cache reads and cache writes.
            uncached_input=usage.input_tokens - cached - cache_write,
            cached_input=cached,
            cache_write=cache_write,
            output=usage.output_tokens,
            service_tier=record.extra["service_tier"] or spec.service_tier,
        )
        record.extra["usage_priced"] = True

        refusal = None
        text = None
        messages = [item for item in response.output if item.type == "message"]
        record.extra["message_phases"] = [getattr(m, "phase", None) for m in messages]
        # Some models (seen on gpt-6-luna at effort none) add a "commentary"
        # message before the schema-constrained final answer; read only the latter.
        final = [m for m in messages if getattr(m, "phase", None) == "final_answer"]
        phased = any(getattr(m, "phase", None) for m in messages)
        # Commentary is never an answer, even when it happens to be valid JSON.
        for item in final if phased else messages[-1:]:
            for content in item.content:
                if content.type == "refusal":
                    refusal = content.refusal
                elif content.type == "output_text":
                    text = (text or "") + content.text
        if refusal is not None:
            stop = "refusal"
        elif response.status == "incomplete":
            reason = getattr(response.incomplete_details, "reason", None)
            stop = "max_tokens" if reason == "max_output_tokens" else f"incomplete:{reason}"
        elif response.status == "completed":
            stop = "tool_use" if phase == "visual" else "end_turn"
        else:
            stop = f"status:{response.status}"
        record.stop_reason = stop
        record.raw_output = text if refusal is None else {"refusal": refusal}

        anthropic_usage = Usage.model_construct(
            input_tokens=usage.input_tokens - cached,
            output_tokens=usage.output_tokens,
            cache_read_input_tokens=cached,
            cache_creation_input_tokens=0,
        )
        content_blocks: list[Any] = []
        if stop in ("tool_use", "end_turn") and text is not None:
            if phase == "visual":
                try:
                    payload = json.loads(text)
                except ValueError:
                    payload = text
                content_blocks.append(
                    ToolUseBlock.model_construct(
                        id=f"toolu_{uuid.uuid4().hex[:20]}",
                        name=VISUAL_TOOL_NAME,
                        input=payload,
                        type="tool_use",
                    )
                )
            else:
                content_blocks.append(TextBlock.model_construct(type="text", text=text))
        message = Message.model_construct(
            id=response.id,
            type="message",
            role="assistant",
            model=spec.model,
            content=content_blocks,
            stop_reason=stop,
            stop_sequence=None,
            usage=anthropic_usage,
        )
        object.__setattr__(message, "_request_id", record.request_id)
        return message


def _anthropic_payload(response: Message) -> Any:
    for block in response.content:
        if isinstance(block, ToolUseBlock):
            return block.input
    texts = [b.text for b in response.content if b.type == "text"]
    return texts[0] if len(texts) == 1 else texts


def to_openai_strict(schema: dict[str, Any]) -> dict[str, Any]:
    """Make an inlined Pydantic schema acceptable to OpenAI strict Structured Outputs.

    Every object property becomes required with additionalProperties false;
    optional fields are already nullable in these response models, and fields
    that only had a default (e.g. empty lists) must now be emitted explicitly.
    """

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            out = {k: walk(v) for k, v in node.items() if k not in ("default", "title")}
            if out.get("type") == "object" and "properties" in out:
                out["required"] = list(out["properties"])
                out["additionalProperties"] = False
            return out
        if isinstance(node, list):
            return [walk(item) for item in node]
        return node

    return walk(json.loads(json.dumps(schema)))


def _dummy_request() -> Any:
    import httpx

    return httpx.Request("POST", "https://shim.invalid/v1/messages")


def record_dict(record: CallRecord) -> dict[str, Any]:
    return asdict(record)

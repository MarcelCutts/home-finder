"""Benchmark configurations. Both phases are set explicitly; nothing relies on a provider default."""

from __future__ import annotations

from mc.shim import PhaseSpec, RunConfig


def anthropic(model: str, effort: str | None, thinking: str | None = "adaptive") -> PhaseSpec:
    return PhaseSpec(provider="anthropic", model=model, effort=effort, thinking=thinking)


def openai(
    model: str,
    effort: str | None,
    *,
    detail: str = "high",
    verbosity: str | None = None,
    tier: str | None = None,
) -> PhaseSpec:
    return PhaseSpec(
        provider="openai",
        model=model,
        effort=effort,
        image_detail=detail,
        verbosity=verbosity,
        service_tier=tier,
    )


def same(config_id: str, phase: PhaseSpec, note: str = "") -> RunConfig:
    return RunConfig(id=config_id, visual=phase, evaluation=phase, note=note)


_configs: list[RunConfig] = [
    # Production (2026-10-10 trial): visual adaptive/low, evaluation between_tools/low.
    RunConfig(
        id="sonnet55-low",
        visual=anthropic("claude-sonnet-5-5", "low"),
        evaluation=anthropic("claude-sonnet-5-5", "low", "between_tools"),
        note="current production baseline",
    ),
    RunConfig(
        id="sonnet55-medium",
        visual=anthropic("claude-sonnet-5-5", "medium"),
        evaluation=anthropic("claude-sonnet-5-5", "low", "between_tools"),
    ),
    RunConfig(
        id="sonnet55-high",
        visual=anthropic("claude-sonnet-5-5", "high"),
        evaluation=anthropic("claude-sonnet-5-5", "low", "between_tools"),
    ),
    RunConfig(
        id="haiku55-low",
        visual=anthropic("claude-haiku-5-5", "low"),
        evaluation=anthropic("claude-haiku-5-5", "low"),
    ),
    RunConfig(
        id="haiku55-medium",
        visual=anthropic("claude-haiku-5-5", "medium"),
        evaluation=anthropic("claude-haiku-5-5", "low"),
    ),
    RunConfig(
        id="haiku55-nothink",
        visual=anthropic("claude-haiku-5-5", None, "disabled"),
        evaluation=anthropic("claude-haiku-5-5", None, "disabled"),
    ),
    RunConfig(
        id="opus55-low",
        visual=anthropic("claude-opus-5-5", "low"),
        evaluation=anthropic("claude-opus-5-5", "low"),
    ),
    RunConfig(
        id="opus55-medium",
        visual=anthropic("claude-opus-5-5", "medium"),
        evaluation=anthropic("claude-opus-5-5", "low"),
    ),
    RunConfig(
        id="fable51-low",
        visual=anthropic("claude-fable-5-1", "low"),
        evaluation=anthropic("claude-fable-5-1", "low"),
    ),
    RunConfig(
        id="opus55-low-fast",
        visual=PhaseSpec("anthropic", "claude-opus-5-5", "low", "adaptive", service_tier="fast"),
        evaluation=PhaseSpec("anthropic", "claude-opus-5-5", "low", "adaptive", service_tier="fast"),
        note="fast mode, 2x price",
    ),
    # Split pipeline: strong eyes, cheap writer.
    RunConfig(
        id="sonnet55-low+haiku-eval",
        visual=anthropic("claude-sonnet-5-5", "low"),
        evaluation=anthropic("claude-haiku-5-5", "low"),
    ),
]


def split(config_id: str, visual: PhaseSpec, evaluation: PhaseSpec, note: str = "") -> RunConfig:
    return RunConfig(id=config_id, visual=visual, evaluation=evaluation, note=note)


# OpenAI: evaluation runs at low effort throughout, like the Anthropic arms.
_configs += [
    # $2/$10 tier, price-matched to Sonnet 5.5.
    same("sol61-low", openai("gpt-6.1-sol", "low")),
    split("sol61-medium", openai("gpt-6.1-sol", "medium"), openai("gpt-6.1-sol", "low")),
    split("sol61-high", openai("gpt-6.1-sol", "high"), openai("gpt-6.1-sol", "low")),
    same("sol61-low-imglow", openai("gpt-6.1-sol", "low", detail="low"), "512px image detail"),
    same("sol61-low-flex", openai("gpt-6.1-sol", "low", tier="flex"), "flex tier, 0.5x price"),
    same("sol61-low-fast", openai("gpt-6.1-sol", "low", tier="fast"), "fast tier, 2x price"),
    same("sol6-low", openai("gpt-6-sol", "low")),
    same("sol6-none", openai("gpt-6-sol", "none")),
    same("terra56-low", openai("gpt-5.6-terra", "low")),
    # $4/$20 tier, price-matched to Opus 5.5.
    same("sol56-low", openai("gpt-5.6-sol", "low")),
    split("sol56-medium", openai("gpt-5.6-sol", "medium"), openai("gpt-5.6-sol", "low")),
    # $0.10/$0.50 tier, price-matched to Haiku 5.5.
    same("luna6-low", openai("gpt-6-luna", "low")),
    split("luna6-medium", openai("gpt-6-luna", "medium"), openai("gpt-6-luna", "low")),
    same("luna6-none", openai("gpt-6-luna", "none")),
    same("luna56-low", openai("gpt-5.6-luna", "low")),
    # $10/$50 tier, price-matched to Fable 5.1.
    same("astra6-low", openai("gpt-6-astra", "low")),
]

CONFIGS: dict[str, RunConfig] = {c.id: c for c in _configs}

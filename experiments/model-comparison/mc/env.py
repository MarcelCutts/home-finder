"""Load provider keys the same way the app does (its .env), without ever printing them."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import dotenv_values

# Worktree experiments read the main checkout's private .env.
MAIN_ENV = Path("/home/marcel/projects/lab/home-finder/.env")

_NAMES = {
    "anthropic": ("HOME_FINDER_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY"),
    "openai": ("OPENAI_API_KEY", "HOME_FINDER_OPENAI_API_KEY"),
}


def provider_key(provider: str, env_file: Path = MAIN_ENV) -> str | None:
    values = dotenv_values(env_file) if env_file.exists() else {}
    for name in _NAMES[provider]:
        value = os.environ.get(name) or values.get(name)
        if value:
            return value
    return None


def make_clients() -> tuple[object | None, object | None]:
    import anthropic
    import openai

    a_key = provider_key("anthropic")
    o_key = provider_key("openai")
    # Pin base URLs: this shell exports ANTHROPIC_BASE_URL for Claude Code itself.
    a = (
        anthropic.AsyncAnthropic(
            api_key=a_key, base_url="https://api.anthropic.com", max_retries=0, timeout=600
        )
        if a_key
        else None
    )
    o = (
        openai.AsyncOpenAI(
            api_key=o_key, base_url="https://api.openai.com/v1", max_retries=0, timeout=900
        )
        if o_key
        else None
    )
    return a, o

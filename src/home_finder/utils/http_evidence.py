"""Shared conservative recognition of challenge responses, independent of parsers."""

from collections.abc import Mapping


def is_challenge(
    html: str, headers: Mapping[str, str] | None = None, *, scan_characters: int = 5000
) -> bool:
    normalized = {key.lower(): value.lower() for key, value in (headers or {}).items()}
    if normalized.get("cf-mitigated") == "challenge":
        return True
    lower = html[:scan_characters].lower()
    return any(
        token in lower
        for token in (
            "checking your browser",
            "just a moment",
            "enable javascript and cookies",
            "cf-chl-",
            "cloudflare ray id",
            "verify you are human",
            "captcha challenge",
        )
    )

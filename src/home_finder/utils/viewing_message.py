"""AI-drafted viewing request message generation."""

from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from home_finder.models import PropertyQualityAnalysis

MODEL: Final = "claude-sonnet-4-6"
MAX_TOKENS: Final = 512
REQUEST_TIMEOUT: Final = 30.0
MAX_RETRIES: Final = 3

SYSTEM_PROMPT: Final = (
    "You write viewing request messages for London rental properties on behalf of a tenant. "
    "Write exactly 2-3 sentences (40-60 words max). "
    "Mention one specific feature from the property analysis that genuinely appeals. "
    "State the tenant's situation in one clause. "
    "Propose flexible viewing availability. "
    "No fluff, no formality, no subject line, no greeting like 'Dear landlord'. "
    "Just the message body, ready to paste into a portal's message box."
)


def _build_user_prompt(
    *,
    title: str,
    postcode: str | None,
    source_name: str,
    quality_analysis: "PropertyQualityAnalysis | None",
    profile: str,
) -> str:
    """Assemble the user prompt with property details in XML tags."""
    parts: list[str] = []
    parts.append(f"<property_title>{title}</property_title>")
    if postcode:
        parts.append(f"<postcode>{postcode}</postcode>")
    parts.append(f"<source>{source_name}</source>")

    if quality_analysis:
        if quality_analysis.highlights:
            parts.append(f"<highlights>{', '.join(quality_analysis.highlights)}</highlights>")
        if quality_analysis.lowlights:
            parts.append(f"<lowlights>{', '.join(quality_analysis.lowlights)}</lowlights>")
        if quality_analysis.one_line:
            parts.append(f"<one_liner>{quality_analysis.one_line}</one_liner>")
        if quality_analysis.kitchen and quality_analysis.kitchen.hob_type != "unknown":
            parts.append(f"<hob_type>{quality_analysis.kitchen.hob_type}</hob_type>")
        if quality_analysis.bedroom and quality_analysis.bedroom.office_separation != "unknown":
            parts.append(
                f"<office_separation>{quality_analysis.bedroom.office_separation}</office_separation>"
            )

    parts.append(f"<tenant_profile>{profile}</tenant_profile>")
    return "\n".join(parts)


async def generate_viewing_message(
    *,
    api_key: str,
    title: str,
    postcode: str | None,
    source_name: str,
    quality_analysis: "PropertyQualityAnalysis | None",
    profile: str,
) -> str:
    """Generate a viewing request message using Claude.

    Creates and closes an Anthropic client per call (on-demand, not batch).
    Exceptions propagate to caller for graceful handling in the route.
    """
    import anthropic
    import httpx

    user_prompt = _build_user_prompt(
        title=title,
        postcode=postcode,
        source_name=source_name,
        quality_analysis=quality_analysis,
        profile=profile,
    )

    client = anthropic.AsyncAnthropic(
        api_key=api_key,
        max_retries=MAX_RETRIES,
        timeout=httpx.Timeout(REQUEST_TIMEOUT),
    )
    try:
        response = await client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user_prompt}],
        )
        return response.content[0].text  # type: ignore[union-attr]
    finally:
        await client.close()

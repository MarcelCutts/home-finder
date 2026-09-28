"""Select bounded, source-labelled listing evidence for analysis."""

from __future__ import annotations

import hashlib
import html
import re
from itertools import zip_longest

from home_finder.models import MergedProperty
from home_finder.models.listing_text import ListingTextInputs


def select_listing_text(merged: MergedProperty) -> ListingTextInputs:
    """Share the existing 3,000-character/15-feature allowance across sources.

    Keep conflicting sources labelled rather than silently selecting whichever
    description is longest. Escape markup before selection so source content
    cannot close the prompt's evidence delimiters. This is not an LLM injection
    proof; the model must still treat listing content as untrusted evidence.
    """
    evidence = sorted(merged.text_by_source.items(), key=lambda item: item[0].value)
    descriptions = [(source, value.description) for source, value in evidence if value.description]
    description_parts = []
    if descriptions:
        allowance = 3000 // len(descriptions) - 40
        for source, description in descriptions:
            assert description is not None
            escaped = html.escape(description, quote=False)
            label = f"[{source.display_name}]\n" if len(descriptions) > 1 else ""
            description_parts.append(label + escaped[:allowance])

    # Round-robin prevents the first portal from consuming every feature slot.
    feature_groups = [
        [
            f"[{source.display_name}] {html.escape(feature, quote=False)[:200]}"
            for feature in (value.features or ())[:15]
            if feature.strip()
        ]
        for source, value in evidence
    ]
    features = [
        feature
        for group in zip_longest(*feature_groups)
        for feature in group
        if feature is not None
    ][:15]
    return ListingTextInputs(
        description="\n\n".join(description_parts) or None,
        features=tuple(features),
        source_hashes={
            source.value: hashlib.sha256(value.model_dump_json().encode()).hexdigest()
            for source, value in evidence
        },
        source_urls={source.value: str(url) for source, url in merged.source_urls.items()},
    )


def supported_council_tax_band(
    claimed_band: str | None, description: str | None, features: list[str] | None
) -> str | None:
    """Retain a claimed band only when the actual listing inputs support it.

    Conservative textual validation: ambiguous, absent, estimated or conflicting
    mentions stay unknown. Reference-area costs and image observations do not
    establish a council-tax band. This does not verify a portal's claim with VOA.
    """
    if claimed_band in (None, "unknown"):
        return claimed_band
    text = html.unescape("\n".join([description or "", *(features or [])]))
    text = re.sub(r"<[^>]*>", " ", text)
    matches = re.finditer(
        r"\bcouncil\s+tax\s*(?:band\s*)?[:\-\u2013]?\s*([A-H])\b", text, re.IGNORECASE
    )
    bands = set()
    for match in matches:
        context = text[max(0, match.start() - 30) : match.end() + 30].lower()
        if re.search(r"\b(estimate[ds]?|assum\w*|budget\w*)\b", context):
            continue
        bands.add(match[1].upper())
    return claimed_band if bands == {claimed_band} else "unknown"

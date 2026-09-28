"""Select bounded, source-labelled listing evidence for analysis."""

from __future__ import annotations

import hashlib
import html
from itertools import zip_longest

from home_finder.models import MergedProperty
from home_finder.models.listing_text import ListingTextInputs
from home_finder.utils.listing_facts import supported_band


def select_listing_text(merged: MergedProperty) -> ListingTextInputs:
    """Share the existing 3,000-character/15-feature allowance across sources.

    Keep conflicting sources labelled rather than silently selecting whichever
    description is longest. Escape markup before selection so source content
    cannot close the prompt's evidence delimiters. This is not an LLM injection
    proof; the model must still treat listing content as untrusted evidence.
    """
    evidence = sorted(merged.text_by_source.items(), key=lambda item: item[0].value)
    descriptions = [
        (
            source,
            "Conflicting descriptions were received for this listing. "
            "Description facts are withheld pending clarification."
            if value.description_conflicting
            else value.description,
        )
        for source, value in evidence
        if value.description or value.description_conflicting
    ]
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
            (source, f"[{source.display_name}] {html.escape(feature, quote=False)[:200]}")
            for feature in (
                (
                    "Conflicting listing features were received; "
                    "feature facts are withheld pending clarification.",
                )
                if value.features_conflicting
                else (value.features or ())[:15]
            )
            if feature.strip()
        ]
        for source, value in evidence
    ]
    selected_features = [
        feature
        for group in zip_longest(*feature_groups)
        for feature in group
        if feature is not None
    ][:15]
    description_sources = {source for source, _description in descriptions}
    feature_sources = {source for source, _feature in selected_features}
    observation_ids = {}
    for source, value in evidence:
        identifiers: set[str] = set()
        if source in description_sources:
            identifiers.update(value.description_observation_ids)
        if source in feature_sources:
            identifiers.update(value.features_observation_ids)
        if identifiers:
            observation_ids[source.value] = tuple(sorted(identifiers))
    return ListingTextInputs(
        policy_version=2,
        description="\n\n".join(description_parts) or None,
        features=tuple(feature for _source, feature in selected_features),
        source_hashes={
            source.value: hashlib.sha256(value.model_dump_json().encode()).hexdigest()
            for source, value in evidence
        },
        source_urls={source.value: str(url) for source, url in merged.source_urls.items()},
        source_observation_ids=observation_ids,
    )


def supported_council_tax_band(
    claimed_band: str | None, description: str | None, features: list[str] | None
) -> str | None:
    """Retain a claimed band only when the actual listing inputs support it.

    Conservative textual validation: ambiguous, absent, estimated or conflicting
    mentions stay unknown. Reference-area costs and image observations do not
    establish a council-tax band. This does not verify a portal's claim with VOA.
    """
    return supported_band("council_tax", claimed_band, description, features)

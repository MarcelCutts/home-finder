"""Select bounded, source-labelled listing evidence for analysis."""

from __future__ import annotations

import hashlib
import html
from itertools import zip_longest

from home_finder.models import MergedProperty
from home_finder.models.listing_member import labelled_members
from home_finder.models.listing_text import ListingTextInputs
from home_finder.utils.listing_facts import supported_band


def select_listing_text(merged: MergedProperty) -> ListingTextInputs:
    """Share the existing 3,000-character/15-feature allowance across sources.

    Keep conflicting sources labelled rather than silently selecting whichever
    description is longest. Escape markup before selection so source content
    cannot close the prompt's evidence delimiters. This is not an LLM injection
    proof; the model must still treat listing content as untrusted evidence.
    """
    members = labelled_members(merged.listing_members)
    # Old serialized assessments/fixtures retain their per-portal keys. New
    # explicit membership always records the exact retained representation.
    evidence = [
        (member.evidence_key if merged.members is not None else member.source.value, label, member)
        for member, label in members
    ]
    descriptions = [
        (
            key,
            label,
            "Conflicting descriptions were received for this listing. "
            "Description facts are withheld pending clarification."
            if member.text.description_conflicting
            else member.text.description,
        )
        for key, label, member in evidence
        if member.text.description or member.text.description_conflicting
    ]
    description_parts: list[str] = []
    description_sources: set[str] = set()
    if descriptions:
        allowance = 3000 // len(descriptions)
        for key, label, description in descriptions:
            assert description is not None
            prefix = f"[{label}]\n" if len(descriptions) > 1 else ""
            available = allowance - len(prefix) - 2
            if available <= 0:
                continue
            description_parts.append(prefix + html.escape(description, quote=False)[:available])
            description_sources.add(key)

    # Round-robin allocates the existing allowance across advertisements, including
    # advertisements on the same portal. Full evidence hashes survive truncation.
    feature_groups = [
        [
            (key, f"[{label}] {html.escape(feature, quote=False)[:200]}")
            for feature in (
                (
                    "Conflicting listing features were received; "
                    "feature facts are withheld pending clarification.",
                )
                if member.text.features_conflicting
                else (member.text.features or ())[:15]
            )
            if feature.strip()
        ]
        for key, label, member in evidence
    ]
    selected_features = [
        feature
        for group in zip_longest(*feature_groups)
        for feature in group
        if feature is not None
    ][:15]
    feature_sources = {key for key, _feature in selected_features}
    observation_ids = {}
    for key, _label, member in evidence:
        identifiers: set[str] = set()
        if key in description_sources:
            identifiers.update(member.text.description_observation_ids)
        if key in feature_sources:
            identifiers.update(member.text.features_observation_ids)
        if identifiers:
            observation_ids[key] = tuple(sorted(identifiers))
    return ListingTextInputs(
        policy_version=3,
        description="\n\n".join(description_parts) or None,
        features=tuple(feature for _key, feature in selected_features),
        source_hashes={
            key: hashlib.sha256(member.text.model_dump_json().encode()).hexdigest()
            for key, _label, member in evidence
        },
        source_urls={key: str(member.url) for key, _label, member in evidence},
        source_labels={key: label for key, label, _member in evidence},
        omitted_description_keys=tuple(
            key for key, _label, _description in descriptions if key not in description_sources
        ),
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

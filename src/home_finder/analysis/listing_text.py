"""Select bounded, source-labelled listing evidence for analysis."""

from __future__ import annotations

import hashlib
import html
import re
from itertools import zip_longest
from typing import Literal

from home_finder.models import MergedProperty, PropertyType
from home_finder.models.listing_text import ListingTextEvidence, ListingTextInputs
from home_finder.utils.listing_facts import plain_listing_text, supported_band


def source_text_hash(evidence: ListingTextEvidence) -> str:
    """Fingerprint one source's whole text evidence, as an analysis keeps it.

    The listing page compares it with the current evidence to tell whether a source
    is unchanged since the analysis read it; observation dates count as a change.
    """
    return hashlib.sha256(evidence.model_dump_json().encode()).hexdigest()


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
        source_hashes={source.value: source_text_hash(value) for source, value in evidence},
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
    return supported_band("council_tax", claimed_band, description, features)


def supported_bills_included(
    claimed: Literal["yes", "no", "unknown"],
    description: str | None,
    features: list[str] | None,
) -> Literal["yes", "no", "unknown"]:
    """Retain only a simple, explicit and unambiguous bills declaration.

    This intentionally misses elaborate prose. Silence, questions, conditions,
    partial inclusions and contradictory statements cannot establish the flag.
    A retained yes still does not establish coverage, caps or a zero bill.
    """
    if claimed == "unknown":
        return claimed
    # Preserve HTML list/line boundaries so one bullet cannot swallow the next.
    text = plain_listing_text([description or "", *(features or [])], preserve_line_breaks=True)
    text = re.sub(r"\[(?:Rightmove|Zoopla|OpenRent|OnTheMarket)\]", "", text, flags=re.I)
    states: set[str] = set()
    subject = r"(?:all\s+)?(?:utility\s+)?bills"
    for clause in re.split(r"[.;\n]", text):
        clause = clause.strip().lower()
        # Strip a leading list marker only, never qualifications within the text.
        clause = re.sub(r"^[-*\u2022\u2013\u2014]\s+", "", clause)
        if not re.search(r"\bbills\b", clause):
            if re.search(
                r"\b(?:gas|electricity|water|broadband|council tax)\b.*"
                r"\b(?:excluded|not included|extra|additional)\b",
                clause,
            ):
                return "unknown"
            continue
        if re.fullmatch(
            rf"{subject}\s+(?:are\s+)?(?:included|inclusive)(?:\s+in\s+(?:the\s+)?rent)?",
            clause,
        ):
            states.add("yes")
        elif re.fullmatch(
            rf"(?:{subject}\s+(?:are\s+)?(?:excluded|not included)|no\s+bills\s+included)",
            clause,
        ):
            states.add("no")
        else:
            return "unknown"
    return claimed if states == {claimed} else "unknown"


# A word for the dwelling itself, singular: plurals usually describe the neighbourhood.
_DWELLING = (
    r"(?:conversion|converted|terraced?|house|building|property|flat|apartment|"
    r"maisonette|home|townhouse|mansion|block|villa|cottage|build)"
)
_ERAS = (PropertyType.VICTORIAN, PropertyType.EDWARDIAN, PropertyType.GEORGIAN)


def _near_dwelling(word: str) -> str:
    """The word within two words of the dwelling, and not as a style ("Victorian style")."""
    return rf"\b{word}\s+(?!style\b)(?:[\w-]+\s+){{0,2}}{_DWELLING}\b"


_STATED_TYPES: dict[PropertyType, str] = {
    **{era: _near_dwelling(era.value) for era in _ERAS},
    PropertyType.PERIOD_CONVERSION: _near_dwelling("period"),
    PropertyType.NEW_BUILD: (
        r"\bnew[\s-]build\b|\bnewly[\s-]built\b|"
        r"\bbrand[\s-]new\s+(?:development|building|block|build)\b"
    ),
    PropertyType.PURPOSE_BUILT: r"\bpurpose[\s-]built\b",
    PropertyType.WAREHOUSE: (
        r"\bwarehouse\s+(?:conversion|apartment|flat|building|development|loft)\b|"
        r"\b(?:converted|former)\s+warehouse\b"
    ),
    PropertyType.EX_COUNCIL: (
        r"\bex[\s-]?(?:council|local[\s-]authority)\b|\bformer\s+(?:council|local[\s-]authority)\b"
    ),
}


def supported_property_type(
    claimed: PropertyType, description: str | None, features: list[str] | None
) -> PropertyType:
    """Retain a claimed building type only when the listing text names it.

    The model sees the building in the photos and tends to name a type the advert
    does not state; that guess would reach the card, the score and the acoustic
    profile as a stated fact. Only wording about the property counts: an era must
    sit within two words of a word for the dwelling, so "Victoria Park" or "Georgian
    squares nearby" establish nothing. An advert naming two eras leaves the era unknown.
    This checks textual support, not the building's actual age or construction.
    """
    if claimed == PropertyType.UNKNOWN:
        return claimed
    text = plain_listing_text([description or "", *(features or [])]).lower()
    text = re.sub(r"\s+", " ", text)
    stated: set[PropertyType] = set()
    for property_type, pattern in _STATED_TYPES.items():
        for match in re.finditer(pattern, text):
            if re.search(r"\b(?:not|non|no)[\s-]+(?:an?\s+)?$", text[: match.start()]):
                continue
            stated.add(property_type)
            # "Victorian conversion" names both the era and a period conversion.
            if property_type in _ERAS and re.search(r"\bconver", match.group()):
                stated.add(PropertyType.PERIOD_CONVERSION)
    if len(stated.intersection(_ERAS)) > 1:
        stated.difference_update(_ERAS)
    return claimed if claimed in stated else PropertyType.UNKNOWN

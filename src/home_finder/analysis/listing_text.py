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


_ERAS = (PropertyType.VICTORIAN, PropertyType.EDWARDIAN, PropertyType.GEORGIAN)
_WORD = r"\w[\w'/-]*"
# "Victorian style" names a look and "Victorian fireplaces" a fitting, not the building's age.
_STYLE = "style[ds]?|inspired|themed|replica"
_FITTING = (
    "features?|fireplaces?|charm|details?|windows?|doors?|ceilings?|cornicing|tiles?|floorboards"
)
# A run of adjectives ends where a new phrase starts ("in", "this"), at a style or a
# fitting, and at a plural, which describes the neighbourhood ("Victorian terraces")
# rather than this home. "mansion" ends it so a period mansion block is not a conversion.
_RUN_ENDERS = (
    "an?|in|into|of|at|on|to|with|and|or|from|for|by|near|this|that|the|its|our|is|are|has|"
    f"have|within|which|mansion|{_STYLE}|{_FITTING}|"
    "houses|homes|terraces|buildings|properties|flats|apartments|conversions|villas|cottages|"
    "townhouses|blocks|mansions|warehouses|streets|roads|squares|shops|pubs|bars"
)
_ADJECTIVE = rf"(?:(?!(?:{_RUN_ENDERS})\b){_WORD},?\s+)"
_HOME = (
    "house|building|property|flat|apartment|home|block|development|maisonette|conversion|residence"
)


def _then(word: str, nouns: str, reach: int) -> str:
    """``word``, at most ``reach`` adjectives, then one of ``nouns`` in the singular."""
    return rf"\b{word},?\s+{_ADJECTIVE}{{0,{reach}}}(?:{nouns})\b"


def _era(name: str) -> str:
    # Neither a revival ("neo-Georgian") nor a style or a fitting ("Victorian-style").
    era = rf"(?<!neo[\s-])(?<!mock[\s-])(?<!faux[\s-]){name}\b(?![\s-]+(?:{_STYLE}|{_FITTING})\b)"
    return "|".join(
        (
            # "Victorian ground floor garden flat"
            _then(era, f"{_HOME}|converted|terraced?|townhouse|mansion|villa|cottage|build", 5),
            # "converted Victorian school"
            rf"\bconverted\s+{_ADJECTIVE}{{0,2}}{era}",
            # "the house is Victorian"
            rf"\b(?:{_HOME})\s+(?:is|was|dates\s+(?:back\s+)?(?:from|to))\s+"
            rf"(?:(?:an?|the|originally|late|early|mid)[\s-]+)*{era}",
        )
    )


_STATED_TYPES: dict[PropertyType, re.Pattern[str]] = {
    property_type: re.compile(pattern)
    for property_type, pattern in {
        **{era: _era(era.value) for era in _ERAS},
        PropertyType.PERIOD_CONVERSION: _then(
            "period",
            "conversion|converted|build|building|property|house|home|terraced?|cottage|villa|"
            "townhouse|flat|apartment|maisonette",
            3,
        ),
        PropertyType.NEW_BUILD: "|".join(
            (
                r"\bnew[\s-]?build\b",
                r"\bbrand[\s-]new\s+(?:development|building|block|build)\b",
                _then(r"newly[\s-](?:built|constructed)", f"{_HOME}|studio|townhouse", 3),
                rf"\b(?:{_HOME})\s+(?:is|was|has\s+been)\s+newly[\s-](?:built|constructed)\b",
            )
        ),
        PropertyType.PURPOSE_BUILT: "|".join(
            (
                # Followed by the building, so "purpose built bike storage" names nothing.
                _then(r"purpose[\s-]built", f"{_HOME}|studio|mansion|complex|estate", 3),
                rf"\b(?:{_HOME})\s+(?:is|was|has\s+been)\s+purpose[\s-]built\b",
                # A feature bullet that says only this, after its source label.
                r"^(?:\[[^\]]*\]\s*)?purpose[\s-]built$",
            )
        ),
        PropertyType.WAREHOUSE: (
            r"\bwarehouse\s+(?:conversion|apartment|flat|building|development|loft|unit|studio|"
            r"space|living|home|property)\b|"
            rf"\b(?:converted|former|original|authentic)\s+{_ADJECTIVE}{{0,2}}warehouse\b"
            r"(?![\s-]+(?:style|district|quarter|area)\b)"
        ),
        PropertyType.EX_COUNCIL: (
            r"\bex[\s-]?(?:council|local[\s-]authority|la)\b|"
            r"\bformer\s+(?:council|local[\s-]authority)\s+"
            r"(?:flat|apartment|property|home|house|housing|block|estate|maisonette)\b"
        ),
    }.items()
}
_NEGATED = re.compile(r"(?:\b(?:not|no|non|never)|n['\u2019]t)[\s-]+(?:(?:an?|the|your)\s+)?$")
# The phrase belongs to somewhere else: "close to the Georgian Sutton House". A comma or
# "this" between the cue and the phrase starts a new subject, so the cue no longer applies.
_ELSEWHERE_BEFORE = re.compile(
    r"\b(?:close to|near(?: to)?|nearby|next(?: door)? to|adjacent to|opposite|beside|alongside|"
    r"behind|moments?(?: away)? (?:from|to)|minutes?(?: away)? (?:from|to)|walk (?:from|to)|"
    r"walking distance (?:of|to|from)|far from|overlook(?:s|ing)|"
    r"views? (?:of|over|across|towards|to)|surrounded by|amongst?|neighbouring|"
    r"back(?:s|ing) onto)"
    rf"\s+(?:(?!this\b){_WORD}\s+){{0,2}}$"
)
_ELSEWHERE_AFTER = re.compile(
    r"\s+(?:next door|nearby|close by|opposite|across the (?:road|street)|over the road|"
    r"around the corner)(?=\s*(?:,|$))"
)
# "Warehouse conversion style apartment": the whole phrase is a style.
_STYLE_AFTER = re.compile(rf"[\s-]+(?:{_STYLE})\b")
_BUILDING_CONVERTED = re.compile(
    r"(?<!loft\s)(?<!garage\s)(?<!attic\s)(?<!basement\s)(?<!cellar\s)"
    r"\bconver(?:sion|ted)\b(?!\s+(?:loft|garage|attic|basement|cellar)\b)"
)
_ERA_NAMES = "|".join(era.value for era in _ERAS)
_TWO_ERAS_OFFERED = re.compile(
    rf"\b(?:{_ERA_NAMES})\s*(?:/|\bor\b|\band\b|\bto\b)\s*(?:(?:early|late|mid)[\s-]+)?"
    rf"(?:{_ERA_NAMES})\b"
)


def _types_stated(sentence: str) -> set[PropertyType]:
    """Building types one lower-case sentence states about the home itself."""
    stated: set[PropertyType] = set()
    for property_type, pattern in _STATED_TYPES.items():
        for match in pattern.finditer(sentence):
            before, after = sentence[: match.start()], sentence[match.end() :]
            if (
                _NEGATED.search(before)
                or _ELSEWHERE_BEFORE.search(before)
                or _ELSEWHERE_AFTER.match(after)
                or _STYLE_AFTER.match(after)
            ):
                continue
            stated.add(property_type)
    # "First floor of a converted Victorian house" names a period conversion too.
    if stated.intersection(_ERAS) and _BUILDING_CONVERTED.search(sentence):
        stated.add(PropertyType.PERIOD_CONVERSION)
    # "Victorian/Edwardian" settles neither era.
    if _TWO_ERAS_OFFERED.search(sentence):
        stated.difference_update(_ERAS)
    return stated


def supported_property_type(
    claimed: PropertyType, description: str | None, features: list[str] | None
) -> PropertyType:
    """Retain a claimed building type only when the listing text names it.

    The model sees the building in the photos and tends to name a type the advert
    does not state; that guess would reach the card, the score and the acoustic
    profile as a stated fact. Only wording about the home itself counts, read one
    sentence at a time: "Victoria Park", "Georgian squares nearby", "Victorian style"
    and "purpose built bike storage" establish nothing. An advert naming two eras
    leaves the era unknown. The wording rules are heuristics that lean towards
    unknown when a phrase could describe something else. This checks textual
    support, not the building's actual age or construction.
    """
    if claimed == PropertyType.UNKNOWN:
        return claimed
    text = plain_listing_text([description or "", *(features or [])], preserve_line_breaks=True)
    stated: set[PropertyType] = set()
    for sentence in re.split(r"[.;!?\n]+", text.lower()):
        stated.update(_types_stated(" ".join(sentence.split())))
    if len(stated.intersection(_ERAS)) > 1:
        stated.difference_update(_ERAS)
    return claimed if claimed in stated else PropertyType.UNKNOWN

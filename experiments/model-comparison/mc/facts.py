"""Deterministic facts stated explicitly in the listing text the models received.

Each extractor returns the value an assessment field must hold when the text is
unambiguous, or None when the text is silent or ambiguous. Deliberately
conservative: a missed fact costs coverage, a wrong one would corrupt scoring.
"""

from __future__ import annotations

import html
import re
from collections.abc import Callable
from typing import Any

from home_finder.filters.listing_text import select_listing_text
from home_finder.models import MergedProperty


def listing_text(merged: MergedProperty) -> str:
    inputs = select_listing_text(merged)
    return html.unescape((inputs.description or "") + "\n" + "\n".join(inputs.features))


def _has(pattern: str, text: str) -> bool:
    return re.search(pattern, text, re.IGNORECASE) is not None


def hob(text: str) -> str | None:
    found = {
        "gas": _has(r"\bgas (hob|cooker|oven|cooking|range)", text),
        "induction": _has(r"\binduction\b", text),
        "electric": _has(r"\b(electric|ceramic) (hob|cooker)", text),
    }
    hits = [k for k, v in found.items() if v]
    return hits[0] if len(hits) == 1 else None


def furnished(text: str) -> str | None:
    if _has(r"furnished (or|/) ?unfurnished|unfurnished (or|/) ?furnished|flexible", text):
        return None
    if _has(r"part[- ]?furnished", text):
        return "part_furnished"
    if _has(r"\bunfurnished\b", text):
        return "unfurnished"
    if _has(r"\b(fully )?furnished\b", text):
        return "furnished"
    return None


def epc(text: str) -> str | None:
    bands = set(
        m.group(1).upper()
        for m in re.finditer(
            r"\bEPC(?: rating| band| grade)?\s*(?:of|is|:|-|=)?\s*([A-G])\b", text, re.IGNORECASE
        )
    )
    return bands.pop() if len(bands) == 1 else None


def council_tax(text: str) -> str | None:
    bands = set(
        m.group(1).upper()
        for m in re.finditer(
            r"council tax(?: band)?\s*(?:is|:|-|=)?\s*(?:band\s*)?([A-H])\b", text, re.IGNORECASE
        )
    )
    return bands.pop() if len(bands) == 1 else None


def floor_level(text: str) -> str | None:
    found = {
        "basement": _has(r"\b(basement|lower[- ]ground)\b", text),
        "ground": _has(r"\bground floor\b", text) and not _has(r"lower[- ]ground", text),
        "top": _has(r"\btop floor\b", text),
    }
    hits = [k for k, v in found.items() if v]
    return hits[0] if len(hits) == 1 else None


_NO_PETS = r"\bno (pets|animals)\b|\bpets? (are )?not (allowed|permitted|accepted)\b"
_PETS_OK = r"\bpets? (are )?(allowed|welcome)\b|\bpet[- ]friendly\b"


def pets(text: str) -> str | None:
    no, yes = _has(_NO_PETS, text), _has(_PETS_OK, text)
    if _has(r"pets? (considered|on request|negotiable|by arrangement)", text) or (no and yes):
        return None
    return "no" if no else "yes" if yes else None


# Listing-only claims a photo cannot establish: a highlight asserting one needs text support.
_BADGE_SUPPORT = {
    "Pets allowed": _PETS_OK,
    "Bills included": r"\bbills? (are )?included\b|\binclusive of (all )?bills\b",
    "Parking included": r"\b(off[- ]street|allocated|private|secure|underground) parking\b|\bparking (space|bay)\b",
}


def badge_violations(analysis: dict[str, Any], text: str) -> list[str]:
    """Highlights that contradict the text, or assert listing-only facts it never states."""
    found = []
    highlights = analysis.get("highlights") or []
    for badge, support in _BADGE_SUPPORT.items():
        if badge in highlights and not _has(support, text):
            found.append(f"unsupported:{badge}")
    if "Pets allowed" in highlights and _has(_NO_PETS, text):
        found.append("contradicted:Pets allowed")
    stated_hob = hob(text)
    hob_badges = {"Gas hob": "gas", "Induction hob": "induction"}
    for badge, kind in hob_badges.items():
        if badge in highlights and stated_hob and stated_hob != kind:
            found.append(f"contradicted:{badge}")
    if "Electric hob" in (analysis.get("lowlights") or []) and stated_hob in ("gas", "induction"):
        found.append("contradicted:Electric hob")
    return found


def yes_if(pattern: str) -> Callable[[str], str | None]:
    return lambda text: "yes" if _has(pattern, text) else None


def true_if(pattern: str, exclude: str | None = None) -> Callable[[str], bool | None]:
    def check(text: str) -> bool | None:
        if exclude and _has(exclude, text):
            return None
        return True if _has(pattern, text) else None

    return check


# field path in the final PropertyQualityAnalysis -> extractor
EXTRACTORS: dict[str, Callable[[str], Any]] = {
    "kitchen.hob_type": hob,
    "kitchen.has_dishwasher": yes_if(r"\bdish ?washer\b"),
    "kitchen.has_washing_machine": yes_if(r"\bwashing machine\b|\bwasher[- /]?dryer\b"),
    "listing_extraction.furnished_status": furnished,
    "listing_extraction.epc_rating": epc,
    "listing_extraction.council_tax_band": council_tax,
    "listing_extraction.pets_allowed": pets,
    "light_space.floor_level": floor_level,
    "outdoor_space.has_balcony": true_if(r"\bbalcon(y|ies)\b", exclude=r"juliet|no balcony"),
    "outdoor_space.has_garden": true_if(r"\bprivate (rear |front )?garden\b"),
    "outdoor_space.has_shared_garden": true_if(r"\b(communal|shared) (rear )?gardens?\b"),
    "flooring_noise.has_double_glazing": yes_if(r"\bdouble[- ]glaz"),
}


def expected_facts(merged: MergedProperty) -> dict[str, Any]:
    text = listing_text(merged)
    return {field: value for field, fn in EXTRACTORS.items() if (value := fn(text)) is not None}


def get_path(obj: Any, path: str) -> Any:
    for part in path.split("."):
        if not isinstance(obj, dict):
            return None
        obj = obj.get(part)
    return obj

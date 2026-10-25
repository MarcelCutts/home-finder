"""The brief: what the search asks for, and how one listing measures up against it.

Rent, bedrooms and commute are always must-haves; the fit dimensions named in
``Settings.brief_must_haves`` join them, and the other dimensions are nice-to-haves.
Every item is met, broken or unknown, with the evidence in a few words, and the
first broken or unknown must-have gives the listing page its opening line.

What counts as what, for a fit dimension:

- broken: a lowlight that contradicts the need outright (``_BREAKERS``), or a factor
  the scorer marked missed with the dimension scoring under ``_BROKEN_SCORE``;
- unknown: the photos gave little or no confidence, or no factor was earned, so a
  low score is a gap in the evidence, not a failure;
- met: otherwise, with the first earned factor as the evidence.

A hard limit (rent, bedrooms, commute) breaks on the numbers alone. A dimension
breaks on photo evidence, which the lead presents as a risk to check, not a fact.
"""

from collections.abc import Mapping
from typing import Any, Final, Literal, TypedDict

from home_finder.filters.fit_score import WEIGHTS, FitDimension
from home_finder.models import PropertyLowlight
from home_finder.models.quality import lowlight_display_label

State = Literal["met", "broken", "unknown"]
Kind = Literal["limit", "dimension"]

# What each fit dimension is called when it is a line in the brief. The scorer's
# workspace dimension combines bedrooms, desk room, layout and broadband, so its
# line is about having somewhere to work, not a separate room.
DIMENSION_NEEDS: Final[dict[str, str]] = {
    "workspace": "Somewhere to work",
    "hosting": "Room to host",
    "sound": "Quiet enough for calls",
    "kitchen": "A kitchen that works",
    "vibe": "Light and feel",
    "condition": "Good condition",
}

# A lowlight that contradicts a need outright, whatever the dimension scored.
_BREAKERS: Final[dict[str, frozenset[str]]] = {
    "workspace": frozenset({PropertyLowlight.NO_WORK_SEPARATION}),
    "hosting": frozenset(
        {
            PropertyLowlight.POOR_HOSTING_LAYOUT,
            PropertyLowlight.COMPACT_LIVING,
            PropertyLowlight.SMALL_LIVING,
        }
    ),
    "sound": frozenset({PropertyLowlight.TRAFFIC_NOISE, PropertyLowlight.NEW_BUILD_ACOUSTICS}),
    "kitchen": frozenset({PropertyLowlight.DATED_KITCHEN}),
    "condition": frozenset({PropertyLowlight.NEEDS_UPDATING}),
}

# Below this, a factor the scorer marked missed is taken as the reason.
_BROKEN_SCORE: Final = 40
# At or below this confidence the photos didn't show enough to judge the dimension:
# the scorer gives 0.5 when it has a bedroom count and nothing else.
_THIN_CONFIDENCE: Final = 0.5


class BriefItem(TypedDict):
    label: str
    state: State
    found: str  # the evidence, in a few words
    must: bool
    kind: Kind


class BriefCheck(TypedDict):
    items: list[BriefItem]
    must_total: int
    must_met: int
    must_broken: int
    must_unknown: int


class Lead(TypedDict):
    kind: Literal["broken", "risk", "unknown", "clear"]
    kicker: str
    title: str
    detail: str


def must_have_dimensions(spec: str) -> list[str]:
    """The dimension keys named in the setting, in WEIGHTS order.

    Unknown names are ignored here; ``Settings`` rejects them when the value is loaded.
    """
    wanted = {part.strip().lower() for part in spec.split(",") if part.strip()}
    return [key for key in WEIGHTS if key in wanted]


def _field(analysis: Any, name: str) -> Any:
    if analysis is None:
        return None
    if isinstance(analysis, dict):
        return analysis.get(name)
    return getattr(analysis, name, None)


def _lowlights(analysis: Any) -> list[str]:
    return [str(tag) for tag in (_field(analysis, "lowlights") or [])]


def _item(label: str, state: State, found: str, must: bool, kind: Kind) -> BriefItem:
    return BriefItem(label=label, state=state, found=found, must=must, kind=kind)


def _dimension_item(dim: FitDimension, analysis: Any, must: bool) -> BriefItem:
    label = DIMENSION_NEEDS.get(dim["key"], dim["label"])
    factors = dim["factors"]
    breaker = next(
        (tag for tag in _lowlights(analysis) if tag in _BREAKERS.get(dim["key"], ())), None
    )
    if breaker is not None:
        found = lowlight_display_label(breaker)
        if breaker == PropertyLowlight.TRAFFIC_NOISE:
            # The photos' own evidence beats the generic label.
            indicators = _field(_field(analysis, "flooring_noise"), "noise_indicators") or []
            if indicators:
                found = str(indicators[0]).rstrip(".")
        return _item(label, "broken", found, must, "dimension")
    missed = next((f["label"] for f in factors if f["state"] == "missed"), None)
    if missed is not None and dim["score"] < _BROKEN_SCORE:
        return _item(label, "broken", missed, must, "dimension")
    earned_labels = [f["label"] for f in factors if f["state"] == "earned"]
    # A bedroom count ("2+ bedrooms") is the weakest evidence; prefer anything specific.
    specific = [label for label in earned_labels if not label[:1].isdigit()]
    chosen = specific or earned_labels
    earned: str | None = chosen[0] if chosen else None
    if dim["confidence"] <= _THIN_CONFIDENCE or earned is None:
        unknowns = [f["label"].lower() for f in factors if f["state"] == "unknown"]
        found = "not seen in the photos"
        if unknowns:
            found += ": " + ", ".join(unknowns)
        return _item(label, "unknown", found, must, "dimension")
    return _item(label, "met", earned, must, "dimension")


def check_brief(
    prop: Mapping[str, Any],
    analysis: Any,
    fit_breakdown: list[FitDimension] | None,
    *,
    min_bedrooms: int,
    max_bedrooms: int,
    max_price: int,
    max_commute_minutes: int,
    must_haves: str,
) -> BriefCheck:
    """Measure one listing against the brief. Hard limits first, then the dimensions."""
    items: list[BriefItem] = []

    bedrooms = prop.get("bedrooms")
    if min_bedrooms == max_bedrooms:
        bed_label = f"{max_bedrooms} bedroom{'s' if max_bedrooms != 1 else ''}"
    elif min_bedrooms == 0:
        bed_label = f"Up to {max_bedrooms} bedrooms"
    else:
        bed_label = f"{min_bedrooms} to {max_bedrooms} bedrooms"
    if bedrooms is None:
        items.append(_item(bed_label, "unknown", "not in the listing", True, "limit"))
    elif min_bedrooms <= bedrooms <= max_bedrooms:
        items.append(_item(bed_label, "met", str(bedrooms), True, "limit"))
    else:
        items.append(_item(bed_label, "broken", f"{bedrooms} bedrooms", True, "limit"))

    price = prop.get("price_pcm")
    rent_label = f"Up to £{max_price:,} pcm"
    if not price:
        items.append(_item(rent_label, "unknown", "not in the listing", True, "limit"))
    elif price <= max_price:
        found = f"£{price:,}, your limit" if price == max_price else f"£{price:,}"
        items.append(_item(rent_label, "met", found, True, "limit"))
    else:
        found = f"£{price:,} pcm, £{price - max_price:,} over your limit"
        items.append(_item(rent_label, "broken", found, True, "limit"))

    minutes = prop.get("commute_minutes")
    commute_label = f"Within {max_commute_minutes} min"
    if minutes is None:
        reason = "no postcode in the listing" if not prop.get("postcode") else "not worked out yet"
        items.append(_item(commute_label, "unknown", reason, True, "limit"))
    elif minutes <= max_commute_minutes:
        # Commute times are route estimates; the facts strip says so beside the number.
        items.append(_item(commute_label, "met", f"{minutes} min, estimated", True, "limit"))
    else:
        found = f"{minutes} min estimated, {minutes - max_commute_minutes} over"
        items.append(_item(commute_label, "broken", found, True, "limit"))

    must_keys = set(must_have_dimensions(must_haves))
    dimensions = {dim["key"]: dim for dim in fit_breakdown or []}
    for key in WEIGHTS:
        must = key in must_keys
        dim = dimensions.get(key)
        if dim is None:
            if must:
                items.append(
                    _item(DIMENSION_NEEDS[key], "unknown", "not assessed yet", True, "dimension")
                )
            continue
        items.append(_dimension_item(dim, analysis, must))

    # Must-haves first, keeping each group's order.
    items.sort(key=lambda item: not item["must"])
    musts = [item for item in items if item["must"]]
    return BriefCheck(
        items=items,
        must_total=len(musts),
        must_met=sum(item["state"] == "met" for item in musts),
        must_broken=sum(item["state"] == "broken" for item in musts),
        must_unknown=sum(item["state"] == "unknown" for item in musts),
    )


def _sentence(text: str) -> str:
    text = text.strip()
    if not text:
        return text
    text = text[0].upper() + text[1:]
    return text if text.endswith((".", "!", "?")) else text + "."


def _join(labels: list[str]) -> str:
    if len(labels) <= 1:
        return "".join(labels)
    return ", ".join(labels[:-1]) + " and " + labels[-1]


def brief_lead(check: BriefCheck, analysis: Any) -> Lead:
    """The one line a listing opens with.

    In order: a hard limit it breaks; a dimension the photos put at risk; a must-have
    that can't be checked; or all clear. The detail always comes from the counts, so
    it never claims more than the check found.
    """
    musts = [item for item in check["items"] if item["must"]]
    nices = [item for item in check["items"] if not item["must"]]

    limit = next((i for i in musts if i["state"] == "broken" and i["kind"] == "limit"), None)
    if limit is not None:
        return Lead(
            kind="broken",
            kicker="Breaks a must-have",
            title=_sentence(limit["found"]),
            detail=f"Your must-have: {limit['label'].lower()}.",
        )

    risk = next((i for i in musts if i["state"] == "broken"), None)
    if risk is not None:
        return Lead(
            kind="risk",
            kicker="Puts a must-have at risk",
            title=_sentence(risk["found"]),
            detail=(
                f"Your must-have: {risk['label'].lower()}. "
                "Photos can't settle this; check it at a viewing."
            ),
        )

    unknowns = [i for i in musts if i["state"] == "unknown"]
    if unknowns:
        first = unknowns[0]
        others = [i["label"].lower() for i in unknowns[1:]]
        met = check["must_met"]
        total = check["must_total"]
        if others:
            detail = f"{met} of {total} must-haves met; {_join(others)} also still unknown."
        else:
            detail = f"The other {met} must-haves are met."
        return Lead(
            kind="unknown",
            kicker="Can't check a must-have",
            title=f"Can't check '{first['label'].lower()}': {first['found']}.",
            detail=detail,
        )

    highlights = [str(tag) for tag in (_field(analysis, "highlights") or [])]
    parts = [f"Best thing: {highlights[0].lower()}." if highlights else ""]
    nice_broken = sum(i["state"] == "broken" for i in nices)
    nice_unknown = sum(i["state"] == "unknown" for i in nices)
    if nice_broken or nice_unknown:
        bits = []
        if nice_broken:
            bits.append(f"{nice_broken} not met")
        if nice_unknown:
            bits.append(f"{nice_unknown} unknown")
        parts.append(f"Nice-to-haves: {', '.join(bits)}.")
    return Lead(
        kind="clear",
        kicker=f"All {check['must_total']} must-haves met",
        title="Nothing breaks your brief.",
        detail=" ".join(p for p in parts if p),
    )

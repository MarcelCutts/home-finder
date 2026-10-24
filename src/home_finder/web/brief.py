"""The brief: what the search asks for, and how one listing measures up against it.

Rent, bedrooms and commute are always must-haves; the fit dimensions named in
``Settings.brief_must_haves`` join them, and the other dimensions are nice-to-haves.
Every item is met, broken or unknown, with the evidence in a few words, and the
first broken or unknown must-have gives the listing page its opening line.
"""

from collections.abc import Mapping
from typing import Any, Final, Literal, TypedDict

from home_finder.filters.fit_score import WEIGHTS, FitDimension
from home_finder.models import PropertyLowlight
from home_finder.models.quality import lowlight_display_label

State = Literal["met", "broken", "unknown"]

# What each fit dimension is called when it is a line in the brief.
DIMENSION_NEEDS: Final[dict[str, str]] = {
    "workspace": "A room to work in",
    "hosting": "Room to host",
    "sound": "Quiet enough for calls",
    "kitchen": "A kitchen that works",
    "vibe": "Light and feel",
    "condition": "Good condition",
}

# A lowlight that breaks a dimension outright, whatever its score says.
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

_BROKEN_SCORE: Final = 40


class BriefItem(TypedDict):
    label: str
    state: State
    found: str
    must: bool


class BriefCheck(TypedDict):
    items: list[BriefItem]
    must_total: int
    must_met: int
    must_broken: int
    must_unknown: int


class Lead(TypedDict):
    kind: Literal["broken", "unknown", "clear"]
    kicker: str
    title: str
    detail: str


def must_have_dimensions(spec: str) -> list[str]:
    """The dimension keys named in the setting, in WEIGHTS order, ignoring unknown names."""
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


def _dimension_item(dim: FitDimension, analysis: Any, must: bool) -> BriefItem:
    label = DIMENSION_NEEDS.get(dim["key"], dim["label"])
    factors = dim["factors"]
    lowlights = _lowlights(analysis)
    breaker = next((tag for tag in lowlights if tag in _BREAKERS.get(dim["key"], ())), None)
    if breaker is not None:
        found = lowlight_display_label(breaker)
        if breaker == PropertyLowlight.TRAFFIC_NOISE:
            # The photos' own evidence beats the generic label.
            indicators = _field(_field(analysis, "flooring_noise"), "noise_indicators") or []
            if indicators:
                found = str(indicators[0]).rstrip(".")
        return BriefItem(label=label, state="broken", found=found, must=must)
    if dim["confidence"] <= 0 or (factors and all(f["state"] == "unknown" for f in factors)):
        return BriefItem(label=label, state="unknown", found="not seen in the photos", must=must)
    if dim["score"] < _BROKEN_SCORE:
        missed = next((f["label"] for f in factors if f["state"] == "missed"), None)
        found = missed if missed else f"scored {dim['score']} of 100"
        return BriefItem(label=label, state="broken", found=found, must=must)
    earned = next((f["label"] for f in factors if f["state"] == "earned"), None)
    found = earned if earned else f"scored {dim['score']} of 100"
    return BriefItem(label=label, state="met", found=found, must=must)


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
        items.append(
            BriefItem(label=bed_label, state="unknown", found="not in the listing", must=True)
        )
    elif min_bedrooms <= bedrooms <= max_bedrooms:
        items.append(BriefItem(label=bed_label, state="met", found=str(bedrooms), must=True))
    else:
        items.append(
            BriefItem(label=bed_label, state="broken", found=f"{bedrooms} bedrooms", must=True)
        )

    price = prop.get("price_pcm")
    rent_label = f"Up to £{max_price:,} pcm"
    if not price:
        items.append(
            BriefItem(label=rent_label, state="unknown", found="not in the listing", must=True)
        )
    elif price <= max_price:
        found = f"£{price:,}, your limit" if price == max_price else f"£{price:,}"
        items.append(BriefItem(label=rent_label, state="met", found=found, must=True))
    else:
        found = f"£{price:,} pcm, £{price - max_price:,} over your limit"
        items.append(BriefItem(label=rent_label, state="broken", found=found, must=True))

    minutes = prop.get("commute_minutes")
    commute_label = f"Within {max_commute_minutes} min"
    if minutes is None:
        reason = "no postcode in the listing" if not prop.get("postcode") else "not worked out yet"
        items.append(BriefItem(label=commute_label, state="unknown", found=reason, must=True))
    elif minutes <= max_commute_minutes:
        items.append(BriefItem(label=commute_label, state="met", found=f"{minutes} min", must=True))
    else:
        found = f"{minutes} min, {minutes - max_commute_minutes} over"
        items.append(BriefItem(label=commute_label, state="broken", found=found, must=True))

    must_keys = set(must_have_dimensions(must_haves))
    dimensions = {dim["key"]: dim for dim in fit_breakdown or []}
    for key in WEIGHTS:
        must = key in must_keys
        dim = dimensions.get(key)
        if dim is None:
            if must:
                items.append(
                    BriefItem(
                        label=DIMENSION_NEEDS[key],
                        state="unknown",
                        found="not assessed yet",
                        must=True,
                    )
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


def brief_lead(check: BriefCheck, analysis: Any) -> Lead:
    """The one line a listing opens with: the first broken must-have, else the first gap."""
    musts = [item for item in check["items"] if item["must"]]
    broken = next((item for item in musts if item["state"] == "broken"), None)
    if broken is not None:
        return Lead(
            kind="broken",
            kicker="Breaks a must-have",
            title=_sentence(broken["found"]),
            detail=f"Your must-have: {broken['label'].lower()}.",
        )
    unknown = next((item for item in musts if item["state"] == "unknown"), None)
    if unknown is not None:
        return Lead(
            kind="unknown",
            kicker="Can't check a must-have",
            title=f"Can't check '{unknown['label'].lower()}': {unknown['found']}.",
            detail="Everything else in the brief is met.",
        )
    highlights = [str(tag) for tag in (_field(analysis, "highlights") or [])]
    best = f" Best thing: {highlights[0].lower()}." if highlights else ""
    return Lead(
        kind="clear",
        kicker=f"All {check['must_total']} must-haves met",
        title="Nothing breaks your brief.",
        detail=best.strip(),
    )

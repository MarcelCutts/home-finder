"""The brief: what the search asks for, and how one listing measures up against it.

Rent, bedrooms and commute are always must-haves; the fit dimensions named in
``Settings.brief_must_haves`` join them, and the other dimensions are nice-to-haves.
Every item makes one of three claims, with the evidence in a few words:

- supported: evidence directly for the need;
- conflict: evidence against it;
- needs checking: no direct evidence, or only evidence that can't settle it.

A hard limit (rent, bedrooms, commute) is judged on the listing's numbers. A fit
dimension is judged only on the observations that bear directly on its need, set
out need by need below. The rest of the analysis still moves the fit score, but
makes no claim here: a large flat whose living room was never measured needs
checking for room to host, however high its hosting score. When direct evidence
points both ways the conflict wins, because it is the one to check at a viewing.

The first must-have in conflict, or failing that the first that needs checking,
gives the listing page its opening line. A dimension's conflict rests on the analysis of
photos, floorplan and text, so the lead presents it as a risk to check, not a fact.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Final, Literal, TypedDict

from home_finder.filters.fit_score import WEIGHTS, FitDimension, normalized_fit_inputs
from home_finder.models import PropertyLowlight
from home_finder.models.quality import lowlight_display_label

Claim = Literal["supported", "conflict", "needs_checking"]
# The templates still read each claim under its earlier name.
State = Literal["met", "broken", "unknown"]
Kind = Literal["limit", "dimension"]

_TEMPLATE_STATE: Final[dict[Claim, State]] = {
    "supported": "met",
    "conflict": "broken",
    "needs_checking": "unknown",
}

# What each fit dimension is called when it is a line in the brief. The workspace
# need is somewhere to work; a separate office is the best evidence for it, not
# the only one.
DIMENSION_NEEDS: Final[dict[str, str]] = {
    "workspace": "Somewhere to work",
    "hosting": "Room to host",
    "sound": "Quiet enough for calls",
    "kitchen": "A kitchen that works",
    "vibe": "Light and feel",
    "condition": "Good condition",
}


class BriefItem(TypedDict):
    label: str
    claim: Claim
    found: str  # the evidence, in a few words
    must: bool
    kind: Kind
    state: State  # the claim under the name the templates read


class BriefCheck(TypedDict):
    items: list[BriefItem]
    must_total: int
    must_supported: int
    must_conflict: int
    must_needs_checking: int
    # The same three counts under the names the templates read.
    must_met: int
    must_broken: int
    must_unknown: int


class Lead(TypedDict):
    # The templates style each kind by name; "unknown" is the needs-checking lead.
    kind: Literal["broken", "risk", "unknown", "clear"]
    kicker: str
    title: str
    detail: str


@dataclass
class _Evidence:
    """The direct observations for one need, each list most telling first."""

    support: list[str] = field(default_factory=list)  # for the need
    against: list[str] = field(default_factory=list)  # against it
    unsure: list[str] = field(default_factory=list)  # read, but can't settle it
    unseen: list[str] = field(default_factory=list)  # what the analysis has no reading of


def must_have_dimensions(spec: str) -> list[str]:
    """The dimension keys named in the setting, in WEIGHTS order.

    Unknown names are ignored here; ``Settings`` rejects them when the value is loaded.
    """
    wanted = {part.strip().lower() for part in spec.split(",") if part.strip()}
    return [key for key in WEIGHTS if key in wanted]


def _section(values: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    section = values.get(name)
    return section if isinstance(section, Mapping) else {}


def _tags(analysis: Any, name: str) -> list[str]:
    """A list of tags from a decoded assessment, a saved one or the model itself."""
    is_mapping = isinstance(analysis, Mapping)
    tags = analysis.get(name) if is_mapping else getattr(analysis, name, None)
    return [str(tag) for tag in tags or []]


# ── Direct evidence, need by need ────────────────────────────────────────────
# Each reads the assessment as the fit scorer decodes it. A need's tagged lowlights
# (``_BREAKERS``) count against it ahead of its own readings. Anything not named
# here is context for the score, not evidence for the claim.

# A lowlight that contradicts a need outright, whatever else the analysis found.
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
    "kitchen": frozenset({PropertyLowlight.DATED_KITCHEN, PropertyLowlight.ELECTRIC_HOB}),
    "condition": frozenset({PropertyLowlight.NEEDS_UPDATING}),
}

# The analysis prompt's rough guide to a living room that holds an office and
# 8+ guests starts at 20 m². At or below 10 m² the hosting score gives it nothing.
_ROOMY_LIVING_SQM: Final = 20
_SMALL_LIVING_SQM: Final = 10


def _workspace_evidence(values: Mapping[str, Any]) -> _Evidence:
    """Somewhere to work: the analysis's reading of work separation and desk room.

    A bedroom count isn't enough: the analysis is told a second bedroom alone
    doesn't make an office. A desk in shared living space answers neither way.
    """
    evidence = _Evidence()
    bedroom = _section(values, "bedroom")
    separation = bedroom.get("office_separation")
    if separation == "dedicated_room":
        evidence.support.append("Dedicated office room")
    elif separation == "separate_area":
        evidence.support.append("Separate work area")
    elif separation == "none":
        evidence.against.append("Nowhere viable for a desk")
    elif separation == "shared_space":
        evidence.unsure.append("work spot in shared living space")
    else:
        evidence.unseen.append("work separation")
    desk = bedroom.get("can_fit_desk")
    if desk == "yes":
        evidence.support.append("Room for a desk")
    elif desk == "no":
        evidence.against.append("No room for a desk")
    else:
        evidence.unseen.append("desk space")
    return evidence


def _hosting_evidence(values: Mapping[str, Any]) -> _Evidence:
    """Room to host: the living room's measured size, and a layout unfit for guests.

    Total floor area, a general sense of space, outdoor space and a good flow all
    help the score, but none says the living room holds a crowd. Nor does a
    "Spacious living room" highlight: without a measurement it can repeat the
    advert's own claim.
    """
    evidence = _Evidence()
    space = _section(values, "space")
    living = space.get("living_room_sqm")
    if isinstance(living, int | float) and living > 0:
        if living >= _ROOMY_LIVING_SQM:
            evidence.support.append(f"Living room ~{int(living)} m²")
        elif living <= _SMALL_LIVING_SQM:
            evidence.against.append(f"Living room only ~{int(living)} m²")
        else:
            evidence.unsure.append(f"living room ~{int(living)} m², not clearly big enough")
    else:
        evidence.unseen.append("living-room size")
    if space.get("hosting_layout") == "poor":
        evidence.against.append("Poor hosting layout")
    return evidence


def _sound_evidence(values: Mapping[str, Any]) -> _Evidence:
    """Quiet enough for calls: the walls and windows, and noise the analysis tagged.

    Floor level and the risk of disturbing neighbours move the score only: the
    first is a weak proxy, and the second is about the noise you make.
    """
    evidence = _Evidence()
    flooring = _section(values, "flooring_noise")
    construction = flooring.get("building_construction")
    if construction == "solid_brick":
        evidence.support.append("Solid brick walls")
    elif construction == "concrete":
        evidence.support.append("Concrete construction")
    elif construction == "timber_frame":
        evidence.against.append("Timber-frame construction")
    elif construction == "mixed":
        evidence.unsure.append("mixed construction")
    else:
        evidence.unseen.append("construction")
    glazing = flooring.get("has_double_glazing")
    if glazing == "yes":
        evidence.support.append("Double glazed")
    elif glazing == "no":
        evidence.against.append("No double glazing")
    else:
        evidence.unseen.append("glazing")
    return evidence


def _kitchen_evidence(values: Mapping[str, Any]) -> _Evidence:
    """A kitchen that works: a gas or induction hob to cook on, and a kitchen not dated.

    A modern finish, a dishwasher and a washing machine help the score, but none
    says what the hob is.
    """
    evidence = _Evidence()
    kitchen = _section(values, "kitchen")
    hob = kitchen.get("hob_type")
    if hob == "gas":
        evidence.support.append("Gas hob")
    elif hob == "induction":
        evidence.support.append("Induction hob")
    elif hob == "electric":
        evidence.against.append("Electric hob")
    else:
        evidence.unseen.append("hob type")
    if kitchen.get("overall_quality") == "dated":
        evidence.against.append("Dated kitchen")
    return evidence


def _vibe_evidence(values: Mapping[str, Any]) -> _Evidence:
    """Light and feel: the natural light.

    Feel has no direct observation of its own; character, ceilings and outlook
    move the score only.
    """
    evidence = _Evidence()
    light = _section(values, "light_space").get("natural_light")
    if light == "excellent":
        evidence.support.append("Excellent natural light")
    elif light == "good":
        evidence.support.append("Good natural light")
    elif light == "poor":
        evidence.against.append("Poor natural light")
    elif light == "fair":
        evidence.unsure.append("natural light only fair")
    else:
        evidence.unseen.append("natural light")
    return evidence


def _condition_evidence(values: Mapping[str, Any]) -> _Evidence:
    """Good condition: the analysis's reading of condition, and concerns it rated.

    The overall rating is about desirability, not condition, so it doesn't count;
    nor do "Excellent condition" or "Recently refurbished" highlights, which can
    repeat the advert. Concerns count against only once the condition was seen:
    without that reading, they are usually about what the photos leave out.
    """
    evidence = _Evidence()
    condition = _section(values, "condition").get("overall_condition")
    if condition in ("excellent", "good"):
        evidence.support.append(f"Looks {condition}")
    elif condition == "poor":
        evidence.against.append("Looks poor")
    elif condition == "fair":
        evidence.unsure.append("condition only fair")
    else:
        evidence.unseen.append("condition")
    severity = values.get("concern_severity")
    seen = condition in ("excellent", "good", "fair", "poor")
    if seen and values.get("condition_concerns") and severity in ("moderate", "serious"):
        evidence.against.append(f"{severity.title()} maintenance concerns")
    return evidence


_EVIDENCE: Final[dict[str, Callable[[Mapping[str, Any]], _Evidence]]] = {
    "workspace": _workspace_evidence,
    "hosting": _hosting_evidence,
    "sound": _sound_evidence,
    "kitchen": _kitchen_evidence,
    "vibe": _vibe_evidence,
    "condition": _condition_evidence,
}


# ── The check ────────────────────────────────────────────────────────────────


def _join(labels: list[str]) -> str:
    if len(labels) <= 1:
        return "".join(labels)
    return ", ".join(labels[:-1]) + " and " + labels[-1]


def _item(label: str, claim: Claim, found: str, must: bool, kind: Kind) -> BriefItem:
    return BriefItem(
        label=label, claim=claim, found=found, must=must, kind=kind, state=_TEMPLATE_STATE[claim]
    )


def _dimension_item(key: str, values: Mapping[str, Any], must: bool) -> BriefItem:
    label = DIMENSION_NEEDS[key]
    evidence = _EVIDENCE[key](values)
    breakers = [tag for tag in _tags(values, "lowlights") if tag in _BREAKERS.get(key, ())]
    # A tagged lowlight is the analysis's own verdict on the need, so it leads.
    against = [*(lowlight_display_label(tag) for tag in breakers), *evidence.against]
    if against:
        return _item(label, "conflict", against[0], must, "dimension")
    if evidence.support:
        return _item(label, "supported", evidence.support[0], must, "dimension")
    gaps = list(evidence.unsure)
    if evidence.unseen:
        gaps.append(f"{_join(evidence.unseen)} not shown")
    return _item(label, "needs_checking", "; ".join(gaps), must, "dimension")


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
    """Measure one listing against the brief. Hard limits first, then the dimensions.

    The dimensions are read from ``analysis`` only where the fit scorer accepted it:
    without a ``fit_breakdown`` each must-have dimension needs checking, and the
    nice-to-haves are left out.
    """
    items: list[BriefItem] = []

    bedrooms = prop.get("bedrooms")
    if min_bedrooms == max_bedrooms:
        bed_label = f"{max_bedrooms} bedroom{'s' if max_bedrooms != 1 else ''}"
    elif min_bedrooms == 0:
        bed_label = f"Up to {max_bedrooms} bedrooms"
    else:
        bed_label = f"{min_bedrooms} to {max_bedrooms} bedrooms"
    if bedrooms is None:
        items.append(_item(bed_label, "needs_checking", "not in the listing", True, "limit"))
    elif min_bedrooms <= bedrooms <= max_bedrooms:
        items.append(_item(bed_label, "supported", str(bedrooms), True, "limit"))
    else:
        items.append(_item(bed_label, "conflict", f"{bedrooms} bedrooms", True, "limit"))

    price = prop.get("price_pcm")
    rent_label = f"Up to £{max_price:,} pcm"
    if not price:
        items.append(_item(rent_label, "needs_checking", "not in the listing", True, "limit"))
    elif price <= max_price:
        found = f"£{price:,}, your limit" if price == max_price else f"£{price:,}"
        items.append(_item(rent_label, "supported", found, True, "limit"))
    else:
        found = f"£{price:,} pcm, £{price - max_price:,} over your limit"
        items.append(_item(rent_label, "conflict", found, True, "limit"))

    minutes = prop.get("commute_minutes")
    commute_label = f"Within {max_commute_minutes} min"
    if minutes is None:
        reason = "no postcode in the listing" if not prop.get("postcode") else "not worked out yet"
        items.append(_item(commute_label, "needs_checking", reason, True, "limit"))
    elif minutes <= max_commute_minutes:
        # Commute times are route estimates; the facts strip says so beside the number.
        found = f"{minutes} min, estimated"
        items.append(_item(commute_label, "supported", found, True, "limit"))
    else:
        found = f"{minutes} min estimated, {minutes - max_commute_minutes} over"
        items.append(_item(commute_label, "conflict", found, True, "limit"))

    must_keys = set(must_have_dimensions(must_haves))
    assessed = {dim["key"] for dim in fit_breakdown or []}
    values = normalized_fit_inputs(analysis) if assessed else None
    for key in WEIGHTS:
        must = key in must_keys
        if values is None or key not in assessed:
            if must:
                label = DIMENSION_NEEDS[key]
                items.append(_item(label, "needs_checking", "not assessed yet", True, "dimension"))
            continue
        items.append(_dimension_item(key, values, must))

    # Must-haves first, keeping each group's order.
    items.sort(key=lambda item: not item["must"])
    musts = [item for item in items if item["must"]]
    supported = sum(item["claim"] == "supported" for item in musts)
    conflict = sum(item["claim"] == "conflict" for item in musts)
    needs_checking = sum(item["claim"] == "needs_checking" for item in musts)
    return BriefCheck(
        items=items,
        must_total=len(musts),
        must_supported=supported,
        must_conflict=conflict,
        must_needs_checking=needs_checking,
        must_met=supported,
        must_broken=conflict,
        must_unknown=needs_checking,
    )


# ── The lead ─────────────────────────────────────────────────────────────────


def _sentence(text: str) -> str:
    text = text.strip()
    if not text:
        return text
    text = text[0].upper() + text[1:]
    return text if text.endswith((".", "!", "?")) else text + "."


def brief_lead(check: BriefCheck, analysis: Any) -> Lead:
    """The one line a listing opens with.

    In order: a hard limit in conflict; a dimension in conflict, as a risk to check;
    a must-have that needs checking; or, only when every must-have is supported, all
    clear. The detail always comes from the counts, so it never claims more than the
    check found.
    """
    musts = [item for item in check["items"] if item["must"]]
    nices = [item for item in check["items"] if not item["must"]]

    limit = next((i for i in musts if i["claim"] == "conflict" and i["kind"] == "limit"), None)
    if limit is not None:
        return Lead(
            kind="broken",
            kicker="Breaks a must-have",
            title=_sentence(limit["found"]),
            detail=f"Your must-have: {limit['label'].lower()}.",
        )

    risk = next((i for i in musts if i["claim"] == "conflict"), None)
    if risk is not None:
        return Lead(
            kind="risk",
            kicker="Puts a must-have at risk",
            title=_sentence(risk["found"]),
            detail=(
                f"Your must-have: {risk['label'].lower()}. "
                "The listing can't settle this; check it at a viewing."
            ),
        )

    to_check = [i for i in musts if i["claim"] == "needs_checking"]
    if to_check:
        first = to_check[0]
        others = [i["label"].lower() for i in to_check[1:]]
        supported = check["must_supported"]
        if others:
            verb = "needs" if len(others) == 1 else "need"
            detail = (
                f"{supported} of {check['must_total']} must-haves supported; "
                f"{_join(others)} also {verb} checking."
            )
        else:
            detail = f"The other {supported} must-haves are supported."
        kicker = (
            "A must-have needs checking"
            if len(to_check) == 1
            else f"{len(to_check)} must-haves need checking"
        )
        return Lead(
            kind="unknown",
            kicker=kicker,
            title=_sentence(f"{first['label']}: {first['found']}"),
            detail=detail,
        )

    highlights = _tags(analysis, "highlights")
    parts = [f"Best thing: {highlights[0].lower()}." if highlights else ""]
    nice_conflict = sum(i["claim"] == "conflict" for i in nices)
    nice_to_check = sum(i["claim"] == "needs_checking" for i in nices)
    if nice_conflict or nice_to_check:
        bits = []
        if nice_conflict:
            bits.append(f"{nice_conflict} in conflict")
        if nice_to_check:
            bits.append(f"{nice_to_check} to check")
        parts.append(f"Nice-to-haves: {', '.join(bits)}.")
    return Lead(
        kind="clear",
        kicker=f"All {check['must_total']} must-haves supported",
        title="Nothing breaks your brief.",
        detail=" ".join(p for p in parts if p),
    )

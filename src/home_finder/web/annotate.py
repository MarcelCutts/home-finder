"""Red-pen notes on the agent's own words.

The listing text is shown as written, and the analysis is turned into numbered
notes attached to the phrases they answer: marketing adjectives are struck
through, and a claim gets a wavy underline when the photos or the area data say
otherwise. Only the first match of each rule is marked, so the text stays readable.
"""

import re
from typing import Any, Final, Literal, TypedDict

from home_finder.models import PropertyLowlight
from home_finder.models.quality import lowlight_display_label

Mark = Literal["strike", "wavy"]

_ADJECTIVES: Final = (
    "stunning|beautiful|immaculate|fantastic|amazing|superb|wonderful|gorgeous|luxurious|"
    "charming|delightful|exceptional|stylish|impressive|spectacular|incredible|lovely|"
    "fabulous|magnificent|breathtaking|outstanding|exquisite"
)
_ADJECTIVE_RE: Final = re.compile(rf"\b(?:{_ADJECTIVES})\b", re.IGNORECASE)
_OUTDOOR_RE: Final = re.compile(
    r"\b(?:private )?(?:balcon(?:y|ies)|terrace|garden|roof terrace)\b", re.I
)
_KITCHEN_RE: Final = re.compile(r"\b(?:[\w-]+ )?(?:fitted )?kitchen\b", re.I)
_PRESTIGE_RE: Final = re.compile(
    r"\b(?:sought.after|highly desirable|desirable|prime|prestigious|exclusive|popular)"
    r"(?: \w+){0,2}\b",
    re.I,
)
_ROOM_RES: Final[dict[str, re.Pattern[str]]] = {
    "bedroom": re.compile(
        r"\b(?:second|2nd|third|3rd|double|single) bedroom\b|\bbedrooms?\b", re.I
    ),
    "bathroom": re.compile(r"\b(?:family |en.?suite )?bathrooms?\b|\bshower room\b", re.I),
    "kitchen": re.compile(r"\bkitchen\b", re.I),
    "living": re.compile(r"\b(?:reception(?: room)?|living room|lounge)\b", re.I),
}


class Segment(TypedDict):
    text: str
    mark: Mark | None
    note: int | None


class Annotated(TypedDict):
    segments: list[Segment]
    notes: list[str]


def _field(analysis: Any, name: str) -> Any:
    if analysis is None:
        return None
    if isinstance(analysis, dict):
        return analysis.get(name)
    return getattr(analysis, name, None)


def _lowlights(analysis: Any) -> set[str]:
    return {str(tag) for tag in (_field(analysis, "lowlights") or [])}


def _noise_note(analysis: Any, lowlights: set[str]) -> str | None:
    noise = _field(analysis, "flooring_noise")
    indicators = list(_field(noise, "noise_indicators") or [])
    if indicators:
        return f"{indicators[0].rstrip('.')}. Not mentioned in the listing."
    if PropertyLowlight.TRAFFIC_NOISE in lowlights:
        return f"{lowlight_display_label(PropertyLowlight.TRAFFIC_NOISE)} in the photos."
    return None


def _kitchen_note(analysis: Any) -> str | None:
    kitchen = _field(analysis, "kitchen")
    missing = [
        name
        for name, field in (
            ("dishwasher", "has_dishwasher"),
            ("washing machine", "has_washing_machine"),
        )
        if _field(kitchen, field) == "no"
    ]
    if not missing:
        return None
    return f"No {' or '.join(missing)} in the photos."


def _value_note(analysis: Any, outcode: str | None) -> str | None:
    value = _field(analysis, "value")
    difference = _field(value, "difference")
    if not difference or difference <= 0:
        return None
    where = f"the {outcode} average" if outcode else "the area average"
    return f"£{int(difference):,} pcm above {where}."


def _missing_rooms(analysis: Any) -> list[str]:
    flags = _field(analysis, "listing_red_flags")
    return [str(room) for room in (_field(flags, "missing_room_photos") or [])]


def annotate_listing(text: str, analysis: Any, *, outcode: str | None = None) -> Annotated:
    """Split the listing text into segments, some marked and numbered, plus the notes."""
    notes: list[str] = []
    spans: list[tuple[int, int, Mark, int]] = []

    def note(message: str) -> int:
        notes.append(message)
        return len(notes)

    def claim(start: int, end: int, mark: Mark, number: int) -> None:
        if all(end <= s or start >= e for s, e, _, _ in spans):
            spans.append((start, end, mark, number))

    adjectives = list(_ADJECTIVE_RE.finditer(text))
    if adjectives:
        number = note(
            "Adjectives, not facts." if len(adjectives) > 1 else "An adjective, not a fact."
        )
        for match in adjectives:
            claim(match.start(), match.end(), "strike", number)

    lowlights = _lowlights(analysis)
    outdoor = _OUTDOOR_RE.search(text)
    noise = _noise_note(analysis, lowlights)
    if outdoor and noise:
        claim(outdoor.start(), outdoor.end(), "wavy", note(noise))

    prestige = _PRESTIGE_RE.search(text)
    value = _value_note(analysis, outcode)
    if prestige and value:
        claim(prestige.start(), prestige.end(), "wavy", note(value))

    kitchen = _KITCHEN_RE.search(text)
    kitchen_note = _kitchen_note(analysis)
    if kitchen and kitchen_note:
        claim(kitchen.start(), kitchen.end(), "wavy", note(kitchen_note))

    for room in _missing_rooms(analysis):
        key = next((k for k in _ROOM_RES if k in room.lower()), None)
        if key is None:
            continue
        # The room as the analysis names it ("second bedroom") beats the generic word.
        exact = re.compile(r"\b" + r"\s+".join(map(re.escape, room.split())) + r"\b", re.I)
        found = exact.search(text) or _ROOM_RES[key].search(text)
        if found:
            claim(
                found.start(), found.end(), "wavy", note(f"{room.capitalize()} not photographed.")
            )

    spans.sort()
    segments: list[Segment] = []
    cursor = 0
    for start, end, mark, number in spans:
        if start > cursor:
            segments.append(Segment(text=text[cursor:start], mark=None, note=None))
        segments.append(Segment(text=text[start:end], mark=mark, note=number))
        cursor = end
    if cursor < len(text):
        segments.append(Segment(text=text[cursor:], mark=None, note=None))
    return Annotated(segments=segments, notes=notes)

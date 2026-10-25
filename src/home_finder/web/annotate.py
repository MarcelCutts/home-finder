"""Notes on the listing's own words.

The listing text is shown as written, and the analysis is attached as numbered
notes to the phrases they concern. Every note names its source (the photo
assessment or the stored area reference) and states what was seen, never that the
listing omitted or contradicted something: this code never compares the text with
the finding. A note attaches to a phrase only when a typed finding exists; the
free-form noise prose on its own is not enough, because it mixes positive and
negative observations. Marketing adjectives are struck through, every one of them,
with one shared note.

Candidates are collected first, overlaps are settled with the specific findings
winning over adjectives, and only then are the notes numbered, in text order, so
every note has an anchor and every anchor a note.
"""

import re
from typing import Any, Final, Literal, TypedDict

from home_finder.models import PropertyLowlight

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
_KITCHEN_RE: Final = re.compile(
    r"\b(?:(?:modern|new|integrated|open.plan|fully|separate) )?(?:fitted )?kitchen\b", re.I
)
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

ADJECTIVE_NOTE: Final = "Marketing wording."

# A specific finding outranks an adjective when the two overlap.
_FINDING: Final = 0
_ADJECTIVE: Final = 1


class Segment(TypedDict):
    text: str
    mark: Mark | None
    note: int | None


class Annotated(TypedDict):
    segments: list[Segment]
    notes: list[str]


class _Candidate(TypedDict):
    priority: int
    start: int
    end: int
    mark: Mark
    message: str


def _field(analysis: Any, name: str) -> Any:
    if analysis is None:
        return None
    if isinstance(analysis, dict):
        return analysis.get(name)
    return getattr(analysis, name, None)


def _lowlights(analysis: Any) -> set[str]:
    return {str(tag) for tag in (_field(analysis, "lowlights") or [])}


def _noise_note(analysis: Any) -> str | None:
    """Only the typed traffic-noise lowlight counts; the prose may be a reassurance."""
    if PropertyLowlight.TRAFFIC_NOISE not in _lowlights(analysis):
        return None
    indicators = list(_field(_field(analysis, "flooring_noise"), "noise_indicators") or [])
    seen = str(indicators[0]).rstrip(".") if indicators else "possible road or rail noise"
    return f"Photo assessment: {seen}. Check noise at a viewing."


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
    return f"Photo assessment: no {' or '.join(missing)} seen."


def _value_note(analysis: Any, outcode: str | None, price_pcm: int | None) -> str | None:
    """The stored reference is unverified and historical, so the note says so.

    The difference is recomputed from the current rent when it is known, because
    the saved one was made at the rent of the time.
    """
    value = _field(analysis, "value")
    average = _field(value, "area_average")
    if not average:
        return None
    difference = (price_pcm - average) if price_pcm else _field(value, "difference")
    if not difference or difference <= 0:
        return None
    where = f"{outcode} " if outcode else ""
    return (
        f"£{int(difference):,} pcm above the stored {where}reference of £{int(average):,} "
        "(unverified, historical)."
    )


def _missing_rooms(analysis: Any) -> list[str]:
    flags = _field(analysis, "listing_red_flags")
    return [str(room) for room in (_field(flags, "missing_room_photos") or [])]


def annotate_listing(
    text: str, analysis: Any, *, outcode: str | None = None, price_pcm: int | None = None
) -> Annotated:
    """Split the listing text into segments, some marked and numbered, plus the notes."""
    candidates: list[_Candidate] = []

    def propose(
        match: re.Match[str] | None, mark: Mark, message: str | None, priority: int
    ) -> None:
        if match is not None and message:
            candidates.append(
                _Candidate(
                    priority=priority,
                    start=match.start(),
                    end=match.end(),
                    mark=mark,
                    message=message,
                )
            )

    propose(_OUTDOOR_RE.search(text), "wavy", _noise_note(analysis), _FINDING)
    propose(_KITCHEN_RE.search(text), "wavy", _kitchen_note(analysis), _FINDING)
    propose(_PRESTIGE_RE.search(text), "wavy", _value_note(analysis, outcode, price_pcm), _FINDING)
    for room in _missing_rooms(analysis):
        key = next((k for k in _ROOM_RES if k in room.lower()), None)
        if key is None:
            continue
        # The room as the analysis names it ("second bedroom") beats the generic word.
        exact = re.compile(r"\b" + r"\s+".join(map(re.escape, room.split())) + r"\b", re.I)
        found = exact.search(text) or _ROOM_RES[key].search(text)
        propose(found, "wavy", f"{room.capitalize()} not seen in the supplied photos.", _FINDING)
    for match in _ADJECTIVE_RE.finditer(text):
        propose(match, "strike", ADJECTIVE_NOTE, _ADJECTIVE)

    # Settle overlaps: specific findings first, then by position.
    accepted: list[_Candidate] = []
    for cand in sorted(candidates, key=lambda c: (c["priority"], c["start"])):
        if all(cand["end"] <= a["start"] or cand["start"] >= a["end"] for a in accepted):
            accepted.append(cand)
    accepted.sort(key=lambda c: c["start"])

    # Number the notes in text order; a repeated message shares its number.
    notes: list[str] = []
    numbers: dict[str, int] = {}
    segments: list[Segment] = []
    cursor = 0
    for cand in accepted:
        number = numbers.get(cand["message"])
        if number is None:
            notes.append(cand["message"])
            number = numbers[cand["message"]] = len(notes)
        if cand["start"] > cursor:
            segments.append(Segment(text=text[cursor : cand["start"]], mark=None, note=None))
        segments.append(
            Segment(text=text[cand["start"] : cand["end"]], mark=cand["mark"], note=number)
        )
        cursor = cand["end"]
    if cursor < len(text):
        segments.append(Segment(text=text[cursor:], mark=None, note=None))
    return Annotated(segments=segments, notes=notes)

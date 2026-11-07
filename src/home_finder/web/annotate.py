"""Notes on the listing's own words.

One advert is chosen, cleaned of the portal's markup, and shown as written, with
the analysis attached as numbered notes to the phrases they concern. Every note
names its source (the photo assessment or the stored area reference) and states
what was seen, never that the listing omitted or contradicted something: this code
never compares the text with the finding. A note attaches to a phrase only when a
typed finding exists; the free-form noise prose on its own is not enough, because
it mixes positive and negative observations. Marketing adjectives get no mark of
their own: striking every one through added little to the findings.

Candidates are collected first, overlaps are settled by position, and only then are
the notes numbered, in text order, so every note has exactly one anchor and every
anchor a note.
"""

import re
from collections.abc import Mapping
from html.parser import HTMLParser
from typing import Any, Final, NamedTuple, TypedDict

from home_finder.analysis.listing_text import source_text_hash
from home_finder.models import ListingTextEvidence, PropertyLowlight, PropertySource

# Cleaning the portal's markup


class _Break(NamedTuple):
    """The newlines a tag stands for: each "<br>" adds one, a block needs at least some."""

    added: int
    at_least: int


# Every tag not named here is dropped, keeping its words.
_BREAKS: Final[dict[str, _Break]] = {
    "br": _Break(added=1, at_least=0),
    "p": _Break(added=0, at_least=2),
    **{
        tag: _Break(added=0, at_least=1)
        for tag in (
            *("div", "li", "ul", "ol", "dt", "dd", "blockquote", "hr", "tr", "td", "th"),
            *("h1", "h2", "h3", "h4", "h5", "h6"),
        )
    },
}
# Their contents are code, never the agent's words.
_CODE_TAGS: Final = frozenset({"script", "style"})
# Collapsed after joining, so spaces either side of an inline tag become one.
_SPACE_RUN_RE: Final = re.compile(r"[ \t\f\v]+")


class _AdvertParser(HTMLParser):
    """Split an advert into its words and the line breaks its tags stand for."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.pieces: list[str | _Break] = []
        self._in_code = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._tag(tag, opening=True)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # "<br/>" is one break; the default would also count it as a closing tag. An
        # empty "<script/>" has no contents to hide, so it must not start hiding words.
        if tag not in _CODE_TAGS:
            self._tag(tag, opening=True)

    def handle_endtag(self, tag: str) -> None:
        self._tag(tag, opening=False)

    def handle_data(self, data: str) -> None:
        if not self._in_code:
            self.pieces.append(data)

    def _tag(self, tag: str, *, opening: bool) -> None:
        if tag in _CODE_TAGS:
            self._in_code = opening
        elif tag in _BREAKS:
            self.pieces.append(_BREAKS[tag])


def _join(pieces: list[str | _Break]) -> str:
    """Join words, turning each run of breaks into the newlines it stands for.

    Whitespace beside a run, newlines included, is the source's own formatting
    around its tags, so it goes; the run alone decides the breaks there.
    """
    out: list[str] = []
    added = at_least = 0
    for piece in pieces:
        if isinstance(piece, _Break):
            added += piece.added
            at_least = max(at_least, piece.at_least)
            continue
        if added or at_least:
            piece = piece.lstrip()
            if not piece:
                continue  # whitespace between two tags does not end the run
            while out and not out[-1].strip():
                out.pop()
            if out:
                out[-1] = out[-1].rstrip()
            out.append("\n" * max(added, at_least))
            added = at_least = 0
        out.append(piece)
    return "".join(out)


def clean_advert_text(text: str) -> str:
    """Return a portal's advert as plain text, for escaping and display.

    Each "<br>" is a line break, a block such as a list item or table cell starts a
    new line, and a paragraph is set off by a blank line; every other tag is
    dropped, along with the contents of scripts and styles, and entities are
    decoded once. Newlines beside those tags are the portal's formatting and go
    (portals send a "\\r" by each "<br/>"); any other newline in the advert is
    kept, so plain paragraphs survive a stray tag. Runs of spaces collapse to one,
    lines are trimmed and no more than one blank line is kept. The result is text,
    never markup, so it must still be escaped when rendered.
    """
    parser = _AdvertParser()
    parser.feed(text.replace("\r\n", "\n").replace("\r", "\n"))
    parser.close()
    words = _SPACE_RUN_RE.sub(" ", _join(parser.pieces))
    words = re.sub(r"[^\S\n]*\n[^\S\n]*", "\n", words)  # any space but a newline, "&nbsp;" too
    return re.sub(r"\n{3,}", "\n\n", words).strip()


# Choosing the advert


def _field(analysis: Any, name: str) -> Any:
    if analysis is None:
        return None
    if isinstance(analysis, dict):
        return analysis.get(name)
    return getattr(analysis, name, None)


def _unchanged_since_read(text: ListingTextEvidence, read_hash: str | None) -> bool:
    """Whether a source's text is exactly what the analysis read.

    The hash covers the whole evidence, so a refresh of the description or the
    features breaks the match. A source the analysis kept no hash for is not hashed.
    """
    return read_hash is not None and read_hash == source_text_hash(text)


def choose_advert_text(
    evidence: Mapping[PropertySource, ListingTextEvidence],
    *,
    canonical_source: str | None,
    analysis: Any,
) -> str:
    """Pick one source's advert for the listing page, cleaned.

    One advert, never a merge, so the notes sit on one agent's words. Sources rank:

    1. those whose text is unchanged since the analysis read it, as the hashes in
       its kept inputs show, so the notes sit on words the analysis saw (perhaps
       only the start of a long advert, which the analysis reads in part);
    2. then the home's canonical source, the listing its record is built on;
    3. then by source name, the order the analysis reads them in.

    Length plays no part: the longest advert is not the most accurate one. With no
    kept inputs, or every source changed since, the canonical source comes first.
    An advert that is blank once cleaned is passed over, and with none left the
    text is empty.
    """
    read_hashes = _field(_field(analysis, "listing_text_inputs"), "source_hashes") or {}
    ranked = sorted(
        evidence.items(),
        key=lambda item: (
            not _unchanged_since_read(item[1], read_hashes.get(item[0].value)),
            item[0] != canonical_source,
            item[0].value,
        ),
    )
    for _, text in ranked:
        cleaned = clean_advert_text(text.description or "")
        if cleaned:
            return cleaned
    return ""


# Notes on the advert

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


class Segment(TypedDict):
    text: str
    note: int | None


class Annotated(TypedDict):
    segments: list[Segment]
    notes: list[str]


class _Candidate(TypedDict):
    start: int
    end: int
    message: str


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
    """Split cleaned advert text into segments, some numbered, plus their notes.

    The text is expected from clean_advert_text, so the segments are exactly what
    the page escapes and shows, and every note's span matches the words it marks.
    """
    candidates: list[_Candidate] = []

    def propose(match: re.Match[str] | None, message: str | None) -> None:
        if match is not None and message:
            candidates.append(_Candidate(start=match.start(), end=match.end(), message=message))

    propose(_OUTDOOR_RE.search(text), _noise_note(analysis))
    propose(_KITCHEN_RE.search(text), _kitchen_note(analysis))
    propose(_PRESTIGE_RE.search(text), _value_note(analysis, outcode, price_pcm))
    for room in _missing_rooms(analysis):
        key = next((k for k in _ROOM_RES if k in room.lower()), None)
        if key is None:
            continue
        # The room as the analysis names it ("second bedroom") beats the generic word.
        exact = re.compile(r"\b" + r"\s+".join(map(re.escape, room.split())) + r"\b", re.I)
        found = exact.search(text) or _ROOM_RES[key].search(text)
        propose(found, f"{room.capitalize()} not seen in the supplied photos.")

    # Settle overlaps by position: the earlier phrase keeps its note, the later loses it.
    # A repeated room yields the same message on the same phrase, so it is dropped here.
    accepted: list[_Candidate] = []
    for cand in sorted(candidates, key=lambda c: c["start"]):
        if all(cand["end"] <= a["start"] or cand["start"] >= a["end"] for a in accepted):
            accepted.append(cand)

    # Number the notes in text order, one per marked phrase.
    notes: list[str] = []
    segments: list[Segment] = []
    cursor = 0
    for cand in accepted:
        notes.append(cand["message"])
        if cand["start"] > cursor:
            segments.append(Segment(text=text[cursor : cand["start"]], note=None))
        segments.append(Segment(text=text[cand["start"] : cand["end"]], note=len(notes)))
        cursor = cand["end"]
    if cursor < len(text):
        segments.append(Segment(text=text[cursor:], note=None))
    return Annotated(segments=segments, notes=notes)

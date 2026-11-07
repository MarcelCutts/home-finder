"""Match a listing's address to EPC certificates on postcode, street and number.

The matcher narrows the certificates registered at a listing's postcode (or
on its street) one listing fact at a time: street name, then building name,
then house or building number, then flat number. Whatever certificates
survive are grouped into homes, because one home can have several
certificates. One home left is a plausible match; several is ambiguous; a
fact that rules out every certificate means no match.

Everything here is pure: no I/O, no network. `epc_api.py` fetches the
certificates and `run.py` feeds them in.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import IntEnum, StrEnum
from functools import cached_property

# --- Normalisation -------------------------------------------------------

_ABBREVIATIONS = {
    "rd": "road",
    "st": "street",
    "str": "street",
    "ave": "avenue",
    "av": "avenue",
    "ln": "lane",
    "gdns": "gardens",
    "gdn": "gardens",
    "ct": "court",
    "crt": "court",
    "pl": "place",
    "sq": "square",
    "cres": "crescent",
    "terr": "terrace",
    "ter": "terrace",
    "cl": "close",
    "gr": "grove",
    "gro": "grove",
    "dr": "drive",
    "pk": "park",
    "hse": "house",
    "hts": "heights",
    "apt": "apartment",
    "apts": "apartments",
    "flt": "flat",
}

_FULL_POSTCODE = re.compile(r"^([A-Z]{1,2}\d[A-Z\d]?)\s*(\d[A-Z]{2})$")
_OUTCODE = re.compile(r"^[a-z]{1,2}\d[a-z\d]?$")
_INCODE = re.compile(r"^\d[a-z]{2}$")
_FLAT_WORDS = frozenset({"flat", "apartment", "unit", "maisonette"})
_FLAT_LABEL = re.compile(r"[a-z]?\d+[a-z]?|[a-z]")
_FLAT_ID = re.compile(r"\b(?:flat|apartment|unit|maisonette)\s+([a-z]?\d+[a-z]?|[a-z])\b")
_NUMBER = re.compile(r"^\d+[a-z]?(?:-\d+[a-z]?)?$")


def normalise_postcode(postcode: str | None) -> str | None:
    """Return a full postcode as "Q9 9ZZ", or None for a district or junk."""
    if not postcode:
        return None
    match = _FULL_POSTCODE.match(postcode.strip().upper())
    return f"{match.group(1)} {match.group(2)}" if match else None


def outcode_of(postcode: str | None) -> str | None:
    """Return the postcode district ("E8") of a full or partial postcode."""
    if not postcode:
        return None
    match = re.match(r"^([A-Z]{1,2}\d[A-Z\d]?)\b", postcode.strip().upper())
    return match.group(1) if match else None


def _tokens(part: str) -> list[str]:
    """Lower-case one comma-separated address part and expand abbreviations.

    "St" at the start of a part is "Saint"; anywhere else it is "Street".
    """
    text = part.lower().replace("&", " and ")
    text = re.sub(r"['\u2019`.]", "", text)
    text = re.sub(r"(?<=\d)\s*-\s*(?=\d)", "-", text)  # keep "12-14" as one token
    text = re.sub(r"[^a-z0-9-]+|(?<!\d)-|-(?!\d)", " ", text)
    words = text.split()
    out: list[str] = []
    for i, word in enumerate(words):
        if word == "st" and i == 0 and len(words) > 1:
            out.append("saint")
        else:
            out.append(_ABBREVIATIONS.get(word, word))
    if out and out[0] == "the":
        out = out[1:]
    return out


def normalise_address(lines: Iterable[str | None]) -> str:
    """Join address lines into one normalised, space-separated string."""
    tokens: list[str] = []
    for line in lines:
        if not line:
            continue
        for part in line.split(","):
            tokens.extend(_tokens(part))
    return " ".join(tokens)


def contains_phrase(text: str, phrase: str) -> bool:
    """True when `phrase` appears in `text` as whole tokens."""
    return f" {phrase} " in f" {text} "


# --- Listing addresses ----------------------------------------------------

# A part with one of these words after its first is a street: "Quillon Street",
# "High Road Quillford".
_STREET_WORDS = frozenset(
    {
        "road",
        "street",
        "lane",
        "avenue",
        "grove",
        "terrace",
        "crescent",
        "close",
        "drive",
        "way",
        "walk",
        "hill",
        "rise",
        "row",
        "parade",
        "passage",
        "vale",
        "boulevard",
        "broadway",
        "approach",
        "path",
        "highway",
        "embankment",
    }
)
# A part ending in one of these is a street or a building: "Quillon Park" is
# a street, "Vexley Court" a block of flats.
_STREET_OR_BUILDING_WORDS = frozenset(
    {
        "court",
        "place",
        "square",
        "gardens",
        "garden",
        "mews",
        "park",
        "yard",
        "green",
        "villas",
        "cottages",
        "market",
        "circus",
        "quay",
        "wharf",
    }
)
# Parts that describe the listing rather than locate it: "2 Bed Flat",
# "Studio Apartment", "Room in a shared house".
_LISTING_DESCRIPTION = re.compile(
    r"^(?:(?:\d+|one|two|three|four)\s+bed(?:room)?s?\b|studio\b|room\s+in\b|"
    r"(?:flat|apartment|house|maisonette)\s+(?:to\s+rent|share)\b)"
)
_LONDON = frozenset({"london", "greater london"})


@dataclass(frozen=True)
class ListingAddress:
    """The facts in one listing address that can select an EPC certificate."""

    streets: tuple[str, ...] = ()
    buildings: tuple[str, ...] = ()
    numbered: str | None = None
    """House or building number with the street or building it belongs to,
    as one phrase: "12 quillon street"."""
    flat: str | None = None
    """Flat identifier from "Flat 3" or "Apartment 3B", lower-case."""

    @property
    def is_empty(self) -> bool:
        return not (self.streets or self.buildings)


def _strip_postcode(tokens: list[str]) -> list[str]:
    """Drop a trailing postcode or district ("q9", "q9 9zz") from a part."""
    if len(tokens) >= 2 and _INCODE.match(tokens[-1]) and _OUTCODE.match(tokens[-2]):
        tokens = tokens[:-2]
    elif (
        tokens
        and _OUTCODE.match(tokens[-1])
        and not (len(tokens) >= 2 and tokens[-2] in _FLAT_WORDS)
    ):
        tokens = tokens[:-1]
    return tokens


def parse_listing_address(address: str) -> ListingAddress:
    """Pull street, building, number and flat from a portal address line.

    Portal addresses run from most to least specific: "Flat 3, Vexley House,
    12 Quillon Street, Upper Brack, London Q9". Parts before the first street are
    buildings or flats; parts after it are areas and are ignored. Without a
    street, the first part is taken as a building name.
    """
    parts: list[list[str]] = []
    for raw in address.split(","):
        tokens = _strip_postcode(_tokens(raw))
        text = " ".join(tokens)
        if not tokens or text in _LONDON or _LISTING_DESCRIPTION.match(text):
            continue
        parts.append(tokens)

    flat: str | None = None
    remaining: list[list[str]] = []
    for tokens in parts:
        if len(tokens) >= 2 and tokens[0] in _FLAT_WORDS and _FLAT_LABEL.fullmatch(tokens[1]):
            flat = flat or tokens[1]
            tokens = tokens[2:]
            if not tokens:
                continue
        remaining.append(tokens)

    street_index = next((i for i, t in enumerate(remaining) if _is_street(t)), None)
    if street_index is None:
        street_index = next(
            (i for i, t in enumerate(remaining) if t[-1] in _STREET_OR_BUILDING_WORDS),
            None,
        )

    streets: list[str] = []
    buildings: list[str] = []
    numbered: str | None = None
    if street_index is None:
        if remaining:
            number, name = _split_number(remaining[0])
            if name:
                buildings.append(name)
                if number:
                    numbered = f"{number} {name}"
    else:
        for tokens in remaining[:street_index]:
            number, name = _split_number(tokens)
            if name:
                buildings.append(name)
        number, name = _split_number(remaining[street_index])
        if name:
            streets.append(name)
            if number:
                numbered = f"{number} {name}"
        # A second street-like part straight after the first ("Quillon Road,
        # Marrow Street, Upper Brack") is kept as a fallback street.
        for tokens in remaining[street_index + 1 :]:
            if _is_street(tokens):
                _, name = _split_number(tokens)
                if name and name not in streets:
                    streets.append(name)
    return ListingAddress(
        streets=tuple(streets), buildings=tuple(buildings), numbered=numbered, flat=flat
    )


def _is_street(tokens: list[str]) -> bool:
    return any(t in _STREET_WORDS for t in tokens[1:])


def _split_number(tokens: list[str]) -> tuple[str | None, str]:
    """Split "12a quillon street" into ("12a", "quillon street")."""
    if len(tokens) >= 2 and _NUMBER.match(tokens[0]):
        return tokens[0], " ".join(tokens[1:])
    return None, " ".join(tokens)


# --- Certificates and homes ----------------------------------------------


@dataclass(frozen=True)
class Certificate:
    """One EPC search result: enough to match on, not the full certificate."""

    certificate_number: str
    address_lines: tuple[str, ...]
    postcode: str
    uprn: str | None = None
    band: str | None = None
    registration_date: str | None = None

    @cached_property
    def text(self) -> str:
        return normalise_address(self.address_lines)

    @cached_property
    def flat(self) -> str | None:
        match = _FLAT_ID.search(self.text)
        return match.group(1) if match else None


@dataclass(frozen=True)
class Home:
    """The certificates that belong to one home, newest first."""

    certificates: tuple[Certificate, ...]

    @property
    def latest(self) -> Certificate:
        return self.certificates[0]

    @property
    def key(self) -> str:
        """A stable identifier: the UPRN when any certificate has one."""
        uprns = sorted({c.uprn for c in self.certificates if c.uprn})
        if uprns:
            return f"uprn:{uprns[0]}"
        return f"address:{self.latest.text}|{self.latest.postcode}"


def group_homes(certificates: Iterable[Certificate]) -> list[Home]:
    """Group certificates into homes by UPRN, or by address when UPRN is missing.

    Two certificates are the same home when they share a UPRN or the same
    normalised address and postcode, so a certificate lodged without a UPRN
    still joins its home.
    """
    certs = list(certificates)
    parent = list(range(len(certs)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    first_seen: dict[str, int] = {}
    for i, cert in enumerate(certs):
        keys = [f"address:{cert.text}|{cert.postcode}"]
        if cert.uprn:
            keys.append(f"uprn:{cert.uprn}")
        for key in keys:
            if key in first_seen:
                parent[find(i)] = find(first_seen[key])
            else:
                first_seen[key] = i

    groups: dict[int, list[Certificate]] = {}
    for i, cert in enumerate(certs):
        groups.setdefault(find(i), []).append(cert)
    homes = [
        Home(tuple(sorted(group, key=lambda c: c.registration_date or "", reverse=True)))
        for group in groups.values()
    ]
    return sorted(homes, key=lambda h: h.key)


# --- Matching -------------------------------------------------------------


class Outcome(StrEnum):
    ONE = "one"
    AMBIGUOUS = "ambiguous"
    NONE = "none"


class Evidence(IntEnum):
    """The most specific listing fact that narrowed the certificates."""

    POSTCODE = 0
    STREET = 1
    BUILDING = 2
    NUMBER = 3
    FLAT = 4


class NoMatchReason(StrEnum):
    """Why a listing matched no certificate."""

    NO_CERTIFICATES = "no_certificates"
    NO_ADDRESS = "no_address"
    STREET_NOT_FOUND = "street_not_found"
    BUILDING_NOT_FOUND = "building_not_found"
    NUMBER_NOT_FOUND = "number_not_found"
    FLAT_NOT_FOUND = "flat_not_found"


@dataclass(frozen=True)
class MatchResult:
    outcome: Outcome
    evidence: Evidence | None = None
    homes: tuple[Home, ...] = ()
    reason: NoMatchReason | None = None
    """Set when the outcome is NONE."""
    variants_disagree: bool = False
    """Set when two of the listing's addresses each picked a different home."""


def match_address(address: ListingAddress, certificates: Sequence[Certificate]) -> MatchResult:
    """Narrow `certificates` by each fact the address gives and classify the rest.

    A street, number or flat the listing states but no certificate carries
    ends the match with no result: the listing's own words rule the
    candidates out. A building name is softer, because certificates often
    leave it out; it narrows only when some certificate has it.
    """
    if not certificates:
        return MatchResult(Outcome.NONE, reason=NoMatchReason.NO_CERTIFICATES)

    pool = list(certificates)
    evidence = Evidence.POSTCODE

    if address.streets:
        for street in address.streets:
            on_street = [c for c in pool if contains_phrase(c.text, street)]
            if on_street:
                pool, evidence = on_street, Evidence.STREET
                break
        else:
            return MatchResult(Outcome.NONE, reason=NoMatchReason.STREET_NOT_FOUND)

    if address.buildings:
        in_building = [
            c for c in pool if any(contains_phrase(c.text, b) for b in address.buildings)
        ]
        if in_building:
            pool, evidence = in_building, Evidence.BUILDING
        elif not address.streets:
            return MatchResult(Outcome.NONE, reason=NoMatchReason.BUILDING_NOT_FOUND)

    if address.numbered:
        numbered = [c for c in pool if contains_phrase(c.text, address.numbered)]
        if not numbered:
            return MatchResult(Outcome.NONE, reason=NoMatchReason.NUMBER_NOT_FOUND)
        pool, evidence = numbered, Evidence.NUMBER

    if address.flat:
        flats = [c for c in pool if c.flat == address.flat]
        if not flats:
            return MatchResult(Outcome.NONE, reason=NoMatchReason.FLAT_NOT_FOUND)
        pool, evidence = flats, Evidence.FLAT

    homes = tuple(group_homes(pool))
    outcome = Outcome.ONE if len(homes) == 1 else Outcome.AMBIGUOUS
    return MatchResult(outcome, evidence=evidence, homes=homes)


def match_listing(addresses: Sequence[str], certificates: Sequence[Certificate]) -> MatchResult:
    """Match a home listed on several portals, each with its own address line.

    Each address is matched on its own. A single home wins over ambiguity,
    ambiguity over no match, and more specific evidence breaks ties. Two
    addresses that each pick a different single home make the listing
    ambiguous.
    """
    parsed = [parse_listing_address(a) for a in dict.fromkeys(addresses)]
    usable = [p for p in parsed if not p.is_empty]
    if not certificates:
        return MatchResult(Outcome.NONE, reason=NoMatchReason.NO_CERTIFICATES)
    if not usable:
        return MatchResult(Outcome.NONE, reason=NoMatchReason.NO_ADDRESS)

    results = [match_address(p, certificates) for p in usable]
    singles = [r for r in results if r.outcome is Outcome.ONE]
    if singles:
        keys = {r.homes[0].key for r in singles}
        if len(keys) > 1:
            homes = {h.key: h for r in singles for h in r.homes}
            return MatchResult(
                Outcome.AMBIGUOUS,
                evidence=max(r.evidence for r in singles if r.evidence is not None),
                homes=tuple(homes[k] for k in sorted(homes)),
                variants_disagree=True,
            )
        return max(singles, key=lambda r: r.evidence or 0)

    ambiguous = [r for r in results if r.outcome is Outcome.AMBIGUOUS]
    if ambiguous:
        return min(ambiguous, key=lambda r: (len(r.homes), -(r.evidence or 0)))
    return results[0]

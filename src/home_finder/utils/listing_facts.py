"""Conservative checks of property claims against retained listing text.

These checks establish textual support, not an EPC/VOA verification. Unknown
and conflicting claims need confirmation with the agent or official register.
"""

from __future__ import annotations

import html
import re
from collections.abc import Iterable


def plain_listing_text(parts: Iterable[str]) -> str:
    return re.sub(r"<[^>]*>", " ", html.unescape("\n".join(parts)))


def listing_band(kind: str, description: str | None, features: list[str] | None) -> str:
    """Return a single explicit, unambiguous council-tax or EPC band."""
    text = plain_listing_text([description or "", *(features or [])])
    prefix = (
        r"council\s+tax\s*[:\-\u2013]?\s*(?:band\s*)?"
        if kind == "council_tax"
        else (
            r"(?:epc|energy(?:\s+(?:performance|efficiency))?)\s*[:\-\u2013]?\s*"
            r"(?:(?:rating|rated|band)\s*)?"
        )
    )
    values = "A-H" if kind == "council_tax" else "A-G"
    pattern = re.compile(rf"\b{prefix}(?:is\s+)?[:\-\u2013]?\s*([{values}])\b", re.IGNORECASE)
    bands = set()
    for clause in re.split(r"[.;\n]", text):
        if kind == "epc":
            # A separately labelled potential rating is not the current rating.
            clause = re.sub(
                r"\(potential(?:\s+(?:epc|rating|band))*\s*[:\-]?\s*[A-G]\s*\)",
                "",
                clause,
                flags=re.I,
            )
        if "?" in clause or re.search(
            r"\b(?:estimate[ds]?|assum\w*|budget\w*|potential|target|possible|possibly|"
            r"provisional|unconfirmed|subject\s+to|to\s+be\s+confirmed|not\s+confirmed)\b",
            clause,
            re.I,
        ):
            continue
        for match in pattern.finditer(clause):
            after = clause[match.end() :]
            if re.match(rf"\s*(?:[/\-\u2013]\s*[{values}]\b|or\b)", after, re.I):
                return "unknown"
            before = clause[: match.start()]
            if re.search(r"\b(?:not|no)\s*$", before, re.I):
                continue
            # Lowercase 'a' can be an article: 'EPC - a copy is available'.
            if match[1] == "a":
                continue
            bands.add(match[1].upper())
    return next(iter(bands)) if len(bands) == 1 else "unknown"


def supported_band(
    kind: str, claimed: str | None, description: str | None, features: list[str] | None
) -> str | None:
    if claimed in (None, "unknown"):
        return claimed
    return claimed if listing_band(kind, description, features) == claimed else "unknown"

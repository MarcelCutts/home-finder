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
        r"council\s+tax\s*(?:band\s*)?"
        if kind == "council_tax"
        else (r"(?:epc|energy\s+(?:performance|efficiency))\s*(?:(?:rating|band)\s*)?")
    )
    values = "A-H" if kind == "council_tax" else "A-G"
    matches = re.finditer(rf"\b{prefix}[:\-\u2013]?\s*([{values}])\b", text, re.IGNORECASE)
    bands = set()
    for match in matches:
        if re.match(rf"\s*(?:[/\-\u2013]|or)\s*[{values}]\b", text[match.end() :], re.I):
            return "unknown"
        context = text[max(0, match.start() - 30) : match.end() + 30].lower()
        if re.search(r"\b(estimate[ds]?|assum\w*|budget\w*|potential|target)\b", context):
            continue
        before = text[max(0, match.start() - 10) : match.start()].lower()
        if re.search(r"\bnot\s*$", before):
            continue
        bands.add(match[1].upper())
    return next(iter(bands)) if len(bands) == 1 else "unknown"


def supported_band(
    kind: str, claimed: str | None, description: str | None, features: list[str] | None
) -> str | None:
    if claimed in (None, "unknown"):
        return claimed
    return claimed if listing_band(kind, description, features) == claimed else "unknown"

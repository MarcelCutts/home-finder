"""Separate advertised tenant costs from bills that still need confirmation."""

from __future__ import annotations

import re
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from home_finder.models import ListingTextEvidence, PropertySource
from home_finder.utils.listing_facts import listing_band, plain_listing_text

_BILLS = {
    "council_tax": ("Council tax", r"council\s+tax"),
    "gas": ("Gas", r"gas"),
    "electricity": ("Electricity", r"electricity"),
    "water": ("Water", r"water"),
    "broadband": ("Broadband", r"(?:broadband|internet)"),
}
_NAME = r"(?:council\s+tax|gas|electricity|water|broadband|internet)"
_NAMES = rf"{_NAME}(?:\s*(?:,|and|&|/|\+)\s*{_NAME})*"
_TREATMENT = re.compile(
    rf"\b(?P<names>{_NAMES})(?:\s+bills?)?\s*[:\-]?\s*(?:(?:is|are)\s+)?"
    r"(?P<state>not\s+included|excluded|included|payable\s+separately)\b",
    re.IGNORECASE,
)
_RENT_INCLUDES = re.compile(
    rf"\brent\s+(?P<state>includes|excludes)\s+(?P<names>{_NAMES})\b", re.IGNORECASE
)
_IN_RENT = re.compile(r"\s+(?:in|within)\s+(?:the\s+)?rent\b", re.IGNORECASE)
_PAID_QUALIFIER = re.compile(
    r"\b(?:optional|package|extra|additional\s+(?:fee|charge|cost)|"
    r"payable\s+separately|service\s+charge|at\s+(?:a\s+)?(?:cost|charge))\b",
    re.IGNORECASE,
)
_AMOUNT = re.compile(
    rf"\b(?P<names>{_NAME})\s*:\s*£(?P<low>\d{{1,4}}(?:\.\d{{1,2}})?)"
    r"(?:\s*[-\u2013]\s*£?(?P<high>\d{1,4}(?:\.\d{1,2})?))?"
    r"\s*(?:pcm|per\s+month|/month)\s*,?\s*"
    r"(?:payable\s+separately|in\s+addition\s+to\s+rent)\b",
    re.IGNORECASE,
)


def _bill_keys(names: str) -> list[str]:
    return [
        key for key, (_, pattern) in _BILLS.items() if re.search(rf"\b{pattern}\b", names, re.I)
    ]


def build_monthly_budget(
    *,
    rent_pcm: int,
    evidence: Mapping[PropertySource, ListingTextEvidence] | Mapping[str, ListingTextEvidence],
    earlier_service_charge: int | None = None,
) -> dict[str, Any]:
    """Use explicit named-bill claims; preserve unknowns and source conflicts.

    This intentionally accepts only a small set of clear wordings. It does not
    infer coverage from "bills included", use a guessed EPC, or add leaseholder
    charges. Unsupported prose stays available in the listing for human review.
    Amount ranges must be explicitly advertised as monthly and separate from
    rent; they are claims, not a finding that a payment is legally due.

    Undated bundled research rates are not current cost references. Dated
    household/tariff estimates will be added in PR17b, outside advertised costs.
    """
    claims: dict[str, set[str]] = {key: set() for key in _BILLS}
    amounts: dict[str, set[tuple[Decimal, Decimal]]] = {key: set() for key in _BILLS}
    quotes: dict[str, list[str]] = {key: [] for key in _BILLS}
    parts: list[str] = []
    general_bills_claim = False
    service_mentioned = earlier_service_charge is not None
    for source, value in evidence.items():
        label = source.display_name if isinstance(source, PropertySource) else source
        text = plain_listing_text([value.description or "", *(value.features or ())])
        parts.append(text)
        general_bills_claim |= bool(
            re.search(
                r"\bbills?\s+(?:not\s+)?included\b|\binclusive\s+of\s+(?:all\s+)?bills\b",
                text,
                re.I,
            )
        )
        service_mentioned |= bool(re.search(r"\b(?:service\s+charge|ground\s+rent)\b", text, re.I))
        # Keep clauses separate: a nearby question/assumption cannot establish a
        # payment. Do not split decimal amounts.
        for clause in re.split(r"[\n;]|(?<!\d)\.(?!\d)", text):
            if "?" in clause or re.search(
                r"\b(?:if|unless|may|might|estimat\w*|assum\w*|potential|subject\s+to)\b",
                clause,
                re.I,
            ):
                continue
            for pattern in (_TREATMENT, _RENT_INCLUDES):
                for match in pattern.finditer(clause):
                    if re.search(r"\b(?:not|no)\s*$", clause[: match.start()], re.I):
                        continue
                    state = (
                        "included"
                        if match["state"].lower() in {"included", "includes"}
                        else "excluded"
                    )
                    if state == "included" and (
                        _PAID_QUALIFIER.search(clause)
                        or (pattern is _TREATMENT and not _IN_RENT.match(clause[match.end() :]))
                    ):
                        continue
                    for key in _bill_keys(match["names"]):
                        claims[key].add(state)
                        quotes[key].append(f"{label}: {clause.strip()[:240]}")
            for match in _AMOUNT.finditer(clause):
                if re.search(r"\b(?:not|no)\s*$", clause[: match.start()], re.I):
                    continue
                quote_low = Decimal(match["low"])
                quote_high = Decimal(match["high"] or match["low"])
                if quote_high < quote_low:
                    continue
                for key in _bill_keys(match["names"]):
                    claims[key].add("excluded")
                    amounts[key].add((quote_low, quote_high))
                    quotes[key].append(f"{label}: {clause.strip()[:240]}")

    items = []
    subtotal_low = subtotal_high = Decimal(rent_pcm)
    for key, (label, _) in _BILLS.items():
        states = claims[key]
        state = next(iter(states)) if len(states) == 1 else "unknown"
        if len(states) > 1 or len(amounts[key]) > 1:
            state = "conflicting"
        low: Decimal | None = None
        high: Decimal | None = None
        if state == "excluded" and len(amounts[key]) == 1:
            low, high = next(iter(amounts[key]))
            subtotal_low += low
            subtotal_high += high
        items.append(
            {
                "label": label,
                "state": state,
                "low": low,
                "high": high,
                "evidence": list(dict.fromkeys(quotes[key])),
            }
        )

    all_text = "\n".join(parts)
    return {
        "rent": rent_pcm,
        "line_items": items,
        "subtotal_low": subtotal_low,
        "subtotal_high": subtotal_high,
        # Even a fully itemised advert is not a complete household budget.
        "complete": False,
        "general_bills_claim": general_bills_claim,
        "service_charge_review": service_mentioned,
        "earlier_service_charge": earlier_service_charge,
        "epc_rating": listing_band("epc", all_text, None),
        "council_tax_band": listing_band("council_tax", all_text, None),
        "assumptions": (
            "Retained advert claims; confirm current amounts, named inclusions and any usage caps. "
            "Additional bills remain unpriced where no supported amount is available. Council tax "
            "depends on the property, current council rates and household discounts; energy "
            "depends on usage, heating and tariff. Deposits, moving costs, TV licensing and "
            "optional insurance are outside this monthly subtotal."
        ),
    }

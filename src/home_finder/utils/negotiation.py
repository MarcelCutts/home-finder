"""Offer preparation grounded in what the collector actually observed."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class NegotiationSignal:
    category: str
    text: str


def generate_negotiation_brief(
    *, days_observed: int, price_history: list[dict[str, Any]]
) -> dict[str, Any]:
    """Do not turn collection age or undated research into offer percentages."""
    age = f"{days_observed} days ago" if days_observed > 0 else "today"
    signals = [
        NegotiationSignal(
            "first_observed",
            f"First observed by Home Finder {age}. The original listing date and continuous "
            "availability are unknown.",
        )
    ]
    decreases = sum(1 for item in price_history if (item.get("change_amount") or 0) < 0)
    increases = sum(1 for item in price_history if (item.get("change_amount") or 0) > 0)
    if decreases or increases:
        signals.append(
            NegotiationSignal(
                "price_changes",
                f"Recorded {decreases} rent decrease(s) and {increases} increase(s). Check the "
                "source and dates in the price history; changes do not establish negotiating room.",
            )
        )
    else:
        signals.append(
            NegotiationSignal("price_changes", "No rent changes recorded by Home Finder.")
        )
    return {
        "signals": signals,
        "suggested_approach": (
            "Confirm availability, the advertised rent and included bills with the agent. "
            "Use current comparable homes and your budget when deciding whether to make an "
            "offer. These observations do not support a fixed discount or a market-value claim."
        ),
    }

"""Negotiation intelligence based on market signals."""

from datetime import datetime
from typing import Any


def generate_negotiation_brief(
    *,
    days_listed: int,
    price_history: list[dict[str, Any]],
    benchmark_diff: int | None,
    area_median: int | None,
    current_price: int,
) -> dict[str, Any] | None:
    """Generate a negotiation intelligence brief for a property.

    Returns dict with: strength, days_context, price_context,
    history_context, seasonal_context, suggested_approach.
    Or None if insufficient data.
    """
    if days_listed == 0 and not price_history and benchmark_diff is None:
        return None

    signals = 0

    # Days context
    if days_listed >= 14:
        days_context = f"Listed {days_listed} days (above 17-day London average)"
        signals += 1
    elif days_listed >= 7:
        days_context = f"Listed {days_listed} days"
    elif days_listed > 0:
        days_context = f"Listed {days_listed} days (fresh listing)"
    else:
        days_context = "Just listed"

    # Price history context
    drops = [h for h in price_history if h.get("change_amount", 0) < 0]
    if drops:
        total_drop = sum(h["change_amount"] for h in drops)
        history_context = (
            f"Already dropped {len(drops)} time{'s' if len(drops) > 1 else ''} "
            f"(total {chr(163)}{abs(total_drop):,})"
        )
        signals += 1
    else:
        history_context = "No price changes"

    # Price context (vs area benchmark)
    if benchmark_diff is not None and area_median:
        pct = round((benchmark_diff / area_median) * 100)
        if pct > 0:
            price_context = f"{pct}% above area median for this bedroom count"
            signals += 1
        elif pct < 0:
            price_context = f"{abs(pct)}% below area median — competitive price"
        else:
            price_context = "At area median"
    else:
        price_context = "No area benchmark data"

    # Seasonal context
    month = datetime.now().month
    if month in (1, 2, 11, 12):
        seasonal_context = f"{datetime.now().strftime('%B')} — rents typically 5-8% lower than peak"
    elif month in (6, 7, 8, 9):
        seasonal_context = f"{datetime.now().strftime('%B')} — peak rental season"
    else:
        seasonal_context = f"{datetime.now().strftime('%B')} — moderate season"

    # Strength
    if signals >= 2:
        strength = "strong"
    elif signals >= 1:
        strength = "moderate"
    else:
        strength = "weak"

    # Approach
    if strength == "strong":
        suggested_approach = (
            "Multiple negotiation signals present. "
            "Consider offering 5-8% below asking with a strong tenant profile."
        )
    elif strength == "moderate":
        suggested_approach = (
            "Some room for negotiation. "
            "A polite offer 3-5% below asking with fast move-in readiness could work."
        )
    else:
        suggested_approach = (
            "Limited negotiation leverage. "
            "Focus on being the strongest applicant rather than pushing on price."
        )

    return {
        "strength": strength,
        "days_context": days_context,
        "price_context": price_context,
        "history_context": history_context,
        "seasonal_context": seasonal_context,
        "suggested_approach": suggested_approach,
    }

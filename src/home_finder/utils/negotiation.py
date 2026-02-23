"""Negotiation intelligence based on graduated market signals."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from home_finder.data.area_context import (
    DEFAULT_BENCHMARK,
    OUTCODE_BOROUGH,
    RENT_TRENDS,
    RENTAL_BENCHMARKS,
    RentTrend,
)


@dataclass(frozen=True)
class NegotiationSignal:
    """A single scored negotiation signal."""

    category: str
    weight: float  # 0.0-1.0
    direction: str  # "buyer" | "seller" | "neutral"
    text: str


def generate_negotiation_brief(
    *,
    days_listed: int,
    price_history: list[dict[str, Any]],
    price_pcm: int,
    outcode: str | None,
    bedrooms: int,
) -> dict[str, Any] | None:
    """Generate a negotiation intelligence brief for a property.

    Returns dict with: strength, signals, suggested_approach, benchmark_source.
    Or None if insufficient data.
    """
    if days_listed == 0 and not price_history and not outcode:
        return None

    # Resolve benchmark
    median, benchmark_source = _resolve_benchmark(outcode, bedrooms)

    # Resolve rent trend
    trend = _resolve_rent_trend(outcode)

    signals: list[NegotiationSignal] = []
    signals.append(_score_days_listed(days_listed))
    signals.append(_score_price_drops(price_history))
    if median is not None and price_pcm > 0:
        signals.append(_score_benchmark(price_pcm, median, outcode))
    if trend is not None:
        signals.append(_score_rent_trend(trend, outcode))
    signals.append(_score_seasonal())

    # Net score: buyer signals positive, seller signals negative
    buyer_weight = sum(s.weight for s in signals if s.direction == "buyer")
    seller_weight = sum(s.weight for s in signals if s.direction == "seller")
    net = buyer_weight - seller_weight

    if net >= 1.5:
        strength = "strong"
    elif net >= 0.5:
        strength = "moderate"
    elif net >= -0.5:
        strength = "balanced"
    else:
        strength = "limited"

    approach = _build_approach(strength, signals, price_pcm, median, outcode)

    return {
        "strength": strength,
        "signals": signals,
        "suggested_approach": approach,
        "benchmark_source": benchmark_source,
    }


def _resolve_benchmark(outcode: str | None, bedrooms: int) -> tuple[int | None, str | None]:
    """Look up the static median rent for an outcode + bedroom count."""
    if not outcode:
        return None, None
    beds_map = RENTAL_BENCHMARKS.get(outcode)
    if beds_map and bedrooms in beds_map:
        return beds_map[bedrooms], f"{outcode} median from area research"
    # Fall back to default benchmark
    if bedrooms in DEFAULT_BENCHMARK:
        return DEFAULT_BENCHMARK[bedrooms], "London-wide median estimate"
    return None, None


def _resolve_rent_trend(outcode: str | None) -> RentTrend | None:
    """Look up rent trend for the borough containing this outcode."""
    if not outcode:
        return None
    borough = OUTCODE_BOROUGH.get(outcode)
    if not borough:
        return None
    return RENT_TRENDS.get(borough)


def _score_days_listed(days: int) -> NegotiationSignal:
    """Graduate from 0.0 at <7d to 1.0 at 28d+."""
    if days >= 28:
        weight = 1.0
        text = f"Listed {days} days — significantly stale"
    elif days >= 21:
        weight = 0.6 + 0.4 * (days - 21) / 7
        text = f"Listed {days} days — sitting longer than typical"
    elif days >= 14:
        weight = 0.4 + 0.2 * (days - 14) / 7
        text = f"Listed {days} days — on the market a while"
    elif days >= 7:
        weight = 0.2 + 0.2 * (days - 7) / 7
        text = f"Listed {days} days"
    elif days > 0:
        weight = 0.2
        text = f"Listed {days} days — fresh listing"
        return NegotiationSignal("days_listed", weight, "seller", text)
    else:
        return NegotiationSignal("days_listed", 0.3, "seller", "Just listed — high demand window")

    return NegotiationSignal("days_listed", round(weight, 2), "buyer", text)


def _score_price_drops(price_history: list[dict[str, Any]]) -> NegotiationSignal:
    """0.3 base + 0.2 per extra drop + 0.1 per £100 dropped, capped 1.0."""
    drops = [h for h in price_history if h.get("change_amount", 0) < 0]
    if not drops:
        return NegotiationSignal("price_drops", 0.0, "neutral", "No price changes")

    total_drop = abs(sum(h["change_amount"] for h in drops))
    weight = min(1.0, 0.3 + 0.2 * (len(drops) - 1) + 0.1 * (total_drop / 100))

    if len(drops) == 1:
        text = f"Price dropped once (total -\u00a3{total_drop:,})"
    else:
        text = f"Price dropped {len(drops)} times (total -\u00a3{total_drop:,})"

    return NegotiationSignal("price_drops", round(weight, 2), "buyer", text)


def _score_benchmark(price_pcm: int, median: int, outcode: str | None) -> NegotiationSignal:
    """Graduate based on % above/below median. ±5% = neutral zone."""
    diff_pct = ((price_pcm - median) / median) * 100

    abs_pct = abs(diff_pct)
    area = outcode or "area"
    median_str = f"\u00a3{median:,}"
    if abs_pct <= 5:
        weight = 0.0
        direction = "neutral"
        text = f"Near {area} median ({median_str})"
    elif abs_pct <= 15:
        # Graduate 0.3 to 1.0 over 5-15% range
        weight = 0.3 + 0.7 * (abs_pct - 5) / 10
        if diff_pct > 0:
            direction = "buyer"
            diff_str = f"\u00a3{price_pcm - median:,}"
            text = f"{diff_str} above {area} median ({median_str})"
        else:
            direction = "seller"
            diff_str = f"\u00a3{median - price_pcm:,}"
            text = f"{diff_str} below {area} median ({median_str}) - competitive price"
    else:
        weight = 1.0
        if diff_pct > 0:
            direction = "buyer"
            diff_str = f"\u00a3{price_pcm - median:,}"
            text = f"{diff_str} above {area} median ({median_str})"
        else:
            direction = "seller"
            diff_str = f"\u00a3{median - price_pcm:,}"
            text = f"{diff_str} below {area} median ({median_str}) - well below market"

    return NegotiationSignal("benchmark", round(weight, 2), direction, text)


def _score_rent_trend(trend: RentTrend, outcode: str | None) -> NegotiationSignal:
    """Rising strongly = seller advantage, flat/falling = buyer advantage."""
    borough = OUTCODE_BOROUGH.get(outcode or "") or "the area"
    direction_str = trend["direction"]
    yoy = trend["yoy_pct"]

    if direction_str == "rising strongly" or yoy >= 7:
        weight = 0.6
        direction = "seller"
        text = f"Rents in {borough} rising strongly (+{yoy}% YoY)"
    elif direction_str == "rising" or yoy >= 3:
        weight = 0.3
        direction = "seller"
        text = f"Rents in {borough} rising (+{yoy}% YoY)"
    elif direction_str in ("falling", "declining") or yoy < 0:
        weight = 0.5
        direction = "buyer"
        text = f"Rents in {borough} falling ({yoy}% YoY)"
    else:
        # Flat / stable / low positive
        weight = 0.2
        direction = "buyer"
        text = f"Rents in {borough} broadly flat (+{yoy}% YoY)"

    return NegotiationSignal("rent_trend", round(weight, 2), direction, text)


def _score_seasonal() -> NegotiationSignal:
    """Off-peak months favour buyers, peak months favour sellers."""
    month = datetime.now().month
    month_name = datetime.now().strftime("%B")

    if month in (1, 2, 11, 12):
        return NegotiationSignal(
            "seasonal",
            0.3,
            "buyer",
            f"{month_name} — off-peak season, fewer competing tenants",
        )
    elif month in (6, 7, 8, 9):
        return NegotiationSignal(
            "seasonal",
            0.3,
            "seller",
            f"{month_name} — peak rental season, higher competition",
        )
    else:
        return NegotiationSignal(
            "seasonal",
            0.1,
            "neutral",
            f"{month_name} — moderate season",
        )


def _build_approach(
    strength: str,
    signals: list[NegotiationSignal],
    price_pcm: int,
    median: int | None,
    outcode: str | None,
) -> str:
    """Compose approach text from signal components."""
    buyer_signals = sorted([s for s in signals if s.direction == "buyer"], key=lambda s: -s.weight)

    if strength == "strong":
        lead = _lead_sentence(buyer_signals)
        if median and price_pcm > median:
            area = outcode or "area"
            guidance = (
                f" Consider offering around \u00a3{median:,}"
                f" (the {area} median) and emphasise"
                " a strong tenant profile."
            )
        else:
            guidance = " Consider offering 5-8% below asking with a strong tenant profile."
        return lead + guidance

    if strength == "moderate":
        lead = _lead_sentence(buyer_signals)
        guidance = " A polite offer 3-5% below asking with fast move-in readiness could work."
        return lead + guidance

    if strength == "balanced":
        return (
            "Mixed signals - neither side has clear leverage."
            " Consider offering at or slightly below asking,"
            " and focus on strong references and flexibility"
            " on move-in dates."
        )

    # limited
    seller_signals = sorted(
        [s for s in signals if s.direction == "seller"],
        key=lambda s: -s.weight,
    )
    reasons = [
        s.text.split(" - ")[0].lower() if " - " in s.text else s.text.lower()
        for s in seller_signals[:2]
    ]
    reason_text = " and ".join(reasons) if reasons else "current market conditions"
    return (
        f"Limited negotiation leverage due to {reason_text}."
        " Focus on being the strongest applicant -"
        " references, proof of income, and flexibility"
        " matter more than price here."
    )


def _lead_sentence(buyer_signals: list[NegotiationSignal]) -> str:
    """Build a lead sentence from the top buyer signals."""
    if not buyer_signals:
        return "Some room for negotiation."

    top = buyer_signals[:2]
    parts = [s.text for s in top]
    if len(parts) == 1:
        return parts[0] + "."
    return parts[0] + ", and " + parts[1].lower() + "."

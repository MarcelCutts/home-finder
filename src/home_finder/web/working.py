"""The fit score with its working shown: each dimension's share of the total."""

from typing import TypedDict

from home_finder.filters.fit_score import FitDimension


class WorkingRow(TypedDict):
    label: str
    weight: int  # the dimension's nominal weight, in percent
    share: float  # its real share once confidence is applied, in percent
    score: int
    points: float


class FitWorking(TypedDict):
    rows: list[WorkingRow]
    total: float  # the points add up to this, before rounding
    adjusted: bool  # True when confidence changed any share from its weight
    lowest: WorkingRow | None


def fit_working(breakdown: list[FitDimension]) -> FitWorking:
    """Show how the score is made: share times score = points, adding up to the score.

    The score is a confidence-weighted mean, so a dimension the photos could not
    judge counts for less than its weight. The share column shows what each one
    really counted for, and the points add up to the unrounded score.
    """
    denominator = sum(dim["weight"] * dim["confidence"] for dim in breakdown)
    rows: list[WorkingRow] = []
    adjusted = False
    for dim in breakdown:
        share = (dim["weight"] * dim["confidence"] / denominator * 100) if denominator else 0.0
        if round(share) != dim["weight"]:
            adjusted = True
        rows.append(
            WorkingRow(
                label=dim["label"],
                weight=dim["weight"],
                share=round(share, 1),
                score=dim["score"],
                points=round(dim["score"] * share / 100, 1),
            )
        )
    total = sum(dim["score"] * dim["weight"] * dim["confidence"] for dim in breakdown)
    total = total / denominator if denominator else 0.0
    scored = [row for row in rows if row["share"] > 0]
    lowest = min(scored, key=lambda row: row["score"]) if scored else None
    return FitWorking(rows=rows, total=round(total, 1), adjusted=adjusted, lowest=lowest)

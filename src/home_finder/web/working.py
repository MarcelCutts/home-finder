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
    total: float  # the unrounded score; the points add up to it before display rounding
    adjusted: bool  # True when confidence changed any share from its weight
    lowest: WorkingRow | None


def fit_working(breakdown: list[FitDimension]) -> FitWorking:
    """Show how the score is made: share times score = points, adding up to the score.

    The score is a confidence-weighted mean, so a dimension the photos could not
    judge counts for less than its weight. The share column shows what each one
    really counted for. The sum uses the scorer's unrounded values (``raw_score``
    and ``raw_confidence`` when the breakdown carries them), so its rounded result
    is the score itself; the displayed points are rounded to one decimal, which is
    why they can differ from the total by a tenth or so.
    """
    raw = [
        (
            float(dim.get("raw_score", dim["score"])),
            float(dim.get("raw_confidence", dim["confidence"])),
        )
        for dim in breakdown
    ]
    denominator = sum(
        dim["weight"] * confidence for dim, (_, confidence) in zip(breakdown, raw, strict=True)
    )
    rows: list[WorkingRow] = []
    adjusted = False
    for dim, (score, confidence) in zip(breakdown, raw, strict=True):
        share = (dim["weight"] * confidence / denominator * 100) if denominator else 0.0
        if round(share) != dim["weight"]:
            adjusted = True
        rows.append(
            WorkingRow(
                label=dim["label"],
                weight=dim["weight"],
                share=round(share, 1),
                score=dim["score"],
                points=round(score * share / 100, 1),
            )
        )
    total = sum(dim["weight"] * c * s for dim, (s, c) in zip(breakdown, raw, strict=True))
    total = total / denominator if denominator else 0.0
    scored = [row for row in rows if row["share"] > 0]
    lowest = min(scored, key=lambda row: row["score"]) if scored else None
    return FitWorking(rows=rows, total=round(total, 1), adjusted=adjusted, lowest=lowest)

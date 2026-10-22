"""Versioned experimental rating, for offline comparison only.

This preserves Opus's 2026-10-21 formula, including its sparse-evidence defects,
so the experiment can be reproduced. Coverage is descriptive, not confidence or
permission to promote the candidate. Never use it as the live overall_rating.
Any change to weights, rounding, penalties or missing-value handling needs a new
policy version and a separate comparison; historical ratings are not backfilled.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final, Literal

SHADOW_RATING_POLICY_VERSION: Final = "opus_visual_weighted_2026_10_21_v1"

_CONDITION: Final = {"excellent": 5.0, "good": 3.5, "fair": 3.0, "poor": 1.5}
_FINISH: Final = {"modern": 5.0, "decent": 3.0, "dated": 2.0}
_LIGHT: Final = {"excellent": 5.0, "good": 4.0, "fair": 3.0, "poor": 1.5}
_WEIGHTS: Final = {"condition": 0.3, "kitchen": 0.2, "bathroom": 0.15, "light": 0.2, "space": 0.15}
_CONCERN_PENALTY: Final = {"moderate": 0.5, "minor": 0.25}


@dataclass(frozen=True)
class ShadowRating:
    policy_version: str
    candidate_rating: int
    coverage: Literal["none", "partial", "complete"]
    observed_weight_percent: int
    observed_components: tuple[str, ...]
    missing_components: tuple[str, ...]


def _section(visual: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    value = visual.get(name)
    return value if isinstance(value, Mapping) else {}


def _score(table: Mapping[str, float], value: Any) -> float | None:
    return table.get(value) if isinstance(value, str) else None


def compute_shadow_rating(visual: Mapping[str, Any]) -> ShadowRating:
    """Describe a candidate without mutating observations or accepting a model rating.

    Unknown components drop out and weights renormalise exactly as in the
    measured proposal. Consequently no evidence yields 3, and one modern kitchen
    yields 5. Those are diagnostic counterexamples, not validated recommendations.
    Callers comparing alerts must first check the original assessment is eligible
    for scoring; this function must never repair a failed or unrated assessment.
    """
    spacious = _section(visual, "space").get("is_spacious_enough")
    parts = {
        "condition": _score(_CONDITION, _section(visual, "condition").get("overall_condition")),
        "kitchen": _score(_FINISH, _section(visual, "kitchen").get("overall_quality")),
        "bathroom": _score(_FINISH, _section(visual, "bathroom").get("overall_condition")),
        "light": _score(_LIGHT, _section(visual, "light_space").get("natural_light")),
        "space": (4.5 if spacious else 2.5) if isinstance(spacious, bool) else None,
    }
    known = {name: score for name, score in parts.items() if score is not None}
    weight = sum(_WEIGHTS[name] for name in known)
    score = sum(_WEIGHTS[name] * value for name, value in known.items()) / weight if known else 3.0
    severity = visual.get("concern_severity")
    score -= _CONCERN_PENALTY.get(severity, 0) if isinstance(severity, str) else 0
    condition = _section(visual, "condition")
    if "yes" in (condition.get("has_visible_damp"), condition.get("has_visible_mold")):
        score = min(score, 2)
    if severity == "serious":
        score = 1
    return ShadowRating(
        policy_version=SHADOW_RATING_POLICY_VERSION,
        candidate_rating=max(1, min(5, int(score + 0.5))),
        coverage="complete" if len(known) == len(parts) else "partial" if known else "none",
        observed_weight_percent=round(weight * 100),
        observed_components=tuple(known),
        missing_components=tuple(name for name in parts if name not in known),
    )

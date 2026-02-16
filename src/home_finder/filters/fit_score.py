"""Marcel Fit Score and Lifestyle Quick-Glance Icons.

Computes a personalised compatibility score (0-100) and lifestyle icon states
from quality analysis data, weighted to Marcel's priorities:
- WFH full-time (needs office separation)
- Music events / social hosting
- Sound insulation
- Cooking (gas/induction preference)
- Creative/cool spaces
"""

from __future__ import annotations

from typing import Any, Final, TypedDict

# ── Highlight/Lowlight signal scoring (used by _score_vibe cluster 6) ────────

_HIGHLIGHT_SCORES: Final[dict[str, float]] = {
    "Period features": 10,
    "Open-plan layout": 6,
    "Floor-to-ceiling windows": 8,
    "Spacious living room": 4,
    "Canal views": 6,
    "Park views": 4,
    "Roof terrace": 6,
    "Recently refurbished": 3,
}

_LOWLIGHT_SCORES: Final[dict[str, float]] = {
    "Needs updating": -8,
    "Compact living room": -4,
    "Small living room": -4,
}

# ── Dimension labels (for UI display) ─────────────────────────────────────────
_DIMENSION_LABELS: dict[str, str] = {
    "workspace": "Workspace",
    "hosting": "Hosting",
    "sound": "Sound",
    "kitchen": "Kitchen",
    "vibe": "Vibe",
    "condition": "Condition",
}


class FitFactor(TypedDict):
    """A single signal contributing to a fit dimension score."""

    label: str  # "Modern kitchen", "Gas hob", "Dishwasher"
    state: str  # "earned", "missed", "unknown"


class FitDimension(TypedDict):
    """Per-dimension breakdown for Marcel Fit Score UI."""

    key: str  # "workspace", "hosting", etc.
    label: str  # "Workspace", "Hosting", etc.
    score: int  # 0-100 (the dimension's raw score)
    weight: int  # 25, 20, 15, etc.
    confidence: float  # 0.0-1.0
    factors: list[FitFactor]  # inline factor breakdown


# ── Dimension weights ──────────────────────────────────────────────────────────
WEIGHTS: dict[str, float] = {
    "workspace": 25,
    "hosting": 20,
    "sound": 15,
    "kitchen": 15,
    "vibe": 15,
    "condition": 10,
}


class _DimensionResult:
    """Score (0-100), confidence (0.0-1.0), and factor breakdown for a single dimension."""

    __slots__ = ("confidence", "factors", "score")

    def __init__(
        self,
        score: float,
        confidence: float,
        factors: list[FitFactor] | None = None,
    ) -> None:
        self.score = max(0.0, min(100.0, score))
        self.confidence = max(0.0, min(1.0, confidence))
        self.factors: list[FitFactor] = factors or []


# ── Dimension scorers ─────────────────────────────────────────────────────────


def _score_workspace(analysis: dict[str, Any], bedrooms: int) -> _DimensionResult:
    score = 0.0
    signals = 0
    factors: list[FitFactor] = []

    if bedrooms >= 2:
        score += 35
        signals += 1
        factors.append({"label": "2+ bedrooms", "state": "earned"})
    elif bedrooms == 0:
        # Studio — very poor for WFH separation
        signals += 1
        factors.append({"label": "Studio (no separation)", "state": "missed"})
    else:
        factors.append({"label": "1 bedroom", "state": "missed"})

    bedroom = analysis.get("bedroom") or {}
    can_fit_desk = bedroom.get("can_fit_desk")
    if can_fit_desk == "yes":
        score += 25
        signals += 1
        factors.append({"label": "Can fit desk", "state": "earned"})
    elif can_fit_desk == "no":
        signals += 1
        factors.append({"label": "No desk space", "state": "missed"})
    else:
        factors.append({"label": "Desk space", "state": "unknown"})

    office_sep = bedroom.get("office_separation")
    if office_sep == "dedicated_room":
        score += 40
        signals += 1
        factors.append({"label": "Dedicated office room", "state": "earned"})
    elif office_sep == "separate_area":
        score += 25
        signals += 1
        factors.append({"label": "Separate work area", "state": "earned"})
    elif office_sep == "shared_space":
        score += 10
        signals += 1
        factors.append({"label": "Shared space", "state": "missed"})
    elif office_sep == "none":
        signals += 1
        factors.append({"label": "No workspace", "state": "missed"})

    space = analysis.get("space") or {}
    is_spacious = space.get("is_spacious_enough")
    if is_spacious is True and bedrooms <= 1:
        score += 40
        signals += 1
        factors.append({"label": "Spacious layout", "state": "earned"})
    elif is_spacious is not None:
        signals += 1

    listing_ext = analysis.get("listing_extraction") or {}
    broadband = listing_ext.get("broadband_type")
    if broadband == "fttp":
        score += 15
        signals += 1
        factors.append({"label": "Full fibre (FTTP)", "state": "earned"})
    elif broadband == "cable":
        score += 10
        signals += 1
        factors.append({"label": "Cable broadband", "state": "earned"})
    elif broadband == "fttc":
        score += 8
        signals += 1
        factors.append({"label": "Superfast (FTTC)", "state": "earned"})
    else:
        factors.append({"label": "Broadband", "state": "unknown"})

    confidence = min(1.0, signals * 0.5) if signals > 0 else 0.0
    return _DimensionResult(score, confidence, factors)


def _score_hosting(analysis: dict[str, Any], _bedrooms: int) -> _DimensionResult:
    score = 0.0
    signals = 0
    factors: list[FitFactor] = []

    space = analysis.get("space") or {}
    is_spacious = space.get("is_spacious_enough")
    if is_spacious is True:
        score += 25
        signals += 1
        factors.append({"label": "Spacious", "state": "earned"})
    elif is_spacious is False:
        signals += 1
        factors.append({"label": "Not spacious", "state": "missed"})

    living_sqm = space.get("living_room_sqm")
    if isinstance(living_sqm, (int, float)) and living_sqm > 0:
        # 0 at <=10sqm, 30 at >=25sqm, graduated between
        sqm_score = max(0.0, min(30.0, (living_sqm - 10) * (30 / 15)))
        score += sqm_score
        signals += 1
        if sqm_score >= 20:
            factors.append(
                {"label": f"Large living room (~{int(living_sqm)}sqm)", "state": "earned"}
            )
        elif sqm_score > 0:
            factors.append({"label": f"Living room ~{int(living_sqm)}sqm", "state": "earned"})
        else:
            factors.append(
                {"label": f"Small living room (~{int(living_sqm)}sqm)", "state": "missed"}
            )

    light_space = analysis.get("light_space") or {}
    feels_spacious = light_space.get("feels_spacious")
    if feels_spacious is True:
        score += 10
        signals += 1
        factors.append({"label": "Feels spacious", "state": "earned"})
    elif feels_spacious is False:
        signals += 1
        factors.append({"label": "Feels cramped", "state": "missed"})

    outdoor = analysis.get("outdoor_space") or {}
    has_any_outdoor = any(
        outdoor.get(k) is True
        for k in ("has_balcony", "has_garden", "has_terrace", "has_shared_garden")
    )
    has_outdoor_data = any(
        k in outdoor for k in ("has_balcony", "has_garden", "has_terrace", "has_shared_garden")
    )
    if has_any_outdoor:
        score += 15
        signals += 1
        factors.append({"label": "Outdoor space", "state": "earned"})
    elif has_outdoor_data:
        signals += 1
        factors.append({"label": "No outdoor space", "state": "missed"})

    hosting_layout = space.get("hosting_layout")
    if hosting_layout == "excellent":
        score += 25
        signals += 1
        factors.append({"label": "Excellent layout", "state": "earned"})
    elif hosting_layout == "good":
        score += 15
        signals += 1
        factors.append({"label": "Good layout", "state": "earned"})
    elif hosting_layout and hosting_layout not in ("unknown", None):
        signals += 1
        factors.append({"label": f"{hosting_layout.title()} layout", "state": "missed"})

    flooring = analysis.get("flooring_noise") or {}
    hosting_noise = flooring.get("hosting_noise_risk")
    if hosting_noise == "low":
        score += 10
        signals += 1
        factors.append({"label": "Low noise risk", "state": "earned"})

    area_tolerance = analysis.get("_area_hosting_tolerance")
    if area_tolerance == "high":
        score += 10
        signals += 1
        factors.append({"label": "Tolerant area", "state": "earned"})
    elif area_tolerance == "low":
        score -= 10
        signals += 1
        factors.append({"label": "Noise-sensitive area", "state": "missed"})
    elif area_tolerance == "moderate":
        signals += 1  # neutral score impact

    confidence = min(1.0, signals * 0.4) if signals > 0 else 0.0
    return _DimensionResult(score, confidence, factors)


def _score_sound(analysis: dict[str, Any], _bedrooms: int) -> _DimensionResult:
    score = 0.0
    signals = 0
    factors: list[FitFactor] = []

    flooring = analysis.get("flooring_noise") or {}

    construction = flooring.get("building_construction")
    if construction in ("solid_brick", "concrete"):
        score += 35
        signals += 1
        label = "Solid brick" if construction == "solid_brick" else "Concrete"
        factors.append({"label": label, "state": "earned"})
    elif construction and construction != "unknown":
        signals += 1
        factors.append({"label": construction.replace("_", " ").title(), "state": "missed"})
    else:
        factors.append({"label": "Construction type", "state": "unknown"})

    glazing = flooring.get("has_double_glazing")
    if glazing == "yes":
        score += 25
        signals += 1
        factors.append({"label": "Double glazed", "state": "earned"})
    elif glazing == "no":
        signals += 1
        factors.append({"label": "No double glazing", "state": "missed"})
    else:
        factors.append({"label": "Double glazing", "state": "unknown"})

    light_space = analysis.get("light_space") or {}
    floor_level = light_space.get("floor_level")
    if floor_level == "top":
        score += 20
        signals += 1
        factors.append({"label": "Top floor", "state": "earned"})
    elif floor_level and floor_level not in ("unknown", None):
        signals += 1
        factors.append({"label": f"{floor_level.title()} floor", "state": "missed"})

    noise_indicators = flooring.get("noise_indicators")
    if isinstance(noise_indicators, list):
        if len(noise_indicators) == 0:
            score += 10
            signals += 1
            factors.append({"label": "No noise indicators", "state": "earned"})
        else:
            signals += 1
            noise_text = ", ".join(noise_indicators[:2])
            factors.append({"label": f"Noise: {noise_text}", "state": "missed"})

    listing_ext = analysis.get("listing_extraction") or {}
    prop_type = listing_ext.get("property_type")
    if prop_type == "warehouse":
        score += 10
        signals += 1
        factors.append({"label": "Warehouse building", "state": "earned"})

    hosting_noise = flooring.get("hosting_noise_risk")
    if hosting_noise == "low":
        score += 20
        signals += 1
        factors.append({"label": "Low hosting noise risk", "state": "earned"})
    elif hosting_noise == "moderate":
        score += 8
        signals += 1
        factors.append({"label": "Moderate hosting noise risk", "state": "earned"})
    elif hosting_noise and hosting_noise not in ("unknown", None):
        signals += 1
        factors.append({"label": "High hosting noise risk", "state": "missed"})

    confidence = min(1.0, signals * 0.35) if signals > 0 else 0.0
    return _DimensionResult(score, confidence, factors)


def _score_kitchen(analysis: dict[str, Any], _bedrooms: int) -> _DimensionResult:
    score = 0.0
    signals = 0
    factors: list[FitFactor] = []

    kitchen = analysis.get("kitchen") or {}

    hob = kitchen.get("hob_type")
    if hob in ("gas", "induction"):
        score += 35
        signals += 1
        factors.append({"label": f"{hob.title()} hob", "state": "earned"})
    elif hob and hob not in ("unknown", None):
        signals += 1
        factors.append({"label": f"{hob.title()} hob (not gas/induction)", "state": "missed"})
    else:
        factors.append({"label": "Gas/induction hob", "state": "unknown"})

    quality = kitchen.get("overall_quality")
    if quality == "modern":
        score += 30
        signals += 1
        factors.append({"label": "Modern kitchen", "state": "earned"})
    elif quality == "decent":
        score += 15
        signals += 1
        factors.append({"label": "Decent kitchen", "state": "earned"})
    elif quality and quality != "unknown":
        signals += 1
        factors.append({"label": f"{quality.title()} kitchen", "state": "missed"})
    else:
        factors.append({"label": "Kitchen quality", "state": "unknown"})

    dishwasher = kitchen.get("has_dishwasher")
    if dishwasher == "yes":
        score += 15
        signals += 1
        factors.append({"label": "Dishwasher", "state": "earned"})
    elif dishwasher == "no":
        signals += 1
        factors.append({"label": "No dishwasher", "state": "missed"})
    else:
        factors.append({"label": "Dishwasher", "state": "unknown"})

    washing = kitchen.get("has_washing_machine")
    if washing == "yes":
        score += 10
        signals += 1
        factors.append({"label": "Washing machine", "state": "earned"})
    elif washing == "no":
        signals += 1
        factors.append({"label": "No washing machine", "state": "missed"})
    else:
        factors.append({"label": "Washing machine", "state": "unknown"})

    confidence = min(1.0, signals * 0.4) if signals > 0 else 0.0
    return _DimensionResult(score, confidence, factors)


def _score_vibe(analysis: dict[str, Any], _bedrooms: int) -> _DimensionResult:
    """Multi-cluster vibe scorer using rich signals from Claude's vision analysis.

    Six signal clusters contribute raw points, then clamped to 0-100.
    Confidence scales with the number of clusters that have at least one signal.
    """
    clusters_with_signal: set[str] = set()
    factors: list[FitFactor] = []

    # === Cluster 1: Architectural Character (0-35 raw) ===
    cluster1 = 0.0
    listing_ext = analysis.get("listing_extraction") or {}
    prop_type = listing_ext.get("property_type")
    if prop_type == "warehouse":
        cluster1 += 35
        clusters_with_signal.add("architecture")
        factors.append({"label": "Warehouse", "state": "earned"})
    elif prop_type == "period_conversion":
        cluster1 += 28
        clusters_with_signal.add("architecture")
        factors.append({"label": "Period conversion", "state": "earned"})
    elif prop_type in ("victorian", "edwardian", "georgian"):
        cluster1 += 20
        clusters_with_signal.add("architecture")
        factors.append({"label": prop_type.title(), "state": "earned"})

    # === Cluster 2: Space & Light Feel (0-30 raw, can go negative) ===
    cluster2 = 0.0
    light_space = analysis.get("light_space") or {}
    natural_light = light_space.get("natural_light")
    if natural_light == "excellent":
        cluster2 += 15
        clusters_with_signal.add("light")
        factors.append({"label": "Excellent light", "state": "earned"})
    elif natural_light == "good":
        cluster2 += 8
        clusters_with_signal.add("light")
        factors.append({"label": "Good light", "state": "earned"})

    window_sizes = light_space.get("window_sizes")
    if window_sizes == "large":
        cluster2 += 10
        clusters_with_signal.add("light")
        factors.append({"label": "Large windows", "state": "earned"})

    ceiling = light_space.get("ceiling_height")
    if ceiling == "high":
        cluster2 += 10
        clusters_with_signal.add("light")
        factors.append({"label": "High ceilings", "state": "earned"})
    elif ceiling == "low":
        cluster2 -= 5
        clusters_with_signal.add("light")
        factors.append({"label": "Low ceilings", "state": "missed"})

    feels_spacious = light_space.get("feels_spacious")
    if feels_spacious is True:
        cluster2 += 5
        clusters_with_signal.add("light")

    # === Cluster 3: Material Character (0-15 raw, can go negative) ===
    cluster3 = 0.0
    flooring = analysis.get("flooring_noise") or {}
    primary_flooring = flooring.get("primary_flooring")
    if primary_flooring == "hardwood":
        cluster3 += 12
        clusters_with_signal.add("material")
        factors.append({"label": "Hardwood floors", "state": "earned"})
    elif primary_flooring == "tile":
        cluster3 += 6
        clusters_with_signal.add("material")
        factors.append({"label": "Tile floors", "state": "earned"})
    elif primary_flooring == "mixed":
        cluster3 += 4
        clusters_with_signal.add("material")

    construction = flooring.get("building_construction")
    if construction == "solid_brick":
        cluster3 += 8
        clusters_with_signal.add("material")
        factors.append({"label": "Solid brick", "state": "earned"})
    elif construction == "concrete":
        cluster3 += 4
        clusters_with_signal.add("material")
    elif construction == "timber_frame":
        cluster3 -= 3
        clusters_with_signal.add("material")
        factors.append({"label": "Timber frame", "state": "missed"})

    # === Cluster 4: Position & Outlook (0-12 raw, can go negative) ===
    cluster4 = 0.0
    floor_level = light_space.get("floor_level")
    if floor_level == "top":
        cluster4 += 10
        clusters_with_signal.add("position")
        factors.append({"label": "Top floor", "state": "earned"})
    elif floor_level == "upper":
        cluster4 += 6
        clusters_with_signal.add("position")
    elif floor_level == "ground":
        cluster4 += 2
        clusters_with_signal.add("position")
    elif floor_level == "basement":
        cluster4 -= 5
        clusters_with_signal.add("position")
        factors.append({"label": "Basement", "state": "missed"})

    # View highlights (exact enum value match)
    highlights = analysis.get("highlights") or []
    if isinstance(highlights, list):
        view_bonus = 0.0
        if "Canal views" in highlights:
            view_bonus += 6
            factors.append({"label": "Canal views", "state": "earned"})
        if "Park views" in highlights:
            view_bonus += 6
            factors.append({"label": "Park views", "state": "earned"})
        if view_bonus > 0:
            cluster4 += min(8.0, view_bonus)
            clusters_with_signal.add("position")

    # === Cluster 5: Layout Flow (0-12 raw, can go negative) ===
    cluster5 = 0.0
    space = analysis.get("space") or {}
    hosting_layout = space.get("hosting_layout")
    if hosting_layout == "excellent":
        cluster5 += 12
        clusters_with_signal.add("layout")
        factors.append({"label": "Excellent flow", "state": "earned"})
    elif hosting_layout == "good":
        cluster5 += 8
        clusters_with_signal.add("layout")
        factors.append({"label": "Good flow", "state": "earned"})
    elif hosting_layout == "awkward":
        clusters_with_signal.add("layout")
        factors.append({"label": "Awkward layout", "state": "missed"})
    elif hosting_layout == "poor":
        cluster5 -= 3
        clusters_with_signal.add("layout")
        factors.append({"label": "Poor layout", "state": "missed"})

    # === Cluster 6: Highlight/Lowlight Signals (capped at 20 raw) ===
    cluster6 = 0.0
    if isinstance(highlights, list):
        for h in highlights:
            if isinstance(h, str) and h in _HIGHLIGHT_SCORES:
                cluster6 += _HIGHLIGHT_SCORES[h]
                clusters_with_signal.add("highlights")

    lowlights = analysis.get("lowlights") or []
    if isinstance(lowlights, list):
        for lowlight in lowlights:
            if isinstance(lowlight, str) and lowlight in _LOWLIGHT_SCORES:
                cluster6 += _LOWLIGHT_SCORES[lowlight]
                clusters_with_signal.add("highlights")
                factors.append({"label": lowlight, "state": "missed"})

    cluster6 = min(20.0, cluster6)

    # === Final calculation ===
    raw_total = cluster1 + cluster2 + cluster3 + cluster4 + cluster5 + cluster6
    score = max(0.0, min(100.0, raw_total))

    # Confidence: count of clusters with at least one signal
    n_clusters = len(clusters_with_signal)
    if n_clusters == 0:
        confidence = 0.0
    elif n_clusters == 1:
        confidence = 0.25
    elif n_clusters == 2:
        confidence = 0.5
    elif n_clusters == 3:
        confidence = 0.7
    else:
        confidence = 1.0

    return _DimensionResult(score, confidence, factors)


def _score_condition(analysis: dict[str, Any], _bedrooms: int) -> _DimensionResult:
    signals = 0
    factors: list[FitFactor] = []

    overall = analysis.get("overall_rating")
    if isinstance(overall, (int, float)) and 1 <= overall <= 5:
        # Map 1→0, 5→100
        score = (overall - 1) * 25
        signals += 1
        if overall >= 3:
            factors.append({"label": f"Rated {overall}/5", "state": "earned"})
        else:
            factors.append({"label": f"Rated {overall}/5", "state": "missed"})
    else:
        score = 50.0  # neutral default, but no signal

    # Condition concern penalties
    concern = analysis.get("condition_concerns")
    severity = analysis.get("concern_severity")
    if concern:
        if severity == "serious":
            score -= 30
            factors.append({"label": "Serious concerns", "state": "missed"})
        elif severity == "moderate":
            score -= 15
            factors.append({"label": "Moderate concerns", "state": "missed"})
        elif severity == "minor":
            score -= 5
            factors.append({"label": "Minor concerns", "state": "missed"})
        signals += 1
    elif concern is not None:
        factors.append({"label": "No concerns", "state": "earned"})

    # Value rating bonus
    value = analysis.get("value") or {}
    val_rating = value.get("quality_adjusted_rating") or value.get("rating")
    if val_rating == "excellent":
        score += 10
        signals += 1
        factors.append({"label": "Excellent value", "state": "earned"})
    elif val_rating == "good":
        score += 5
        signals += 1
        factors.append({"label": "Good value", "state": "earned"})
    elif val_rating and val_rating != "unknown":
        signals += 1
        factors.append({"label": f"{val_rating.title()} value", "state": "missed"})

    confidence = min(1.0, signals * 0.5) if signals > 0 else 0.0
    return _DimensionResult(max(0.0, score), confidence, factors)


# ── Dimension registry ────────────────────────────────────────────────────────
_SCORERS: dict[str, Any] = {
    "workspace": _score_workspace,
    "hosting": _score_hosting,
    "sound": _score_sound,
    "kitchen": _score_kitchen,
    "vibe": _score_vibe,
    "condition": _score_condition,
}


# ── Shared dimension computation ─────────────────────────────────────────────


def _compute_dimension_results(
    analysis: dict[str, Any], bedrooms: int
) -> dict[str, _DimensionResult]:
    """Run all dimension scorers once and return results keyed by dimension name."""
    return {dim: _SCORERS[dim](analysis, bedrooms) for dim in WEIGHTS}


# ── Main entry points ─────────────────────────────────────────────────────────


def compute_fit_score(analysis: dict[str, Any] | None, bedrooms: int) -> int | None:
    """Compute Marcel's personalised fit score (0-100).

    Returns None if no analysis data or all dimensions have zero confidence.
    """
    if not analysis:
        return None

    results = _compute_dimension_results(analysis, bedrooms)

    weighted_sum = 0.0
    weight_confidence_sum = 0.0

    for dim, weight in WEIGHTS.items():
        result = results[dim]
        weighted_sum += result.score * weight * result.confidence
        weight_confidence_sum += weight * result.confidence

    if weight_confidence_sum == 0:
        return None

    return round(weighted_sum / weight_confidence_sum)


def compute_fit_breakdown(
    analysis: dict[str, Any] | None, bedrooms: int
) -> list[FitDimension] | None:
    """Compute per-dimension breakdown for Marcel Fit Score UI.

    Returns None if no analysis data or all dimensions have zero confidence.
    """
    if not analysis:
        return None

    results = _compute_dimension_results(analysis, bedrooms)
    dimensions: list[FitDimension] = []
    any_confidence = False

    for dim, weight in WEIGHTS.items():
        result = results[dim]
        if result.confidence > 0:
            any_confidence = True
        dimensions.append(
            FitDimension(
                key=dim,
                label=_DIMENSION_LABELS[dim],
                score=round(result.score),
                weight=int(weight),
                confidence=round(result.confidence, 2),
                factors=result.factors,
            )
        )

    if not any_confidence:
        return None

    return dimensions


def compute_fit_score_and_breakdown(
    analysis: dict[str, Any] | None, bedrooms: int
) -> tuple[int | None, list[FitDimension] | None]:
    """Compute both fit score and breakdown in a single pass over all scorers.

    Use this instead of calling compute_fit_score + compute_fit_breakdown separately
    to avoid scoring each dimension twice.

    Returns:
        Tuple of (fit_score, breakdown). Either or both may be None if no analysis
        data or all dimensions have zero confidence.
    """
    if not analysis:
        return None, None

    results = _compute_dimension_results(analysis, bedrooms)

    weighted_sum = 0.0
    weight_confidence_sum = 0.0
    dimensions: list[FitDimension] = []
    any_confidence = False

    for dim, weight in WEIGHTS.items():
        result = results[dim]
        weighted_sum += result.score * weight * result.confidence
        weight_confidence_sum += weight * result.confidence
        if result.confidence > 0:
            any_confidence = True
        dimensions.append(
            FitDimension(
                key=dim,
                label=_DIMENSION_LABELS[dim],
                score=round(result.score),
                weight=int(weight),
                confidence=round(result.confidence, 2),
                factors=result.factors,
            )
        )

    if not any_confidence:
        return None, None

    score = round(weighted_sum / weight_confidence_sum)
    return score, dimensions


# ── Lifestyle Icons ────────────────────────────────────────────────────────────


class LifestyleIcon(TypedDict):
    state: str  # "good", "neutral", "concern"
    tooltip: str


def compute_lifestyle_icons(
    analysis: dict[str, Any] | None, bedrooms: int
) -> dict[str, LifestyleIcon] | None:
    """Compute lifestyle quick-glance icon states.

    Returns dict with keys: workspace, hosting, kitchen, vibe, space, internet.
    Each value has 'state' ("good"/"neutral"/"concern") and 'tooltip' string.
    Returns None if no analysis data.
    """
    if not analysis:
        return None

    return {
        "workspace": _icon_workspace(analysis, bedrooms),
        "hosting": _icon_hosting(analysis, bedrooms),
        "kitchen": _icon_kitchen(analysis),
        "vibe": _icon_vibe(analysis),
        "space": _icon_space(analysis),
        "internet": _icon_internet(analysis),
    }


def _icon_workspace(analysis: dict[str, Any], bedrooms: int) -> LifestyleIcon:
    bedroom = analysis.get("bedroom") or {}
    space = analysis.get("space") or {}
    can_desk = bedroom.get("can_fit_desk")
    is_spacious = space.get("is_spacious_enough")
    office_sep = bedroom.get("office_separation")

    # Prefer office_separation when available
    if office_sep and office_sep != "unknown":
        if office_sep == "dedicated_room":
            return {"state": "good", "tooltip": "Dedicated office room"}
        if office_sep == "separate_area":
            return {"state": "good", "tooltip": "Separate work area"}
        if office_sep == "shared_space":
            if bedrooms >= 2:
                return {"state": "neutral", "tooltip": "2-bed but office in shared space"}
            return {"state": "concern", "tooltip": "Desk in shared space — no separation"}
        if office_sep == "none":
            return {"state": "concern", "tooltip": "No viable workspace"}

    # Fall back to existing bedroom count / can_fit_desk logic
    if bedrooms >= 2:
        return {"state": "good", "tooltip": "2+ beds — dedicated office possible"}
    if can_desk == "yes":
        return {"state": "good", "tooltip": "Can fit desk in bedroom"}
    if bedrooms == 1 and is_spacious is True:
        return {"state": "good", "tooltip": "Spacious 1-bed — desk space likely"}
    if bedrooms == 0:
        return {"state": "concern", "tooltip": "Studio — no office separation"}
    if bedrooms == 1 and is_spacious is False:
        return {"state": "concern", "tooltip": "Compact 1-bed — limited desk space"}
    return {"state": "neutral", "tooltip": "Workspace potential unclear"}


def _icon_hosting(analysis: dict[str, Any], bedrooms: int) -> LifestyleIcon:
    space = analysis.get("space") or {}
    flooring = analysis.get("flooring_noise") or {}
    is_spacious = space.get("is_spacious_enough")
    construction = flooring.get("building_construction")
    glazing = flooring.get("has_double_glazing")

    good_sound = construction in ("solid_brick", "concrete") or glazing == "yes"

    if is_spacious is True and good_sound:
        return {"state": "good", "tooltip": "Spacious + good sound insulation"}
    if is_spacious is False:
        noise_indicators = flooring.get("noise_indicators") or []
        if isinstance(noise_indicators, list) and len(noise_indicators) > 0:
            return {"state": "concern", "tooltip": "Compact space + noise concerns"}
        return {"state": "concern", "tooltip": "Compact — limited hosting space"}
    if is_spacious is True:
        return {"state": "neutral", "tooltip": "Spacious but sound insulation unknown"}
    return {"state": "neutral", "tooltip": "Hosting suitability unclear"}


def _icon_kitchen(analysis: dict[str, Any]) -> LifestyleIcon:
    kitchen = analysis.get("kitchen") or {}
    hob = kitchen.get("hob_type")
    quality = kitchen.get("overall_quality")

    good_hob = hob in ("gas", "induction")
    good_quality = quality in ("modern", "decent")

    if good_hob and good_quality:
        assert hob is not None and quality is not None  # guarded by good_hob/good_quality
        return {"state": "good", "tooltip": f"{hob.title()} hob, {quality} kitchen"}
    if good_hob:
        assert hob is not None  # guarded by good_hob
        return {"state": "good", "tooltip": f"{hob.title()} hob"}
    if hob == "electric":
        if quality == "dated":
            return {"state": "concern", "tooltip": "Electric hob, dated kitchen"}
        return {"state": "concern", "tooltip": "Electric hob"}
    if quality == "dated":
        return {"state": "concern", "tooltip": "Dated kitchen"}
    if good_quality:
        assert quality is not None  # guarded by good_quality
        return {"state": "neutral", "tooltip": f"{quality.title()} kitchen, hob type unknown"}
    return {"state": "neutral", "tooltip": "Kitchen details unclear"}


def _icon_vibe(analysis: dict[str, Any]) -> LifestyleIcon:
    listing_ext = analysis.get("listing_extraction") or {}
    prop_type = listing_ext.get("property_type")
    highlights = analysis.get("highlights") or []

    cool_types = {"warehouse", "period_conversion", "victorian", "edwardian", "georgian"}
    has_character_highlights = False
    if isinstance(highlights, list):
        for h in highlights:
            if isinstance(h, str):
                hl = h.lower()
                if any(
                    kw in hl
                    for kw in (
                        "period",
                        "character",
                        "original",
                        "warehouse",
                        "conversion",
                        "high ceiling",
                        "exposed",
                    )
                ):
                    has_character_highlights = True
                    break

    if prop_type in cool_types:
        label = prop_type.replace("_", " ").title()
        return {"state": "good", "tooltip": f"{label} — character property"}
    if has_character_highlights:
        return {"state": "good", "tooltip": "Character features noted"}
    if prop_type in ("new_build", "purpose_built"):
        return {"state": "neutral", "tooltip": prop_type.replace("_", " ").title()}
    return {"state": "neutral", "tooltip": "Style unclear"}


def _icon_space(analysis: dict[str, Any]) -> LifestyleIcon:
    space = analysis.get("space") or {}
    outdoor = analysis.get("outdoor_space") or {}
    is_spacious = space.get("is_spacious_enough")

    has_outdoor = any(
        outdoor.get(k) is True
        for k in ("has_balcony", "has_garden", "has_terrace", "has_shared_garden")
    )

    if is_spacious is True and has_outdoor:
        return {"state": "good", "tooltip": "Spacious with outdoor space"}
    if is_spacious is True:
        return {"state": "good", "tooltip": "Spacious layout"}
    if is_spacious is False:
        return {"state": "concern", "tooltip": "Not spacious enough"}
    if has_outdoor:
        return {"state": "neutral", "tooltip": "Has outdoor space, size unclear"}
    return {"state": "neutral", "tooltip": "Space unclear"}


def _icon_internet(analysis: dict[str, Any]) -> LifestyleIcon:
    listing_ext = analysis.get("listing_extraction") or {}
    broadband = listing_ext.get("broadband_type")

    if broadband == "fttp":
        return {"state": "good", "tooltip": "Full fibre (FTTP) available"}
    if broadband == "fttc":
        return {"state": "neutral", "tooltip": "Superfast broadband (FTTC)"}
    if broadband == "cable":
        return {"state": "neutral", "tooltip": "Cable broadband"}
    if broadband == "standard":
        return {"state": "concern", "tooltip": "Basic broadband only"}
    return {"state": "neutral", "tooltip": "Broadband not mentioned"}

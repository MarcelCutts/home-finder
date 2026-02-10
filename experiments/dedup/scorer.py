"""Parameterized scorer for property deduplication.

Wraps signal functions with configurable weights and thresholds.
"""

from dataclasses import dataclass, field

from signals import SignalBundle, compute_all_signals, PropertyDict


@dataclass
class ScorerConfig:
    """Weights and thresholds for the dedup scorer.

    Each weight is multiplied by the signal's value (0-1).
    The match_threshold and min_signals gate the final decision.
    """

    # Signal weights
    full_postcode: float = 40.0
    outcode: float = 10.0
    coordinates: float = 40.0
    street_name: float = 20.0
    price: float = 15.0
    fuzzy_address: float = 0.0  # New signal — start disabled, calibrate with labels
    address_number: float = 0.0
    title_similarity: float = 0.0
    description_tfidf: float = 15.0
    description_semantic: float = 0.0  # Start disabled — calibrate with labels
    feature_overlap: float = 0.0
    gallery_images: float = 40.0  # Graduated: 1 match=+20, 2=+34, 3+=+40
    gallery_embeddings: float = 25.0  # SSCD embeddings — 2.7x lift in eval

    # Decision thresholds
    match_threshold: float = 70.0
    min_signals: int = 2

    # Signal-specific thresholds (minimum value for a signal to "count")
    fuzzy_address_threshold: float = 0.75
    title_similarity_threshold: float = 0.70
    description_tfidf_threshold: float = 0.50
    description_semantic_threshold: float = 0.60
    feature_overlap_threshold: float = 0.50

    def to_dict(self) -> dict:
        return {
            "weights": {
                "full_postcode": self.full_postcode,
                "outcode": self.outcode,
                "coordinates": self.coordinates,
                "street_name": self.street_name,
                "price": self.price,
                "fuzzy_address": self.fuzzy_address,
                "address_number": self.address_number,
                "title_similarity": self.title_similarity,
                "description_tfidf": self.description_tfidf,
                "description_semantic": self.description_semantic,
                "feature_overlap": self.feature_overlap,
                "gallery_images": self.gallery_images,
                "gallery_embeddings": self.gallery_embeddings,
            },
            "thresholds": {
                "match_threshold": self.match_threshold,
                "min_signals": self.min_signals,
                "fuzzy_address_threshold": self.fuzzy_address_threshold,
                "title_similarity_threshold": self.title_similarity_threshold,
                "description_tfidf_threshold": self.description_tfidf_threshold,
                "description_semantic_threshold": self.description_semantic_threshold,
                "feature_overlap_threshold": self.feature_overlap_threshold,
            },
        }


# Production baseline — mirrors current constants in deduplication.py
PRODUCTION_CONFIG = ScorerConfig()


@dataclass
class ScorerResult:
    """Result of scoring a property pair."""

    score: float
    signal_count: int
    is_match: bool
    breakdown: dict[str, float]  # signal_name -> weighted contribution
    bundle: SignalBundle  # Raw signal results


def score_pair(
    a: PropertyDict,
    b: PropertyDict,
    config: ScorerConfig = PRODUCTION_CONFIG,
    bundle: SignalBundle | None = None,
) -> ScorerResult:
    """Score a property pair using the given config.

    Args:
        a: First property dict.
        b: Second property dict.
        config: Scorer configuration with weights and thresholds.
        bundle: Pre-computed signal bundle (optional, avoids recomputation).

    Returns:
        ScorerResult with score, signal count, match decision, and breakdown.
    """
    # Gate: bedrooms must match
    if a.get("bedrooms") != b.get("bedrooms"):
        return ScorerResult(
            score=0.0,
            signal_count=0,
            is_match=False,
            breakdown={},
            bundle=SignalBundle(),
        )

    if bundle is None:
        bundle = compute_all_signals(a, b)

    weight_map = {
        "full_postcode": config.full_postcode,
        "outcode": config.outcode,
        "coordinates": config.coordinates,
        "street_name": config.street_name,
        "price": config.price,
        "fuzzy_address": config.fuzzy_address,
        "address_number": config.address_number,
        "title_similarity": config.title_similarity,
        "description_tfidf": config.description_tfidf,
        "description_semantic": config.description_semantic,
        "feature_overlap": config.feature_overlap,
        "gallery_images": config.gallery_images,
        "gallery_embeddings": config.gallery_embeddings,
    }

    # Threshold map — signals with continuous values need a minimum to "fire"
    threshold_map = {
        "fuzzy_address": config.fuzzy_address_threshold,
        "title_similarity": config.title_similarity_threshold,
        "description_tfidf": config.description_tfidf_threshold,
        "description_semantic": config.description_semantic_threshold,
        "feature_overlap": config.feature_overlap_threshold,
    }

    total_score = 0.0
    signal_count = 0
    breakdown: dict[str, float] = {}

    for signal in bundle.signals:
        weight = weight_map.get(signal.name, 0.0)
        if weight == 0.0 or not signal.fired:
            breakdown[signal.name] = 0.0
            continue

        # Apply threshold for continuous signals
        threshold = threshold_map.get(signal.name, 0.0)
        effective_value = signal.value

        if threshold > 0 and effective_value < threshold:
            breakdown[signal.name] = 0.0
            continue

        # Handle anti-signals (negative values, e.g. address_number mismatch)
        contribution = weight * effective_value
        breakdown[signal.name] = contribution
        total_score += contribution

        if effective_value > 0:
            signal_count += 1

    is_match = total_score >= config.match_threshold and signal_count >= config.min_signals

    return ScorerResult(
        score=total_score,
        signal_count=signal_count,
        is_match=is_match,
        breakdown=breakdown,
        bundle=bundle,
    )

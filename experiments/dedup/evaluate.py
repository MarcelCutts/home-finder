"""Evaluate scorer against labeled ground truth.

Usage:
    uv run python evaluate.py labels/labels_v1.json data/candidates.json
    uv run python evaluate.py labels/labels_v1.json data/candidates.json --sweep  # Weight sweep
"""

import argparse
import dataclasses
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from scorer import PRODUCTION_CONFIG, ScorerConfig, score_pair
from signals import compute_all_signals


@dataclass
class EvalMetrics:
    """Precision/recall/F-score metrics."""

    tp: int = 0  # True positives (scorer says match, label says match)
    fp: int = 0  # False positives (scorer says match, label says no_match)
    tn: int = 0  # True negatives (scorer says no match, label says no_match)
    fn: int = 0  # False negatives (scorer says no match, label says match)

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if (self.tp + self.fp) > 0 else 0.0

    @property
    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if (self.tp + self.fn) > 0 else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) > 0 else 0.0

    @property
    def f05(self) -> float:
        """F0.5 — weights precision 2x more than recall."""
        p, r = self.precision, self.recall
        beta_sq = 0.25  # 0.5^2
        return (1 + beta_sq) * p * r / (beta_sq * p + r) if (beta_sq * p + r) > 0 else 0.0

    @property
    def accuracy(self) -> float:
        total = self.tp + self.fp + self.tn + self.fn
        return (self.tp + self.tn) / total if total > 0 else 0.0


@dataclass
class SignalFiringStats:
    """Per-signal firing rate analysis."""

    name: str
    tp_fired: int = 0  # Fired on true positives
    fp_fired: int = 0  # Fired on false positives
    tn_fired: int = 0  # Fired on true negatives
    fn_fired: int = 0  # Fired on false negatives
    tp_total: int = 0
    fp_total: int = 0
    tn_total: int = 0
    fn_total: int = 0

    @property
    def tp_rate(self) -> float:
        return self.tp_fired / self.tp_total if self.tp_total > 0 else 0.0

    @property
    def fp_rate(self) -> float:
        return self.fp_fired / self.fp_total if self.fp_total > 0 else 0.0


@dataclass
class LabeledPair:
    """A labeled property pair with full data for signal computation."""

    pair_id: str
    raw_a: dict  # Full property dict (with detail sub-dict)
    raw_b: dict
    summary_a: dict  # Compact summary for display
    summary_b: dict
    label: str  # "match" or "no_match"


def load_labeled_pairs(
    labels_path: Path,
    candidates_path: Path,
) -> list[LabeledPair]:
    """Load labeled pairs with their property data.

    Returns list of LabeledPair (excludes uncertain).
    """
    labels_data = json.loads(labels_path.read_text())
    candidates_data = json.loads(candidates_path.read_text())

    labels = labels_data.get("labels", labels_data)  # Support both formats

    # Index candidates by pair_id
    candidates_by_id = {}
    for pair in candidates_data["pairs"]:
        candidates_by_id[pair["pair_id"]] = pair

    result = []
    skipped = 0
    for pair_id, label_data in labels.items():
        label = label_data if isinstance(label_data, str) else label_data.get("label")
        if label == "uncertain":
            skipped += 1
            continue
        if label not in ("match", "no_match"):
            continue

        if pair_id not in candidates_by_id:
            print(f"Warning: pair_id {pair_id} not found in candidates", file=sys.stderr)
            continue

        pair = candidates_by_id[pair_id]
        # Use raw dicts if available (have detail sub-dict), fall back to summaries
        raw_a = pair.get("raw_a", pair["property_a"])
        raw_b = pair.get("raw_b", pair["property_b"])

        result.append(
            LabeledPair(
                pair_id=pair_id,
                raw_a=raw_a,
                raw_b=raw_b,
                summary_a=pair["property_a"],
                summary_b=pair["property_b"],
                label=label,
            )
        )

    if skipped:
        print(f"Skipped {skipped} uncertain labels")

    return result


@dataclass
class ErrorCase:
    """A misclassified pair for error analysis."""

    pair: LabeledPair
    category: str  # "fp" or "fn"
    result: "ScorerResult"
    bundle: "SignalBundle"


def _build_corpus_state(
    labeled_pairs: list[LabeledPair],
) -> tuple["TfidfVectorizer | None", dict]:
    """Build corpus-level TF-IDF vectorizer and sentence embeddings from labeled pairs."""
    from signals import _get_description, _prop_uid, extract_property_text
    from sklearn.feature_extraction.text import TfidfVectorizer

    # Collect unique properties
    seen_uids: dict[str, dict] = {}
    for lp in labeled_pairs:
        for prop in (lp.raw_a, lp.raw_b):
            uid = _prop_uid(prop)
            if uid not in seen_uids:
                seen_uids[uid] = prop

    desc_texts = []
    desc_uids = []
    for uid, prop in seen_uids.items():
        desc = _get_description(prop)
        if desc:
            cleaned = extract_property_text(desc)
            if len(cleaned) >= 20:
                desc_texts.append(cleaned)
                desc_uids.append(uid)

    vectorizer = None
    if len(desc_texts) >= 2:
        vectorizer = TfidfVectorizer(
            stop_words="english",
            max_features=5000,
            ngram_range=(1, 2),
        )
        vectorizer.fit(desc_texts)

    embeddings_dict: dict = {}
    if desc_texts:
        from sentence_transformers import SentenceTransformer

        model = SentenceTransformer("all-MiniLM-L6-v2")
        truncated = [t[:512] for t in desc_texts]
        all_embeddings = model.encode(truncated, batch_size=32, show_progress_bar=False)
        embeddings_dict = dict(zip(desc_uids, all_embeddings))

    return vectorizer, embeddings_dict


def evaluate(
    labeled_pairs: list[LabeledPair],
    config: ScorerConfig,
    *,
    vectorizer=None,
    description_embeddings: dict | None = None,
) -> tuple[EvalMetrics, dict[str, SignalFiringStats], list[ErrorCase]]:
    """Run scorer against labeled pairs and compute metrics.

    Args:
        vectorizer: Pre-fitted corpus-level TfidfVectorizer.
        description_embeddings: Pre-computed sentence embeddings dict.

    Returns:
        (EvalMetrics, signal stats dict, list of error cases)
    """

    metrics = EvalMetrics()
    signal_stats: dict[str, SignalFiringStats] = {}
    errors: list[ErrorCase] = []

    for lp in labeled_pairs:
        bundle = compute_all_signals(
            lp.raw_a,
            lp.raw_b,
            vectorizer=vectorizer,
            description_embeddings=description_embeddings,
        )
        result = score_pair(lp.raw_a, lp.raw_b, config=config, bundle=bundle)

        predicted_match = result.is_match
        actual_match = lp.label == "match"

        # Confusion matrix
        if predicted_match and actual_match:
            metrics.tp += 1
            category = "tp"
        elif predicted_match and not actual_match:
            metrics.fp += 1
            category = "fp"
        elif not predicted_match and not actual_match:
            metrics.tn += 1
            category = "tn"
        else:
            metrics.fn += 1
            category = "fn"

        if category in ("fp", "fn"):
            errors.append(ErrorCase(pair=lp, category=category, result=result, bundle=bundle))

        # Per-signal firing analysis
        for signal in bundle.signals:
            if signal.name not in signal_stats:
                signal_stats[signal.name] = SignalFiringStats(name=signal.name)

            stats = signal_stats[signal.name]

            # Update totals
            setattr(stats, f"{category}_total", getattr(stats, f"{category}_total") + 1)

            # Update fired counts (signal fired = value > 0 and the signal was able to compute)
            if signal.fired and signal.value > 0:
                setattr(stats, f"{category}_fired", getattr(stats, f"{category}_fired") + 1)

    return metrics, signal_stats, errors


def print_results(
    metrics: EvalMetrics,
    signal_stats: dict[str, SignalFiringStats],
    config: ScorerConfig,
    labeled_count: int,
) -> None:
    """Print evaluation results."""
    print("=" * 60)
    print("EVALUATION RESULTS")
    print("=" * 60)
    print(f"Labeled pairs: {labeled_count}")
    print(f"Config: threshold={config.match_threshold}, min_signals={config.min_signals}")
    print()

    print("Confusion Matrix:")
    print("                  Predicted Match    Predicted No Match")
    print(f"  Actual Match        {metrics.tp:>4} (TP)          {metrics.fn:>4} (FN)")
    print(f"  Actual No Match     {metrics.fp:>4} (FP)          {metrics.tn:>4} (TN)")
    print()

    print("Metrics:")
    print(f"  Precision:  {metrics.precision:.3f}  (of predicted matches, how many are correct)")
    print(f"  Recall:     {metrics.recall:.3f}  (of actual matches, how many were found)")
    print(f"  F1:         {metrics.f1:.3f}")
    print(f"  F0.5:       {metrics.f05:.3f}  (precision-weighted — our target metric)")
    print(f"  Accuracy:   {metrics.accuracy:.3f}")
    print()

    print("Per-Signal Firing Rates:")
    print(f"  {'Signal':<22} {'TP rate':>8} {'FP rate':>8} {'Lift':>8}")
    print(f"  {'-' * 46}")
    for name, stats in sorted(signal_stats.items()):
        lift = stats.tp_rate / stats.fp_rate if stats.fp_rate > 0 else float("inf")
        lift_str = f"{lift:.1f}x" if lift < float("inf") else "∞"
        print(f"  {name:<22} {stats.tp_rate:>7.1%} {stats.fp_rate:>7.1%} {lift_str:>8}")
    print()

    if metrics.fp > 0:
        print(f"WARNING: {metrics.fp} false positives (false merges) — these are the worst errors.")
    if metrics.fn > 0:
        print(f"INFO: {metrics.fn} false negatives (missed merges) — duplicate notifications.")


def print_errors(errors: list[ErrorCase]) -> None:
    """Print detailed breakdown of misclassified pairs."""
    fps = [e for e in errors if e.category == "fp"]
    fns = [e for e in errors if e.category == "fn"]

    if fps:
        print("\n" + "=" * 60)
        print(f"FALSE POSITIVES ({len(fps)}) — scorer says match, actually not")
        print("=" * 60)
        for e in fps:
            _print_error_case(e)

    if fns:
        print("\n" + "=" * 60)
        print(f"FALSE NEGATIVES ({len(fns)}) — scorer says no match, actually match")
        print("=" * 60)
        for e in fns:
            _print_error_case(e)


def _print_error_case(e: ErrorCase) -> None:
    a = e.pair.summary_a
    b = e.pair.summary_b

    print(f"\n  [{a['source']}] {a['address']}")
    print(f"    {a['postcode'] or '—':12s}  £{a['price_pcm']}  {a['bedrooms']}bed")
    print(f"  [{b['source']}] {b['address']}")
    print(f"    {b['postcode'] or '—':12s}  £{b['price_pcm']}  {b['bedrooms']}bed")
    print(f"  Score: {e.result.score:.1f} ({e.result.signal_count} signals)")
    print("  Signals:")
    for sig in e.bundle.signals:
        if not sig.fired:
            continue
        contrib = e.result.breakdown.get(sig.name, 0)
        marker = f" → +{contrib:.0f}" if contrib > 0 else ""
        print(f"    {sig.name:22s}  {sig.value:+.2f}  {sig.detail}{marker}")


def sweep_thresholds(
    labeled_pairs: list[LabeledPair],
    base_config: ScorerConfig,
    *,
    vectorizer=None,
    description_embeddings: dict | None = None,
) -> None:
    """Sweep match_threshold and report metrics at each level."""
    print("\n" + "=" * 60)
    print("THRESHOLD SWEEP")
    print("=" * 60)
    print(
        f"  {'Threshold':>10} {'Prec':>7} {'Rec':>7} {'F1':>7} {'F0.5':>7} {'TP':>5} {'FP':>5} {'FN':>5}"
    )
    print(f"  {'-' * 58}")

    best_f05 = 0.0
    best_threshold = 0.0

    for threshold in range(20, 120, 5):
        config = dataclasses.replace(base_config, match_threshold=float(threshold))
        metrics, _, _ = evaluate(
            labeled_pairs,
            config,
            vectorizer=vectorizer,
            description_embeddings=description_embeddings,
        )

        marker = " ◀" if metrics.f05 > best_f05 else ""
        if metrics.f05 > best_f05:
            best_f05 = metrics.f05
            best_threshold = threshold

        print(
            f"  {threshold:>10.0f} {metrics.precision:>7.3f} {metrics.recall:>7.3f} "
            f"{metrics.f1:>7.3f} {metrics.f05:>7.3f} {metrics.tp:>5} {metrics.fp:>5} "
            f"{metrics.fn:>5}{marker}"
        )

    print(f"\nBest F0.5: {best_f05:.3f} at threshold={best_threshold}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate dedup scorer against labels")
    parser.add_argument("labels", type=Path, help="Labels JSON file")
    parser.add_argument("candidates", type=Path, help="Candidates JSON file")
    parser.add_argument(
        "--sweep",
        action="store_true",
        help="Sweep match_threshold values and report metrics",
    )
    parser.add_argument(
        "--errors",
        action="store_true",
        help="Print detailed breakdown of false positives and false negatives",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=None,
        help="Override match threshold",
    )
    parser.add_argument(
        "--min-signals",
        type=int,
        default=None,
        help="Override minimum signal count",
    )
    args = parser.parse_args()

    if not args.labels.exists():
        print(f"Error: {args.labels} not found")
        raise SystemExit(1)
    if not args.candidates.exists():
        print(f"Error: {args.candidates} not found")
        raise SystemExit(1)

    labeled_pairs = load_labeled_pairs(args.labels, args.candidates)
    if not labeled_pairs:
        print("No labeled pairs found (match/no_match). Label some pairs first.")
        raise SystemExit(1)

    print(f"Loaded {len(labeled_pairs)} labeled pairs")

    config = PRODUCTION_CONFIG
    if args.threshold is not None:
        config = ScorerConfig(
            match_threshold=args.threshold,
            min_signals=args.min_signals or config.min_signals,
        )
    elif args.min_signals is not None:
        config = ScorerConfig(min_signals=args.min_signals)

    # Build corpus-level state (TF-IDF vectorizer + sentence embeddings)
    print("Building corpus-level TF-IDF and sentence embeddings...")
    vectorizer, description_embeddings = _build_corpus_state(labeled_pairs)
    if vectorizer:
        print(f"  TF-IDF: {len(vectorizer.vocabulary_)} features")
    print(f"  Embeddings: {len(description_embeddings)} properties")

    metrics, signal_stats, errors = evaluate(
        labeled_pairs,
        config,
        vectorizer=vectorizer,
        description_embeddings=description_embeddings,
    )
    print_results(metrics, signal_stats, config, len(labeled_pairs))

    if args.errors:
        print_errors(errors)

    if args.sweep:
        sweep_thresholds(
            labeled_pairs,
            config,
            vectorizer=vectorizer,
            description_embeddings=description_embeddings,
        )


if __name__ == "__main__":
    main()

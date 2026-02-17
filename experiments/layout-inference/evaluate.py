"""Step 3: Compare photo-only inference against floorplan ground truth.

Produces a markdown decision report with quantitative metrics and a
pass/fail verdict based on the decision matrix.

Usage:
    uv run python evaluate.py
    uv run python evaluate.py --results data/inference_results_reference_objects.json
    uv run python evaluate.py --results data/inference_results_gallery8.json
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"

# Hosting layout ordering for "within-1-step" agreement
HOSTING_LAYOUT_ORDER = ["excellent", "good", "awkward", "poor"]

# Office separation ordering for "within-1-step" agreement
OFFICE_SEP_ORDER = ["dedicated_room", "separate_area", "shared_space", "none"]


@dataclass
class SqmMetrics:
    """Metrics for living_room_sqm comparison."""

    errors: list[float] = field(default_factory=list)  # signed errors (inference - truth)
    abs_errors: list[float] = field(default_factory=list)
    skipped: int = 0  # pairs where one or both are None

    @property
    def n(self) -> int:
        return len(self.abs_errors)

    @property
    def mae(self) -> float:
        return sum(self.abs_errors) / len(self.abs_errors) if self.abs_errors else 0.0

    @property
    def median_ae(self) -> float:
        if not self.abs_errors:
            return 0.0
        s = sorted(self.abs_errors)
        mid = len(s) // 2
        return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2

    @property
    def mean_error(self) -> float:
        """Mean signed error (positive = overestimate)."""
        return sum(self.errors) / len(self.errors) if self.errors else 0.0

    def within(self, threshold: float) -> float:
        """Percentage of estimates within ±threshold sqm."""
        if not self.abs_errors:
            return 0.0
        return sum(1 for e in self.abs_errors if e <= threshold) / len(self.abs_errors) * 100


@dataclass
class AgreementMetrics:
    """Metrics for categorical field agreement."""

    exact_matches: int = 0
    within_one_step: int = 0
    total: int = 0
    skipped: int = 0

    @property
    def exact_rate(self) -> float:
        return self.exact_matches / self.total * 100 if self.total else 0.0

    @property
    def within_one_rate(self) -> float:
        return self.within_one_step / self.total * 100 if self.total else 0.0


@dataclass
class ConfidenceDistribution:
    """Confidence value distribution."""

    gt_counts: dict[str, int] = field(default_factory=lambda: {"high": 0, "medium": 0, "low": 0})
    inf_counts: dict[str, int] = field(default_factory=lambda: {"high": 0, "medium": 0, "low": 0})
    total: int = 0


def _is_within_one_step(a: str, b: str, ordering: list[str]) -> bool:
    """Check if two values are within 1 step of each other in the ordering."""
    if a not in ordering or b not in ordering:
        return False
    return abs(ordering.index(a) - ordering.index(b)) <= 1


def evaluate(results: list[dict]) -> tuple[SqmMetrics, AgreementMetrics, AgreementMetrics, AgreementMetrics, ConfidenceDistribution]:
    """Evaluate inference results against ground truth.

    Returns:
        Tuple of (sqm_metrics, spacious_agreement, hosting_agreement, office_agreement, confidence_dist)
    """
    sqm = SqmMetrics()
    spacious = AgreementMetrics()
    hosting = AgreementMetrics()
    office = AgreementMetrics()
    confidence = ConfidenceDistribution()

    for entry in results:
        gt = entry["ground_truth"]
        inf = entry["inference"]

        # living_room_sqm
        gt_sqm = gt.get("living_room_sqm")
        inf_sqm = inf.get("living_room_sqm")
        if gt_sqm is not None and inf_sqm is not None:
            error = inf_sqm - gt_sqm
            sqm.errors.append(error)
            sqm.abs_errors.append(abs(error))
        else:
            sqm.skipped += 1

        # is_spacious_enough
        gt_spacious = gt.get("is_spacious_enough")
        inf_spacious = inf.get("is_spacious_enough")
        if gt_spacious is not None and inf_spacious is not None:
            spacious.total += 1
            if gt_spacious == inf_spacious:
                spacious.exact_matches += 1
                spacious.within_one_step += 1
        else:
            spacious.skipped += 1

        # hosting_layout
        gt_hosting = gt.get("hosting_layout")
        inf_hosting = inf.get("hosting_layout")
        if gt_hosting and inf_hosting and gt_hosting != "unknown" and inf_hosting != "unknown":
            hosting.total += 1
            if gt_hosting == inf_hosting:
                hosting.exact_matches += 1
                hosting.within_one_step += 1
            elif _is_within_one_step(gt_hosting, inf_hosting, HOSTING_LAYOUT_ORDER):
                hosting.within_one_step += 1
        else:
            hosting.skipped += 1

        # office_separation
        gt_office = gt.get("office_separation")
        inf_office = inf.get("office_separation")
        if gt_office and inf_office and gt_office != "unknown" and inf_office != "unknown":
            office.total += 1
            if gt_office == inf_office:
                office.exact_matches += 1
                office.within_one_step += 1
            elif _is_within_one_step(gt_office, inf_office, OFFICE_SEP_ORDER):
                office.within_one_step += 1
        else:
            office.skipped += 1

        # confidence distribution
        gt_conf = gt.get("confidence")
        inf_conf = inf.get("confidence")
        if gt_conf in confidence.gt_counts:
            confidence.gt_counts[gt_conf] += 1
            confidence.total += 1
        if inf_conf in confidence.inf_counts:
            confidence.inf_counts[inf_conf] += 1

    return sqm, spacious, hosting, office, confidence


def decide(sqm: SqmMetrics, spacious: AgreementMetrics) -> tuple[str, str]:
    """Apply the decision matrix.

    Returns:
        Tuple of (verdict, explanation).
    """
    spacious_rate = spacious.exact_rate

    if spacious_rate < 75:
        return "DONT_PURSUE", (
            f"is_spacious agreement ({spacious_rate:.0f}%) is below 75% threshold. "
            "Original design decision was correct — properties without floorplans "
            "aren't worth evaluating for space."
        )

    if sqm.mae <= 5:
        return "FULL_GO", (
            f"sqm MAE ({sqm.mae:.1f}) ≤ 5sqm AND is_spacious agreement ({spacious_rate:.0f}%) ≥ 75%. "
            "Photo-based estimates are accurate enough for fit scoring. "
            "Relax the floorplan gate, sqm is usable in fit score."
        )

    if sqm.mae <= 10:
        return "QUALITATIVE_ONLY", (
            f"sqm MAE ({sqm.mae:.1f}) is 5-10sqm, is_spacious agreement ({spacious_rate:.0f}%) ≥ 75%. "
            "Photo-based sqm is too noisy for fit scoring, but qualitative signals "
            "(is_spacious, hosting_layout) are reliable. Relax the gate, suppress sqm, "
            "use categorical fields."
        )

    return "QUALITATIVE_ONLY", (
        f"sqm MAE ({sqm.mae:.1f}) > 10sqm, is_spacious agreement ({spacious_rate:.0f}%) ≥ 75%. "
        "Sqm estimates are unreliable from photos, but categorical signals work. "
        "Relax the gate, suppress sqm, use categorical fields only."
    )


def print_report(
    results: list[dict],
    sqm: SqmMetrics,
    spacious: AgreementMetrics,
    hosting: AgreementMetrics,
    office: AgreementMetrics,
    confidence: ConfidenceDistribution,
    config: dict,
) -> str:
    """Generate and print the evaluation report. Returns the report as a string."""
    verdict, explanation = decide(sqm, spacious)

    lines: list[str] = []

    def p(line: str = "") -> None:
        lines.append(line)
        print(line)

    p("# Layout Inference Evaluation Report")
    p()
    p(f"**Date:** {__import__('datetime').date.today()}")
    p(f"**Properties evaluated:** {config.get('successful', len(results))}")
    p(f"**Prompt variant:** {config.get('prompt_variant', 'baseline')}")
    p(f"**Max gallery:** {config.get('max_gallery', 'default')}")
    p()

    # Verdict
    p("## Verdict")
    p()
    p(f"**{verdict}**")
    p()
    p(explanation)
    p()

    # sqm metrics
    p("## Room Size Accuracy (living_room_sqm)")
    p()
    if sqm.n > 0:
        p(f"| Metric | Value |")
        p(f"|--------|-------|")
        p(f"| Pairs compared | {sqm.n} |")
        p(f"| Skipped (None) | {sqm.skipped} |")
        p(f"| MAE | {sqm.mae:.1f} sqm |")
        p(f"| Median AE | {sqm.median_ae:.1f} sqm |")
        p(f"| Mean signed error | {sqm.mean_error:+.1f} sqm |")
        p(f"| Within ±3 sqm | {sqm.within(3):.0f}% |")
        p(f"| Within ±5 sqm | {sqm.within(5):.0f}% |")
        p(f"| Within ±10 sqm | {sqm.within(10):.0f}% |")
        p()

        # Per-property breakdown
        p("### Per-Property Breakdown")
        p()
        p("| Property | GT sqm | Inferred sqm | Error |")
        p("|----------|--------|-------------|-------|")
        for entry in results:
            gt_sqm = entry["ground_truth"].get("living_room_sqm")
            inf_sqm = entry["inference"].get("living_room_sqm")
            if gt_sqm is not None and inf_sqm is not None:
                error = inf_sqm - gt_sqm
                uid = entry["unique_id"]
                short_id = uid if len(uid) <= 25 else uid[:22] + "..."
                p(f"| {short_id} | {gt_sqm:.0f} | {inf_sqm:.0f} | {error:+.0f} |")
        p()
    else:
        p("No sqm data available for comparison.")
        p()

    # Spaciousness agreement
    p("## Spaciousness Agreement (is_spacious_enough)")
    p()
    p(f"| Metric | Value |")
    p(f"|--------|-------|")
    p(f"| Pairs compared | {spacious.total} |")
    p(f"| Exact agreement | {spacious.exact_rate:.0f}% |")
    p(f"| Skipped | {spacious.skipped} |")
    p()

    # Hosting layout
    p("## Hosting Layout Agreement")
    p()
    p(f"| Metric | Value |")
    p(f"|--------|-------|")
    p(f"| Pairs compared | {hosting.total} |")
    p(f"| Exact agreement | {hosting.exact_rate:.0f}% |")
    p(f"| Within 1 step | {hosting.within_one_rate:.0f}% |")
    p(f"| Skipped (unknown) | {hosting.skipped} |")
    p()

    # Office separation
    p("## Office Separation Agreement")
    p()
    p(f"| Metric | Value |")
    p(f"|--------|-------|")
    p(f"| Pairs compared | {office.total} |")
    p(f"| Exact agreement | {office.exact_rate:.0f}% |")
    p(f"| Within 1 step | {office.within_one_rate:.0f}% |")
    p(f"| Skipped (unknown) | {office.skipped} |")
    p()

    # Confidence distribution
    p("## Confidence Self-Calibration")
    p()
    p("Does the model lower its confidence when no floorplan is available?")
    p()
    p(f"| Level | With Floorplan (GT) | Photo-Only (Inference) |")
    p(f"|-------|--------------------|-----------------------|")
    for level in ["high", "medium", "low"]:
        gt_n = confidence.gt_counts.get(level, 0)
        inf_n = confidence.inf_counts.get(level, 0)
        p(f"| {level} | {gt_n} | {inf_n} |")
    p()

    # Decision matrix reference
    p("## Decision Matrix Reference")
    p()
    p("| sqm MAE | is_spacious agree | Next step |")
    p("|---------|-------------------|-----------|")
    p("| ≤5sqm   | ≥85%              | Full go |")
    p("| 5-10sqm | ≥75%              | Qualitative only |")
    p("| >10sqm  | ≥75%              | Qualitative only |")
    p("| any     | <75%              | Don't pursue |")
    p()

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate layout inference results")
    parser.add_argument(
        "--results",
        type=Path,
        default=DATA_DIR / "inference_results.json",
        help="Path to inference results JSON",
    )
    parser.add_argument(
        "--save-report",
        type=Path,
        default=None,
        help="Save markdown report to file (default: data/report_{variant}.md)",
    )
    args = parser.parse_args()

    if not args.results.exists():
        print(f"Error: {args.results} not found. Run run_inference.py first.", file=sys.stderr)
        raise SystemExit(1)

    data = json.loads(args.results.read_text())
    results = data.get("results", data)
    config = data.get("config", {})

    if not results:
        print("No results to evaluate.")
        raise SystemExit(1)

    print(f"Evaluating {len(results)} results from {args.results.name}\n")

    sqm, spacious, hosting, office, confidence = evaluate(results)
    report = print_report(results, sqm, spacious, hosting, office, confidence, config)

    # Save report
    report_path = args.save_report
    if report_path is None:
        variant = config.get("prompt_variant") or "baseline"
        max_gal = config.get("max_gallery")
        suffix = f"_{variant}"
        if max_gal:
            suffix += f"_gallery{max_gal}"
        report_path = DATA_DIR / f"report{suffix}.md"

    report_path.write_text(report)
    print(f"\nReport saved to {report_path}")


if __name__ == "__main__":
    main()

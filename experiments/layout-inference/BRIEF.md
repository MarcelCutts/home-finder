# Photo-Based Layout Inference Experiment

## Hypothesis

Claude Sonnet 4.5 can infer layout dimensions and spatial assessments from 8+ interior photos accurately enough to make fit scores meaningful for properties that lack dedicated floorplan images.

## Background

The floorplan gate (pipeline step 10) drops properties without floorplans before quality analysis. This saves ~$0.06/property and prevents unreliable space data from polluting fit scores. But it also means many viable properties are never evaluated.

The quality analysis system already has a `has_labeled_floorplan=False` code path (`quality_prompts.py`) that asks Claude to detect floorplans in gallery images and estimate dimensions from photos. We just don't know how accurate it is.

The fit score weights space signals heavily (up to 120pts combined across workspace/hosting dimensions), so noisy estimates could be worse than no estimate.

## What We're Measuring

For properties where we have both floorplan-based analysis (ground truth) and photo-only analysis:

| Metric | Field | How |
|--------|-------|-----|
| Room size accuracy | `living_room_sqm` | MAE, median AE, % within ±3/5/10 sqm |
| Spaciousness agreement | `is_spacious_enough` | Boolean match rate |
| Hosting layout agreement | `hosting_layout` | Exact + within-1-step |
| Office separation agreement | `office_separation` | Exact match rate |
| Self-calibration | `confidence` | Distribution shift (high/medium/low) |

## Decision Matrix

| sqm MAE | is_spacious agree | Next step |
|---------|-------------------|-----------|
| ≤5sqm   | ≥85%              | **Full go** — relax gate, sqm usable in fit score |
| 5-10sqm | ≥75%              | **Qualitative only** — relax gate, suppress sqm, use is_spacious/hosting_layout |
| >10sqm  | ≥75%              | **Qualitative only** — same as above |
| any     | <75%              | **Don't pursue** — original design decision was correct |

## Pipeline

### Step 1: Collect Ground Truth (`collect_ground_truth.py`)

Query production DB for properties with floorplans + quality analysis + 6+ gallery images + cached images on disk.

```bash
uv run python collect_ground_truth.py
```

**Cost:** $0

### Step 2: Run Photo-Only Inference (`run_inference.py`)

Re-analyze ground truth properties with floorplan stripped. Uses existing `PropertyQualityFilter.analyze_single_merged()` with `has_labeled_floorplan=False`.

```bash
uv run python run_inference.py --limit 3        # Smoke test
uv run python run_inference.py                   # Full run
uv run python run_inference.py --max-gallery 8   # Test photo count sensitivity
uv run python run_inference.py --prompt-variant reference_objects  # Prompt iteration
```

**Cost:** ~$0.06/property. Full run of 25 properties = ~$1.50.

### Step 3: Evaluate Results (`evaluate.py`)

Compare photo-only results against floorplan ground truth, produce decision report.

```bash
uv run python evaluate.py
```

**Cost:** $0

## Key Assumptions

1. Ground truth set needs 15+ properties to be statistically meaningful
2. The existing `<floorplan_note>` prompt path is the baseline — no production code changes needed for the experiment
3. If ground truth set is too small, run the main pipeline with `--dry-run` to build it up first

## Follow-on Research

See `TICKETS.md` for conditional research tickets (prompt engineering, photo count sensitivity, thinking budget experiments).

## Results

**Date:** 2026-02-17
**Run parameters:** 239 properties, claude-sonnet-4-5, 10 concurrent workers, ~$14 total cost, ~37 min wall clock
**Verdict:** FULL_GO (sqm MAE 5.0 meets ≤5 threshold, is_spacious 89% exceeds ≥85% threshold)

Full evaluation report: [`data/report_baseline.md`](data/report_baseline.md)

### Key Metrics

| Dimension | Metric | Value |
|-----------|--------|-------|
| Room size (living_room_sqm) | MAE | 5.0 sqm |
| | Median AE | 3.2 sqm |
| | Within ±5 sqm | 67% |
| | Within ±10 sqm | 90% |
| Spaciousness (is_spacious_enough) | Agreement | 89% |
| Hosting layout | Exact match | 62% |
| | Within 1 step | 96% |
| Office separation | Exact match | 78% |
| | Within 1 step | 89% |
| Confidence calibration | High | 216 → 113 |
| | Medium | 23 → 118 |
| | Low | 0 → 8 |

### Interpretation

- **Systematic underestimation bias.** Mean signed error of -2.5 sqm — the model consistently guesses smaller than reality. Worst errors cluster on large open-plan rooms (40–57 sqm GT → 16–25 sqm inferred), where scale cues are ambiguous without a floorplan.
- **Categorical signals are reliable even where sqm is noisy.** Hosting layout (96% within 1 step) and office separation (89% within 1 step) hold up well, meaning fit scoring based on these dimensions is trustworthy.
- **Model is well-calibrated.** Confidence appropriately drops from 90% high → 47% high when floorplans are removed. The shift to medium/low reflects genuine uncertainty about spatial estimates.

### Conditional Ticket Triage

Based on the FULL_GO verdict:

| Ticket | Decision | Reason |
|--------|----------|--------|
| T3 (Reference Objects) | **Skipped** | MAE already ≤5, prompt engineering not needed |
| T4 (Range Estimates) | **Skipped** | Point estimates are adequate |
| T5 (Photo Count Sensitivity) | **Open** | Condition met — worth finding minimum photo threshold for the gate |
| T6 (Extended Thinking) | **Skipped** | MAE is not borderline, already meets threshold |
| T7 (Qualitative-Only Design) | **Skipped** | Sqm is usable, no need for qualitative-only path |
| T8 (Production Implementation) | **Open** | Full go path: gate relaxation + 8+ photo requirement |

### What This Means for Production

1. **Floorplan gate can be relaxed** for photo-rich properties (8+ gallery images). These properties currently get dropped at pipeline step 10 — after T8, they'll proceed to quality analysis with photo-inferred layout data.
2. **Sqm estimates are usable in fit scoring.** The 5.0 MAE is within the ≤5 threshold, meaning `living_room_sqm` from photo inference can feed into workspace and hosting dimension scores without a discount.
3. **No need for a qualitative-only fallback path.** T7 is skipped — we don't need to suppress sqm or add a `layout_source` discriminator field.

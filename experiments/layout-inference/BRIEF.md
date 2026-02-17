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

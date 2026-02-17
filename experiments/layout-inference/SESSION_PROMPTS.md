# Session Prompts for Layout Inference Experiment

Each ticket below is a self-contained prompt for a **new Claude Code session**.
Copy the full prompt block and paste it into a fresh session.

The tickets have dependencies — run them in order, and check the "When to skip"
notes before starting conditional tickets.

---

## T1: Data Audit

```
I'm running an experiment to test whether Claude can infer room dimensions from
interior photos (without a floorplan). The experiment lives at
`experiments/layout-inference/`.

Read `experiments/layout-inference/BRIEF.md` and `experiments/layout-inference/TICKETS.md`
to understand the full context — then run the T1 (Data Audit) ticket.

Specifically:

1. `cd experiments/layout-inference`
2. Run `uv run python collect_ground_truth.py` (you may need to adjust the --db
   path if the default doesn't find my production database — check `data/properties.db`
   relative to the repo root, or look for .db files in the project)
3. Review the printed audit report and tell me:
   - How many properties have floorplans vs don't
   - How many floorplan-less properties have 8+ gallery photos (the opportunity set)
   - Whether the ground truth set is large enough (target: 15+ entries)
4. If fewer than 15 properties qualify, tell me and we'll discuss options

Don't modify any code. This is a read-only data collection step.
```

---

## T2: Baseline Accuracy

**Depends on:** T1 completed, `data/ground_truth.json` exists with 15+ entries.

```
I'm continuing the layout inference experiment at `experiments/layout-inference/`.

Read `experiments/layout-inference/BRIEF.md` and `experiments/layout-inference/TICKETS.md`
for full context. T1 (Data Audit) is done — `data/ground_truth.json` exists.

Run the T2 (Baseline Accuracy) ticket:

1. `cd experiments/layout-inference`
2. First do a smoke test: `uv run python run_inference.py --limit 3`
   - This calls the Anthropic API (~$0.18). Confirm it works before the full run.
   - Check the output makes sense (sqm values, hosting layouts, etc.)
3. If the smoke test looks good, run the full set:
   `uv run python run_inference.py`
   - This will cost ~$0.06/property. Watch for errors.
4. Run the evaluation: `uv run python evaluate.py`
5. Read the generated report in `data/report_baseline.md`
6. Tell me:
   - The verdict from the decision matrix (FULL_GO / QUALITATIVE_ONLY / DONT_PURSUE)
   - The key metrics (sqm MAE, is_spacious agreement %, hosting agreement %)
   - Whether the confidence distribution shifted down (self-calibration check)
   - Your recommendation on which conditional tickets (T3-T7) to pursue next

IMPORTANT: This session needs ANTHROPIC_API_KEY set in the environment.
Don't modify any production code. Don't modify experiment code unless
something is broken.
```

---

## T3: Prompt Engineering — Reference Objects

**When to skip:** Skip if T2 showed sqm MAE ≤ 5 (already good enough) or
is_spacious agreement < 75% (not worth pursuing).
**When to run:** T2 showed sqm MAE between 5-15sqm — close but not good enough.

```
I'm continuing the layout inference experiment at `experiments/layout-inference/`.

Read `experiments/layout-inference/BRIEF.md` and `experiments/layout-inference/TICKETS.md`
for full context. Also read `data/report_baseline.md` to see the T2 baseline results.

T2 showed the sqm estimates need improvement. Run the T3 (Reference Objects) ticket:

1. `cd experiments/layout-inference`
2. Run inference with the reference objects prompt variant:
   `uv run python run_inference.py --prompt-variant reference_objects`
3. Evaluate: `uv run python evaluate.py --results data/inference_results_reference_objects.json`
4. Compare the new report against the baseline (`data/report_baseline.md`):
   - Did sqm MAE improve? By how much?
   - Did any other metrics change (spacious agreement, hosting layout)?
   - Did the reference objects help or hurt?
5. Summarise the comparison and whether this variant should replace the baseline

Don't modify any production code.
```

---

## T4: Prompt Engineering — Range Estimates

**When to skip:** Skip unless T2/T3 showed sqm estimates have high variance
(some very close, some wildly off).
**When to run:** sqm point estimates are noisy but the model seems to "know"
the right ballpark.

```
I'm continuing the layout inference experiment at `experiments/layout-inference/`.

Read `experiments/layout-inference/BRIEF.md`, `TICKETS.md`, and the existing
reports in `data/` to understand where we are.

Run the T4 (Range Estimates) ticket. This one requires code changes:

1. Read `run_inference.py` and `evaluate.py` to understand the current structure
2. Design a new prompt variant that asks for `living_room_sqm_min` and
   `living_room_sqm_max` instead of (or in addition to) a single point estimate
3. This will need:
   - A new prompt variant in PROMPT_VARIANTS dict in run_inference.py
   - The response schema doesn't change (we're just asking for a range in the
     prompt text — the model can still report living_room_sqm as the midpoint)
   - Or: if you think it's better to actually add min/max fields, that would
     require changes to the _VisualAnalysisResponse in quality.py — discuss
     with me before doing that
4. Run the variant and evaluate
5. Compare range accuracy against point estimates

Keep changes minimal and contained to the experiment directory where possible.
```

---

## T5: Photo Count Sensitivity

**When to skip:** Skip if T2 showed DONT_PURSUE verdict.
**When to run:** T2 showed positive results — now find the minimum useful photo count.

```
I'm continuing the layout inference experiment at `experiments/layout-inference/`.

Read `experiments/layout-inference/BRIEF.md`, `TICKETS.md`, and the baseline
report in `data/report_baseline.md`.

Run the T5 (Photo Count Sensitivity) ticket:

1. `cd experiments/layout-inference`
2. Run inference at different gallery caps:
   `uv run python run_inference.py --max-gallery 6`
   `uv run python run_inference.py --max-gallery 8`
   `uv run python run_inference.py --max-gallery 10`
   `uv run python run_inference.py --max-gallery 12`
   (Each run costs ~$1.50 — total ~$6 for all four)
3. Evaluate each:
   `uv run python evaluate.py --results data/inference_results_gallery6.json`
   `uv run python evaluate.py --results data/inference_results_gallery8.json`
   `uv run python evaluate.py --results data/inference_results_gallery10.json`
   `uv run python evaluate.py --results data/inference_results_gallery12.json`
4. Create a comparison summary:
   - Table of (photo count → sqm MAE, is_spacious agreement, hosting agreement)
   - Identify the knee point — where does accuracy stop improving?
   - Recommend a minimum photo threshold for the production gate

Don't modify production code.
```

---

## T6: Extended Thinking Budget

**When to skip:** Skip unless sqm accuracy is borderline (MAE 5-10sqm)
and you've exhausted prompt engineering options (T3/T4).
**When to run:** Prompt changes didn't help enough, trying more compute.

```
I'm continuing the layout inference experiment at `experiments/layout-inference/`.

Read `experiments/layout-inference/BRIEF.md`, `TICKETS.md`, and the existing
reports in `data/`.

Run the T6 (Extended Thinking Budget) ticket:

1. Read `run_inference.py` and `src/home_finder/filters/quality.py` to understand
   how thinking_budget_tokens is configured (currently 10k in PropertyQualityFilter.__init__)
2. Add a `--thinking-budget` CLI flag to `run_inference.py` that overrides
   the thinking budget when constructing PropertyQualityFilter
3. Run: `uv run python run_inference.py --thinking-budget 20000`
   (This may cost slightly more per property due to extra thinking tokens)
4. Evaluate and compare against the 10k baseline
5. Is the accuracy improvement worth the extra cost?

Keep changes to run_inference.py only.
```

---

## T7: Qualitative-Only Path Design

**When to skip:** Skip if T2 showed FULL_GO (sqm is reliable enough) or
DONT_PURSUE (nothing works).
**When to run:** T2 showed QUALITATIVE_ONLY — sqm unreliable but categorical
signals (is_spacious, hosting_layout, office_separation) are accurate.

```
I'm continuing the layout inference experiment at `experiments/layout-inference/`.

Read `experiments/layout-inference/BRIEF.md`, `TICKETS.md`, and all reports
in `data/` to understand the experiment results so far.

Run the T7 (Qualitative-Only Path Design) ticket. This is a design task,
not a coding task:

1. Read these production files to understand what would need to change:
   - `src/home_finder/models/quality.py` — SpaceAnalysis model
   - `src/home_finder/filters/fit_score.py` — _score_hosting(), _score_workspace()
   - `src/home_finder/filters/quality.py` — the floorplan gate logic
   - `src/home_finder/filters/quality_prompts.py` — the <floorplan_note> path

2. Design the production changes:
   - Add `layout_source: Literal["floorplan", "photo_inference"]` to SpaceAnalysis
   - How should the floorplan gate change? (require N+ photos if no floorplan?)
   - How should fit_score.py discount photo-inferred signals?
     (e.g., suppress living_room_sqm entirely, or halve its weight?)
   - What's the minimum photo count threshold (from T5 if run)?

3. Write the design as a ticket in `experiments/layout-inference/TICKETS.md`
   under T8, with:
   - Exact files and functions to change
   - Model changes with field definitions
   - Fit score adjustment logic
   - Estimated lines of code

Don't write production code yet — just the design document.
```

---

## T8: Production Implementation

**When to skip:** Skip if the experiment concluded DONT_PURSUE.
**When to run:** After T7 design is reviewed and approved, or after FULL_GO from T2.

```
I'm implementing the layout inference changes based on the experiment at
`experiments/layout-inference/`.

Read these files to understand the experiment results and design:
- `experiments/layout-inference/BRIEF.md` — experiment overview
- `experiments/layout-inference/TICKETS.md` — T8 has the implementation design
- `experiments/layout-inference/data/report_baseline.md` — accuracy results
- Any other reports in `experiments/layout-inference/data/`

The experiment verdict was [FULL_GO / QUALITATIVE_ONLY — fill this in based on
actual results]. Implement the production changes:

For FULL_GO:
- Relax the floorplan gate: allow properties with N+ gallery photos through
  (use the threshold from T5, or default to 8)
- No fit score changes needed — sqm estimates are accurate enough

For QUALITATIVE_ONLY:
- Relax the floorplan gate with photo count minimum
- Add `layout_source` field to SpaceAnalysis
- Discount/suppress living_room_sqm in fit score when layout_source == "photo_inference"
- Keep is_spacious, hosting_layout, office_separation at full weight

In both cases:
- Write tests for the new gate logic
- Write tests for any fit score changes
- Run the full test suite to verify nothing breaks
- Don't commit — I'll review the diff first
```

---

## Notes on Session Management

- **T1 and T2 are always sequential** — run T1 first, then T2 in a new session
- **T3-T6 are conditional and independent** — after T2, pick the relevant ones
  based on the verdict. They can run in any order.
- **T7 depends on knowing which conditional tickets were run** — do it last
  among the conditional tickets
- **T8 is always last** — requires a firm decision from the experiment

Each session should start in the repo root (`/Users/marcel/projects/labs/home-finder`).
Sessions that call the Anthropic API (T2, T3, T5, T6) need `ANTHROPIC_API_KEY` set.

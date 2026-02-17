# Layout Inference — Research Tickets

Research backlog for the photo-based layout inference experiment. Each ticket is self-contained so a fresh session can pick it up cold.

---

## T1: Data Audit

**Status:** open
**Dependencies:** none

Run `collect_ground_truth.py` and review the audit report.

**Key questions:**
- How many properties does the floorplan gate currently drop?
- How many of those have 8+ gallery photos (the "opportunity" set)?
- Is the ground truth set large enough (target: 20+)?

**Steps:**
1. `uv run python collect_ground_truth.py`
2. Review the printed audit report (total enriched, with/without floorplans by source, gallery count distribution)
3. Check `data/ground_truth.json` has 15+ entries

**If fewer than 15 properties qualify:** Run the main pipeline with `uv run home-finder --dry-run` to build up the dataset, then re-run collection.

**Accept:** Audit report printed, `data/ground_truth.json` created with 15+ entries.

---

## T2: Baseline Accuracy

**Status:** open
**Dependencies:** T1

Run photo-only inference on the full ground truth set and evaluate accuracy.

**Steps:**
1. `uv run python run_inference.py` (full run, ~$1.50 for 25 properties)
2. `uv run python evaluate.py` to produce the comparison report
3. Check the decision matrix verdict

**Accept:** Decision matrix evaluated, markdown report with MAE/agreement tables, verdict documented.

---

## T3: Prompt Engineering — Reference Objects

**Status:** open
**Dependencies:** T2 (conditional)
**Condition:** Only pursue if T2 shows sqm MAE between 5-15sqm (close but not good enough)

**Hypothesis:** Explicit reference object dimensions in the prompt improve sqm accuracy.

**Prompt variant to test:** Add to `<floorplan_note>`:
- UK standard door width: ~76cm
- Double bed: ~135 × 190cm
- Kitchen base units: ~60cm depth
- Ceiling height: ~240cm modern, ~270-300cm Victorian

**Steps:**
1. `uv run python run_inference.py --prompt-variant reference_objects`
2. `uv run python evaluate.py` — compare with baseline

**Accept:** MAE improvement quantified, report updated with variant comparison.

---

## T4: Prompt Engineering — Range Estimates

**Status:** open
**Dependencies:** T2 (conditional)
**Condition:** Only pursue if T2 shows sqm estimates are noisy (high variance)

**Hypothesis:** Asking for a range (e.g., "15-20sqm") instead of a point estimate improves calibration.

**Design notes:** Would require adding `living_room_sqm_min`/`living_room_sqm_max` fields to the response schema. This is a more invasive change — only worth pursuing if point estimates are clearly unreliable.

**Accept:** Range accuracy quantified, decision on whether range estimates are more useful.

---

## T5: Photo Count Sensitivity

**Status:** open
**Dependencies:** T2 (conditional)
**Condition:** Only pursue if T2 shows positive results at the default gallery size

**Hypothesis:** There's a minimum photo count threshold below which inference degrades.

**Steps:**
1. `uv run python run_inference.py --max-gallery 6`
2. `uv run python run_inference.py --max-gallery 8`
3. `uv run python run_inference.py --max-gallery 10`
4. `uv run python run_inference.py --max-gallery 12`
5. Compare accuracy across runs, find the knee point

**Cost:** ~$6 total (4 runs × 25 properties × $0.06).

**Accept:** Chart/table showing accuracy by photo count, recommended minimum threshold.

---

## T6: Extended Thinking Budget

**Status:** open
**Dependencies:** T2 (conditional)
**Condition:** Only pursue if T2 shows sqm accuracy is borderline (MAE 5-10sqm)

**Hypothesis:** More thinking tokens (20k vs 10k) improve spatial reasoning.

**Steps:**
1. Modify `run_inference.py` to override `thinking_budget_tokens` (or add a CLI flag)
2. Run with 20k thinking budget
3. Compare accuracy vs baseline 10k

**Accept:** Accuracy comparison at 10k vs 20k thinking budget, cost-benefit analysis.

---

## T7: Qualitative-Only Path Design

**Status:** open
**Dependencies:** T2 (conditional)
**Condition:** Only pursue if T2 shows sqm is unreliable but is_spacious/hosting_layout are accurate

**Design:** Relax the floorplan gate for photo-rich properties, but:
- Add `layout_source: Literal["floorplan", "photo_inference"]` to `SpaceAnalysis`
- Suppress `living_room_sqm` in fit score when `layout_source == "photo_inference"`
- Only use boolean/categorical signals (is_spacious, hosting_layout, office_separation)

**Accept:** Implementation design documented with model changes and fit score adjustments.

---

## T8: Production Implementation

**Status:** open
**Dependencies:** T2 + any conditional tickets that were pursued

**Condition:** Only pursue after experiment reaches a "go" or "qualitative only" decision.

**Scope depends on findings:**
- **Full go:** Gate relaxation (require 8+ photos if no floorplan) + field description update (~50 lines)
- **Qualitative only:** Gate relaxation + `layout_source` field + fit score discounting for photo-inferred sqm
- **Don't pursue:** Document findings and close

**Accept:** PR with production changes, tests passing.

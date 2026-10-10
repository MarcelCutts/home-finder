# Focused reliability repairs

The owner workflow needs five bounded repairs after the consolidation at
`7fa12c6`. This checklist replaces the former automatic implementation queue.
It preserves the eventual small hosted-service target while keeping this series
within the current one-owner application.

## Delivery tasks

### R1 CI allowance and pagination documentation — draft #44

- [x] Allow 25 minutes for the hosted test job and report its 20 slowest tests.
- [x] Preserve coverage, socket blocking, the CI Hypothesis profile and manual release.
- [x] Describe production pagination through known pages within normal bounds.
- [x] Verify and review the fresh draft PR on its final head.

The final consolidation head passed 3,019 tests at 88.54% coverage; its main-branch
rerun reached 97% before the 15-minute job limit cancelled it. This repair changes
the allowance and diagnostics, not the test selection or acceptance threshold.

R1 local verification: 31 existing pagination/accounting tests passed with
network sockets blocked; Ruff lint/format, strict mypy and an offline wheel/sdist
build passed. Review confirmed only the test-job allowance/reporting changed;
manual release, live-test opt-in and required checks remain intact. Hosted
verification passed on `7bceffc`: [run 38074935433](https://github.com/MarcelCutts/home-finder/actions/runs/38074935433)
finished its test job in 15m29s. Lint, typing and build also passed.

### R2 Accessible listings during incomplete processing — draft #45

- [x] Show pending, failed-assessment and image-less listings without reviving dropped listings.
- [x] Keep existing detail URLs accessible with placeholders; unknown IDs still return 404.
- [x] Add consistent assessed/unassessed filtering, badges, counts and map behavior.
- [x] Exclude unknown assessments from explicit score filters; show first-seen age only.
- [x] Verify query contracts, mobile/desktop behavior and status persistence.

R2 treats the existing non-null overall rating as the assessment contract, matching
the pipeline's distinction between completed and fallback analysis. A stored fit
score on a fallback cannot satisfy even `min_fit_score=0`, appear on the map, or
rank ahead of assessed homes. A requested refresh retains the previous assessment
and sent status. Dropped records remain excluded from dashboard queries; existing
direct URLs identify them as excluded. Off-market and owner triage filters retain
their existing semantics. Diagnostics count current recorded processing states,
including excluded and off-market records, without inventing wait durations or
unrecorded failure causes.

R2 review and verification: the affected web/storage selection passed 441 tests.
The final presentation review corrected image-less card sizing and verified
pagination (including a minimum score of zero), HTMX counts, map data, direct
detail URLs and owner status changes. Ruff lint/format and strict mypy passed.
Browser review used fictional in-memory listings with no provider clients at
1440×1000 and 390×844; the mobile page had no horizontal overflow. Screenshots:
[desktop](../../output/playwright/r2-desktop.jpg),
[mobile](../../output/playwright/r2-mobile.jpg),
[image-less detail](../../output/playwright/r2-detail-mobile.jpg).
Hosted checks passed on `c923079` ([run 38075891565](https://github.com/MarcelCutts/home-finder/actions/runs/38075891565)); the test job took 16m36s.
No schema or pipeline mutation behavior changes in this PR. Its parent is #44 so
the fresh hosted run uses the corrected test allowance.

### R3 Conservative floorplan handling — draft #46

- [x] Stop promoting heuristic gallery guesses into authoritative floorplans.
- [x] Preserve gallery membership and persist source-declared versus unknown legacy provenance.
- [x] Apply that distinction to gates, prompts and presentation without asserting readability.
- [x] Verify bright-photo negatives, real plans, legacy rows and source refreshes.

R3 removes the enrichment heuristic entirely. Migration 10 gives existing image
rows `floorplan_origin='unknown'`; it does not rewrite their historical analyses,
delete bytes or enqueue paid work. Fresh structural extraction records
`source_declared`. Saving the same URL can upgrade its provenance; a fresh gallery
observation can restore an unknown legacy image to the gallery. Grouping and cache
reload prefer source-declared plans. Only those satisfy the dedicated-floorplan
gate; the existing source/photo exemptions remain available. Unknown legacy images
are still visible and usable as unverified gallery input. Prompts, Telegram copy
and the dashboard distinguish image designation from readable dimensions.

The retained bathroom photograph (SHA-256
`c76272dea9a498267c103035fe8f6c512885a05665ace07c4be716c234a122a7`)
reproduced the old heuristic's `(True, 0.665)` result. An offline replay through the
new enrichment path retained one gallery image, produced no floorplan and failed
the dedicated-floorplan gate. No portal or model request was made. Synthetic
diagram tests cover gallery retention, source designation and cached bytes.

R3 review checked the full path from extraction through SQLite reload, grouping,
gating, request construction and presentation. 487 existing affected tests passed
on the initial run; the expected prompt snapshots and migration compatibility
check were corrected and the final targeted selection passed 141 tests. Another
26 provenance/migration/request checks passed. Ruff lint/format and strict mypy
passed. A further 35 core-upgrade and backup checks passed after making the
upgrade expectation follow the current migration count. [Browser evidence](../../output/playwright/r3-floorplan-origin.jpg) uses
authored diagrams. Rollback after migration requires the verified pre-upgrade
database copy; earlier application versions reject schema 10.

### R4 Visual checkpoints for unfinished analysis — draft #47

- [x] Identify visual inputs by actual ordered image bytes, prompts and relevant configuration.
- [x] Store one validated result with its settled paid attempt before evaluation starts.
- [x] Reuse compatible results after evaluation failure/restart; invalidate changed inputs.
- [x] Remove the matching checkpoint on completed assessment; retain all spending history.
- [x] Verify repeated evaluation failures, attempt caps, uncertain holds and crash boundaries.

R4 identifies the physical visual request, including ordered image bytes, rendered
prompt, model, output schema and visual settings. A validated result and its usage
receipt commit together. Recovery validates both before reuse; successful
assessment consumes only its matching checkpoint in the assessment transaction.
Evaluation still respects its own budget, attempt cap and uncertain-charge holds.
The checkpoint preserves observation field order because the evaluation prompt
contains JSON text: changing order on reload would incorrectly create a new paid
operation identity. A changed evaluation configuration can reuse the visual result.

Review covered cancellation, settlement failure, input invalidation, corrupted
receipts, exhausted evaluation attempts, budget changes and sent-state refreshes.
367 existing affected tests passed; the final checkpoint, spending and upgrade
selection passed 64 tests, including 16 fault/identity cases using the real SDK
with a mock transport. Ruff lint/format and strict mypy passed. No paid calls were
made. A further 72 checkpoint/configuration/spending checks passed after updating
the older provenance and cancellation test contracts. Migration 11 requires the same verified-backup rollback discipline as R3.

### R5 Notification recovery

- [x] Persist a bounded delivery plan for each recipient and logical discovery/price event.
- [x] Store intended parts and acknowledged message IDs; use the same format across runs.
- [x] Resume known unfinished parts, respect retry delays and expose ambiguous outcomes.
- [x] Preserve sent-state reanalysis and require review of untracked legacy delivery outcomes.
- [x] Verify partial album/photo delivery, restart, lost acknowledgement and permanent failure.

R5 creates a discovery/price-revision intent in the readiness transaction. Before
sending it binds the owner recipient and snapshots the intended parts. Every
Bot API receipt is saved before moving on; the final receipt and property/price
completion commit together. Known rate delays survive restart. Ambiguous
responses, interrupted dispatch or missing acknowledgements remain held for
review, with earlier receipts preserved and no text fallback. A new recipient
cannot inherit old receipts. Unstarted obsolete price alerts are superseded;
completion of an older alert cannot consume a newer change, including changes
recorded within the same second.

Migration 12 quarantines legacy untracked outcomes; it preserves sent homes and
does not invent receipts. Owner diagnostics and a local command expose metadata
and permit explicit reconciliation under the runner lock. There are at most three
parts per event, with no general delivery-event history. Cached media are checked
against saved hashes and uploaded from those same bytes. No classifier, broker,
workflow service or multi-user routing was added.

27 new offline fault/backup tests pass. They cover partial photo/album sends,
restarts, lost receipts, cancellation, concurrency, permanent failures, recipient
changes, assets, price revisions, legacy state and backup/upgrade preservation of
spending holds/checkpoints. Existing pipeline tests now exercise the real delivery
transactions with only the Bot API mocked. The final full-suite result is recorded
in the [verification review](../reviews/2026-10-10-focused-repair-verification.md).

## Operational qualification

- [ ] Verify deployed owner access and authenticated mutation boundaries.
- [x] Inspect the deployed dashboard database, serving process and scheduler flags.
- [ ] Qualify one active writer/scheduler layout before activation.
- [x] Verify an isolated export backup, restore and migration; test spending-hold preservation.
- [ ] Verify a fresh production backup including assets before release.
- [x] Record the release and rollback procedure before production changes.
- [ ] Obtain a new explicit spending allowance and notification authorization before the live trial.

The October 10 read-only probe returned HTTP 200 for anonymous dashboard access;
deployed protection is therefore not qualified by the merged access-control code.
Response bodies were discarded. All 80 offline owner-access and backup/restore
checks passed with test socket blocking retained. A sandbox-only
TestClient fixture hang disappeared outside the execution sandbox. These checks
do not establish current production protection or a production restore.

Read-only Fly inspection found schema 6, 1,016 properties, 1,001 assessments and
57 runs; integrity passed with no foreign-key violations. The serving process uses
`--no-pipeline` and the latest recorded run completed February 28. The September 27
production export passed backup/verification/isolated restore and migration to
schema 12, preserving every historical column and its original hash. Its assets
were not included. The default local development database has orphaned images and
is not the qualified export. See the [full review and release prerequisites](../reviews/2026-10-10-focused-repair-verification.md).

## Review boundaries

Create fresh draft PRs and commit coherent tasks. Keep CI and visibility changes
independent where possible; order schema changes through provenance, checkpoints
and delivery. Each PR records its own verification and review against its actual
parent. Do not mix the unrelated dependency update into these repairs.

Structured diagnostics contain identifiers, states, reasons and timing rather
than prompts, image bytes, credentials or model response bodies. Keep collection,
paid analysis, real notifications and deployment disabled during implementation.
The previous experiment budgets are not continuing authorization.

Model upgrades, manual ingestion, multi-user isolation, observation journals,
reversible grouping and distributed workers remain outside this series.

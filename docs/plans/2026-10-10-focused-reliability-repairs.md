# Focused reliability repairs

The owner workflow needs five bounded repairs after the consolidation at
`7fa12c6`. This checklist replaces the former automatic implementation queue.
It preserves the eventual small hosted-service target while keeping this series
within the current one-owner application.

## Delivery tasks

### R1 CI allowance and pagination documentation

- [x] Allow 25 minutes for the hosted test job and report its 20 slowest tests.
- [x] Preserve coverage, socket blocking, the CI Hypothesis profile and manual release.
- [x] Describe production pagination through known pages within normal bounds.
- [ ] Verify and review the fresh draft PR on its final head.

The final consolidation head passed 3,019 tests at 88.54% coverage; its main-branch
rerun reached 97% before the 15-minute job limit cancelled it. This repair changes
the allowance and diagnostics, not the test selection or acceptance threshold.

R1 local verification: 31 existing pagination/accounting tests passed with
network sockets blocked; Ruff lint/format, strict mypy and an offline wheel/sdist
build passed. Review confirmed only the test-job allowance/reporting changed;
manual release, live-test opt-in and required checks remain intact. Hosted
verification is recorded on the draft PR after publication.

### R2 Accessible listings during incomplete processing

- [ ] Show pending, failed-assessment and image-less listings without reviving dropped listings.
- [ ] Keep existing detail URLs accessible with placeholders; unknown IDs still return 404.
- [ ] Add consistent assessed/unassessed filtering, badges, counts and map behavior.
- [ ] Exclude unknown assessments from explicit score filters; show first-seen age only.
- [ ] Verify query contracts, mobile/desktop behavior and status persistence.

### R3 Conservative floorplan handling

- [ ] Stop promoting heuristic gallery guesses into authoritative floorplans.
- [ ] Preserve gallery membership and persist source-declared versus unknown legacy provenance.
- [ ] Apply that distinction to gates, prompts and presentation without asserting readability.
- [ ] Verify bright-photo negatives, real plans, legacy rows and source refreshes.

### R4 Visual checkpoints for unfinished analysis

- [ ] Identify visual inputs by actual ordered image bytes, prompts and relevant configuration.
- [ ] Store one validated result with its settled paid attempt before evaluation starts.
- [ ] Reuse compatible results after evaluation failure/restart; invalidate changed inputs.
- [ ] Remove the matching checkpoint on completed assessment; retain all spending history.
- [ ] Verify repeated evaluation failures, attempt caps, uncertain holds and crash boundaries.

### R5 Notification recovery

- [ ] Persist a bounded delivery plan for each recipient and logical discovery/price event.
- [ ] Store intended parts and acknowledged message IDs; use the same format across runs.
- [ ] Resume known unfinished parts, respect retry delays and expose ambiguous outcomes.
- [ ] Preserve sent-state reanalysis and require review of untracked legacy delivery outcomes.
- [ ] Verify partial album/photo delivery, restart, lost acknowledgement and permanent failure.

## Operational qualification

- [ ] Verify deployed owner access and authenticated mutation boundaries.
- [ ] Identify the authoritative database, active writer and scheduler.
- [ ] Verify an isolated backup, restore and migration preserving owner state and spending holds.
- [ ] Record the release and rollback procedure before production changes.
- [ ] Obtain a new explicit spending allowance and notification authorization before the live trial.

The October 10 read-only probe returned HTTP 200 for anonymous dashboard access;
deployed protection is therefore not qualified by the merged access-control code.
Response bodies were discarded. Eleven targeted offline owner-access/liveness and
WAL restore checks passed with test socket blocking retained. A sandbox-only
TestClient fixture hang disappeared outside the execution sandbox. These checks
do not establish current production protection or a production restore.

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

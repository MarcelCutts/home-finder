# Home Finder: reviewed stack of draft PRs

**Reviewed:** 10 October 2026. **Native GitHub stack:** [#29, visible from the original top draft](https://github.com/MarcelCutts/home-finder/pull/28). **Scope:** 37 implementation commits, presented as 26 draft PRs from `main` at `a86e51e`. The original `codex/pr01-ci-baseline` branch is preserved; the stable-home segment was committed there as `545ad7c`.

**Follow-on implementation:** [PR07b-3a: journaled ownership](2026-10-10-pr07b3a-membership-decisions.md) is draft [#30](https://github.com/MarcelCutts/home-finder/pull/30), position 27; [PR07b-3b: home groups and original personal state](2026-10-10-pr07b3b-home-group-routing.md) is draft [#31](https://github.com/MarcelCutts/home-finder/pull/31), position 28. [PR07b-4a: every listing in text and link consumers](2026-10-10-pr07b4a-listing-members.md) is draft [#32](https://github.com/MarcelCutts/home-finder/pull/32), position 29. The verified listing-input lifecycle is being appended as layer 30; all layers remain drafts in native stack #29; PR titles use stable position numbers as the series grows. Separate verification records cover the follow-on code; the 26 boundary counts below remain the evidence for their original heads.

This index is the review and navigation layer for the [delivery plan](2026-09-28-research-synthesis-and-delivery-plan.md). Stack positions are separate from the plan's original PR numbers. Each draft has a bounded behavior change, an author review, fresh verification, limits and a link to its detailed handoff. None has been approved on behalf of another reviewer, merged or deployed.

## Assessment of the work

The work materially improves the application's foundations: owner access, complete SQLite transaction ownership, retained listing text, honest unknown/partial outcomes, recoverable work, bounded model spending, verified backups and immutable evidence. These contracts make the existing Python/FastAPI/Telegram application more dependable without introducing a new service topology.

The identity changes preserve the distinction between a portal advertisement and a saved home. Exact provider keys expose historical duplicates and ownership conflicts; persisted home IDs now give web and Telegram a durable target. Keeping the 17 ownership conflicts unresolved is intentional. A shared provider ID does not authorize moving another home's assessment, status or price history.

The 50-listing experiment is useful development evidence, but it is not a successful model-quality or hosted-readiness trial. Its 50 selected listings became 35 homes, with no completed assessments before the model failures stopped processing. The separate one-property smoke completed successfully. The direct dedup review supports the 15 accepted pairs and suggests three additional consolidations, but those agent judgments are provisional. Increasing image thresholds produced a false merge before recovering a useful missing match. Automatic thresholds therefore remain unchanged.

For the confirmed target of a modest hosted service introduced gradually, preservation and explainable uncertainty remain the highest-value work. Multi-user isolation, durable delivery, complete source coverage, qualified source access and independent quality labels still need implementation or operating evidence. Passing this stack does not complete the hosted-service goal.

## Why these boundaries

The linear chain preserves the verified development order. It is not a claim that every adjacent UI or parser repair depends semantically on its immediate predecessor. The table distinguishes architectural prerequisites from that mechanical review order.

Large state boundaries remain separate: database ownership, spending, backups, observations, merge preservation, provider identity and home identity. Small commits are grouped when the combined PR ends at a coherent contract: capture with its two Rightmove decoders; transport with phase configuration and output validation; the paid ledger with request enforcement; search with detail observation capture; and the batch experiment with the fixes and dedup review it produced. No application changes were reordered or squashed away to manufacture smaller diffs.

Every reconstructed boundary was compared with its original commit. The application, tests and original documentation match exactly. The only initial packaging delta is the CI PR-base filter for `codex/stack/**`; the final layer also carries this index, its verification record and handoff status updates. New commit IDs are mapped to all 37 original IDs in the [machine-readable verification record](2026-10-10-stack-verification.json).

## Drafts and architectural dependencies

Prerequisites below use **stack positions**, not GitHub or delivery-plan PR numbers. Each draft's Git base is the immediately preceding stack branch; the first targets `main`.

| Position | Draft and behavior | Plan slice | Architectural prerequisites | Fresh focused tests |
| --- | --- | --- | --- | ---: |
| 01 | [#3: Restore reproducible CI and require explicit releases](https://github.com/MarcelCutts/home-finder/pull/3) | PR01 | — | 14 |
| 02 | [#4: Protect owner access and make Telegram callbacks replay-safe](https://github.com/MarcelCutts/home-finder/pull/4) | PR02 | 01 | 287 |
| 03 | [#5: Repair the runtime dependency set and configure map providers](https://github.com/MarcelCutts/home-finder/pull/5) | PR03 | 01, 02 | 118 |
| 04 | [#6: Give each SQLite operation complete transaction ownership](https://github.com/MarcelCutts/home-finder/pull/6) | PR04 | 02, 03 | 78 |
| 05 | [#7: Retain listing text through persistence and model input](https://github.com/MarcelCutts/home-finder/pull/7) | PR06a | 04 | 405 |
| 06 | [#8: Ground tenant costs and guidance in retained evidence](https://github.com/MarcelCutts/home-finder/pull/8) | PR17a | 05 | 588 |
| 07 | [#9: Preserve unknown commute results and advisory behavior](https://github.com/MarcelCutts/home-finder/pull/9) | PR16a | 03 | 306 |
| 08 | [#10: Keep price ranges coherent and bind availability to the listing](https://github.com/MarcelCutts/home-finder/pull/10) | PR08a | 04, 05 | 126 |
| 09 | [#11: Retain fetched listings and report incomplete collection honestly](https://github.com/MarcelCutts/home-finder/pull/11) | PR09a | 04, 08 | 163 |
| 10 | [#12: Recover pending work and exclude overlapping pipeline runners](https://github.com/MarcelCutts/home-finder/pull/12) | PR12a | 09 | 102 |
| 11 | [#13: Capture isolated evidence and repair Rightmove payload decoding](https://github.com/MarcelCutts/home-finder/pull/13) | PR18a / PR10 | 09, 10 | 107 |
| 12 | [#14: Use native model transport with explicit phases and strict output contracts](https://github.com/MarcelCutts/home-finder/pull/14) | PR20a–c | 03, 05 | 328 |
| 13 | [#15: Reserve and account for every paid model attempt](https://github.com/MarcelCutts/home-finder/pull/15) | PR13a–b | 04, 12 | 151 |
| 14 | [#16: Verify complete backups and retire database-file replacement](https://github.com/MarcelCutts/home-finder/pull/16) | PR05a | 04, 10 | 34 |
| 15 | [#17: Build blind development reference packs for assessment review](https://github.com/MarcelCutts/home-finder/pull/17) | PR19a | 11, 14 | 42 |
| 16 | [#18: Add immutable source observations and historical baselines](https://github.com/MarcelCutts/home-finder/pull/18) | PR06b-1 | 04, 14 | 39 |
| 17 | [#19: Retain search and detail revisions before downstream work](https://github.com/MarcelCutts/home-finder/pull/19) | PR06b-2/3 | 11, 16 | 390 |
| 18 | [#20: Derive current listing text from retained evidence](https://github.com/MarcelCutts/home-finder/pull/20) | PR06b-4 | 05, 17 | 204 |
| 19 | [#21: Verify raw replay references and expire managed captures explicitly](https://github.com/MarcelCutts/home-finder/pull/21) | PR06b-5 | 11, 17 | 78 |
| 20 | [#22: Preserve source area units, ranges and minimums](https://github.com/MarcelCutts/home-finder/pull/22) | PR06b-6 | 15, 17 | 79 |
| 21 | [#23: Separate request context from material listing revisions](https://github.com/MarcelCutts/home-finder/pull/23) | PR06b-7 | 17, 19 | 104 |
| 22 | [#24: Add observable bounded model smoke runs and repair feature objects](https://github.com/MarcelCutts/home-finder/pull/24) | PR10 / bounded smoke | 11, 13, 21 | 362 |
| 23 | [#25: Protect established property history during duplicate merges](https://github.com/MarcelCutts/home-finder/pull/25) | PR07a | 04, 13, 17 | 42 |
| 24 | [#26: Exercise bounded batches and retain the deduplication review evidence](https://github.com/MarcelCutts/home-finder/pull/26) | Batch / dedup review | 11, 22, 23 | 63 |
| 25 | [#27: Retain exact provider identities and audit conflicting home ownership](https://github.com/MarcelCutts/home-finder/pull/27) | PR07b-1 | 16, 23, 24 | 101 |
| 26 | [#28: Give saved homes durable identities and short web and Telegram links](https://github.com/MarcelCutts/home-finder/pull/28) | PR07b-2 | 02, 23, 25 | 124 |

## Verification and findings

All 26 boundaries passed fresh Ruff lint, Ruff formatting, strict mypy, actionlint and relevant network-blocked tests: **4,435 focused test executions** across the stack. This number includes tests repeated at different boundaries; it is not 4,435 newly written or independent tests. Three separate locked dependency environments were installed using each boundary's configured uv version. Every test run asserted that imports came from that boundary's isolated source checkout, preventing an editable install from silently testing the original working directory.

The latest application passed **3,259 tests on each of Python 3.14.7 and 3.11.16**, with **88.32% and 88.40%** branch-inclusive coverage. The earlier implementation handoffs contain their own dated full matrices, migration rehearsals and browser evidence; those historical results are not presented as fresh full runs of every draft.

Because remote CI was unavailable, the bottom PR additionally received a fresh full Python 3.14.7 run: **2,378 passed**, **85.20%** branch-inclusive coverage.

Source archives and wheels were rebuilt at positions 01, 03, 12 and 26, covering the baseline, dependency transitions and final application. All tracked application files were compared byte-for-byte against the respective wheels: **92, 94, 111 and 141 files**. The three changed offline-review Python scripts also pass Ruff lint/format. The source-distribution allowlist excludes unrelated local research, logs and databases.

The stable-home copy rehearsal preserved every original row in both retained databases, all **1,016 historical homes**, and all **201 historical aliases**. Reinitialization changed no rows or IDs, and both copies passed integrity and foreign-key checks. Network-blocked replay reproduced the same 35 homes and all 50 selected memberships, with no new model work or changes to the original evidence. Private UUID maps, database copies, captures and property images remain outside Git.

An initial full matrix exposed three test-fixture assumptions: a historical schema seeded through the current writer, and two SQL interceptors that lost aiosqlite's context-manager interface. Those fixtures were repaired before the identity commit; their rollback and migration assertions remain intact. The final matrix is fully passing.

**Remote CI is unrun, not green.** The GitHub API reports workflow `262027904` as `disabled_inactivity`; repository-level Actions remains enabled. `main` still contains the old Sunday live-portal schedule and push-triggered deployment job, so workflow activation is left for review of the first PR. GitHub documents inactivity disabling and default-branch scheduling in its [workflow activation guidance](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/disable-and-enable-workflows) and [schedule event reference](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule). No remote settings were changed to hide or bypass missing checks.

Current native stacks inherit their trunk's CI rules. The added explicit branch filter supports the ordinary chained-PR interval during creation and remains useful if the stack is unlinked; it does not activate a disabled workflow. This distinction was checked against the current [native stack reference](https://docs.github.com/en/pull-requests/reference/stacked-pull-requests). All 26 remote draft flags, branch heads, base branches, native positions and the stack's `main` trunk were verified after linking. Existing Dependabot PRs #1 and #2 remain untouched.

All 57 pre-existing user-owned files, including `.env.example` edits and the research corpus, have preservation hashes. Their uncommitted contents are excluded from the stack. Some historical synthesis links therefore still refer to owner-local research files; the committed implementation handoffs and this index are the shared review record. Publishing the stack does not imply publishing that separate corpus.

## Author review of each layer

These are scoped implementation reviews, not independent approvals or a claim that every parent epic is complete. The limits state what a reviewer must not infer from a passing slice.

### 01 — [#3: Restore reproducible CI and require explicit releases](https://github.com/MarcelCutts/home-finder/pull/3)

Every later review needs a reproducible build and a release path that cannot deploy merely because a branch changes. Pin tooling/actions, verify a cold locked install and distribution contents, document the delivery contract, and move deployment behind an explicit main-only workflow. Extend PR validation to this stack's base-branch namespace.

**Review:** Checked event filters, minimal workflow permissions, release conditions, package inclusion, and the temporary SDK bound. The stack filter is the only packaging change to this original segment.

**Limit:** This is an operating baseline, not security approval for the old runtime. Owner access and dependency repairs follow before any deployment.

### 02 — [#4: Protect owner access and make Telegram callbacks replay-safe](https://github.com/MarcelCutts/home-finder/pull/4)

The dashboard and mutation endpoints need an explicit access boundary before further user-state behavior is added. Enforce owner access across pages/assets and mutations, verify Telegram credentials and identity, retain callback update IDs, and suppress oversized legacy callback payloads.

**Review:** Reviewed constant-time credential comparison, request-origin enforcement, bounded request bodies, mounted-file protection, webhook sender/chat binding, and atomic replay detection. Long legacy IDs remain link-only until the final stable-home PR.

**Limit:** Single-owner protection is not multi-user isolation. Actual ingress/environment setup remains a deployment check.

### 03 — [#5: Repair the runtime dependency set and configure map providers](https://github.com/MarcelCutts/home-finder/pull/5)

A protected service still needs compatible, audited dependencies and map behavior that does not assume unavailable provider credentials. Update the compatible runtime group, verify Python 3.11/3.14 and both container targets, tighten packaging/runtime privileges, and make tile-provider activation explicit.

**Review:** Reviewed grouped lockfile updates, SDK compatibility boundary, map URL/key escaping and CSP, and the recorded residual native/Crawlee findings. Existing browser screenshots and fixture-based tile checks document visual behavior.

**Limit:** The dated dependency/container audit is not perpetual certification. Real map entitlements, origin restrictions, and current image findings remain rollout gates.

### 04 — [#6: Give each SQLite operation complete transaction ownership](https://github.com/MarcelCutts/home-finder/pull/6)

Concurrent dashboard and pipeline calls could commit or roll back another task's work; all later durable state relies on fixing that first. Own complete read/write scopes, pass explicit handles for composition, and finish commit/rollback/close cleanup before releasing connection ownership.

**Review:** Reviewed cancellation during begin/commit/rollback, child-task handle rejection, query-only reads, close failures, and real interleaving tests. This is the architectural base for every subsequent migration and ledger.

**Limit:** SQLite access is deliberately serialized per storage object. This does not introduce a distributed worker or PostgreSQL.

### 05 — [#7: Retain listing text through persistence and model input](https://github.com/MarcelCutts/home-finder/pull/7)

Description and feature loss makes later model output less useful regardless of model capability. Carry source-specific description/features through merge, persistence, reload, enrichment and analysis; distinguish absent text from deliberately empty evidence.

**Review:** Traced the source-to-prompt path, source separation, retry reloads, and legacy compatibility. The initial text representation is intentionally replaced by an observation-backed projection later in the stack.

**Limit:** Retaining text fixes an input defect; it does not prove historical assessment accuracy or complete evidence provenance.

### 06 — [#8: Ground tenant costs and guidance in retained evidence](https://github.com/MarcelCutts/home-finder/pull/8)

The rental decision UI must distinguish advertised tenant obligations from estimates and unsupported advice. Remove unsupported tenant service-charge and negotiation assumptions, constrain council-tax use to retained evidence, and update dashboard/Telegram explanations and dated guidance.

**Review:** Reviewed arithmetic and source support across model, cost calculator, templates and notifications. Committed desktop/mobile screenshots show the revised presentation; primary guidance checks are dated in the handoff.

**Limit:** Legal and market guidance needs continuing review. Estimates are not an individual tenancy quote.

### 07 — [#9: Preserve unknown commute results and advisory behavior](https://github.com/MarcelCutts/home-finder/pull/9)

A failed route calculation must not appear as a zero-minute journey or silently exclude a promising home for a WFH user. Represent route outcome categories explicitly, keep commute advisory, and show uncertainty consistently in the dashboard, maps and Telegram.

**Review:** Reviewed provider errors, absent coordinates, request-boundary semantics, unknown rendering, and end-to-end admission. Existing mobile/desktop evidence covers the UI change.

**Limit:** Route caching, profile-specific travel preferences and provider retention entitlements remain separate work.

### 08 — [#10: Keep price ranges coherent and bind availability to the listing](https://github.com/MarcelCutts/home-finder/pull/10)

Price and availability changes need to refer to the exact advertisement before they can safely update a saved home. Update persisted min/max prices consistently and reject availability evidence belonging to a different listing or an unqualified response.

**Review:** Reviewed single-source and multi-source range changes, price-history retention, identity binding, and fail-open handling of unclear portal responses.

**Limit:** This repairs current scalar behavior; complete membership-aware price and availability projections remain PR08b work.

### 09 — [#11: Retain fetched listings and report incomplete collection honestly](https://github.com/MarcelCutts/home-finder/pull/11)

A partially failed collection run should preserve useful receipts without reporting false completeness. Persist received listings across partial source/page failures and cancellation, and distinguish completed, partial and failed run accounting.

**Review:** Reviewed pagination failure boundaries, retained partial buffers, cancellation propagation and source-run totals. Later observation revisions extend this receipt-preservation contract.

**Limit:** Run accounting is not a qualified source coverage model or evidence that all portals are usable.

### 10 — [#12: Recover pending work and exclude overlapping pipeline runners](https://github.com/MarcelCutts/home-finder/pull/12)

Retained work must resume after failure, and CLI/scheduler entry points must not run conflicting pipelines. Recover persisted enrichment/analysis work, bound retries, enforce cross-entry-point run exclusion, and correct interval scheduling.

**Review:** Reviewed first-run and restart paths, no-new-listing recovery, process-death lock release versus live ownership, cancellation and retry counts.

**Limit:** This is local durable recovery, not the later general work-state machine or distributed execution contract.

### 11 — [#13: Capture isolated evidence and repair Rightmove payload decoding](https://github.com/MarcelCutts/home-finder/pull/13)

Parser repairs need retained, replayable responses so source behavior can be verified without repeated live requests. Add isolated capture/export/replay tooling and decode structured search plus indexed detail payloads with explicit malformed/blocked outcomes.

**Review:** Grouped capture and both parser repairs because the captured format supplies their verification boundary. Reviewed output isolation, snapshot read-only handling, listing-ID binding, and decoder cycle/bounds controls.

**Limit:** A successful fixture is not a source canary, typeahead implementation, permission grant, or production restart.

### 12 — [#14: Use native model transport with explicit phases and strict output contracts](https://github.com/MarcelCutts/home-finder/pull/14)

Transport compatibility, phase configuration and output acceptance must be explicit before durable spending can wrap each physical request. Migrate to the native Anthropic SDK, record per-phase configuration/provenance, and reject refusals, truncation and incomplete required output with typed failures.

**Review:** Reviewed exact request/response mapping, refusal/stop reasons, raw-output limits, no invented required fields, and pending-work preservation. These three commits form one model I/O boundary.

**Limit:** No model-quality improvement is claimed. Historical coercion remains a legacy read concern; fresh responses must satisfy the new contract.

### 13 — [#15: Reserve and account for every paid model attempt](https://github.com/MarcelCutts/home-finder/pull/15)

A request cap is ineffective if retries or interrupted requests can bypass it; admission and settlement need durable state. Add the paid-attempt ledger, policy administration/reporting, conservative reservations and enforcement around each native model request.

**Review:** Reviewed concurrent admission, exact decimal accounting, unknown outcome retention, retry charging, settlement idempotence and disabled-policy behavior. The ledger and its enforcement are reviewed together so the PR ends with an active guard.

**Limit:** Provider usage is an estimate rather than an invoice. Unknown attempts retain reservations; no normal-operation spend policy is silently enabled.

### 14 — [#16: Verify complete backups and retire database-file replacement](https://github.com/MarcelCutts/home-finder/pull/16)

New state is unsafe if an operational sync can overwrite user actions or a restore omits image evidence. Create and verify database/asset backup manifests, rehearse restore, and retire the old push-a-database synchronization workflow.

**Review:** Reviewed online SQLite backup consistency, hash/path validation, restore isolation, and authoritative-store ownership. The original rehearsal preserved the retained database and all 27,517 assets.

**Limit:** A backup rehearsal is not an automatic production restore or migration rollback. Restore destinations must remain isolated until reviewed.

### 15 — [#17: Build blind development reference packs for assessment review](https://github.com/MarcelCutts/home-finder/pull/17)

Model and dedup quality need inspectable source evidence and independent labels rather than agreement with existing output. Select grouped cases, retain exact available images, render blind review packs and provide a rubric with blank human labels.

**Review:** Reviewed deterministic sampling, image provenance, missing-evidence reporting, blind presentation and case/group separation. Private packs are excluded from commits.

**Limit:** The 48-case development pack has not become an independently labeled holdout; no model promotion follows from generating it.

### 16 — [#18: Add immutable source observations and historical baselines](https://github.com/MarcelCutts/home-finder/pull/18)

Changing current rows cannot explain which evidence existed at an earlier assessment; later projections need immutable inputs. Introduce observation/field records with deterministic identity and an additive historical baseline migration without inventing fresh capture times.

**Review:** Reviewed immutability, duplicate and conflicting receipts, transaction rollback, restart idempotence, and preservation of every original row in migration rehearsals.

**Limit:** A historical baseline records what the old database retained, not a reconstruction of missing original portal responses.

### 17 — [#19: Retain search and detail revisions before downstream work](https://github.com/MarcelCutts/home-finder/pull/19)

The immutable ledger only helps when real adapter receipts enter it before filtering, image work or model calls can fail. Capture search revisions and detail evidence with source-specific IDs, dates/branch metadata and assessment evidence references; persist details before image processing.

**Review:** Reviewed source-ID isolation, per-source text, receipt persistence on downstream failure, parser provenance and idempotent replay. Search and detail integration share the same ledger admission boundary.

**Limit:** This does not yet derive every current fact from observations or define collection completeness.

### 18 — [#20: Derive current listing text from retained evidence](https://github.com/MarcelCutts/home-finder/pull/20)

Once revisions are retained, current descriptions/features need deterministic precedence and visible conflicts. Project current text from observations, preserve equal-rank conflicts and supply the stored projection to retries, analysis and the detail page.

**Review:** Reviewed evidence rank versus arrival order, explicit empty values, stale fallback prevention, source conflict display and recovery input consistency.

**Limit:** Non-text projections, markup normalization and full artifact invalidation remain separate contracts.

### 19 — [#21: Verify raw replay references and expire managed captures explicitly](https://github.com/MarcelCutts/home-finder/pull/21)

A normalized observation must point to the exact retained body, and body expiry must not erase the observation's existence. Bind replay evidence to raw hashes and normalized observations, reject corrupt references, and record explicit expiry for managed raw bodies.

**Review:** Reviewed hash/path tampering, retention boundaries, repeat expiry and observation stability when raw bodies are removed.

**Limit:** Retention scheduling and source-specific permissions remain operational work; the command only manages its own capture store.

### 20 — [#22: Preserve source area units, ranges and minimums](https://github.com/MarcelCutts/home-finder/pull/22)

A printed range or minimum cannot safely become a single total floor area used for value judgments. Retain exact decimal units/bounds and keep qualified quantities distinct from scalar totals in parsing, evidence and model input.

**Review:** Reviewed unit conversion, range/minimum semantics, parser fallback and reference-pack compatibility using authored source fixtures.

**Limit:** This does not solve missed floorplan totals or classify whether an image is a genuine floorplan.

### 21 — [#23: Separate request context from material listing revisions](https://github.com/MarcelCutts/home-finder/pull/23)

Receipt clocks and query metadata should explain collection without forcing unnecessary reassessment of unchanged evidence. Retain requested query/page metadata separately from source facts and derive material fingerprints independent of receipt/parser provenance.

**Review:** Reviewed same-fact/different-receipt equivalence, meaningful source changes, parser context and replay stability.

**Limit:** Material fingerprints are groundwork; complete assessment dependency and scheduling invalidation is not claimed.

### 22 — [#24: Add observable bounded model smoke runs and repair feature objects](https://github.com/MarcelCutts/home-finder/pull/24)

After evidence and spend controls, a small isolated run can test the actual pipeline with an explicit request and dollar ceiling. Fix structured Rightmove feature objects and add an isolated one-property smoke with request limits, spend admission, structured stage/model diagnostics and post-run audits.

**Review:** Reviewed configuration isolation, every physical request/retry boundary, redaction, ledger totals and guard failure behavior. The parser fix is retained here because this bounded run exposed it against the newer evidence contract.

**Limit:** The recorded two-request US$0.082314500 experiment is historical and exhausted; it does not authorize further paid runs or change the default model.

### 23 — [#25: Protect established property history during duplicate merges](https://github.com/MarcelCutts/home-finder/pull/25)

A larger dedup experiment must not destroy established assessments, status, price history or cached evidence. Guard established records, retire only eligible empty staging rows atomically, retain source aliases and original caches, and defer protected merge proposals.

**Review:** Reviewed each protected table/state, injected retirement rollback, missing/stale proposals and real copied-database preservation. This is a safety boundary before further identity changes.

**Limit:** Established merges remain blocked. Reversible merge/split decisions are later work, not an exception to the guard.

### 24 — [#26: Exercise bounded batches and retain the deduplication review evidence](https://github.com/MarcelCutts/home-finder/pull/26)

The matching pipeline needs a larger inspectable sample before identity-policy changes are justified. Add the bounded 50-listing runner, repair exhausted Rightmove pages and redacted output diagnostics, and retain offline replay plus the qualitative dedup audit.

**Review:** Grouped the experiment, directly observed repairs and its review so readers can trace each conclusion to the run. Verified same/split-run memberships, known-input no-op behavior, spend-stop behavior, and agent-label limitations. Threshold experiments support leaving automatic matching unchanged.

**Limit:** The historical run produced 35 homes but no complete assessments; three additional consolidations are provisional review findings, not production changes or independent human labels.

### 25 — [#27: Retain exact provider identities and audit conflicting home ownership](https://github.com/MarcelCutts/home-finder/pull/27)

Portal advertisement identity must be recovered without conflating multiple advertisements or silently choosing a historical owner. Add exact provider keys and immutable original-representation claims, retain unresolved cases, and provide a read-only ownership audit.

**Review:** Reviewed exact URL/provider-ID normalization, malformed/URL-shaped legacy IDs, multiple same-portal advertisements and additive backfill. Rehearsals retain all 261 duplicate representations and 17 conflicts across 28 historical homes.

**Limit:** Provider identity is not home identity. Ownership conflicts are surfaced rather than automatically repaired.

### 26 — [#28: Give saved homes durable identities and short web and Telegram links](https://github.com/MarcelCutts/home-finder/pull/28)

Home links must survive staging retirement and long portal IDs before reviewed membership changes can safely follow. Assign persisted home UUIDs, retain exact legacy aliases and guarded staging redirects, resolve mutations inside their write transaction, and use short IDs for dashboard/map/Telegram links while keeping image/history storage keys.

**Review:** Reviewed additive backfill, all canonical save paths, alias ambiguity/cycles, rollback, callback size and authorization, replay protection and cached-evidence lookup. Copy rehearsals preserve every original row, 1,016 homes and 201 historical aliases.

**Limit:** General anchor reassociation and reversible membership decisions remain PR07b-3. This PR also records the stack's review/verification manifest; it does not reconcile historical ownership conflicts.

## Reviewing and landing the stack

Review bottom-up through the native stack map. Each PR compares against the branch immediately below it, keeping its diff focused. Keep drafts until their review is complete. The original implementation branch is a preserved working record, not a second PR to merge alongside the stack.

Before enabling remote CI, review and land the operating-baseline changes that separate deployment and disable automatic live tests. Then enable the CI workflow and obtain the configured remote checks with browser/live inputs left false unless separately qualified. Local verification supports review while that operating change is pending; it does not substitute a fabricated GitHub status.

Use native stack operations for later rebases and merges; inspect changed diffs and rerun relevant checks after a lower layer changes. GitHub's [stack management guidance](https://docs.github.com/en/pull-requests/how-tos/create-pull-requests/managing-stacked-pull-requests) describes cascading rebases and synchronization. Ordinary manual squash/rebase of a lower branch needs corresponding descendant updates. Do not merge upper PRs into predecessors as a substitute for reviewing the stack. No merge or deployment was performed here.

Fresh local verification details and reproduction test selections are in the JSON record. Temporary logs, isolated checkouts, exact-lock environments, build artifacts and publication metadata are under `/tmp/home-finder-2026-10-09/`; they are local artifacts rather than permanent CI storage. Private migration/replay reports remain under `data/home-identity-2026-10-09/` as described in the [stable-home handoff](2026-10-09-pr07b2-stable-home-links.md).

## Follow-on layer 27 — [#30: Journal listing ownership and add reversible review commands](https://github.com/MarcelCutts/home-finder/pull/30)

PR07b-3a adds a single ownership writer, additive immutable decisions, stale-review protection, split/reversal and retained personal-state provenance. It depends architecturally on transaction ownership (#6), merge preservation (#25), provider identity (#27) and stable links (#28). The migration guards and every ordinary writer form one atomic boundary; the old destructive image-audit utility also becomes read-only.

**Author review:** traced all ownership writes, rollback/cancellation, concurrency, retry, source-evidence revisions, original-state preservation, empty reviewed donors, SQLite replacement behavior and maintenance bypasses. **Verification:** 3,317 tests on each of Python 3.11.16/3.14.7, more than 88% coverage, lint/format/mypy/build checks, copy preservation and unchanged offline 50-listing groups. **Limit:** grouped routing/cards, recoverable staging retirement and complete projections still need the following segments; reviewed commands remain for isolated rehearsals. The [handoff](2026-10-10-pr07b3a-membership-decisions.md) and [separate verification record](2026-10-10-pr07b3a-verification.json) contain this layer’s evidence without rewriting the original 26-boundary results.

## Follow-on layer 28 — [#31: Route home groups and preserve original personal state](https://github.com/MarcelCutts/home-finder/pull/31)

PR07b-3b makes reviewed routing, source ownership, work selection and personal actions agree. Groups have one active representative, while retained original homes own their statuses, messages and history. New staging retirement becomes reversible. These consumers must ship together: changing routing alone could transfer actions or duplicate work. Prerequisites are transaction ownership (#6), preservation (#25), provider identity (#27), stable links (#28) and the journal (#30). Complete member-derived caches form the next boundary.

**Author review:** traced every selector and mutation, source/route atomicity, v1 compatibility, chained groups, representative changes, split ambiguity and reversals. Fixed retained-alias preservation, cleanup of absorbed donors, reservation-to-dispatch races and selected-status rendering. **Verification:** 3,348 tests on each of Python 3.11.16/3.14.7, more than 88% coverage, static/type/build checks, synthetic desktop/mobile browser checks, fresh copy rehearsals and unchanged 50-listing replay. **Limit:** representative/portal-keyed caches still need PR07b-4; old physically deleted state cannot be reconstructed, and the 17 historical conflicts remain separate. The [handoff](2026-10-10-pr07b3b-home-group-routing.md), [verification record](2026-10-10-pr07b3b-verification.json) and sanitized screenshots contain the evidence. Missing temporary evidence after an interruption was replaced by fresh verification in persistent `.worktrees/evidence/28-home-groups`; the old `/tmp` paths above are historical locations, not currently available artifacts.

## Follow-on layer 29 — [#32: Preserve every listing in text, assessment inputs and links](https://github.com/MarcelCutts/home-finder/pull/32)

PR07b-4a carries exact retained representations through save/reload, enrichment, bounded model text selection, dashboard claims and Telegram links. Repeated-portal adverts remain individually labelled, and split/reversal removes departed-member text and URLs. The representation and these consumers form one boundary because any remaining collapse in that path would undo preservation. Prerequisites are transaction ownership (#6), text/observations (#7, #18–20), provider/home identity (#27–28), membership decisions (#30), and group routing (#31). Schema remains 014.

**Author review:** traced all queue/anchor reads and exact writes, same-portal prompt inputs, source claims, URL aliases, consistent cache snapshots and UI fallbacks. Fixed ordering, alias validation, missing receipt fixtures, multiline escaping, short-text fading and explicit-empty membership behavior. **Verification:** 3,365 tests on each Python 3.11/3.14 with more than 88% coverage; final UI changes then passed all 335 web tests on each. Five test-only SQLite readers were corrected and their cases passed on both runtimes with resource warnings treated as errors. Static/type/build checks, copied-database preservation, unchanged 50-listing replay and synthetic desktop/mobile browser checks pass. All 150 wheel application files match the checkout.

**Limit:** invalidation consumption, price/availability eligibility, image provenance, assessment freshness and the reconciliation surface remain PR07b-4b. The matcher and all 17 historical conflicts are unchanged. The [handoff](2026-10-10-pr07b4a-listing-members.md), [verification record](2026-10-10-pr07b4a-verification.json) and screenshots record the review and test ordering. Private evidence is in `.worktrees/evidence/29-member-projections`. Remote CI remains unrun because its workflow is disabled.

## Follow-on layer 30 — durable listing input generations and rebuild receipts

PR07b-4b-1 adds schema 015, per-home input generations, immutable listing snapshots, atomically published pointers and receipts for the `listing_inputs` component only. Source refreshes and every ownership/route decision make obsolete inputs detectably stale. A bounded read-only command writes private before/after review artifacts without consuming work. These changes belong together because an acknowledgement without the snapshot and generation fence could lose work. Architectural prerequisites are transaction ownership, retained observations, provider/home identity, membership/routing and exact members; the immediate base is #32.

**Author review:** generation coverage, scoped acknowledgement, replacement guards, checksums, material/receipt separation, transaction rollback/cancellation, separate-connection races, migration/restart and WAL-aware private inspection. Corrected automatic-invalidation versus reviewed-history classification. **Verification:** 3,406 tests on each of Python 3.11/3.14, over 88% coverage; final eight audit tests on each; static/type/archive checks; 1,016 historical home rebuilds and retries; Southborough/Wilmot split/reversal; unchanged offline 50-listing groups. See the [handoff](2026-10-10-pr07b4b1-projection-lifecycle.md) and [verification record](2026-10-10-pr07b4b1-verification.json).

**Activation limit:** this is retained optional groundwork. No automatic rebuild worker runs, and price/availability, asset and assessment consumers are not certified current by `listing_inputs`. The new review's reproduced core defects and stale-assessment activation blocker remain open. Remote CI is still unrun.

## Next implementation after independent review

The owner-local `opus-ultra-findings.md` prompted a priority correction. Against this branch, three synthetic socket-blocked checks reproduce detail evidence assigned to the wrong OTM advert, paid bill packages shown as included in rent, and historical export accepting uncommitted hot-journal pages. Passing full suites does not invalidate those findings. The review's proportionality concern is also sound: proving useful rental discovery should precede further optional reconciliation.

1. Enforce that reviewed regrouping is disabled by default until its derived consumers are safe; preserve explicit copy rehearsals and existing conservative automatic matching.
2. Fix returned-detail identity, qualified bill inclusion and frozen snapshot validity in separate bounded PRs, including the missing adversarial regressions.
3. Resolve intermediate deployment coupling (particularly #7 with #19), prepare safe CI activation and validate actual merge candidates. Keep the old live/deploy workflow disabled until its replacement is safely installed.
4. Prove the core collection → assessment → shortlist → notification flow with a small offline scenario, then qualify any live pilot under explicit source, spending and delivery permissions. Prior paid budgets are not renewed.

The remaining PR07b-4b price/availability, asset, assessment and reconciliation boundaries are a conditional correctness sequence if regrouping becomes necessary. Keep the 17 historical conflicts separate; do not fund the rest solely to finish an architectural programme. Existing stack heads remain preserved rather than being reordered without dependency verification.

The full delivery goal remains active. The hosted destination and original modular application scope are unchanged; this stack is a reviewed implementation increment toward that destination.

# PR disposition — 10 October 2026

This follows the first review pass and the user’s instruction to finish the queue.
Application scope: one owner’s collection → enrichment → assessment → shortlist →
notification flow, with reliable recovery and a small hosted dashboard. A potential
future audience does not justify shipping a multi-user or evidence platform today.

Status: implementation and final validation in progress; outcomes below are the
selected actions. The completion record will identify the merged head and checks.

## Each open PR

| PR | Selected action | Reason |
| --- | --- | --- |
| [#5](https://github.com/MarcelCutts/home-finder/pull/5) | Include in repaired #16; close as superseded | A compatible locked runtime, shared container targets and configurable maps are useful. Keep graceful map failure; avoid a separate hosting platform. |
| [#7](https://github.com/MarcelCutts/home-finder/pull/7) | Include with receipt repairs; close as superseded | Advert text must survive collection and reach assessment. Fix fresh search receipts and detail precedence in current-value columns, without an observation journal. |
| [#8](https://github.com/MarcelCutts/home-finder/pull/8) | Include with cost corrections; close as superseded | Tenant budgets need supported evidence. Keep unknowns explicit, reject optional paid packages as rent-inclusive, and avoid fabricated utility totals or bargaining claims. |
| [#9](https://github.com/MarcelCutts/home-finder/pull/9) | Include; close as superseded | Unknown or failed commute results must remain distinct from measured travel times and must not silently hide otherwise useful homes. |
| [#10](https://github.com/MarcelCutts/home-finder/pull/10) | Include; close as superseded | Coherent source price ranges and target-bound availability prevent false bargains and false removals. HTTP success alone is not availability evidence. |
| [#11](https://github.com/MarcelCutts/home-finder/pull/11) | Include with pagination repair; close as superseded | Persist fetched listings before downstream filtering and report incomplete collection honestly. A known page must not suppress unseen later pages. |
| [#12](https://github.com/MarcelCutts/home-finder/pull/12) | Include with recovery repairs; close as superseded | Pending enrichment, analysis and notification work must survive interruption. Keep one runner and completion-relative scheduling; no new distributed scheduler. |
| [#13](https://github.com/MarcelCutts/home-finder/pull/13) | Extract production parsers; close original bundle | Keep Rightmove decoding and fixture-based contracts. Drop the capture/replay/export research CLI, including its unsafe immutable-export path. |
| [#14](https://github.com/MarcelCutts/home-finder/pull/14) | Include with output/recovery corrections; close as superseded | Use native SDK transport, explicit phases and meaningful required output validation. Accept equivalent JSON integer forms; never publish malformed assessments. |
| [#15](https://github.com/MarcelCutts/home-finder/pull/15) | Include with spend corrections; close as superseded | Durable paid-attempt accounting prevents duplicate or unbounded charges. Preserve unknown-charge holds; provider failures do not consume property output-failure allowances. |
| [#16](https://github.com/MarcelCutts/home-finder/pull/16) | Repair existing PR and merge after validation | Use this existing PR for the coherent pre-observation-ledger core, including verified backups and isolated restore. Retire destructive database-file replacement and preserve the old PR head. |
| [#17](https://github.com/MarcelCutts/home-finder/pull/17) | Close; preserve branch | Blind evaluation packs and their product surface are premature infrastructure. Offline contract tests and separately scoped model qualification are enough for this repair pass. |
| [#18](https://github.com/MarcelCutts/home-finder/pull/18) | Close; preserve branch | An immutable source-observation database adds growth, migrations and retention work without a demonstrated current product need. Keep current values and ordinary history. |
| [#19](https://github.com/MarcelCutts/home-finder/pull/19) | Salvage receipt semantics; close original bundle | Actual acquisition timestamps and persistence-before-processing are necessary and retained. Raw revision capture and observation-ledger coupling are not. |
| [#20](https://github.com/MarcelCutts/home-finder/pull/20) | Salvage text precedence; close original bundle | Keep deterministic detail-over-summary and newer-within-authority rules in existing current-text fields. Full retained-evidence projection machinery is unnecessary. |
| [#21](https://github.com/MarcelCutts/home-finder/pull/21) | Close; preserve branch | Managed raw capture verification, retention and replay solve a research-platform problem. They do not need to run in the rental service. |
| [#22](https://github.com/MarcelCutts/home-finder/pull/22) | Salvage area parsing; close original bundle | Ranges/minimums must not masquerade as exact areas, and ordinary dual-unit rounding should work. Keep those semantics without a historical quantity ledger. |
| [#23](https://github.com/MarcelCutts/home-finder/pull/23) | Close; preserve branch | Request-context versus material-revision bookkeeping depends on the unselected observation ledger; it has no standalone deployed consumer. |
| [#24](https://github.com/MarcelCutts/home-finder/pull/24) | Extract bounded requests and parser repair; close bundle | Input limits and redacted runtime diagnostics make explicit budgets usable. Do not introduce another model, new reasoning mode or paid smoke-run harness without qualification. |
| [#25](https://github.com/MarcelCutts/home-finder/pull/25) | Include staging history guards; close as superseded | Automatic merging must not erase established owner/delivery/analysis history. Absorb only unused pending donors and queue assessed-anchor reanalysis in the same transaction. |
| [#26](https://github.com/MarcelCutts/home-finder/pull/26) | Extract exhaustion/diagnostic fixes; close bundle | Keep correct exhausted-page parsing and redacted output diagnostics. Batch tooling and raw review datasets are not needed in the application or main history. |
| [#27](https://github.com/MarcelCutts/home-finder/pull/27) | Salvage exact advert identity; close infrastructure | Retain exact portal URL/ID agreement and guarded merges. Claim-ledger migrations and identity-review tooling add more state than this owner workflow needs. |
| [#28](https://github.com/MarcelCutts/home-finder/pull/28) | Close; preserve branch | Stable-home route migration introduces another identity authority. Existing property URLs and conservative history guards remain the supported scope. |
| [#30](https://github.com/MarcelCutts/home-finder/pull/30) | Close; preserve branch | Reversible membership journals and review commands need a justified operator workflow and projection lifecycle; routine collection does not need them. |
| [#31](https://github.com/MarcelCutts/home-finder/pull/31) | Close; preserve branch | Routing through home groups adds state migration and personal-state ambiguity. Keep the current single-owner property model and preserve established records. |
| [#32](https://github.com/MarcelCutts/home-finder/pull/32) | Close; preserve branch | Member-wide projections depend on unselected grouping infrastructure. Retain source-specific text/links through the existing bounded model. |
| [#33](https://github.com/MarcelCutts/home-finder/pull/33) | Salvage atomic handoff; close infrastructure | Persist accepted detail text before media work and reanalysis flags with merges. The broader revision/projection lifecycle is unnecessary when regrouping is not shipped. |
| [#34](https://github.com/MarcelCutts/home-finder/pull/34) | Close; feature absent from selected core | The default-off gate correctly contains unfinished regrouping, but the selected application does not expose that feature at all. No activation API is needed. |
| [#35](https://github.com/MarcelCutts/home-finder/pull/35) | Extract and verify advert binding; close as superseded | Requested/final/page/payload identity must agree. Keep a pure provider-ID utility and correct secondary-portal request IDs, without importing home identity tables. |
| [#37](https://github.com/MarcelCutts/home-finder/pull/37) | Merge independently after refreshed checks | The pinned artifact-action patch is narrow and upstream verified. Update it against current main and satisfy the same required checks. |
| [#39](https://github.com/MarcelCutts/home-finder/pull/39) | Integrate compatible lock; close as superseded | The grouped raw bump fails typing on old code. Adopt selected updates with the notifier/parser compatibility repairs and full integrated checks. |
| [#40](https://github.com/MarcelCutts/home-finder/pull/40) | Integrate compatible lock; close as superseded | The logging major update is compatible with the application APIs; verify it alongside the combined runtime rather than creating another drifting lock. |
| [#41](https://github.com/MarcelCutts/home-finder/pull/41) | Integrate native SDK update; close as superseded | The raw major bump breaks old transport types. Anthropic 1.11 is tested with the native httpx2 request path, billing middleware and output contracts. |
| [#42](https://github.com/MarcelCutts/home-finder/pull/42) | Integrate compatible lock; close as superseded | The type checker major update is useful only with a clean strict check of the final source and compatible locked tooling. |

## Verification gates

- Preserve the already merged owner controls and transaction ownership.
- Exercise collection failure/retry, cross-portal identity, history-preserving merges, retained text, availability and price regressions.
- Exercise native model transport, output contracts, budget admission, provider failures, unknown-charge holds and missing-image recovery with mocked providers.
- Verify schema 7→9 preserves owner decisions and history; prove consistent backups and isolated rollback.
- Run strict types, lint/format, complete offline coverage on Python 3.11 and 3.14, dashboard browser tests, package/container build and non-root runtime smoke.
- Independently review the frozen candidate with Astra; merge only a current head with required hosted checks.

## Repository policy and operational boundaries

Main now requires lint, typecheck, test and build checks on an up-to-date branch,
including administrators. Force pushes and deletion are disabled. The production
environment accepts main only. Automatic merging remains disabled.

The legacy repository deployment secret remains unchanged: automatic approval
review requires separate authorization to delete that specific credential. No
production deployment, live portal collection or paid model run is included.
The release workflow requires an explicitly configured production-environment
credential. See [operations](../operations.md) for activation and rollback.

## Task method

The user requested research and use of Astra Ultra’s task approach. Official
[Ultra guidance](https://learn.chatgpt.com/docs/models#choose-a-model) describes
parallel subagents; [long-running work guidance](https://learn.chatgpt.com/docs/long-running-work)
calls for explicit outcomes, constraints, verification and separate write ownership.
Three implementation lanes own separate modules; a separate Astra task challenges
scope and the frozen merge candidate. Its initial review reproduced two narrow
recovery gaps (atomic reanalysis enqueue and missing-floorplan/no-image handling),
which are addressed through existing transactions and queues rather than new systems.

Original private reports and raw listing research are not part of this publication.
Closed PR branches are retained; closed does not mean their historical work is lost.
Earlier #36/#38 merged CI and owner/transaction controls, superseding #1/#2/#3/#4/#6.

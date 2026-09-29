# PR07a: preserve history before retiring merge candidates

Date: 29 September 2026. Parent: [PR07 in the delivery plan](2026-09-28-research-synthesis-and-delivery-plan.md#pr07--reconcile-provider-identity-and-make-canonical-merges-reversible-l).
Preceded by the [bounded live pipeline and observability work](2026-09-29-bounded-pipeline-smoke.md), committed as `b6bddb0` after parser repair `16c4633`.

## Problem and bounded outcome

Both retroactive deduplication and cross-run retry cleanup could delete established property history. The maintenance command updated an anchor, committed, copied files, then separately deleted the absorbed property's images, assessment, status events, viewing message, price history and enquiry log. Source aliases could cascade away too. The retry path inferred deletion from `re_enrichment_ids - genuinely_new_ids`, even when the deduplicator had never established a successful destination for a retry.

This slice replaces those destructive merge paths with a preservation gate and one atomic staging operation. Existing homes with history remain separate until PR07's reviewed, reversible membership operation exists. This does not introduce a new fuzzy matcher or perform historical reconciliation.

## Rules and transaction ownership

`absorb_staging_properties` can retire a property row only when it is unused staging data:

- Enrichment is `pending`, notification is `pending_enrichment`, and no notification timestamp exists.
- User state is `new` and there are no analysis attempts.
- No property images, quality assessment, price history, status events, saved viewing message, enquiry log, source aliases pointing to it, or paid attempts reference it.
- Every requested source identity exists. Its current owner must be the destination, an explicitly included staging member, or no canonical property. A source linked to an unmentioned canonical property cannot be taken.
- The destination exists. It is excluded from retirement even if mistakenly repeated in the absorbed IDs.

A source may be unpromoted and have no `properties` row. It can still be associated with the anchor, subject to ownership and history checks. Paid-attempt references are checked even in this case.

The storage layer owns a `BEGIN IMMEDIATE` transaction. It checks the **entire group before changing anything**, moves all source rows owned by each absorbed staging property (not just the matching source ID), rebuilds the anchor's existing caches/text/images, and retires the staging property rows. A helper explicitly joins the same transaction instead of independently committing. A simulated failure during the final deletion leaves memberships, projections and histories unchanged.

The older `update_merged_sources(..., absorbed_ids=...)` entry point applies the same admission checks; it cannot bypass protection when reassociating an established identity. Standalone `delete_property` is restricted to unused staging cleanup and never deletes protected children. Its source observations remain independent, and a surviving source row becomes unlinked by the existing foreign key policy.

The former post-merge relinking pass and set-difference deletion loop are removed. Only IDs actually retired by the committed operation count as consumed retries. A candidate omitted from the deduplicator's output is retained as independent work; absence is not proof of absorption. Groups involving multiple established anchors are deferred explicitly. Original candidate objects remain processable when a proposed merge is refused, without substituting the combined proposal for their own source data.

Original image-cache directories are retained after successful staging merges. Copying images to the anchor still occurs after the database transaction. Filesystem copying, price-change effects and reanalysis intent are **not** made atomic by this slice; failures there need the later artifact/work contracts. Keeping original files preserves recovery material and avoids pretending SQLite can commit filesystem operations. Managed cache cleanup belongs to PR14/retention work.

## Observability and operator behavior

| Event | Meaning |
| --- | --- |
| `canonical_merge_deferred` | No membership change accepted. Contains anchor(s), proposed absorbed IDs and bounded reason categories or per-property blockers. |
| `merge_projection_prepared` | Cache/text projection work has run inside the owning transaction; it is not a commit acknowledgement. |
| `canonical_merge_committed` | The transaction completed. Includes all associated IDs, the subset of property rows actually retired, and original-cache retention. |
| `canonical_merge_interrupted` | Unexpected failure/cancellation. Includes exception type and an instruction to inspect membership before retrying; cancellation may arrive while commit acknowledgement is being received. |
| `cross_run_property_fates` | Distinguishes genuinely new work, represented new groups, committed anchor absorption, consumed retries and `retained_for_review`. |
| `dedup_deferred` property event | Durable per-property diagnostic when an event recorder is active. |
| `dedup_complete` | Separates proposed absorptions, actual retirements, deferred candidates and remaining loaded properties. |

`--dedup-existing` therefore reports established duplicate proposals while retaining their records. The command currently loads established anchor candidates, so their automatic retirement is blocked. A refusal is an expected review outcome, not successful deduplication. Operators must not work around it by clearing history, changing state columns or calling SQL deletion manually. Reversible membership review is the next PR07 slice.

## Production-copy rehearsal

The rehearsal copied `data/backup-rehearsal-2026-09-28/fresh-backup/properties.db` to a new private directory, migrated **only the disposable copy**, then compared row counts and ordered row-content hashes before and after the real maintenance entry point. `pytest_socket` blocked external networking and image hashing was disabled. No model, source, route or notification request was made.

The complete comparison is retained in `data/pr07-preservation-rehearsal-2026-09-29-v2/report.json`. The earlier rehearsal used a subset of tables; the `v2` report supersedes it by comparing every application table, excluding SQLite's internal statistics tables. The reproduction script and console log are `/tmp/home-finder-smoke/pr07_rehearsal.py` and `pr07-rehearsal-v2.log`.

The matcher loaded **1,015 eligible properties** from **1,016 stored properties**, proposed **19 absorptions across 18 groups**, and committed **zero**. All 19 proposed retirements were blocked by established state, images and quality assessments; one also had alias history, and another had user/status history. The command's `remaining=1015` refers to its loaded set, not total database rows.

All application-table comparisons matched. Retained data includes:

| Data | Rows preserved |
| --- | ---: |
| Properties | 1,016 |
| Source listings | 3,267 |
| Historical source-observation baselines | 3,267 |
| Source aliases | 201 |
| Property image references | 15,872 |
| Quality assessments | 1,001 |
| Price history | 106 |
| Status history | 30 |
| Saved viewing messages | 1 |
| Property events | 43,521 |

The historical copy contains no new-style observations or paid attempts; those preservation paths are exercised with explicit fixtures. All **17 cross-canonical duplicate-URL groups** remain present for reviewed reconciliation. The 19 matcher proposals are a different set/measure and must not be confused with those 17 URL groups.

The original backup remains byte-identical at SHA-256 `e1494adf0f5b174cd9a2e142f50aa25b8ffd5a85ba3f4d66383a3e8762cea639`. The disposable database reports integrity `ok` and zero foreign-key errors. No production cutover, archive rewrite, remote execution or source activation occurred.

## Verification

Focused storage, preservation and cross-run integration checks: **138 passed**. The new preservation suite covers every protected history category, whole-group refusal, source ownership conflicts, preservation of all member source rows, repeat invocation, injected deletion failure, cache retention, omitted/unrelated retries and ambiguous anchors. Existing tests that expected destructive established-property deletion now assert retained records and explicit refusal accounting.

Both full network-blocked suites passed **3,139 tests**: Python **3.14.7** at **88.06%** branch-inclusive coverage and Python **3.11.16** at **88.15%**. Logs and coverage XML are under `/tmp/home-finder-smoke/`, named `pr07-full314.log`, `pr07-full311.log` and `pr07-coverage314.xml` / `pr07-coverage311.xml`.

The final **138-test** focused set was rerun with populated observation receipts and field rows already projected before deduplication. This verifies retained evidence without relying on an empty ledger. CLI help was aligned with the changed maintenance behavior and inspected directly. No functional application code changed after the full coverage runs. Ruff lint and formatting passed for **233 files**; strict mypy passed for **101 source files**. Whitespace and local handoff links also passed.

## Next slice and rollback

**Next: PR07b, provider identity and reversible canonical membership.** Add stable internal identity, exact provider-ID/URL/legacy alias reconciliation, reviewable unresolved groups, a persistent membership-decision history, legacy link/callback resolution and merge/split semantics for conflicting user state. Build and inspect a dry-run map before changing the 261 historical duplicate-URL groups. The six affected historical price histories and all other user history must survive. Preserve the original matcher until a separate accuracy evaluation justifies changes.

This guard is not the complete PR07 outcome. It does not reconcile historical groups, introduce stable internal IDs, make established merges reversible, redesign every source-write API, or finish the non-text projections/work/artifact contracts. Continue to PR08b/09b only when their identity dependencies are satisfied.

No migration is added. Reverting the implementation would restore destructive behavior; keep the maintenance operation inactive if reverting, and preserve its reports and original caches. Do not roll back by restoring an old database file over newer user state. The original production backup and all requested user edits remain untouched.

# PR07b-3a: journaled listing ownership and reversible review commands

**Date:** 10 October 2026. **Base:** `codex/stack/26-stable-home-links` at `3eec6fec59b7a429294a96bcc445f27d2c29a58c`. **Branch:** `codex/stack/27-reversible-membership`. **Application commit:** `6798c76`. **Draft:** [#30](https://github.com/MarcelCutts/home-finder/pull/30), position 27 in native stack #29. This is the first implementation slice of PR07b-3, following the [reviewed original 26-PR stack](2026-10-10-stacked-pr-review.md). The final publication commit changes documentation only.

The ownership transaction contract is implemented. Reviewed merges, splits and reversals preserve original home records, issued links and personal/history rows. Ordinary saves cannot silently take a listing back from its reviewed owner. Migration 013 retains the historical ownership conflicts without resolving them.

**PR07b-3 remains open.** This slice exposes reviewed commands as a Python repository API for isolated rehearsals. It does not publish a mutation endpoint or enable established-home reconciliation in the running pipeline. Reviewed home grouping/routing and recoverable staging retirement remain PR07b-3b; complete member-based display/evaluation projections remain PR07b-4. Finish those consumers before applying reviewed decisions to an active catalogue.

## Why this boundary

An audit log beside the old SQL updates would not protect reviewed decisions. Five canonical save paths, explicit linkage, URL linkage, staging absorption and staging cleanup all previously had ways to establish or change ownership. The migration, journal, guards and those writers therefore belong in one PR: installing the guards without adapting the writers would break ordinary collection.

General home grouping has a different consumer boundary. The current cards, retry queues, price/image caches and portal-keyed dictionaries cannot yet describe every consequence of a reviewed merge. Keeping original home rows and recording projection invalidations makes that remaining work explicit. This PR does not hide it behind deletion, overwrite a saved status, or claim the dashboard is already consolidated.

The old `scripts/audit_merges.py` was an additional bypass found during final review. It opened SQLite without the application's transaction/foreign-key configuration, deleted properties and assessments by default, and removed cached images based on zero shared photographs. The Wilmot evidence already disproves that identity inference. The command is now always read-only, opens SQLite with `mode=ro`, reports review/coverage outcomes and retains caches. Its old `--dry-run` argument remains compatible. The source distribution explicitly includes this utility and its regression tests.

## Contract and storage

| Part | Responsibility |
| --- | --- |
| `models/membership.py` | Frozen, versioned commands, complete before/after maps, personal-state summaries and receipts |
| `db/membership_schema.py` | Migration 013, retained baselines/origins, journal and projection guards |
| `db/membership_ops.py` | The single transaction-local ownership writer used by pipeline and review paths |
| `db/membership.py` | Consistent review preparation, application, reversal and replay audit |
| `storage.memberships` | Repository API; `identities.members` also accepts stable home IDs now |

`membership_baselines` records the owner actually present at migration or the initial unowned state of a new source row. This is a retained baseline, not a claim about the first owner in an unrecorded past. `membership_origins` retains the first owner known to this journal. Neither collapses raw source representations or asserts that the 17 conflicting provider groups are one home.

`membership_decisions` retains the command ID, schema version in the payload, operation, actor, reason, evidence references, expected revision, complete reviewed payload, optional reversal target and recording time. `membership_changes` records each actual old/new home assignment. `source_memberships` is the current stable-home projection; `source_listings.merged_id` remains the compatible legacy projection. Both update in the same owning transaction. Reviewed decisions also append `membership_projection_invalidations` for every affected home.

Journal/baseline/origin evidence cannot be updated, deleted or replaced through ordinary SQL. Current ownership changes must correspond to a recorded change with a newer revision. New sources enter unowned and acquire ownership through the journal. Source renaming, owned-source deletion/replacement and unjournaled reassignment are refused. A reviewed home with no remaining members is protected from staging retirement, because a future split or reversal may need it.

Existing automatic matching policy is unchanged. Ordinary linkage permits an unowned source or an exact no-op; taking another home's member requires review. URL matching retains its existing unowned-only scope. Staging changes still run PR07a's live preservation gate before the journal writer. Cleanup explicitly journals unlinking before deleting an eligible unused staging row. Historical migrations run before the new guards are installed.

## Review, reversal and personal state

Preparation captures a complete member map for the scoped homes under a short `BEGIN IMMEDIATE` transaction. It includes a global ownership revision, a digest of persisted source rows and retained observation/identity claims, and the original homes' status/message summaries. New ownership, new evidence, changed status history, changed saved messages, missing members or changed bindings make an unapplied review stale. The global revision is deliberately conservative for this modest service; unrelated ownership changes can also require a fresh review.

`prepare_merge` requires at least two populated owners and one destination. `prepare_split` moves an explicit subset while retaining members in the original home. Destinations must already be saved homes. Several IDs from one portal remain distinct members. The caller supplies a nonempty actor, reason and bounded evidence references; those references document a judgment rather than automatically proving its correctness.

`apply` revalidates deserialized or copied models, compares the complete live snapshot, and records the command and both ownership projections atomically. A reused decision ID with different content is rejected. Retrying the exact committed command returns its original receipt with `replayed=True`, even after a later split; it never reapplies the old merge. That receipt acknowledges a historical commit, not the current grouping.

`prepare_reversal` constructs the inverse from recorded changes. It refuses an unknown/unreviewed operation, a decision already reversed, or a member subsequently touched by another ownership decision. A reversal is a new immutable decision and can itself be reversed. Automatic links/cleanup and old staging redirects are not offered as reversible reviewed commands in this slice; no deleted property is reconstructed from guesses.

Personal state stays attached to its original home/property. No statuses, event notes, saved messages, assessments, observations, prices, spending or delivery rows are transferred or overwritten by reviewed commands. `inspect` exposes member origins and conflicting statuses/message hashes. Tests exercise a status action through an old home link after a merge, followed by a split; the action remains with that original home. This is preservation and an inspection API. A coherent grouped dashboard and ambiguous group-action behavior still need PR07b-3b.

The original `homes` bindings, aliases and staging-redirect evidence remain intact. A reviewed donor's old URL therefore still opens its original saved record in this slice. Do not describe it as a finished redirect-to-group experience.

## Research and author review

SQLite allows one writer at a time, and `BEGIN IMMEDIATE` acquires the write transaction before review validation. That supports the existing owned-transaction pattern and ensures competing connections cannot both apply different decisions against one prior revision. Preparation also uses that scope so its several reads cannot straddle another writer's commit. [SQLite transaction documentation](https://www.sqlite.org/lang_transaction.html).

A trigger's `RAISE(ABORT)` aborts the statement; it does not replace the application transaction's rollback responsibility. The owning storage context handles the whole operation, including cancellation. Tests inject failures after journal work and in the legacy projection, then compare every table. Prepared logs are not called committed logs. [SQLite trigger documentation](https://www.sqlite.org/lang_createtrigger.html).

`INSERT OR REPLACE` can remove a conflicting row without firing a deletion trigger when recursive triggers are disabled. The new evidence tables therefore reject replacement at insertion too; dedicated tests cover replacement of evidence, an owned source and an emptied reviewed home. [SQLite conflict-resolution documentation](https://www.sqlite.org/lang_conflict.html).

Foreign-key enforcement is connection-specific. This was material to the old maintenance script: its separate plain `sqlite3` connection could not be assumed to inherit the application's protections. Removing its mutation path and opening it read-only addresses that concrete bypass. These guards are consistency protections, not an authorization boundary against an administrator who can alter the schema. [SQLite foreign-key documentation](https://www.sqlite.org/foreignkeys.html).

The author review covered every application ownership SQL write, historical migration order, all canonical save paths, URL and explicit linkage, staging gates, empty reviewed donors, replacement semantics, retry/idempotency behavior, current/legacy projection agreement, retained personal state and package contents. This is an author review, not independent approval. It identified and fixed the maintenance deletion path and the empty-donor retirement problem before publication.

## Verification and observability

Exact results, original-input hashes and package verification are in [the verification record](2026-10-10-pr07b3a-verification.json). Default tests block external sockets. Both supported Python runtimes use the existing locked dependencies and import this isolated worktree's source. Ruff lint/format, strict mypy and distribution checks are recorded there.

The tests include competing SQLite connections, transaction rollback and cancellation, partial/tampered/stale review maps, source-evidence changes, status changes away and back, exact retry after split, reversal of reversal, all ordinary save paths refusing an ownership theft, immutable evidence, migration failure/idempotency, stable-ID member queries, legacy links and read-only maintenance. The final full suites each pass **3,317 tests**: Python 3.14.7 at **88.34%** branch-aware coverage and Python 3.11.16 at **88.42%**. This includes 58 new regression cases. All 145 application files in the built wheel match the checkout byte-for-byte; the source archive includes the read-only audit utility and excludes private artifacts. No cloud CI result is implied.

Two fresh, socket-blocked copy rehearsals use the same immutable original inputs as PR07b-2:

| Copy | Preserved result |
| --- | --- |
| Historical schema 6 backup | Every original table/column row preserved; 1,016 homes, 1,217 aliases, 3,267 memberships; zero invented decisions; all 17 provider ownership conflicts across 28 homes retained |
| Historical affected prices | All eight price-history rows across six affected homes retained; the broader status/message/assessment/image/event history is unchanged |
| Recent schema 10 batch | Every original row preserved after merge → split and merge → reversal; all 35 saved home records and 55 observed source rows remain |
| Southborough | Both Rightmove IDs remain distinct; one membership changes on merge and returns on split/reversal |
| Wilmot | One Rightmove and two OnTheMarket IDs remain distinct; two memberships change and return |

The recent copy retains eight new reviewed decisions and twelve changes from these exercises, plus twenty projection invalidations. Restart and the immutable-history replay audit agree with the live ownership projections. Both databases pass integrity/foreign-key checks, and both original database SHA-256 values remain unchanged. No historical conflict was adjudicated or reconciled.

The separate offline 50-selection pipeline replay still yields **35 groups / 50 members** in both same-run and split-run cases. Known replays produce zero new work and both spending ledgers contain zero attempts. All input database and image artifacts retain their original hashes. This remains development replay evidence, not a fresh portal or paid quality run.

Structured events are `membership_decision_prepared`, `membership_decision_committed`, `membership_decision_replayed`, `membership_decision_refused`, `membership_decision_interrupted` and `membership_audit_finished`. They expose command ID, operation/revision, counts, refusal reason and conflict flags without logging message text, evidence bodies or URL-shaped source identifiers. An interrupted commit acknowledgement is reported as requiring inspection of the decision ID, never as a guessed rollback. `memberships.audit()` replays baselines/changes in memory and compares ownership, revision, origin and legacy projections without repairing data.

Local artifacts live under `/tmp/home-finder-2026-10-10/`: `pr07b3-all314.*`, `pr07b3-all311.*`, `membership-rehearsal-final/`, `membership-dedup-replay-final/`, `pr07b3-final-dist/` and `rehearse_membership.py`. Review JSON containing source IDs/personal summaries stays private there. The committed JSON contains aggregate evidence only. The 57 pre-existing owner-controlled files in the original workspace have unchanged preservation hashes.

For a normal checkout with the pinned tooling:

```sh
uv sync --locked --all-extras
uv run ruff check src tests scripts/audit_merges.py
uv run ruff format --check src tests scripts/audit_merges.py
uv run mypy src
uv run pytest --cov --cov-report=term
uv build
```

The existing GitHub CI workflow remains disabled by inactivity while the unmerged default-branch workflow still schedules live portal requests and automatic deployment. Local checks are the available validation for this draft; workflow activation still belongs after the operating-baseline review.

## Next eligible segment

**Follow-up now implemented:** [PR07b-3b](2026-10-10-pr07b3b-home-group-routing.md) is [draft #31](https://github.com/MarcelCutts/home-finder/pull/31), position 28. It completes the grouping, original-action provenance and recoverable retirement work described below. The next eligible segment is PR07b-4. The following text records this layer's original handoff boundary.

**PR07b-3b: integrate reversible home grouping and provenance.** Use this contract as the ownership writer. Add a reviewed home-group/routing projection without altering immutable origin identity or deleting old staging-redirect evidence. Define how a grouped card, an original issued link and a status/message action identify their original home/member provenance. Expose conflicting states rather than selecting a winner. Integrate retained donor rows with retry/dedup selection so they do not create duplicate work. Make new staging retirement recoverable, and explicitly refuse restoration where old deleted state is unavailable. Validate chained grouping, split, reversal, callback authorization and retained history on fixtures/copies.

**Then PR07b-4:** consume projection invalidations, carry every member ID through text, price, availability, images and links, and reconcile only reviewed evidence after complete consumers are ready. Keep same-portal automatic guards and matching thresholds unchanged. The 17 historical conflicts remain unresolved. PR08b/PR09b follow that foundation; no fresh portal/model/Telegram work or production cutover is authorized by these offline exercises.

The full delivery goal remains active. This PR is a reviewable implementation segment, not completion of PR07 or hosted-service readiness. The owner's instruction to commit each step and continue supersedes the original pause-after-segment wording in the stored goal.

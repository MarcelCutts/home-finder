# PR07b-3b — home groups with original personal-state provenance

Status: implementation, author review and local verification complete; published as [draft #31](https://github.com/MarcelCutts/home-finder/pull/31). Application commit: `588186b2c9684e1564871061aa3cd00e254ddccb`; the final publication commit changes documentation only. Base: `8350a650becaa6ae2311fe379b34538ff04a29c0`, [draft #30](https://github.com/MarcelCutts/home-finder/pull/30). Branch: `codex/stack/28-home-group-routing`; position 28 in native stack #29. This extends [PR07b-3a](2026-10-10-pr07b3a-membership-decisions.md), and completes the grouping/consumer part of PR07b-3. PR07b-4 remains required before active reviewed reconciliation.

## Outcome and boundary

A reviewed duplicate used to change source ownership while both original properties continued to appear in cards and work queues. Staging absorption also physically deleted the donor, preventing recovery of its original state. A single display resolver for status mutations would make an old link edit the surviving representative's personal state.

This segment gives a saved home two deliberately separate resolutions: its current display group and its original personal-action record. Grouped donors remain stored, leave active work selection, and retain their original status, messages, images, assessments, prices, delivery state and paid-attempt history. New staging retirement is journaled and reversible. Existing immutable staging redirects remain historical evidence.

This is one architectural boundary because routing, source ownership, selection and mutation provenance must agree at commit. Publishing only the routing table would leave duplicate work; switching only the detail resolver would transfer personal actions. The preceding journal remains the sole ownership writer. Complete member-derived caches are a separate next boundary because their policies extend beyond identity/routing.

Prerequisites: transaction ownership [#6](https://github.com/MarcelCutts/home-finder/pull/6), guarded preservation [#25](https://github.com/MarcelCutts/home-finder/pull/25), provider identity [#27](https://github.com/MarcelCutts/home-finder/pull/27), stable home links [#28](https://github.com/MarcelCutts/home-finder/pull/28), and journaled membership [#30](https://github.com/MarcelCutts/home-finder/pull/30). The predecessor handoff/body's reference to transaction ownership as #7 was a numbering error; #6 is the relevant layer.

## Contracts for subsequent agents

| Concept | Durable identity/state | Current behavior |
|---|---|---|
| Portal representation | `source_listings`, retained identity claims and observations | Every representation remains available; automatic matcher thresholds and same-source guards stay unchanged. |
| Original saved home | Immutable home ID/origin, retained property binding | Owns its status, messages and histories even while its display points elsewhere. |
| Membership | Existing baselines, decisions, changes and `source_memberships` | A non-null owner must be an active group representative with a retained property. |
| Display routing | New `home_route_baselines`, `home_route_changes`, `home_routes` | Flat map to a retained representative, or explicit `retired` / `review_required` state. Shares the membership decision revision/transaction. |
| Active work | `active_properties` read view | One row per active representative. Retained donors do not enter dedup anchors, enrichment/analysis/reanalysis, commute/ward, availability, notification or price-alert selection. |
| Personal-state presentation | `group_personal_states` read view | Includes routed original records and first-origin provenance of current members. A split original can remain represented in multiple groups without duplicating or transferring its personal state. |

Migration 014 creates three tables and two views, with immutable evidence and guarded projection updates. It derives initial routes from exact retained ownership/origins and legacy redirects. Current source owners remain distinct roots. Multiple original-member destinations without a current owner become `review_required`; cycles or missing redirect destinations are not guessed. Migration does not invent decisions or reconcile the 17 historical conflicts.

The journal appends source and route changes in the same owned transaction. Source ownership moves before donor routes become inactive. Commit-time validation checks retained, flat destinations and owner/root consistency. SQL guards reject unrecorded projection edits, replacement of evidence, and loss of a retained binding after route history exists. The audit independently replays both baselines/change streams and reports drift without repairing it. Legacy redirect insertion is closed; new saved-home routing must use the journal.

### Reviewed commands

Version 2 carries the complete scoped before/after route maps alongside every member assignment. Preparation expands the whole affected groups and includes source evidence and personal-state preconditions. Unchanged or partially specified decisions are refused. A merge can choose a retained alias as the new representative; all issued home routes flatten to that representative. A split must leave members in the original owner. Non-root aliases follow an unambiguous original-member destination; `alias_routes={original_home_id: destination_home_id}` is required when that provenance spans destinations.

`prepare_reversal` uses the recorded source and route changes. It refuses a later decision touching either changed projection, even when a later change returns to the same value. Newly retained automatic `staging_link` / `cleanup` events can be reversed, including a source returning to an unowned state. A never-promoted staging alias then ceases to resolve until that source is owned again. Reversing a reversal is supported; reversing the same decision twice is refused.

Exact committed version-1 reviews still return their historical receipt using parsed payload equality. Unapplied version-1 reviews need refreshing. Reversal of a legacy reviewed decision derives reviewable routing from retained memberships, since version 1 recorded no route history; ambiguous reassociation is refused. Older automatic deletion events have no recoverable route/state history and are not presented as reversible. Missing original records are never reconstructed from the surviving property's state.

Use the existing operator API only on an explicitly authorized database. `inspect`, preparation and apply each run in a short consistent write scope. Persist the prepared JSON for review, then pass the validated `MembershipDecision` to `apply`. Keep the original home UUIDs from that review for future splits; resolving a legacy link now returns its display representative, not its original action identity. A changed review revision or changed source/personal evidence requires preparing again. On an interrupted acknowledgement, inspect the decision ID and retry that exact payload; do not generate a new decision ID speculatively.

### Links and personal actions

`homes.resolve(identifier)` resolves the current display group. `resolve_action` resolves the exact original saved home. Canonical and legacy aliases retain original-home provenance. Multiple origins that now share one display group can open that group, but status/message mutations refuse the ambiguous action with HTTP 409 or a Telegram callback acknowledgement. An old deleted origin can still display its retained destination; it cannot silently modify that destination's status.

Cards, counts and map selection use active representatives. Cards with multiple saved records offer a record chooser and suppress quick actions; divergent statuses show “Mixed saved statuses”. Status filters match represented original statuses, so an interested/archived conflict remains discoverable under both filters. Detail links select their original record, show the group's saved records, and keep status history and message storage under that original key. Images and other current display projections still use the representative's key. The selected status label updates immediately after a successful browser action. Existing history timelines load from the selected origin on page load.

Telegram owner/secret validation, callback target grammar and update-ID deduplication remain intact. Stale notification payloads continue to fail the existing resolver check rather than sending a donor notification under the representative's identity.

### Retirement and spending

New absorption and standalone cleanup retain physical property rows. Replaying an absorption does not append another decision; cleanup of an already absorbed donor does not discard its issued link. Preservation checks extend over retained aliases of a staging group, so a status or history saved on an earlier donor prevents a later automatic absorption/cleanup of that group.

Ordinary saves cannot revive a reassociated home. An eligible cleanup-retired row can be reactivated through journaled linkage while retaining its original home identity. Splits/reversals restore selection only from the retained work states: completed assessments, delivered notifications and paid-attempt counts are not reset or copied.

Model admission refuses a known inactive home. If grouping occurs after reservation but before dispatch, the unused reservation is durably released with zero accounted cost and dispatch is refused. A request already marked dispatched retains its existing accounting/recovery semantics; this change cannot cancel a provider request already in flight. No external model request was needed to test these paths.

## Author review and research

Reviewed every property selector, source-owner write, routing resolution, personal action, migration guard and package change. The review explicitly traced chained staging groups, representative changes, split ambiguity, v1 payload compatibility, retry after later split, route/source cancellation and rollback, and two-connection review conflicts.

Findings fixed during review:

- Retained aliases with saved user/history state must participate in the staging preservation gate.
- Cleanup of an already absorbed donor must be a no-op rather than erase its display route.
- Model reservations made before a grouping need a second inactive-home check before dispatch.
- Grouped cards must expose status conflicts and remove ambiguous quick actions; detail actions must retain original provenance.
- The selected record's rendered status needs immediate synchronization after a status change.
- An old end-to-end retry fixture bypassed identity creation with raw SQL; it now uses the actual save and notification-failure APIs. The old destructive-retirement fault injection now faults the route write and still proves full rollback.

This is an author review, not independent approval. External checks used primary documentation: SQLite [views](https://www.sqlite.org/lang_createview.html) are read-only selection abstractions, [trigger semantics](https://www.sqlite.org/lang_createtrigger.html) support the evidence/projection guards, and [Telegram callback handling](https://core.telegram.org/bots/api#callbackquery) requires acknowledgement even when a mutation is refused. The earlier journal review covers SQLite REPLACE/foreign-key caveats; new route evidence also rejects replacement. No claim is made that SQL triggers protect against a database administrator deliberately removing guards.

## Verification

Final matrix results and artifact hashes: [verification record](2026-10-10-pr07b3b-verification.json). Both full socket-blocked suites pass **3,348 tests**, including 31 new tests: Python **3.14.7 / 88.31%** coverage (four workers), and **3.11.16 / 88.38%** (eight workers). A final ambiguous Telegram acknowledgement assertion was added after full-suite collection; all four group web tests were rerun and passed on each interpreter against unchanged application code. Ruff lint/format, strict mypy (114 source files), and diff checks pass. The wheel contains all 148 application files byte-for-byte; the source archive includes the final fixtures and read-only audit utility and excludes private artifacts.

The original restored Python 3.14 parallel run ended with SIGTERM and no complete result; it is not counted. The successful four-worker rerun is the reported result. The Python 3.11 run initially found the raw-SQL retry fixture described above; its corrected full rerun is the reported result.

Fresh, socket-blocked historical-copy migration retains 1,016 homes, 1,217 home aliases, 3,267 memberships, all original table rows, and all 17 ownership conflicts across 28 homes, including eight price-history rows across six affected homes. It creates 1,016 route baselines and zero invented decisions. Restart is idempotent; integrity, foreign-key and replay audits pass.

The recent copy retains 35 homes and 55 source representations. Southborough and Wilmot exercises run merge → split → old-command replay and merge → reversal. Old issued links display the selected representative during grouping and return to their original destinations after reversal. All original rows are restored/preserved; the exercises append eight decisions, twelve member changes, twelve route changes and twenty invalidations. Both input database SHA-256 values remain unchanged.

The retained fifty-property replay remains 35 groups / 50 selected members in both same-run and split-run shapes, with no repeat work for known listings, zero model attempts and unchanged database/image input hashes. This is development-fixture replay, not a new portal canary or independent deduplication quality benchmark.

Local Playwright review uses synthetic records and a loopback-only server with the pipeline disabled and Telegram registration unconfigured. It checks original-record selection, status mutation, immediate label synchronization, desktop/mobile layout, and the database's exact status-event ownership. The representative stays interested; only the selected donor becomes viewing-booked. Browser console: zero errors/warnings. Both viewports have no horizontal overflow. Sanitized screenshots: [group card](../reviews/images/2026-10-10-home-group-card.png), [desktop detail](../reviews/images/2026-10-10-home-group-detail.png), and [mobile detail](../reviews/images/2026-10-10-home-group-mobile.png).

The temporary worktree/runtime disappeared during interruption. The committed stack and original workspace remained intact; the unfinished edits were recovered from this thread's recorded edit commands, checkpointed locally, and verified again in persistent `.worktrees/pr07b3b`. Missing earlier temporary full-suite output is not counted as successful verification. Private copied databases, detailed evidence and runtime artifacts now live in `.worktrees/evidence/28-home-groups` and are excluded from commits.

## Limits and next eligible segment

Proceed to **PR07b-4: complete member projections and reviewed reconciliation**. Consume the immutable invalidations; make source links, text, prices, availability, images and assessment inputs carry every member identity, including multiple distinct advertisements on one portal. Define how stale assessments/paid work are invalidated without losing provenance or causing duplicate charges. Validate representative changes and every split/reversal against these projections, and add a concrete review/dry-run surface before applying a reconciliation to active data.

The current operator API remains exercised on fixtures and copies. Current display caches may still describe the previous representative or collapse portal-keyed members; routing integration alone is not active-reconciliation readiness. The 17 historical conflicts remain unresolved. Matcher thresholds and the automatic same-source guard remain unchanged. PR08b/PR09b and eventual multi-user isolation/durable delivery follow the established plan; this segment does not add a service or change the intended modest hosted scale.

Remote CI remains `disabled_inactivity`; unmerged `main` still contains scheduled live portal tests and automatic deployment. No workflows were enabled, PRs merged, deployment performed, production data changed, portal fetched, model charged or Telegram notification sent for this segment. Local preview asset loading is separate from portal/model work. The delivery goal remains active under the owner's instruction to commit segments and continue.
